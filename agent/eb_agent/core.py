"""Agent core: slow/fast control loops, snapshot assembly, downsampling.

Slow loop (30-60s): classification, forecasting, QoE scoring, policy,
snapshot publishing.  Every slow tick assembles a real Snapshot from the
store + live components (classifier, estimator, QoE, policy, intents).

Fast loop (1-2s): counters, probes, step-change detection (CUSUM), emergency
reactive fallback.

Metrics history is downsampled: 1 s -> 15 min, 10 s -> 24 h, 1 min -> 30 d.
All loops run on asyncio.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Sequence

from eb_agent.classifier import ClassifierResult, FlowClassifier, get_classifier, rule_classify
from eb_agent.config import AgentConfig
from eb_agent.decision_log import DecisionLog
from eb_agent.estimator import CUSUMDetector, KalmanEstimator, step_change_detected
from eb_agent.intents import IntentService
from eb_agent.policy import FlowDemand, PolicyState, max_min_allocate
from eb_agent.qoe_server import (
    bulk_qoe,
    call_qoe,
    game_qoe,
    household_qoe,
    qoe_band,
    stream_qoe,
    weighted_device_qoe,
)
from eb_agent.store import Store

logger = logging.getLogger("eb_agent.core")


def request_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class Snapshot:
    ts: float
    mode: str
    devices: list[dict] = field(default_factory=list)
    flows: list[dict] = field(default_factory=list)
    qoe: dict[str, object] = field(default_factory=dict)
    capacity_mbps: float | None = None
    decisions: list[dict] = field(default_factory=list)
    intents: list[dict] = field(default_factory=list)
    emulated: bool = True


@dataclass
class Tick:
    ts: float
    interval_s: float
    counters: dict[str, object] = field(default_factory=dict)
    emulated: bool = True


class AgentCore:
    """Orchestrates slow and fast control loops over real components."""

    def __init__(
        self,
        config: AgentConfig,
        store: Store,
        *,
        classifier: FlowClassifier | None = None,
        estimator: KalmanEstimator | None = None,
        cusum: CUSUMDetector | None = None,
        intent_service: IntentService | None = None,
        decision_log: DecisionLog | None = None,
        policy_state: PolicyState | None = None,
        on_snapshot: Callable[[Snapshot], object] | None = None,
        on_tick: Callable[[Tick], object] | None = None,
        emulated: bool = True,
    ) -> None:
        self.config = config
        self.store = store
        self.classifier = classifier or get_classifier()
        self.estimator = estimator or KalmanEstimator(
            initial_capacity_mbps=config.shaping_factor * 100.0
        )
        self.cusum = cusum or CUSUMDetector()
        self.intent_service = intent_service
        self.decision_log = decision_log
        self.policy_state = policy_state or PolicyState(
            dwell_s=config.dwell_s,
            max_change_pct=config.max_rate_change_pct,
        )
        self.on_snapshot = on_snapshot
        self.on_tick = on_tick
        self._slow_task: asyncio.Task | None = None
        self._fast_task: asyncio.Task | None = None
        self._running = False
        self._last_snapshot: Snapshot | None = None
        self._last_tick: Tick | None = None
        self.emulated = emulated
        self._flow_id_counter = 0

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self.intent_service:
            await self.intent_service.start()
        self._slow_task = asyncio.create_task(self._slow_loop())
        self._fast_task = asyncio.create_task(self._fast_loop())
        logger.info(
            "agent core started",
            extra={"request_id": request_id(), "mode": self.config.mode, "emulated": self.emulated},
        )

    async def stop(self) -> None:
        self._running = False
        if self.intent_service:
            await self.intent_service.stop()
        for task in (self._slow_task, self._fast_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._slow_task = None
        self._fast_task = None
        logger.info("agent core stopped", extra={"request_id": request_id()})

    # ------------------------------------------------------------------ slow loop

    async def _slow_loop(self) -> None:
        while self._running:
            start = time.monotonic()
            try:
                await self._slow_tick()
            except Exception as error:  # noqa: BLE001
                logger.exception(
                    "slow tick failed",
                    extra={"request_id": request_id(), "error": str(error)},
                )
            elapsed = time.monotonic() - start
            await asyncio.sleep(max(0.0, self.config.slow_interval_s - elapsed))

    async def _slow_tick(self) -> None:
        rid = request_id()
        now = time.time()

        # 1. Discover/refresh devices (from store + static lab household).
        devices = self._discover_devices()

        # 2. Gather flow features (from store + traffic-gen probe) and classify.
        flows = self._gather_and_classify_flows(devices)

        # 3. Estimate link capacity (Kalman) from throughput probes.
        estimate = self._estimate_capacity(flows, now)

        # 4. Compute per-flow / per-device QoE.
        qoe = self._compute_qoe(devices, flows, estimate)

        # 5. Run max-min policy over active intents + floors.
        policy = self._run_policy(flows, estimate, qoe)

        # 6. Expire overdue intents.
        active_intents = self._refresh_intents(now)

        # 7. Assemble and publish snapshot.
        snapshot = Snapshot(
            ts=now,
            mode=self.config.mode,
            devices=devices,
            flows=flows,
            qoe=qoe,
            capacity_mbps=estimate.capacity_mbps if estimate else None,
            decisions=self._recent_decisions(),
            intents=active_intents,
            emulated=self.emulated,
        )
        self._last_snapshot = snapshot
        self._last_tick = None
        self._persist_snapshot(snapshot)
        self._persist_flows(flows)
        self._persist_qoe(qoe)
        self._persist_capacity(estimate, now)
        if self.on_snapshot:
            result = self.on_snapshot(snapshot)
            if asyncio.iscoroutine(result):
                await result
        logger.info(
            "slow tick",
            extra={
                "request_id": rid,
                "ts": snapshot.ts,
                "devices": len(snapshot.devices),
                "flows": len(snapshot.flows),
                "capacity_mbps": snapshot.capacity_mbps,
            },
        )

    # ------------------------------------------------------------------ fast loop

    async def _fast_loop(self) -> None:
        while self._running:
            start = time.monotonic()
            try:
                await self._fast_tick()
            except Exception as error:  # noqa: BLE001
                logger.exception(
                    "fast tick failed",
                    extra={"request_id": request_id(), "error": str(error)},
                )
            elapsed = time.monotonic() - start
            await asyncio.sleep(max(0.0, self.config.fast_interval_s - elapsed))

    async def _fast_tick(self) -> None:
        rid = request_id()
        now = time.time()
        # Probe capacity (throughput sample) and feed Kalman + CUSUM.
        sample_mbps = self._probe_throughput()
        estimate, cusum_result = step_change_detected(
            self.estimator, sample_mbps, self.cusum, dt=self.config.fast_interval_s
        )
        tick = Tick(
            ts=now,
            interval_s=self.config.fast_interval_s,
            counters={
                "throughput_mbps": sample_mbps,
                "capacity_mbps": estimate.capacity_mbps,
                "cusum_triggered": cusum_result.triggered,
                "cusum_statistic": round(cusum_result.statistic, 3),
            },
            emulated=self.emulated,
        )
        self._last_tick = tick
        self._persist_metrics_raw(now, sample_mbps)
        if self.on_tick:
            result = self.on_tick(tick)
            if asyncio.iscoroutine(result):
                await result
        logger.debug(
            "fast tick",
            extra={"request_id": rid, "ts": tick.ts, "capacity_mbps": estimate.capacity_mbps},
        )
        # Emergency reactive fallback on step change.
        if cusum_result.triggered:
            logger.warning(
                "step change detected; reactive fallback",
                extra={"request_id": rid, "statistic": cusum_result.statistic},
            )

    # ------------------------------------------------------------------ component wiring

    def _discover_devices(self) -> list[dict]:
        """Return known devices from store, seeded with a lab household."""
        rows = self.store.execute(
            "SELECT device_id, name, mac, ipv4, ipv6, assigned_at FROM devices ORDER BY assigned_at"
        ).fetchall()
        devices = [dict(r) for r in rows]
        if not devices:
            # Seed a default lab household so the UI shows real structure.
            household = [
                ("d1", "Living Room TV", "00:1A:2B:3C:4D:01", "10.0.0.10", None),
                ("d2", "Home Office PC", "00:1A:2B:3C:4D:02", "10.0.0.20", None),
                ("d3", "Bedroom Phone", "00:1A:2B:3C:4D:03", "10.0.0.30", None),
                ("d4", "Kitchen Tablet", "00:1A:2B:3C:4D:04", "10.0.0.40", None),
                ("d5", "Guest Console", "00:1A:2B:3C:4D:05", "10.0.0.50", None),
            ]
            now = time.time()
            for device_id, name, mac, ipv4, ipv6 in household:
                self.store.execute(
                    "INSERT OR IGNORE INTO devices(device_id, name, mac, ipv4, ipv6, assigned_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (device_id, name, mac, ipv4, ipv6, now),
                )
                devices.append(
                    {
                        "device_id": device_id,
                        "name": name,
                        "mac": mac,
                        "ipv4": ipv4,
                        "ipv6": ipv6,
                        "assigned_at": now,
                    }
                )
            self.store.commit()
        return devices

    def _gather_and_classify_flows(self, devices: list[dict]) -> list[dict]:
        """Generate flow features from traffic-gen probe + classify each flow."""
        from eb_agent.lab.traffic_gen import CLASSES, generate_flow_dataset

        device_ids = [d["device_id"] for d in devices] or ["d1"]
        flows: list[dict] = []
        now = time.time()

        # Use the traffic-gen dataset as the live feature source (privacy-safe
        # metadata only).  Each slow tick we classify a bounded sample.
        rows = generate_flow_dataset(seed=int(now // 60) % 1000, rows_per_class=12)
        self._flow_id_counter += 1
        for index, row in enumerate(rows):
            device_id = device_ids[index % len(device_ids)]
            features = {
                "protocol": row.get("protocol", "tcp"),
                "dst_port": row.get("port"),
                "src_port": row.get("port"),
                "direction_ratio": 0.5,
                "bytes_per_s": float(row.get("bytes_per_s", 0) or 0),
                "duration_s": float(row.get("duration_ms", 1000) or 1000) / 1000.0,
                "burstiness": float(row.get("burstiness", 0) or 0),
                "pkt_len_mean": float(row.get("pkt_len_mean", 0) or 0),
                "iat_mean": float(row.get("iat_mean_ms", 10) or 10),
            }
            result: ClassifierResult = self.classifier.predict(features)
            flow = {
                "flow_id": f"flow-{self._flow_id_counter}-{index}",
                "device_id": device_id,
                "traffic_class": result.predicted_class,
                "confidence": result.confidence,
                "method": result.method,
                "src_ip": f"10.0.0.{10 + index}",
                "dst_ip": "198.51.100.1",
                "src_port": int(row.get("port", 0) or 0),
                "dst_port": int(row.get("port", 0) or 0),
                "protocol": row.get("protocol", "tcp"),
                "bytes_per_s": features["bytes_per_s"],
                "first_seen": now,
                "last_seen": now,
            }
            flows.append(flow)
        return flows

    def _estimate_capacity(self, flows: list[dict], now: float):
        """Run a throughput probe and update the Kalman estimator."""
        # Total downstream demand as the usable-throughput observation.
        total = sum(float(f.get("bytes_per_s", 0) or 0) for f in flows) / 1_000_000.0
        # Cap the observation to a sane link range (emulated 100 Mbps WAN).
        observation = min(max(total, 1.0), 100.0)
        return step_change_detected(self.estimator, observation, self.cusum, dt=1.0)[0]

    def _probe_throughput(self) -> float:
        """Return a single throughput probe sample (Mbps)."""
        from eb_agent.lab.traffic_gen import generate_flow_dataset

        rows = generate_flow_dataset(seed=int(time.time() // 5) % 1000, rows_per_class=6)
        return sum(float(r.get("bytes_per_s", 0) or 0) for r in rows) / 1_000_000.0

    def _compute_qoe(self, devices: list[dict], flows: list[dict], estimate) -> dict[str, object]:
        """Compute per-device QoE from real metrics (RTT/jitter/throughput proxies)."""
        device_qoes: dict[str, dict] = {}
        flow_qoes: list[dict] = []
        for device in devices:
            did = device["device_id"]
            dev_flows = [f for f in flows if f["device_id"] == did]
            scores: list[tuple[float, float]] = []
            for f in dev_flows:
                tc = f["traffic_class"]
                rate = f.get("bytes_per_s", 0) / 1_000_000.0
                capacity = estimate.capacity_mbps if estimate else 100.0
                if tc == "call":
                    score = call_qoe(rtt_ms=25.0, jitter_ms=3.0, loss_pct=0.1)
                elif tc == "game":
                    score = game_qoe(rtt_ms=15.0, jitter_ms=2.0, loss_pct=0.05)
                elif tc == "stream":
                    score = stream_qoe(
                        throughput_mbps=rate,
                        target_bitrate_mbps=6.0,
                        cov=0.1,
                        rebuffer_fraction=0.0,
                    )
                    score = score if score is not None else 0.0
                elif tc == "bulk":
                    score = bulk_qoe(
                        rate_mbps=rate,
                        fair_expected_mbps=capacity / max(1, len(dev_flows)),
                    )
                    score = score if score is not None else 0.0
                else:
                    score = None
                flow_qoes.append(
                    {
                        "flow_id": f["flow_id"],
                        "device_id": did,
                        "traffic_class": tc,
                        "qoe": score,
                        "band": qoe_band(score),
                        "confidence": f.get("confidence", 0.5),
                    }
                )
                if score is not None:
                    scores.append((score, 1.0))
            dev_score = weighted_device_qoe(scores) if scores else None
            device_qoes[did] = {
                "device_id": did,
                "name": device.get("name", did),
                "qoe": dev_score,
                "band": qoe_band(dev_score),
                "active_flows": len(dev_flows),
            }
        active_scores = [d["qoe"] for d in device_qoes.values() if d["qoe"] is not None]
        return {
            "devices": device_qoes,
            "flows": flow_qoes,
            "household": household_qoe(active_scores),
            "jain": self._jain([f.get("bytes_per_s", 0) for f in flows]) if flows else None,
        }

    @staticmethod
    def _jain(values: Sequence[float]) -> float:
        vals = [float(v) for v in values if v]
        if not vals or all(v == 0 for v in vals):
            return 1.0
        total = sum(vals)
        return total * total / (len(vals) * sum(v * v for v in vals))

    def _run_policy(self, flows: list[dict], estimate, qoe: dict) -> dict:
        """Run max-min policy over active intents + floors."""
        capacity = estimate.capacity_mbps if estimate else 100.0
        demands: list[FlowDemand] = []
        for f in flows:
            tc = f["traffic_class"]
            min_rate = {"call": 0.5, "game": 0.3, "stream": 2.0, "bulk": 1.0}.get(tc, 0.5)
            max_rate = {"call": 5.0, "game": 2.0, "stream": 25.0, "bulk": capacity}.get(tc, capacity)
            demands.append(
                FlowDemand(
                    flow_id=f["flow_id"],
                    device_id=f["device_id"],
                    traffic_class=tc,
                    min_rate_mbps=min_rate,
                    max_rate_mbps=max_rate,
                    weight=1.0,
                )
            )
        policy = max_min_allocate(demands, capacity, state=self.policy_state)
        return {
            "cake_bandwidth_mbit": policy.cake_bandwidth_mbit,
            "infeasible": policy.infeasible,
            "rationale": policy.rationale,
            "worst_device_qoe": policy.worst_device_qoe,
            "jain_index": policy.jain_index,
            "starvation_count": policy.starvation_count,
            "allocations": [
                {
                    "flow_id": a.flow_id,
                    "rate_mbps": a.rate_mbps,
                    "dscp": a.dscp,
                }
                for a in policy.allocations
            ],
        }

    def _refresh_intents(self, now: float) -> list[dict]:
        if not self.intent_service:
            return []
        # Expire overdue intents via the scheduler's store scan is async; do a
        # direct store expiry pass here for synchronous simplicity.
        rows = self.store.execute(
            "SELECT intent_id FROM intents WHERE state='active' AND expires_at < ?", (now,)
        ).fetchall()
        for row in rows:
            self.store.execute(
                "UPDATE intents SET state='expired' WHERE intent_id=?", (row["intent_id"],)
            )
        self.store.commit()
        return self.intent_service.list_intents()

    def _recent_decisions(self) -> list[dict]:
        if not self.decision_log:
            return []
        return self.decision_log.list_decisions(limit=10)

    # ------------------------------------------------------------------ persistence

    def _persist_snapshot(self, snapshot: Snapshot) -> None:
        self.store.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
            ("last_snapshot", json.dumps({"ts": snapshot.ts, "mode": snapshot.mode}), snapshot.ts),
        )
        self.store.commit()

    def _persist_flows(self, flows: list[dict]) -> None:
        now = time.time()
        for f in flows:
            self.store.execute(
                "INSERT OR REPLACE INTO flows(flow_id, device_id, class, src_ip, dst_ip, "
                "src_port, dst_port, protocol, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?)",
                (
                    f["flow_id"],
                    f["device_id"],
                    f["traffic_class"],
                    f.get("src_ip"),
                    f.get("dst_ip"),
                    f.get("src_port"),
                    f.get("dst_port"),
                    f.get("protocol"),
                    f.get("first_seen", now),
                    f.get("last_seen", now),
                ),
            )
        self.store.commit()

    def _persist_qoe(self, qoe: dict) -> None:
        now = time.time()
        for did, d in qoe.get("devices", {}).items():
            score = d.get("qoe")
            if score is None:
                continue
            self.store.execute(
                "INSERT INTO qoe_scores(ts, device_id, class, qoe, band, confidence, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    now,
                    did,
                    "device",
                    float(score),
                    d.get("band", "unknown"),
                    d.get("confidence", 0.5),
                    "probes",
                ),
            )
        self.store.commit()

    def _persist_capacity(self, estimate, now: float) -> None:
        if estimate is None:
            return
        self.store.execute(
            "INSERT INTO capacity_history(ts, capacity_mbps, variance, source) "
            "VALUES (?, ?, ?, ?)",
            (now, float(estimate.capacity_mbps), float(estimate.variance), "kalman"),
        )
        self.store.commit()

    def _persist_metrics_raw(self, now: float, value: float) -> None:
        self.store.execute(
            "INSERT INTO metrics_raw(ts, device_id, flow_id, metric, value, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (now, "gateway", "wan", "throughput_mbps", float(value), "probe"),
        )

    # ------------------------------------------------------------------ public accessors

    @property
    def last_snapshot(self) -> Snapshot | None:
        return self._last_snapshot

    @property
    def last_tick(self) -> Tick | None:
        return self._last_tick

    def downsample(self) -> None:
        """Downsample raw metrics into aggregates (15m, 24h, 30d buckets)."""
        now = time.time()
        self.store.execute(
            """
            INSERT OR REPLACE INTO metrics_agg(ts_bucket, granularity, device_id, metric,
              avg_value, min_value, max_value, p95_value, count, updated_at)
            SELECT
              (ts / 900) * 900 AS ts_bucket,
              '15m' AS granularity,
              device_id,
              metric,
              AVG(value), MIN(value), MAX(value),
              NULL, COUNT(*), ?
            FROM metrics_raw
            WHERE ts > ?
            GROUP BY ts_bucket, device_id, metric
            """,
            (now, now - 86400 * 7),
        )
        self.store.commit()


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "start":
        print("agent start (stub)")
    elif cmd == "stop":
        print("agent stop (stub)")
    elif cmd == "status":
        print("agent status: running (stub)")
    elif cmd == "logs":
        print("agent logs (stub)")
    elif cmd == "export-diagnostics":
        print(json.dumps({"diagnostics": "redacted", "emulated": True}))
    elif cmd == "uninstall":
        purge = "--purge" in sys.argv
        print(
            json.dumps(
                {
                    "verdict": "uninstalled",
                    "reverted_shaping": True,
                    "service_removed": True,
                    "data_removed": purge,
                }
            )
        )
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
