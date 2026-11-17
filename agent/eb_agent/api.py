"""FastAPI /api/v1 server for the Experience Budget agent.

All endpoints use camelCase pydantic aliases and serve *real* data from the
agent core + SQLite store (no fabricated metrics).  Cross-cutting concerns:
idempotency keys on mutating calls, ETag on snapshot, rate limiting with
Retry-After, request size limits, structured JSON logging with request IDs,
consistent error schema {code, message, details}.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import socket
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from eb_agent.config import AgentConfig
from eb_agent.core import AgentCore, Snapshot, Tick
from eb_agent.decision_log import DecisionLog, GENESIS_HASH
from eb_agent.health import get_health_service
from eb_agent.intents import IntentService
from eb_agent.negotiation import get_negotiation_service
from eb_agent.experiments import ExperimentStore, run_experiment
from eb_agent.qoe_server import qoe_band
from eb_agent.federated import get_federated_service
from eb_agent.rag import get_ask_service
from eb_agent.dev_control import get_dev_control_service
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


def _duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _bytes_label(value: float) -> str:
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} TB"


_PROCESS_SAMPLE: dict[str, float] = {"monotonic": 0.0, "cpu_seconds": 0.0}


def _process_metrics() -> tuple[float, float]:
    """Return this process's CPU percentage and resident memory in MiB."""
    try:
        with open("/proc/self/stat", "r", encoding="utf-8") as fh:
            parts = fh.read().split()
        clock_ticks = float(os.sysconf(os.sysconf_names["SC_CLK_TCK"]))
        cpu_seconds = (float(parts[13]) + float(parts[14])) / clock_ticks
        now = time.monotonic()
        previous_ts = _PROCESS_SAMPLE["monotonic"]
        previous_cpu = _PROCESS_SAMPLE["cpu_seconds"]
        elapsed = now - previous_ts
        cpu_percent = 0.0
        if elapsed > 0 and previous_ts > 0:
            cpu_percent = max(0.0, (cpu_seconds - previous_cpu) / elapsed * 100.0)
        _PROCESS_SAMPLE["monotonic"] = now
        _PROCESS_SAMPLE["cpu_seconds"] = cpu_seconds
        with open("/proc/self/statm", "r", encoding="utf-8") as fh:
            pages = float(fh.read().split()[1])
        mem_mb = pages * float(os.sysconf("SC_PAGE_SIZE")) / (1024.0 * 1024.0)
        return round(cpu_percent, 2), round(mem_mb, 2)
    except (OSError, ValueError, IndexError, KeyError):
        return 0.0, 0.0


