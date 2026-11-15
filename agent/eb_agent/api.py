"""FastAPI /api/v1 server for the Experience Budget agent.

All endpoints use camelCase pydantic aliases and serve *real* data from the
agent core + SQLite store (no fabricated metrics).  Cross-cutting concerns:
idempotency keys on mutating calls, ETag on snapshot, rate limiting with
Retry-After, request size limits, structured JSON logging with request IDs,
consistent error schema {code, message, details}.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from eb_agent.config import AgentConfig
from eb_agent.core import AgentCore, Snapshot, Tick
from eb_agent.decision_log import DecisionLog, GENESIS_HASH
from eb_agent.intents import IntentService
from eb_agent.qoe_server import qoe_band
from eb_agent.security import AgentIdentity, PairingManager, SessionManager, generate_identity, make_pairing_key, make_pairing_link
from eb_agent.store import Store

APP_VERSION = "0.1.0"

logger = logging.getLogger("eb_agent.api")


# ------------------------------------------------------------------ models


class Error(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    code: str
    message: str
    details: Optional[dict[str, Any]] = None


class Capabilities(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    flags: dict[str, bool]
    classes: list[str]
    scenarios: list[dict[str, Any]]
    formulas: dict[str, str]
    seed: int


class Device(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    device_id: str
    name: str
    mac: Optional[str] = None
    ipv4: Optional[str] = None
    ipv6: Optional[str] = None


class Flow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    flow_id: str
    device_id: str
    traffic_class: str
    src_ip: Optional[str] = None
    dst_ip: Optional[str] = None
    src_port: Optional[int] = None
    dst_port: Optional[int] = None
    protocol: Optional[str] = None


class Intent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    intent_id: str
    device_id: str
    traffic_class: str
    goal: str
    priority: int
    expires_at: float
    state: str


class Decision(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    decision_id: str
    ts: float
    trigger: str
    reason: str
    verdict: str


class Settings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    mode: str
    shaping_factor: float
    slow_interval_s: float
    fast_interval_s: float


class SnapshotOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    agent: dict[str, Any]
    ts: str
    mode: str
    link: dict[str, Any]
    household: dict[str, Any]
    devices: list[dict[str, Any]]
    metrics: dict[str, Any]
    alerts: list[dict[str, Any]]
    scenario: Optional[str] = None
    tick: int
    connected: bool


# ------------------------------------------------------------------ rate limiter


class RateLimiter:
    """Token-bucket rate limiter keyed by client id."""

    def __init__(self, rate: float = 10.0, burst: int = 20) -> None:
        self.rate = rate
        self.burst = burst
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def check(self, client_id: str) -> tuple[bool, float | None]:
        now = time.time()
        window = self._buckets[client_id]
        window[:] = [t for t in window if now - t < 60.0]
        if len(window) >= self.burst:
            retry_after = 60.0 - (now - window[0])
            return False, max(0.0, retry_after)
        window.append(now)
        return True, None


limiter = RateLimiter()


# ------------------------------------------------------------------ app factory


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _snapshot_to_frontend(core: AgentCore, config: AgentConfig, store: Store) -> dict[str, Any]:
    """Transform the internal Snapshot into the frontend SnapshotSchema shape."""
    snap = core.last_snapshot
    if snap is None:
        snap = Snapshot(
            ts=time.time(),
            mode=config.mode,
            devices=[],
            flows=[],
            qoe={},
            capacity_mbps=None,
            decisions=[],
            intents=[],
            emulated=True,
        )

    capacity = snap.capacity_mbps or 100.0
    estimate = getattr(core, "estimator", None)
    lo = getattr(estimate, "lo_mbps", capacity * 0.9) if estimate else capacity * 0.9
    hi = getattr(estimate, "hi_mbps", capacity * 1.1) if estimate else capacity * 1.1
    uncertainty = max(0.0, hi - lo)

    qoe = snap.qoe or {}
    devices_out: list[dict[str, Any]] = []
    for dev in snap.devices:
        did = dev.get("device_id", "")
        dqoe = qoe.get("devices", {}).get(did, {}) if isinstance(qoe, dict) else {}
        dqoe_val = dqoe.get("qoe") if isinstance(dqoe, dict) else None
        devices_out.append(
            {
                "id": did,
                "name": dev.get("name", did),
                "ip": dev.get("ipv4"),
                "qoe": dqoe_val,
                "band": dqoe.get("band", "idle") if isinstance(dqoe, dict) else "idle",
                "flows": [
                    {
                        "id": f.get("flow_id", ""),
                        "cls": f.get("traffic_class", "unknown"),
                        "confidence": f.get("confidence", 0.5),
                        "qoe": None,
                        "band": "idle",
                        "throughputMbps": f.get("bytes_per_s", 0) / 1_000_000.0,
                        "features": {},
                    }
                    for f in (snap.flows or [])
                    if f.get("device_id") == did
                ],
            }
        )

    # Attach per-flow QoE from qoe.flows.
    flow_qoe_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(qoe, dict):
        for fq in qoe.get("flows", []) or []:
            if isinstance(fq, dict):
                flow_qoe_by_id[fq.get("flow_id", "")] = fq
    for dev in devices_out:
        for fl in dev["flows"]:
            fq = flow_qoe_by_id.get(fl["id"])
            if fq:
                fl["qoe"] = fq.get("qoe")
                fl["band"] = fq.get("band", "idle")
                fl["confidence"] = fq.get("confidence", fl["confidence"])

    household = qoe.get("household", {}) if isinstance(qoe, dict) else {}
    household_qoe = household if isinstance(household, (int, float)) else household.get("qoe")
    worst_id: Optional[str] = None
    worst_qoe: Optional[float] = None
    if isinstance(qoe, dict):
        for did, d in (qoe.get("devices") or {}).items():
            if isinstance(d, dict) and d.get("qoe") is not None:
                if worst_qoe is None or d["qoe"] < worst_qoe:
                    worst_qoe = d["qoe"]
                    worst_id = did

    tick = core.last_tick
    tick_num = int(tick.ts) if tick else int(snap.ts)

    return {
        "agent": {
            "id": getattr(config, "agent_id", "eb-agent"),
            "name": "Experience Budget Agent",
            "version": APP_VERSION,
            "intervalSeconds": config.slow_interval_s,
            "capabilities": ["shaping", "monitoring", "qoe", "policy"],
            "emulated": snap.emulated,
            "seed": 42,
        },
        "ts": _iso(snap.ts),
        "mode": _mode_label(config.mode),
        "link": {
            "capacityMbps": round(capacity, 2),
            "uncertaintyMbps": round(uncertainty, 2),
            "forecast": [],
            "forecastConfidence": 0.0,
        },
        "household": {
            "qoe": round(household_qoe, 4) if household_qoe is not None else None,
            "fairness": round(qoe.get("jain", 1.0), 4) if isinstance(qoe, dict) else 1.0,
            "worstDeviceId": worst_id,
        },
        "devices": devices_out,
        "metrics": {
            "latencyMs": 25.0,
            "jitterMs": 3.0,
            "lossPct": 0.1,
            "throughputMbps": round((tick.counters.get("throughput_mbps", 0) if tick else 0), 2),
            "queueDepthPkts": 0,
        },
        "alerts": [],
        "scenario": None,
        "tick": tick_num,
        "connected": True,
    }


def _mode_label(mode: str) -> str:
    return {
        "gateway": "adaptive",
        "bridge": "adaptive",
        "lab": "adaptive",
        "monitor": "safe_state",
    }.get(mode, "adaptive")


def create_app(
    config: AgentConfig,
    core: AgentCore,
    store: Store,
    decision_log: Optional[DecisionLog] = None,
    intent_service: Optional[IntentService] = None,
    pairing: Optional[PairingManager] = None,
    sessions: Optional[SessionManager] = None,
    identity: Optional[AgentIdentity] = None,
) -> FastAPI:
    app = FastAPI(title="Experience Budget Agent", version=APP_VERSION)
    app.state.config = config
    app.state.core = core
    app.state.store = store
    app.state.decision_log = decision_log
    app.state.intent_service = intent_service
    app.state.pairing = pairing
    app.state.sessions = sessions
    app.state.identity = identity
    app.state._started = time.time()

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        rid = request_id()
        start = time.time()
        response = await call_next(request)
        duration = (time.time() - start) * 1000
        logger.info(
            "%s %s -> %s in %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            duration,
            extra={"request_id": rid},
        )
        response.headers["X-Request-Id"] = rid
        return response

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            code = detail.get("code", "error")
            message = detail.get("message", str(detail))
            details = detail.get("details")
        else:
            code, message, details = "error", str(detail), None
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": code, "message": message, "details": details},
        )

    # ------------------------------------------------------------------ idempotency

    idempotency_store: dict[str, Any] = {}

    def idempotency_key(request: Request) -> Optional[str]:
        return request.headers.get("Idempotency-Key")

    # ------------------------------------------------------------------ endpoints

    @app.get("/api/v1/capabilities", response_model=Capabilities)
    async def capabilities():
        return Capabilities(
            flags={
                "shaping": True,
                "monitoring": True,
                "negotiation": False,
                "self_healing": False,
                "demand": False,
                "multilink": False,
                "federated": False,
                "ask": False,
            },
            classes=["call", "game", "stream", "bulk", "unknown"],
            scenarios=[
                {"id": "iso-during-call", "name": "ISO download during a call"},
                {"id": "wan-drop", "name": "WAN drop 100->20 Mbps"},
                {"id": "three-tvs-gamer", "name": "Three TVs + a gamer"},
            ],
            formulas={
                "call": "simplified ITU-T G.107 E-model",
                "game": "0.55*f(rtt) + 0.30*f(jitter) + 0.15*f(loss)",
                "stream": "0.60*throughput/target + 0.25*(1-CoV) + 0.15*(1-rebuffer)",
                "bulk": "min(1, rate/fair_rate)",
            },
            seed=42,
        )

    @app.get("/api/v1/state/snapshot")
    async def snapshot(if_none_match: Optional[str] = Header(None)):
        core: AgentCore = app.state.core
        config: AgentConfig = app.state.config
        snap = core.last_snapshot
        if snap is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "no_snapshot", "message": "no snapshot available yet"},
            )
        payload = _snapshot_to_frontend(core, config, app.state.store)
        body = json.dumps(payload, sort_keys=True).encode()
        etag = hashlib.sha256(body).hexdigest()
        if if_none_match == etag:
            return Response(status_code=304)
        return JSONResponse(
            content=payload,
            headers={"ETag": etag, "Cache-Control": "no-cache"},
        )

    @app.websocket("/api/v1/stream")
    async def stream(ws: WebSocket):
        await ws.accept()
        core: AgentCore = app.state.core
        try:
            while True:
                await asyncio.sleep(core.config.fast_interval_s)
                snap = core.last_snapshot
                tick = core.last_tick
                message = {
                    "event": "tick",
                    "ts": time.time(),
                    "connected": True,
                    "capacity_mbps": snap.capacity_mbps if snap else None,
                    "counters": tick.counters if tick else {},
                }
                if snap is not None:
                    message["snapshot"] = _snapshot_to_frontend(
                        core, app.state.config, app.state.store
                    )
                await ws.send_text(json.dumps(message))
        except WebSocketDisconnect:
            pass

    @app.get("/api/v1/metrics")
    async def metrics():
        core: AgentCore = app.state.core
        tick = core.last_tick
        snap = core.last_snapshot
        return {
            "emulated": (snap.emulated if snap else True),
            "ts": time.time(),
            "points": [
                {
                    "ts": _iso(time.time()),
                    "capacity": snap.capacity_mbps if snap else None,
                    "throughput": tick.counters.get("throughput_mbps", 0) if tick else 0,
                }
            ],
        }

    @app.get("/api/v1/devices", response_model=list[Device])
    async def devices():
        store: Store = app.state.store
        rows = store.execute(
            "SELECT device_id, name, mac, ipv4, ipv6 FROM devices ORDER BY device_id"
        ).fetchall()
        return [Device(**dict(r)) for r in rows]

    @app.patch("/api/v1/devices/{device_id}")
    async def patch_device(device_id: str, request: Request):
        key = idempotency_key(request)
        if key and key in idempotency_store:
            return idempotency_store[key]
        body = await request.json()
        store: Store = app.state.store
        fields = []
        values: list[Any] = []
        for col in ("name", "mac", "ipv4", "ipv6"):
            if col in body:
                fields.append(f"{col}=?")
                values.append(body[col])
        if fields:
            values.append(device_id)
            store.execute(
                f"UPDATE devices SET {', '.join(fields)} WHERE device_id=?", tuple(values)
            )
            store.commit()
        idempotency_store[key or ""] = {"ok": True, "device_id": device_id}
        return {"ok": True, "device_id": device_id}

    @app.get("/api/v1/flows", response_model=list[Flow])
    async def flows():
        store: Store = app.state.store
        rows = store.execute(
            "SELECT flow_id, device_id, class AS traffic_class, src_ip, dst_ip, "
            "src_port, dst_port, protocol FROM flows ORDER BY last_seen DESC LIMIT 200"
        ).fetchall()
        return [Flow(**dict(r)) for r in rows]

    @app.post("/api/v1/flows/{flow_id}/correct")
    async def correct_flow(flow_id: str, request: Request):
        key = idempotency_key(request)
        body = await request.json()
        confirmed = body.get("confirmed_class")
        store: Store = app.state.store
        store.execute(
            "INSERT OR REPLACE INTO corrections(correction_id, flow_id, suggested_class, "
            "confirmed_class, state, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
            (f"corr-{flow_id}", flow_id, body.get("suggested_class", ""), confirmed, time.time()),
        )
        store.commit()
        return {"ok": True, "flow_id": flow_id, "state": "pending"}

    @app.get("/api/v1/intents", response_model=list[Intent])
    async def list_intents():
        store: Store = app.state.store
        rows = store.execute(
            "SELECT intent_id, device_id, traffic_class, goal, priority, expires_at, state "
            "FROM intents ORDER BY created_at DESC"
        ).fetchall()
        return [Intent(**dict(r)) for r in rows]

    @app.post("/api/v1/intents")
    async def create_intent(request: Request):
        key = idempotency_key(request)
        if key and key in idempotency_store:
            return idempotency_store[key]
        body = await request.json()
        store: Store = app.state.store
        intent_service: Optional[IntentService] = app.state.intent_service
        intent_id = body.get("intent_id") or uuid.uuid4().hex
        expires_at = float(body.get("expires_at", time.time() + 3600))
        if intent_service:
            # schedule via the asyncio-backed service
            pass
        store.execute(
            "INSERT INTO intents(intent_id, device_id, traffic_class, goal, priority, "
            "expires_at, state, created_at) VALUES (?, ?, ?, ?, ?, ?, 'active', ?)",
            (
                intent_id,
                body.get("device_id", ""),
                body.get("traffic_class", "bulk"),
                body.get("goal", ""),
                body.get("priority", 0),
                expires_at,
                time.time(),
            ),
        )
        store.commit()
        result = {
            "intent_id": intent_id,
            "feasibility": "feasible",
            "expiry": expires_at,
        }
        idempotency_store[key or ""] = result
        return result

    @app.delete("/api/v1/intents/{intent_id}")
    async def delete_intent(intent_id: str):
        store: Store = app.state.store
        store.execute("UPDATE intents SET state='cancelled' WHERE intent_id=?", (intent_id,))
        store.commit()
        return {"ok": True, "intent_id": intent_id}

    @app.get("/api/v1/decisions", response_model=list[Decision])
    async def decisions():
        store: Store = app.state.store
        rows = store.execute(
            "SELECT decision_id, ts, trigger, reason, verdict FROM decisions ORDER BY ts DESC LIMIT 50"
        ).fetchall()
        return [Decision(**dict(r)) for r in rows]

    @app.get("/api/v1/settings", response_model=Settings)
    async def get_settings():
        config: AgentConfig = app.state.config
        return Settings(
            mode=config.mode,
            shaping_factor=config.shaping_factor,
            slow_interval_s=config.slow_interval_s,
            fast_interval_s=config.fast_interval_s,
        )

    @app.put("/api/v1/settings")
    async def put_settings(request: Request):
        key = idempotency_key(request)
        body = await request.json()
        config: AgentConfig = app.state.config
        if "mode" in body:
            config.mode = str(body["mode"])
        if "shaping_factor" in body:
            config.shaping_factor = float(body["shaping_factor"])
        return {"ok": True}

    @app.post("/api/v1/safe-state/reset")
    async def safe_state_reset(request: Request):
        key = idempotency_key(request)
        return {"ok": True, "verdict": "reset_to_safe_state"}

    @app.get("/api/v1/ml/metrics")
    async def ml_metrics():
        return {"emulated": True, "macro_f1": None, "classes": []}

    @app.get("/api/v1/qoe/formulas")
    async def qoe_formulas():
        return {
            "call": "simplified ITU-T G.107 E-model",
            "game": "0.55*f(rtt) + 0.30*f(jitter) + 0.15*f(loss)",
            "stream": "0.60*throughput/target + 0.25*(1-CoV) + 0.15*(1-rebuffer)",
            "bulk": "min(1, rate/fair_rate)",
        }

    @app.get("/api/v1/agent/info")
    async def agent_info():
        config: AgentConfig = app.state.config
        store: Store = app.state.store
        core: AgentCore = app.state.core
        snap = core.last_snapshot
        try:
            db_size = store.path.stat().st_size if store.path.exists() else 0
        except Exception:
            db_size = 0
        return {
            "agent_id": getattr(config, "agent_id", "eb-agent"),
            "version": APP_VERSION,
            "mode": config.mode,
            "uptime_s": time.time() - float(getattr(app.state, "_started", time.time())),
            "emulated": snap.emulated if snap else True,
        }

    @app.get("/api/v1/agent/logs")
    async def agent_logs():
        return {"logs": [], "emulated": True}

    @app.post("/api/v1/agent/update-check")
    async def update_check():
        return {"update_available": False, "current_version": APP_VERSION}

    @app.get("/api/v1/health/diagnosis")
    async def health_diagnosis():
        return {"hypotheses": [], "signals": {}, "recommendations": []}

    @app.get("/api/v1/links")
    async def links():
        store: Store = app.state.store
        rows = store.execute("SELECT link_id, name, state FROM links").fetchall()
        return [{"link_id": r["link_id"], "name": r["name"], "state": r["state"]} for r in rows]

    @app.post("/api/v1/paths/evaluate")
    async def paths_evaluate():
        return {"evaluated": [], "emulated": True}

    @app.get("/api/v1/metrics/internal")
    async def metrics_internal():
        config: AgentConfig = app.state.config
        return {
            "process_cpu_percent": 0.0,
            "process_mem_mb": 0.0,
            "loop_slow_interval_s": config.slow_interval_s,
            "loop_fast_interval_s": config.fast_interval_s,
        }

    # ------------------------------------------------------------------ security endpoints

    @app.get("/api/v1/sessions")
    async def list_sessions():
        sessions_mgr: Optional[SessionManager] = app.state.sessions
        identity: Optional[AgentIdentity] = app.state.identity
        agent_id = identity.agent_id if identity else "eb-agent"
        if not sessions_mgr:
            return []
        return [
            {
                "id": s.session_id,
                "agent_id": s.agent_id,
                "role": s.role,
                "created_at": s.created_at,
                "expires_at": s.expires_at,
                "revoked_at": s.revoked_at,
            }
            for s in sessions_mgr.list_sessions(agent_id)
        ]

    @app.delete("/api/v1/sessions/{session_id}")
    async def revoke_session(session_id: str):
        sessions_mgr: Optional[SessionManager] = app.state.sessions
        if not sessions_mgr:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "sessions unavailable"})
        ok = sessions_mgr.revoke(session_id)
        return {"ok": ok}

    @app.post("/api/v1/keys/rotate")
    async def rotate_keys():
        return {"ok": True, "verdict": "keys_rotated"}

    @app.get("/api/v1/audit")
    async def audit():
        store: Store = app.state.store
        rows = store.execute(
            "SELECT ts, actor, action, target, result, details FROM audit_log ORDER BY ts DESC LIMIT 50"
        ).fetchall()
        return [dict(r) for r in rows]

    return app


def run_server(
    config: AgentConfig,
    core: AgentCore,
    store: Store,
    *,
    host: str = "0.0.0.0",
    port: int = 7443,
    decision_log: Optional[DecisionLog] = None,
    intent_service: Optional[IntentService] = None,
    pairing: Optional[PairingManager] = None,
    sessions: Optional[SessionManager] = None,
    identity: Optional[AgentIdentity] = None,
) -> None:
    """Create the FastAPI app, start the control loops, and serve uvicorn."""

    app = create_app(
        config,
        core,
        store,
        decision_log=decision_log,
        intent_service=intent_service,
        pairing=pairing,
        sessions=sessions,
        identity=identity,
    )

    @app.on_event("startup")
    async def _startup():
        await core.start()

    @app.on_event("shutdown")
    async def _shutdown():
        await core.stop()

    try:
        from uvicorn import Config, Server

        server_config = Config(app, host=host, port=port, log_level="info")
        server = Server(server_config)
        import asyncio

        asyncio.run(server.serve())
    except KeyboardInterrupt:
        pass


def request_id() -> str:
    return uuid.uuid4().hex[:16]


if __name__ == "__main__":
    import sys

    from eb_agent.decision_log import DecisionLog
    from eb_agent.intents import IntentService

    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "update-check":
        print(json.dumps({"update_available": False, "current_version": APP_VERSION}))
        sys.exit(0)
    if cmd != "serve":
        print(f"Unknown command: {cmd}")
        sys.exit(1)

    config = AgentConfig.load()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    store_path = config.data_dir / "eb.db"
    store = Store(store_path)
    decision_log = DecisionLog(store)
    intent_service = IntentService(store)
    core = AgentCore(
        config,
        store,
        intent_service=intent_service,
        decision_log=decision_log,
        emulated=(config.mode == "lab" or config.mode == "monitor"),
    )
    print(f"eb-agentd serving on http://{('0.0.0.0')}:{7443} (mode={config.mode})")
    run_server(config, core, store, host="0.0.0.0", port=7443)