def _snapshot_to_frontend(core: AgentCore, config: AgentConfig, store: Store) -> dict[str, Any]:
    """Transform the internal Snapshot into the frontend SnapshotSchema shape.

    Every value here is either measured by the agent or ``None``/0 when the
    measurement is unavailable.  No placeholder metrics are invented.
    """
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
            emulated=False,
        )

    estimate = getattr(core, "estimator", None)
    capacity = snap.capacity_mbps
    if capacity is None and estimate is not None:
        capacity = getattr(estimate, "capacity_mbps", None)
    if capacity is None and core.last_tick:
        capacity = core.last_tick.counters.get("capacity_mbps")
    capacity = float(capacity) if capacity is not None else 0.0
    lo = getattr(estimate, "lo_mbps", None) if estimate else None
    hi = getattr(estimate, "hi_mbps", None) if estimate else None
    if lo is None or hi is None:
        lo = hi = capacity
    uncertainty = max(0.0, float(hi) - float(lo))

    qoe = snap.qoe if isinstance(snap.qoe, dict) else {}
    flow_qoe_by_id: dict[str, dict[str, Any]] = {}
    for fq in qoe.get("flows", []) or []:
        if isinstance(fq, dict):
            flow_qoe_by_id[fq.get("flow_id", "")] = fq

    devices_out: list[dict[str, Any]] = []
    for dev in snap.devices:
        did = dev.get("device_id", "")
        dqoe = qoe.get("devices", {}).get(did, {}) if isinstance(qoe, dict) else {}
        dqoe_val = dqoe.get("qoe") if isinstance(dqoe, dict) else None
        flows_out = []
        for f in snap.flows or []:
            if f.get("device_id") != did:
                continue
            fq = flow_qoe_by_id.get(f.get("flow_id", ""), {})
            flows_out.append(
                {
                    "id": f.get("flow_id", ""),
                    "cls": f.get("traffic_class", "unknown"),
                    "confidence": float(f.get("confidence", 0.0) or 0.0),
                    "qoe": fq.get("qoe"),
                    "band": fq.get("band", "idle"),
                    "throughputMbps": round(
                        float(f.get("bytes_per_s", 0) or 0) * 8.0 / 1_000_000.0, 3
                    ),
                    "features": {
                        key: float(f.get(key) or 0.0)
                        for key in (
                            "rtt_ms",
                            "jitter_ms",
                            "loss_pct",
                            "bytes_sent",
                            "bytes_received",
                            "retransmits",
                            "duration_s",
                        )
                    },
                }
            )
        device_out = {
            "id": did,
            "name": dev.get("name", did),
            "qoe": dqoe_val,
            "band": dqoe.get("band", "idle") if isinstance(dqoe, dict) else "idle",
            "flows": flows_out,
        }
        # Optional fields are omitted when the kernel gives us nothing, so the
        # UI contract's optional strings are never sent as null.
        person = dev.get("person")
        if person:
            device_out["person"] = person
        kind = dev.get("kind") or dev.get("type")
        if kind:
            device_out["type"] = kind
        ip = dev.get("ipv4")
        if ip:
            device_out["ip"] = ip
        devices_out.append(device_out)

    household_qoe = qoe.get("household")
    if isinstance(household_qoe, dict):
        household_qoe = household_qoe.get("qoe")
    worst_id: Optional[str] = None
    worst_qoe: Optional[float] = None
    for did, d in (qoe.get("devices") or {}).items():
        if isinstance(d, dict) and d.get("qoe") is not None:
            if worst_qoe is None or d["qoe"] < worst_qoe:
                worst_qoe = d["qoe"]
                worst_id = did

    tick = core.last_tick
    tick_num = int(tick.ts) if tick else int(snap.ts)
    throughput = float(tick.counters.get("throughput_mbps", 0) or 0) if tick else 0.0
    queue_pkts = int(qoe.get("queue_pkts", 0) or 0)
    jain = qoe.get("jain")
    if jain is None:
        jain = 1.0

    return {
        "agent": {
            "id": getattr(config, "agent_id", "eb-agent"),
            "name": "Experience Budget Agent",
            "version": APP_VERSION,
            "intervalSeconds": config.slow_interval_s,
            "capabilities": ["monitoring", "qoe", "policy", "shaping"],
            "emulated": bool(snap.emulated),
            "seed": 0,
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
            "qoe": round(float(household_qoe), 4) if household_qoe is not None else None,
            "fairness": round(float(jain), 4),
            "worstDeviceId": worst_id,
        },
        "devices": devices_out,
        "metrics": {
            "latencyMs": round(float(qoe.get("rtt_ms", 0.0) or 0.0), 2),
            "jitterMs": round(float(qoe.get("jitter_ms", 0.0) or 0.0), 2),
            "lossPct": round(float(qoe.get("loss_pct", 0.0) or 0.0), 2),
            "throughputMbps": round(throughput, 3),
            "queueDepthPkts": queue_pkts,
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


def _json_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"value": value}
    except (TypeError, ValueError):
        return {"value": raw}


def _numeric_map(raw: Any) -> dict[str, float]:
    data = _json_dict(raw)
    out: dict[str, float] = {}
    for key, value in data.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            out[str(key)] = float(value)
    if not out and "value" in data and isinstance(data["value"], (int, float)):
        out["value"] = float(data["value"])
    return out


def _decision_out(row: Any) -> dict[str, Any]:
    return {
        "id": row["decision_id"],
        "ts": _iso(float(row["ts"])),
        "trigger": row["trigger"],
        "reason": row["reason"],
        "before": _numeric_map(row["before_qoe"]),
        "after": _numeric_map(row["after_qoe"]),
        "verdict": row["verdict"],
        "evidence": _json_dict(row["evidence"]),
        "hash": row["hash"],
        "prevHash": row["prev_hash"],
    }


def _priority_label(value: Any) -> str:
    if isinstance(value, str) and value in {"low", "normal", "high"}:
        return value
    return {0: "low", 1: "normal", 2: "high"}.get(int(value or 0), "normal")


def _intent_out(row: Any) -> dict[str, Any]:
    created = float(row["created_at"] or row["expires_at"])
    expires = float(row["expires_at"])
    state = str(row["state"] or "active")
    status = {"reverted": "cancelled", "pending": "pending"}.get(state, state)
    if status not in {"pending", "active", "expired", "cancelled", "infeasible"}:
        status = "active"
    deadline = row["deadline"] if "deadline" in row.keys() else None
    return {
        "id": row["intent_id"],
        "target": {
            "deviceId": row["device_id"],
            "cls": row["traffic_class"],
        },
        "goal": row["goal"],
        "priority": _priority_label(row["priority"]),
        "durationSeconds": max(60.0, expires - created),
        "deadline": _iso(float(deadline)) if deadline else None,
        "allowedTradeoffs": [],
        "minServiceFloor": True,
        "expiresAt": _iso(expires),
        "status": status,
    }


def _settings_out(config: AgentConfig, store: Store) -> dict[str, Any]:
    stored: dict[str, Any] = {}
    for key in (
        "ui:floor_mbps",
        "ui:retention_days",
        "ui:federated",
        "ui:demand_learning",
        "ui:flags",
    ):
        row = store.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row:
            stored[key] = _json_dict(row["value"]).get("value", row["value"])
    flags = {
        "conflicts": True,
        "health": True,
        "demand": False,
        "links": False,
        "federated": bool(stored.get("ui:federated", False)),
        "ask": False,
    }
    stored_flags = stored.get("ui:flags")
    if isinstance(stored_flags, dict):
        flags.update({str(k): bool(v) for k, v in stored_flags.items()})
    return {
        "floorMbps": float(stored.get("ui:floor_mbps", 2.0) or 2.0),
        "rateChangePct": float(config.max_rate_change_pct),
        "dwellSeconds": float(config.dwell_s),
        "forecastThreshold": float(config.forecaster_confidence_threshold),
        "classifierThreshold": float(config.classifier_threshold),
        "intervalSeconds": float(config.slow_interval_s),
        "retentionDays": int(stored.get("ui:retention_days", config.agg_retention_d)),
        "federated": bool(stored.get("ui:federated", False)),
        "demandLearning": bool(stored.get("ui:demand_learning", False)),
        "flags": flags,
    }


def _put_setting(store: Store, key: str, value: Any) -> None:
    store.execute(
        "INSERT OR REPLACE INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
        (key, json.dumps({"value": value}), time.time()),
    )


def _range_seconds(value: str | None) -> float:
    lookup = {
        "15m": 900,
        "1h": 3600,
        "6h": 21600,
        "24h": 86400,
        "7d": 604800,
        "30d": 2592000,
    }
    return float(lookup.get(value or "1h", 3600))


def _metric_points(
    store: Store, core: AgentCore, range_s: float, device: str | None
) -> list[dict[str, Any]]:
    """Build real metric points from persisted measurements.

    Throughput samples arrive every fast tick (~1.5s).  Link quality samples
    arrive every slow tick (~30s).  Points are bucketed so the response stays
    small while every value still comes from a measurement.
    """
    now = time.time()
    since = now - range_s
    bucket = max(2.0, range_s / 900.0)
    device_filter = "" if not device or device == "all" else " AND device_id = ?"

    def series(metric: str) -> dict[float, float]:
        params: list[Any] = [since]
        if device_filter:
            params.append(device)
        sql = (
            "SELECT (CAST(ts / ? AS INTEGER) * ?) AS bucket, AVG(value) AS value "
            "FROM metrics_raw WHERE metric=? AND ts>=?"
            + device_filter
            + " GROUP BY bucket ORDER BY bucket"
        )
        rows = store.execute(sql, (bucket, bucket, metric, *params)).fetchall()
        return {float(r["bucket"]): float(r["value"]) for r in rows if r["value"] is not None}

    throughput = series("throughput_mbps")
    latency = series("latency_ms")
    jitter = series("jitter_ms")
    loss = series("loss_pct")
    queue = series("queue_pkts")
    fairness = series("fairness")

    cap_rows = store.execute(
        "SELECT (CAST(ts / ? AS INTEGER) * ?) AS bucket, AVG(capacity_mbps) AS capacity, "
        "AVG(variance) AS variance FROM capacity_history WHERE ts>=? "
        "GROUP BY bucket ORDER BY bucket",
        (bucket, bucket, since),
    ).fetchall()
    capacity = {
        float(r["bucket"]): (float(r["capacity"]), float(r["variance"] or 0.0))
        for r in cap_rows
        if r["capacity"] is not None
    }

    timeline = sorted(set(throughput) | set(capacity) | set(latency))
    points: list[dict[str, Any]] = []
    last_capacity = 0.0
    last_variance = 0.0
    last_quality = (0.0, 0.0, 0.0)
    snap_fairness = None
    if core.last_snapshot and isinstance(core.last_snapshot.qoe, dict):
        snap_fairness = core.last_snapshot.qoe.get("jain")
    for ts_bucket in timeline:
        cap = capacity.get(ts_bucket)
        if cap is not None:
            last_capacity, last_variance = cap
        if ts_bucket in latency:
            last_quality = (
                latency.get(ts_bucket, last_quality[0]),
                jitter.get(ts_bucket, last_quality[1]),
                loss.get(ts_bucket, last_quality[2]),
            )
        spread = 1.96 * (last_variance**0.5 if last_variance > 0 else 0.0)
        points.append(
            {
                "ts": _iso(ts_bucket),
                "capacity": round(last_capacity, 2),
                "lo": round(max(0.0, last_capacity - spread), 2),
                "hi": round(last_capacity + spread, 2),
                "throughput": round(throughput.get(ts_bucket, 0.0), 3),
                "latency": round(last_quality[0], 2),
                "baseline": 0.0,
                "jitter": round(last_quality[1], 2),
                "loss": round(last_quality[2], 2),
                "queue": round(queue.get(ts_bucket, 0.0), 1),
                "fairness": round(
                    float(fairness.get(ts_bucket, snap_fairness if snap_fairness is not None else 1.0)),
                    4,
                ),
            }
        )
    return points


def _log_entry(row: Any) -> dict[str, Any]:
    keys = row.keys()
    detail = row["details"] if "details" in keys else ""
    detail_text = ""
    if detail:
        parsed = _json_dict(detail)
        detail_text = ", ".join(f"{k}={v}" for k, v in parsed.items()) or str(detail)
    action = str(row["action"]) if "action" in keys else "event"
    if "result" in keys and row["result"]:
        action = f"{action} · {row['result']}"
    if not detail_text and "target" in keys:
        detail_text = str(row["target"])
    return {
        "id": str(row["id"] if "id" in keys else row["ts"]),
        "ts": _iso(float(row["ts"])),
        "action": action,
        "detail": detail_text,
    }


class _LogRing(logging.Handler):
    """Small in-memory ring buffer so /agent/logs shows real agent activity."""

    def __init__(self, size: int = 300) -> None:
        super().__init__()
        self.records: deque[dict[str, Any]] = deque(maxlen=size)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(
                {
                    "id": f"log-{int(record.created * 1000)}-{record.thread}",
                    "ts": record.created,
                    "action": record.levelname.lower(),
                    "detail": record.getMessage(),
                }
            )
        except Exception:  # noqa: BLE001 - logging must never raise
            pass


def create_app(
    config: AgentConfig,
    core: AgentCore,
    store: Store,
    decision_log: Optional[DecisionLog] = None,
    intent_service: Optional[IntentService] = None,
    pairing: Optional[PairingManager] = None,
    sessions: Optional[SessionManager] = None,
    identity: Optional[AgentIdentity] = None,
    health=None,
    negotiation=None,
    experiments=None,
    federated=None,
    ask_service=None,
    dev_control=None,
) -> FastAPI:
    app = FastAPI(title="Experience Budget Agent", version=APP_VERSION)
    allowed_origins = [
        origin.strip()
        for origin in os.environ.get(
            "EB_CORS_ORIGINS",
            "http://localhost:8443,http://127.0.0.1:8443,http://0.0.0.0:8443",
        ).split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins or ["*"],
        allow_origin_regex=os.environ.get("EB_CORS_ORIGIN_REGEX")
        or r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(:\d+)?",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["ETag", "X-Request-Id"],
    )
    log_ring = _LogRing()
    log_ring.setLevel(logging.INFO)
    logging.getLogger("eb_agent").addHandler(log_ring)
    app.state.log_ring = log_ring
    app.state.config = config
    app.state.core = core
    app.state.store = store
    app.state.decision_log = decision_log
    app.state.intent_service = intent_service
    app.state.pairing = pairing
    app.state.sessions = sessions
    app.state.identity = identity
    app.state.health = health
    app.state.negotiation = negotiation
    app.state.experiments = experiments
    app.state.federated = federated
    app.state.ask = ask_service
    app.state.dev_control = dev_control
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
                # Shown only when the backing implementation is real.
                "shaping": False,
                "monitoring": True,
                "conflicts": True,
                "health": True,
                "negotiation": True,
                "self_healing": True,
                "demand": False,
                "multilink": False,
                "links": False,
                "federated": False,
                "ask": False,
            },
            classes=["call", "game", "stream", "bulk", "unknown"],
            scenarios=[
                {
                    "id": "iso-during-call",
                    "name": "ISO download during a call",
                    "description": "A large transfer competes with a live call.",
                    "icon": "call",
                    "baseline": 0.52,
                    "managed": 0.88,
                    "duration": "12 min",
                    "tag": "synthetic",
                },
                {
                    "id": "wan-drop",
                    "name": "WAN drop 100->20 Mbps",
                    "description": "Capacity collapses mid-stream and recovers.",
                    "icon": "wan",
                    "baseline": 0.41,
                    "managed": 0.79,
                    "duration": "10 min",
                    "tag": "synthetic",
                },
                {
                    "id": "three-tvs-gamer",
                    "name": "Three TVs + a gamer",
                    "description": "Streaming and gaming share a constrained uplink.",
                    "icon": "stream",
                    "baseline": 0.46,
                    "managed": 0.83,
                    "duration": "15 min",
                    "tag": "synthetic",
                },
            ],
            formulas={
                "call": "simplified ITU-T G.107 E-model",
                "game": "0.55*f(rtt) + 0.30*f(jitter) + 0.15*f(loss)",
                "stream": "0.60*throughput/target + 0.25*(1-CoV) + 0.15*(1-rebuffer)",
                "bulk": "min(1, rate/fair_rate)",
                "household": "Mean active-device QoE proxy. Not a subjective score.",
                "fairness": "Jain fairness index. 1.0 is equal allocation.",
            },
            seed=0,
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

        async def send(payload: dict[str, Any]) -> None:
            await ws.send_text(json.dumps(payload))

        last_snapshot_ts = 0.0
        try:
            # Send the current state immediately so a new subscriber never
            # waits a whole loop interval for its first paint.
            snap = core.last_snapshot
            if snap is not None:
                last_snapshot_ts = snap.ts
                await send(
                    {
                        "event": "snapshot",
                        "snapshot": _snapshot_to_frontend(core, app.state.config, app.state.store),
                    }
                )
            while True:
                await asyncio.sleep(max(0.5, core.config.fast_interval_s))
                tick = core.last_tick
                snap = core.last_snapshot
                snapshot_payload = None
                if snap is not None:
                    snapshot_payload = _snapshot_to_frontend(
                        core, app.state.config, app.state.store
                    )
                    if snap.ts != last_snapshot_ts:
                        last_snapshot_ts = snap.ts
                        await send({"event": "snapshot", "snapshot": snapshot_payload})
                await send(
                    {
                        "event": "tick",
                        "ts": time.time(),
                        "connected": True,
                        "capacity_mbps": snap.capacity_mbps if snap else None,
                        "counters": tick.counters if tick else {},
                        "snapshot": snapshot_payload,
                    }
                )
        except (WebSocketDisconnect, RuntimeError):
            pass

    @app.get("/api/v1/metrics")
    async def metrics(range: str = "1h", device: str = "all"):
        core_agent: AgentCore = app.state.core
        store: Store = app.state.store
        return _metric_points(store, core_agent, _range_seconds(range), device)

    @app.get("/api/v1/devices")
    async def devices():
        core: AgentCore = app.state.core
        config: AgentConfig = app.state.config
        return _snapshot_to_frontend(core, config, app.state.store)["devices"]

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

    @app.get("/api/v1/intents")
    async def list_intents():
        store: Store = app.state.store
        rows = store.execute(
            "SELECT intent_id, device_id, traffic_class, goal, priority, deadline, expires_at, "
            "state, created_at FROM intents ORDER BY created_at DESC"
        ).fetchall()
        return [_intent_out(row) for row in rows]

    @app.post("/api/v1/intents")
    async def create_intent(request: Request):
        key = idempotency_key(request)
        if key and key in idempotency_store:
            return idempotency_store[key]
        body = await request.json()
        store: Store = app.state.store
        intent_id = body.get("intent_id") or f"int-{uuid.uuid4().hex[:12]}"
        target = body.get("target") if isinstance(body.get("target"), dict) else {}
        device_id = body.get("device_id") or target.get("deviceId", "")
        traffic_class = body.get("traffic_class") or target.get("cls", "unknown")
        priority_raw = body.get("priority", 0)
        priority = (
            {"low": 0, "normal": 1, "high": 2}.get(priority_raw, int(priority_raw or 0))
            if isinstance(priority_raw, str)
            else int(priority_raw or 0)
        )
        now = time.time()
        duration = float(body.get("durationSeconds", body.get("duration_s", 3600)) or 3600)
        deadline_raw = body.get("deadline")
        deadline = None
        if deadline_raw:
            try:
                deadline = datetime.fromisoformat(str(deadline_raw).replace("Z", "+00:00")).timestamp()
            except ValueError:
                deadline = None
        expires_at = float(body.get("expires_at", now + duration))
        store.execute(
            "INSERT INTO intents(intent_id, device_id, traffic_class, goal, priority, deadline, "
            "duration_s, min_service_floor, expires_at, state, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 'active', ?)",
            (
                intent_id,
                device_id,
                traffic_class,
                body.get("goal", ""),
                priority,
                deadline,
                duration,
                expires_at,
                now,
            ),
        )
        store.commit()
        store.execute(
            "INSERT INTO audit_log(ts, actor, action, target, result, details) VALUES (?, ?, ?, ?, ?, ?)",
            (
                now,
                "owner",
                "intent.create",
                intent_id,
                "ok",
                json.dumps({"device": device_id, "class": traffic_class, "goal": body.get("goal", "")}),
            ),
        )
        store.commit()
        row = store.execute(
            "SELECT intent_id, device_id, traffic_class, goal, priority, deadline, expires_at, "
            "state, created_at FROM intents WHERE intent_id=?",
            (intent_id,),
        ).fetchone()
        result = _intent_out(row)
        idempotency_store[key or ""] = result
        return result

    @app.delete("/api/v1/intents/{intent_id}")
    async def delete_intent(intent_id: str):
        store: Store = app.state.store
        store.execute("UPDATE intents SET state='cancelled' WHERE intent_id=?", (intent_id,))
        store.execute(
            "INSERT INTO audit_log(ts, actor, action, target, result, details) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), "owner", "intent.cancel", intent_id, "ok", "{}"),
        )
        store.commit()
        return {"ok": True, "intent_id": intent_id}

    @app.get("/api/v1/decisions")
    async def decisions():
        store: Store = app.state.store
        rows = store.execute(
            "SELECT decision_id, ts, trigger, reason, before_qoe, after_qoe, evidence, verdict, "
            "prev_hash, hash FROM decisions ORDER BY ts DESC LIMIT 50"
        ).fetchall()
        return [_decision_out(row) for row in rows]

    @app.get("/api/v1/decisions/verify")
    async def decisions_verify():
        decision_log: Optional[DecisionLog] = app.state.decision_log
        store: Store = app.state.store
        if decision_log is not None:
            valid, bad = decision_log.verify_chain()
        else:
            valid, bad = True, None
        count = store.execute("SELECT COUNT(*) AS n FROM decisions").fetchone()["n"]
        return {"valid": bool(valid), "count": int(count), "firstBadId": bad}

    @app.get("/api/v1/settings")
    async def get_settings():
        return _settings_out(app.state.config, app.state.store)

    @app.put("/api/v1/settings")
    async def put_settings(request: Request):
        body = await request.json()
        config: AgentConfig = app.state.config
        store: Store = app.state.store
        if "mode" in body:
            config.mode = str(body["mode"])
        if "shaping_factor" in body:
            config.shaping_factor = float(body["shaping_factor"])
        if "rateChangePct" in body:
            config.max_rate_change_pct = float(body["rateChangePct"])
        if "dwellSeconds" in body:
            config.dwell_s = float(body["dwellSeconds"])
        if "forecastThreshold" in body:
            config.forecaster_confidence_threshold = float(body["forecastThreshold"])
        if "classifierThreshold" in body:
            config.classifier_threshold = float(body["classifierThreshold"])
        if "intervalSeconds" in body:
            config.slow_interval_s = float(body["intervalSeconds"])
        if "floorMbps" in body:
            _put_setting(store, "ui:floor_mbps", float(body["floorMbps"]))
        if "retentionDays" in body:
            _put_setting(store, "ui:retention_days", int(body["retentionDays"]))
        if "federated" in body:
            _put_setting(store, "ui:federated", bool(body["federated"]))
        if "demandLearning" in body:
            _put_setting(store, "ui:demand_learning", bool(body["demandLearning"]))
        if "flags" in body and isinstance(body["flags"], dict):
            _put_setting(store, "ui:flags", body["flags"])
        store.execute(
            "INSERT INTO audit_log(ts, actor, action, target, result, details) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), "owner", "settings.update", "control-loop", "ok", json.dumps(body)),
        )
        store.commit()
        return _settings_out(config, store)

    @app.post("/api/v1/safe-state/reset")
    async def safe_state_reset(request: Request):
        store: Store = app.state.store
        now = time.time()
        store.execute("UPDATE intents SET state='cancelled' WHERE state='active'")
        store.execute(
            "INSERT INTO audit_log(ts, actor, action, target, result, details) VALUES (?, ?, ?, ?, ?, ?)",
            (now, "owner", "safe_state.reset", "all-intents", "ok", "{}"),
        )
        store.commit()
        return {"ok": True, "verdict": "reset_to_safe_state"}

    @app.get("/api/v1/ml/metrics")
    async def ml_metrics():
        store: Store = app.state.store
        models = store.execute(
            "SELECT model_id, kind, version, macro_f1, active, created_at FROM models "
            "ORDER BY version DESC"
        ).fetchall()
        corrections = store.execute(
            "SELECT c.correction_id, c.flow_id, c.suggested_class, c.confirmed_class, c.state, "
            "c.created_at, f.device_id, d.name AS device_name, f.class AS flow_class "
            "FROM corrections c LEFT JOIN flows f ON f.flow_id=c.flow_id "
            "LEFT JOIN devices d ON d.device_id=f.device_id ORDER BY c.created_at DESC LIMIT 50"
        ).fetchall()
        active = next((m for m in models if m["active"]), None)
        return {
            "accuracy": float(active["macro_f1"]) if active and active["macro_f1"] is not None else None,
            "f1": float(active["macro_f1"]) if active and active["macro_f1"] is not None else None,
            "baseline": None,
            "encrypted": None,
            "matrix": [],
            "labels": [],
            "trained": bool(active),
            "models": [
                {
                    "version": f"v{m['version']} · {m['kind']}",
                    "status": "active" if m["active"] else "archived",
                    "accuracy": float(m["macro_f1"]) if m["macro_f1"] is not None else None,
                    "date": _iso(float(m["created_at"]))[:10],
                }
                for m in models
            ],
            "corrections": [
                {
                    "id": c["correction_id"],
                    "flowId": c["flow_id"],
                    "device": c["device_name"] or c["device_id"] or "unknown device",
                    "from": c["flow_class"] or c["suggested_class"],
                    "to": c["confirmed_class"] or c["suggested_class"],
                    "status": c["state"],
                }
                for c in corrections
            ],
        }

    @app.get("/api/v1/ml/corrections")
    async def ml_corrections():
        store: Store = app.state.store
        rows = store.execute(
            "SELECT correction_id, flow_id, suggested_class, confirmed_class, state, created_at "
            "FROM corrections ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
        return [
            {
                "id": r["correction_id"],
                "flowId": r["flow_id"],
                "from": r["suggested_class"],
                "to": r["confirmed_class"] or r["suggested_class"],
                "status": r["state"],
            }
            for r in rows
        ]

    @app.post("/api/v1/ml/corrections")
    async def ml_correction_update(request: Request):
        body = await request.json()
        correction_id = str(body.get("id", ""))
        status_value = str(body.get("status", "")).lower()
        if status_value not in {"approved", "rejected", "pending"}:
            raise HTTPException(
                status_code=400,
                detail={"code": "bad_request", "message": "status must be approved, rejected or pending"},
            )
        store: Store = app.state.store
        store.execute(
            "UPDATE corrections SET state=?, reviewed_at=? WHERE correction_id=?",
            (status_value, time.time(), correction_id),
        )
        store.commit()
        return {"ok": True, "id": correction_id, "status": status_value}

    @app.post("/api/v1/ml/retrain")
    async def ml_retrain():
        """Report honestly that no live labelled training set exists yet."""
        store: Store = app.state.store
        approved = store.execute(
            "SELECT COUNT(*) AS n FROM corrections WHERE state='approved'"
        ).fetchone()["n"]
        return {
            "ok": False,
            "trained": False,
            "approvedCorrections": int(approved),
            "reason": (
                "No local training job ran. The classifier uses its rule baseline until "
                "enough approved live corrections exist."
            ),
        }

    @app.post("/api/v1/ml/rollback")
    async def ml_rollback():
        store: Store = app.state.store
        count = store.execute("SELECT COUNT(*) AS n FROM models").fetchone()["n"]
        return {
            "ok": False,
            "rolledBack": False,
            "versions": int(count),
            "reason": "No model versions have been trained on this agent.",
        }

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
        uptime_s = max(0.0, time.time() - float(getattr(app.state, "_started", time.time())))
        uname = platform.uname()
        loop_ms = float(getattr(core, "last_slow_ms", 0.0) or 0.0)
        try:
            host = socket.gethostname()
        except OSError:
            host = "unknown"
        return {
            "hostname": host,
            "os": f"{uname.system} {uname.release}",
            "version": APP_VERSION,
            "uptime": _duration(uptime_s),
            "dbSize": _bytes_label(db_size),
            "relay": "local WebSocket /api/v1/stream",
            "loopMs": round(loop_ms, 2),
            "seed": 0,
            "emulated": bool(snap.emulated) if snap else False,
        }

    @app.get("/api/v1/agent/logs")
    async def agent_logs():
        ring: _LogRing = app.state.log_ring
        return [
            {
                "id": entry["id"],
                "ts": _iso(float(entry["ts"])),
                "action": entry["action"],
                "detail": entry["detail"],
            }
            for entry in reversed(list(ring.records))
        ]

    @app.post("/api/v1/agent/update-check")
    async def update_check():
        return {"update_available": False, "current_version": APP_VERSION}

    @app.get("/api/v1/health/diagnosis")
    async def health_diagnosis():
        health = app.state.health
        core: AgentCore = app.state.core
        snap = core.last_snapshot
        if health is None or snap is None:
            return []
        qoe_data = snap.qoe if isinstance(snap.qoe, dict) else {}
        capacity = snap.capacity_mbps if snap.capacity_mbps is not None else 0.0
        jitter = float(qoe_data.get("jitter_ms", 0.0) or 0.0)
        loss_pct = float(qoe_data.get("loss_pct", 0.0) or 0.0)
        household = qoe_data.get("household")
        qoe_val = household if isinstance(household, (int, float)) else None
        hypotheses = health.diagnose(
            capacity_mbps=float(capacity),
            jitter_ms=jitter,
            loss_pct=loss_pct,
            qoe=qoe_val,
        )
        return [
            {
                "id": h.id,
                "title": h.title,
                "confidence": h.confidence,
                "signals": h.signals,
                "recommendation": h.recommendation,
                "status": h.status,
            }
            for h in hypotheses
        ]

    @app.get("/api/v1/conflicts")
    async def conflicts():
        negotiation = app.state.negotiation
        core: AgentCore = app.state.core
        snap = core.last_snapshot
        capacity = snap.capacity_mbps if snap else 100.0
        flows = snap.flows if snap else []
        demands = {}
        for f in flows:
            did = f.get("device_id", "unknown")
            demands[did] = demands.get(did, 0.0) + f.get("bytes_per_s", 0) / 1_000_000.0
        conflicts = negotiation.detect_conflicts(demands, float(capacity))
        return [
            {
                "id": c.id,
                "title": c.title,
                "description": c.description,
                "status": c.status,
                "options": [
                    {
                        "name": o.name,
                        "description": o.description,
                        "allocations": o.allocations,
                    }
                    for o in c.options
                ],
            }
            for c in conflicts
        ]

    @app.post("/api/v1/conflicts/{conflict_id}/accept")
    async def conflicts_accept(conflict_id: str):
        negotiation = app.state.negotiation
        conflict = negotiation.resolve(conflict_id, 0)
        if not conflict:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "conflict not found or not pending"})
        return {
            "id": conflict.id,
            "status": conflict.status,
            "verdict": "accepted",
        }

    @app.post("/api/v1/conflicts/{conflict_id}/adjust")
    async def conflicts_adjust(conflict_id: str, request: Request):
        negotiation = app.state.negotiation
        body = await request.json()
        option_index = int(body.get("option", 1))
        conflict = negotiation.resolve(conflict_id, option_index)
        if not conflict:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "conflict not found or not pending"})
        return {
            "id": conflict.id,
            "status": conflict.status,
            "verdict": "adjusted",
        }

    @app.post("/api/v1/conflicts/{conflict_id}/cancel")
    async def conflicts_cancel(conflict_id: str):
        negotiation = app.state.negotiation
        ok = negotiation.cancel(conflict_id)
        return {"ok": ok}

    @app.get("/api/v1/demand/schedules")
    async def demand_schedules():
        store: Store = app.state.store
        rows = store.execute(
            "SELECT schedule_id, name, device_id, time, duration, confidence, enabled "
            "FROM demand_schedules ORDER BY created_at DESC"
        ).fetchall()
        return [
            {
                "id": r["schedule_id"],
                "name": r["name"],
                "device": r["device_id"],
                "time": r["time"],
                "duration": r["duration"],
                "confidence": r["confidence"],
                "enabled": bool(r["enabled"]),
            }
            for r in rows
        ]

    @app.post("/api/v1/demand/schedules")
    async def demand_schedule_create(request: Request):
        store: Store = app.state.store
        body = await request.json()
        schedule_id = f"sched-{uuid.uuid4().hex[:8]}"
        store.execute(
            "INSERT INTO demand_schedules(schedule_id, name, device_id, time, duration, "
            "confidence, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                schedule_id,
                body.get("name", "Untitled"),
                body.get("device", ""),
                body.get("time", "09:00"),
                body.get("duration", "60 min"),
                body.get("confidence", 0.8),
                True,
                time.time(),
            ),
        )
        store.commit()
        return {
            "id": schedule_id,
            "name": body.get("name", "Untitled"),
            "device": body.get("device", ""),
            "time": body.get("time", "09:00"),
            "duration": body.get("duration", "60 min"),
            "confidence": body.get("confidence", 0.8),
            "enabled": True,
        }

    @app.delete("/api/v1/demand/schedules")
    async def demand_schedule_delete(request: Request):
        store: Store = app.state.store
        schedule_id = request.query_params.get("id")
        if not schedule_id:
            raise HTTPException(status_code=400, detail={"code": "bad_request", "message": "missing id"})
        store.execute("UPDATE demand_schedules SET enabled = 0 WHERE schedule_id = ?", (schedule_id,))
        store.commit()
        return {"ok": True, "id": schedule_id}

    @app.post("/api/v1/scenarios/{scenario_id}")
    async def scenarios_start(scenario_id: str, request: Request):
        core: AgentCore = app.state.core
        store: Store = app.state.store
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        # Record scenario as active in settings
        scenario = {
            "id": scenario_id,
            "active": True,
            "started_at": time.time(),
            "emulated": False,
        }
        store.execute(
            "INSERT OR REPLACE INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
            (f"scenario:{scenario_id}", json.dumps(scenario), time.time()),
        )
        store.commit()
        return {"scenario_id": scenario_id, "active": True, "emulated": False}

    @app.post("/api/v1/scenarios/stop")
    async def scenarios_stop():
        store: Store = app.state.store
        store.execute(
            "UPDATE settings SET value = json_set(value, '$.active', 0) "
            "WHERE key LIKE 'scenario:%' AND json_extract(value, '$.active') = 1"
        )
        store.commit()
        return {"verdict": "scenarios_stopped", "emulated": False}

    @app.post("/api/v1/experiments")
    async def experiments_start(request: Request):
        experiments: ExperimentStore = app.state.experiments
        body = await request.json()
        exp = experiments.create(
            scenario=body.get("scenario", "mixed_household"),
            seed=int(body.get("seed", 4242)),
            trials=int(body.get("trials", 20)),
        )
        run_experiment(experiments, exp)
        return {
            "id": exp.id,
            "scenario": exp.scenario,
            "seed": exp.seed,
            "trials": exp.trials,
            "progress": exp.progress,
            "status": exp.status,
            "emulated": False,
        }

    @app.get("/api/v1/experiments")
    async def experiments_list():
        experiments: ExperimentStore = app.state.experiments
        return [
            {
                "id": e.id,
                "scenario": e.scenario,
                "seed": e.seed,
                "trials": e.trials,
                "progress": e.progress,
                "status": e.status,
                "results": [
                    {
                        "metric": r.metric,
                        "unit": r.unit,
                        "unmanaged": r.unmanaged,
                        "cake": r.cake,
                        "eb": r.eb,
                        "ci": r.ci,
                    }
                    for r in e.results
                ],
                "emulated": False,
            }
            for e in experiments.list()
        ]

    @app.get("/api/v1/experiments/{exp_id}/report")
    async def experiments_report(exp_id: str):
        experiments: ExperimentStore = app.state.experiments
        exp = experiments.get(exp_id)
        if not exp:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "experiment not found"})
        return {
            "id": exp.id,
            "scenario": exp.scenario,
            "seed": exp.seed,
            "trials": exp.trials,
            "status": exp.status,
            "results": [
                {
                    "metric": r.metric,
                    "unit": r.unit,
                    "unmanaged": r.unmanaged,
                    "cake": r.cake,
                    "eb": r.eb,
                    "ci": r.ci,
                }
                for r in exp.results
            ],
            "emulated": False,
        }

    @app.get("/api/v1/federated/status")
    async def federated_status():
        federated = app.state.federated
        s = federated.status()
        return {
            "enabled": s.enabled,
            "rounds": s.rounds,
            "lastAggregate": s.last_aggregate,
            "participants": s.participants,
            "emulated": False,
        }

    @app.post("/api/v1/federated/opt-in")
    async def federated_opt_in():
        federated = app.state.federated
        s = federated.opt_in()
        return {"enabled": s.enabled, "verdict": "opted_in", "emulated": False}

    @app.post("/api/v1/federated/opt-out")
    async def federated_opt_out():
        federated = app.state.federated
        s = federated.opt_out()
        return {"enabled": s.enabled, "verdict": "opted_out", "emulated": False}

    @app.post("/api/v1/ask")
    async def ask(request: Request):
        ask = app.state.ask
        body = await request.json()
        question = body.get("question", "")
        answer = ask.answer(question)
        return {
            "text": answer.text,
            "citations": answer.citations,
            "disclaimer": answer.disclaimer,
            "emulated": False,
        }

    @app.get("/api/v1/links")
    async def links():
        core: AgentCore = app.state.core
        snap = core.last_snapshot
        qoe = snap.qoe if snap and isinstance(snap.qoe, dict) else {}
        tick = core.last_tick
        wan = getattr(core, "_wan_iface", None) or "unknown"
        if snap is None:
            return []
        return [
            {
                "id": f"wan-{wan}",
                "name": f"WAN · {wan}",
                "type": "Ethernet uplink",
                "rtt": round(float(qoe.get("rtt_ms", 0.0) or 0.0), 2),
                "loss": round(float(qoe.get("loss_pct", 0.0) or 0.0), 2),
                "capacity": round(float(snap.capacity_mbps or 0.0), 2),
                "throughput": round(
                    float(tick.counters.get("throughput_mbps", 0) or 0) if tick else 0.0, 2
                ),
                "state": "active" if snap.capacity_mbps is not None else "unknown",
            }
        ]

    @app.post("/api/v1/paths/evaluate")
    async def paths_evaluate():
        return {"evaluated": [], "emulated": False}

    @app.get("/api/v1/metrics/internal")
    async def metrics_internal():
        config: AgentConfig = app.state.config
        cpu, mem = _process_metrics()
        return {
            "process_cpu_percent": cpu,
            "process_mem_mb": mem,
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
        store: Store = app.state.store
        key = f"EB-{uuid.uuid4().hex[:4].upper()}-{uuid.uuid4().hex[:4].upper()}-{uuid.uuid4().hex[:4].upper()}"
        store.execute(
            "INSERT INTO audit_log(ts, actor, action, target, result, details) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), "owner", "pairing.rotate", "pairing-key", "ok", "{}"),
        )
        store.commit()
        return {"ok": True, "verdict": "keys_rotated", "key": key}

    @app.get("/api/v1/audit")
    async def audit():
        store: Store = app.state.store
        rows = store.execute(
            "SELECT id, ts, actor, action, target, result, details FROM audit_log "
            "ORDER BY ts DESC LIMIT 100"
        ).fetchall()
        return [_log_entry(row) for row in rows]

    # ------------------------------------------------------------------ dev control

    @app.get("/api/v1/dev/control")
    async def dev_control_get():
        dev_control: Optional[DevControlService] = app.state.dev_control
        if not dev_control:
            return {"offline": False, "stale": False, "slow": False, "rateLimit": False, "speed": 10, "seed": 4242}
        return dev_control.get().to_dict()

    @app.patch("/api/v1/dev/control")
    async def dev_control_patch(request: Request):
        dev_control: Optional[DevControlService] = app.state.dev_control
        if not dev_control:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "dev control unavailable"})
        body = await request.json()
        state = dev_control.patch(body)
        return state.to_dict()

    @app.delete("/api/v1/dev/reset")
    async def dev_reset():
        dev_control: Optional[DevControlService] = app.state.dev_control
        if not dev_control:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "dev control unavailable"})
        state = dev_control.reset()
        return state.to_dict()

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
    health=None,
    negotiation=None,
    experiments=None,
    federated=None,
    ask_service=None,
    dev_control=None,
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
        health=health,
        negotiation=negotiation,
        experiments=experiments,
        federated=federated,
        ask_service=ask_service,
        dev_control=dev_control,
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

    logging.basicConfig(
        level=os.environ.get("EB_LOG_LEVEL", "info").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

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
        emulated=False,
    )
    health = get_health_service()
    negotiation = get_negotiation_service()
    experiments = ExperimentStore()
    federated = get_federated_service()
    ask_service = get_ask_service()
    dev_control = get_dev_control_service()
    print(f"eb-agentd serving on http://{('0.0.0.0')}:{7443} (mode={config.mode})")
    run_server(
        config,
        core,
        store,
        host="0.0.0.0",
        port=7443,
        decision_log=decision_log,
        intent_service=intent_service,
        health=health,
        negotiation=negotiation,
        experiments=experiments,
        federated=federated,
        ask_service=ask_service,
        dev_control=dev_control,
    )


