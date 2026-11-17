"""Agent core: slow/fast control loops, snapshot assembly, downsampling.

Slow loop (30-60s): classification, forecasting, QoE scoring, policy,
snapshot publishing.  Every slow tick assembles a real Snapshot from the
store + live measurements (interface counters, conntrack, QoE, policy,
intents).

Fast loop (1-2s): counters, probes, step-change detection (CUSUM), emergency
reactive fallback.

Metrics history is downsampled: 1 s -> 15 min, 10 s -> 24 h, 1 min -> 30 d.
All loops run on asyncio.  All data comes from real measurements -- no
synthetic or demo data is generated.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import re
import socket
import statistics
import struct
import subprocess
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

NET_DEV = "/proc/net/dev"
SIOCGIFADDR = 0x8915


def _interface_ipv4(ifname: str) -> str | None:
    """Read an interface's IPv4 address without shelling out."""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        packed = fcntl.ioctl(
            sock.fileno(), SIOCGIFADDR, struct.pack("256s", ifname[:15].encode("utf-8"))
        )
        return ".".join(str(byte) for byte in packed[20:24])
    except (OSError, ValueError):
        return None
    finally:
        if sock is not None:
            sock.close()


def _interface_mac(ifname: str) -> str | None:
    try:
        with open(f"/sys/class/net/{ifname}/address", "r", encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _interface_ipv6(ifname: str) -> str | None:
    """Return the first global IPv6 address assigned to an interface."""
    try:
        with open("/proc/net/if_inet6", "r", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 6 or parts[5] != ifname:
                    continue
                if parts[3] != "00":  # prefer global scope over link-local
                    continue
                packed = bytes.fromhex(parts[0])
                return socket.inet_ntop(socket.AF_INET6, packed)
    except (OSError, ValueError):
        return None
    return None


def _local_interfaces() -> list[str]:
    try:
        names = [
            name
            for name in os.listdir("/sys/class/net")
            if name != "lo" and os.path.isdir(f"/sys/class/net/{name}")
        ]
    except OSError:
        return []
    return sorted(names)


def _read_neighbors() -> list[dict[str, str]]:
    """Read the kernel IPv4 ARP table (real LAN neighbours)."""
    neighbors: list[dict[str, str]] = []
    try:
        with open("/proc/net/arp", "r", encoding="utf-8") as fh:
            next(fh, None)
            for line in fh:
                parts = line.split()
                if len(parts) < 6 or parts[3] == "00:00:00:00:00:00":
                    continue
                neighbors.append(
                    {"ip": parts[0], "mac": parts[3].lower(), "ifname": parts[5]}
                )
    except OSError:
        pass
    return neighbors


def _split_endpoint(value: str) -> tuple[str, int]:
    raw = value.strip()
    if not raw or raw == "*:*":
        return "", 0
    if raw.startswith("["):
        host, _, port = raw.rpartition("]:")
        return host.lstrip("["), int(port) if port.isdigit() else 0
    host, sep, port = raw.rpartition(":")
    if not sep:
        return raw, 0
    host = host.split("%", 1)[0]
    return host, int(port) if port.isdigit() else 0


def _parse_ss_output(text: str, protocol: str) -> list[dict]:
    """Parse ``ss -tinp`` / ``ss -uinp`` output into socket records."""
    records: list[dict] = []
    current: dict | None = None

    def finalize(record: dict | None) -> None:
        if record is None:
            return
        metrics = record.pop("metrics", "")
        rtt = re.search(r"rtt:([\d.]+)/([\d.]+)", metrics)
        sent = re.search(r"bytes_sent:(\d+)", metrics)
        received = re.search(r"bytes_received:(\d+)", metrics)
        retrans = re.search(r"retrans:\d+/(\d+)", metrics)
        segs_out = re.search(r"segs_out:(\d+)", metrics)
        record["rtt_ms"] = float(rtt.group(1)) if rtt else 0.0
        record["jitter_ms"] = float(rtt.group(2)) if rtt else 0.0
        record["bytes_sent"] = int(sent.group(1)) if sent else 0
        record["bytes_received"] = int(received.group(1)) if received else 0
        record["retransmits"] = int(retrans.group(1)) if retrans else 0
        record["segs_out"] = int(segs_out.group(1)) if segs_out else 0
        record["loss_pct"] = (
            round(record["retransmits"] / record["segs_out"] * 100.0, 3)
            if record["segs_out"]
            else 0.0
        )
        records.append(record)

    for line in text.splitlines():
        if not line.strip():
            continue
        if line[0].isspace():
            if current is not None:
                current["metrics"] += " " + line.strip()
            continue
        finalize(current)
        current = None
        parts = line.split(None, 5)
        if len(parts) < 5:
            continue
        state, recv_q, send_q, local, peer = parts[:5]
        if state != "ESTAB":
            continue
        src_ip, src_port = _split_endpoint(local)
        dst_ip, dst_port = _split_endpoint(peer)
        process = ""
        if len(parts) > 5:
            match = re.search(r'users:\(\("([^"]+)"', parts[5])
            if match:
                process = match.group(1)
        current = {
            "protocol": protocol,
            "state": state,
            "recv_q": int(recv_q) if recv_q.isdigit() else 0,
            "send_q": int(send_q) if send_q.isdigit() else 0,
            "src_ip": src_ip,
            "src_port": src_port,
            "dst_ip": dst_ip,
            "dst_port": dst_port,
            "process": process,
            "metrics": "",
        }
    finalize(current)
    return records




def request_id() -> str:
    return uuid.uuid4().hex[:16]


def _read_interface_counters(ifname: str) -> dict[str, int] | None:
    """Read rx_bytes/tx_bytes/rx_packets/tx_packets from /proc/net/dev for an interface."""
    try:
        with open(NET_DEV, "r", encoding="utf-8") as fh:
            for line in fh:
                if ":" not in line:
                    continue
                name, rest = line.split(":", 1)
                if name.strip() != ifname:
                    continue
                fields = rest.split()
                # /proc/net/dev fields: bytes packets errs drop fifo frame compressed multicast | bytes packets errs drop fifo colls carrier compressed
                return {
                    "rx_bytes": int(fields[0]),
                    "rx_packets": int(fields[1]),
                    "tx_bytes": int(fields[8]),
                    "tx_packets": int(fields[9]),
                }
    except (OSError, ValueError, IndexError):
        return None
    return None


def _discover_wan_interface() -> str | None:
    """Detect the WAN/uplink interface from the default route."""
    try:
        with open("/proc/net/route", "r", encoding="utf-8") as fh:
            for line in fh:
                fields = line.split()
                if len(fields) < 2:
                    continue
                if fields[1] == "00000000":  # default route
                    return fields[0]
    except OSError:
        return None
    return None


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
    emulated: bool = False


@dataclass
class Tick:
    ts: float
    interval_s: float
    counters: dict[str, object] = field(default_factory=dict)
    emulated: bool = False


class AgentCore:
    """Orchestrates slow and fast control loops over real measurements."""

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
        emulated: bool = False,
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
        self._wan_iface = _discover_wan_interface()
        self._last_rx_bytes: int | None = None
        self._last_tx_bytes: int | None = None
        self._last_probe_ts: float | None = None
        self._flow_id_counter = 0
        self._socket_samples: dict[str, tuple[float, int, float]] = {}
        self._conntrack_samples: dict[str, tuple[float, int, float]] = {}
        self._last_throughput_mbps = 0.0
        self._last_slow_ms = 0.0
        self._last_quality = (0.0, 0.0, 0.0)
        self._last_queue_pkts = 0
        self._last_policy_signature: tuple | None = None
        self._last_policy_qoe: float | None = None
        self._last_policy_capacity: float | None = None

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
        started = time.monotonic()

        # 1. Discover/refresh devices from real kernel state (interfaces + ARP).
        devices = self._refresh_devices()

        # 2. Gather real flow features (from sockets + conntrack) and classify.
        flows = self._gather_and_classify_flows(devices)

        # 3. Estimate link capacity (Kalman) from real interface throughput probes.
        estimate = self._estimate_capacity(now)

        # 4. Compute per-flow / per-device QoE from real measurements.
        qoe = self._compute_qoe(devices, flows, estimate, now)

        # 5. Run max-min policy over active intents + floors.
        policy = self._run_policy(flows, estimate, qoe)
        self._record_policy_decision(policy, qoe, estimate)

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
        self._persist_link_metrics(qoe, now)
        self._last_slow_ms = (time.monotonic() - started) * 1000.0
        if self.on_snapshot:
            result = self.on_snapshot(snapshot)
            if asyncio.iscoroutine(result):
                await result
        logger.info(
            "slow tick: %d devices, %d flows, %.2f Mbps capacity, %.1f ms rtt",
            len(snapshot.devices),
            len(snapshot.flows),
            snapshot.capacity_mbps or 0.0,
            float(qoe.get("rtt_ms", 0.0) or 0.0),
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
        # Probe real interface throughput and feed Kalman + CUSUM.
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
                "wan_interface": self._wan_iface,
            },
            emulated=self.emulated,
        )
        self._last_tick = tick
        self._last_throughput_mbps = sample_mbps
        self._persist_metrics_raw(now, "throughput_mbps", sample_mbps, "wan")
        if self.on_tick:
            result = self.on_tick(tick)
            if asyncio.iscoroutine(result):
                await result
        logger.debug(
            "fast tick",
            extra={"request_id": rid, "ts": tick.ts, "capacity_mbps": estimate.capacity_mbps},
        )
        if cusum_result.triggered:
            logger.warning(
                "step change detected; reactive fallback",
                extra={"request_id": rid, "statistic": cusum_result.statistic},
            )

    # ------------------------------------------------------------------ real data sources

    def _refresh_devices(self) -> list[dict]:
        """Discover real devices on this host/LAN and persist them.

        Sources: local interfaces (this host) and the kernel ARP/neighbour
        table (other LAN hosts).  The default gateway is the router, not a
        household endpoint, so it is not reported as a device.  No names are
        invented: a new device is named after its address until the user
        renames it.
        """
        now = time.time()
        host_ips: set[str] = set()
        gateway = self._default_gateway()
        for ifname in _local_interfaces():
            ipv4 = _interface_ipv4(ifname)
            if not ipv4:
                continue
            host_ips.add(ipv4)
            ipv6 = _interface_ipv6(ifname)
            if ipv6:
                host_ips.add(ipv6)
            mac = _interface_mac(ifname)
            device_id = f"host-{ifname}"
            self.store.execute(
                "INSERT INTO devices(device_id, name, mac, ipv4, ipv6, assigned_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(device_id) DO UPDATE SET mac=excluded.mac, ipv4=excluded.ipv4, "
                "ipv6=excluded.ipv6",
                (device_id, f"This host · {ifname}", mac, ipv4, ipv6, now),
            )
        for neighbor in _read_neighbors():
            ipv4 = neighbor["ip"]
            mac = neighbor["mac"]
            if ipv4 in host_ips or ipv4 == gateway:
                continue
            if ipv4.startswith("169.254."):
                continue
            device_id = f"dev-{mac.replace(':', '')}"
            self.store.execute(
                "INSERT INTO devices(device_id, name, mac, ipv4, ipv6, assigned_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(device_id) DO UPDATE SET mac=excluded.mac, ipv4=excluded.ipv4",
                (device_id, f"Device {ipv4}", mac, ipv4, None, now),
            )
        self.store.commit()
        rows = self.store.execute(
            "SELECT device_id, name, mac, ipv4, ipv6, assigned_at FROM devices ORDER BY assigned_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def _read_socket_flows(self) -> tuple[list[dict], int]:
        """Read real per-socket byte counters and RTT from ``ss``.

        Works without root on Linux and gives per-connection throughput,
        retransmits and RTT for flows owned by this host.  Returns the raw
        records plus the number of packets currently queued in send buffers.
        """
        records: list[dict] = []
        for args, protocol in ((["ss", "-tinp"], "tcp"), (["ss", "-uinp"], "udp")):
            try:
                result = subprocess.run(
                    args, capture_output=True, text=True, timeout=5, check=False
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode == 0:
                records.extend(_parse_ss_output(result.stdout, protocol))

        now = time.time()
        flows: list[dict] = []
        queue_pkts = 0
        active_ids: set[str] = set()
        for record in records:
            src_ip = record.get("src_ip", "")
            dst_ip = record.get("dst_ip", "")
            if not src_ip or not dst_ip:
                continue
            if src_ip.startswith("127.") or dst_ip.startswith("127."):
                continue
            if dst_ip.startswith("169.254.") or dst_ip.startswith("fe80:"):
                continue
            queue_pkts += int(record.get("send_q", 0)) + int(record.get("recv_q", 0))
            flow_id = (
                f"sock-{record['protocol']}-{src_ip}:{record.get('src_port', 0)}"
                f"-{dst_ip}:{record.get('dst_port', 0)}"
            )
            active_ids.add(flow_id)
            total = int(record.get("bytes_sent", 0)) + int(record.get("bytes_received", 0))
            previous = self._socket_samples.get(flow_id)
            if previous:
                delta = max(0, total - previous[1])
                elapsed = max(0.001, now - previous[0])
                bytes_per_s = delta / elapsed
                first_seen = previous[2]
                # A byte counter can reset if a socket tuple is reused.
                if total < previous[1]:
                    bytes_per_s = 0.0
            else:
                bytes_per_s = 0.0
                first_seen = now
            self._socket_samples[flow_id] = (now, total, first_seen)
            sent = int(record.get("bytes_sent", 0))
            received = int(record.get("bytes_received", 0))
            direction_ratio = sent / (sent + received) if (sent + received) else 0.5
            flows.append(
                {
                    "flow_id": flow_id,
                    "src_ip": src_ip,
                    "dst_ip": dst_ip,
                    "src_port": record.get("src_port"),
                    "dst_port": record.get("dst_port"),
                    "protocol": record["protocol"],
                    "process": record.get("process") or "",
                    "bytes_per_s": bytes_per_s,
                    "bytes_sent": sent,
                    "bytes_received": received,
                    "rtt_ms": float(record.get("rtt_ms", 0.0) or 0.0),
                    "jitter_ms": float(record.get("jitter_ms", 0.0) or 0.0),
                    "loss_pct": float(record.get("loss_pct", 0.0) or 0.0),
                    "retransmits": int(record.get("retransmits", 0) or 0),
                    "direction_ratio": direction_ratio,
                    "duration_s": max(1.0, now - first_seen),
                    "first_seen": first_seen,
                    "last_seen": now,
                }
            )
        for flow_id in list(self._socket_samples):
            if flow_id not in active_ids:
                del self._socket_samples[flow_id]
        self._last_queue_pkts = queue_pkts
        return flows, queue_pkts


    def _gather_and_classify_flows(self, devices: list[dict]) -> list[dict]:
        """Gather real flow features from sockets + conntrack, then classify."""
        now = time.time()
        candidates: list[dict] = []

        # Per-socket measurements (works for traffic owned by this host).
        socket_flows, _queue = self._read_socket_flows()
        candidates.extend(socket_flows)

        # Kernel connection tracking (gateway/bridge mode, when readable).
        for cf in self._read_conntrack_flows():
            flow_id = (
                f"ct-{cf.get('protocol', 'tcp')}-{cf.get('src_ip')}:{cf.get('src_port')}"
                f"-{cf.get('dst_ip')}:{cf.get('dst_port')}"
            )
            total = int(cf.get("bytes", 0) or 0)
            previous = self._conntrack_samples.get(flow_id)
            if previous:
                elapsed = max(0.001, now - previous[0])
                bytes_per_s = max(0, total - previous[1]) / elapsed
                first_seen = previous[2]
            else:
                bytes_per_s = 0.0
                first_seen = now
            self._conntrack_samples[flow_id] = (now, total, first_seen)
            cf.update(
                {
                    "flow_id": flow_id,
                    "bytes_per_s": bytes_per_s,
                    "duration_s": max(1.0, now - first_seen),
                    "first_seen": first_seen,
                    "last_seen": now,
                    "direction_ratio": 0.5,
                    "rtt_ms": 0.0,
                    "jitter_ms": 0.0,
                    "loss_pct": 0.0,
                    "retransmits": 0,
                    "bytes_sent": 0,
                    "bytes_received": 0,
                    "process": "",
                }
            )
            candidates.append(cf)

        flows: list[dict] = []
        seen: set[str] = set()
        for cf in candidates:
            flow_id = str(cf.get("flow_id", ""))
            if not flow_id or flow_id in seen:
                continue
            seen.add(flow_id)
            device_id = self._device_for_ip(str(cf.get("src_ip", "")), devices)
            if not device_id:
                continue
            features = {
                "protocol": cf.get("protocol", "tcp"),
                "dst_port": cf.get("dst_port"),
                "src_port": cf.get("src_port"),
                "direction_ratio": float(cf.get("direction_ratio", 0.5)),
                "bytes_per_s": float(cf.get("bytes_per_s", 0.0)),
                "duration_s": float(cf.get("duration_s", 1.0)),
                "burstiness": float(cf.get("burstiness", 0.0) or 0.0),
                "pkt_len_mean": float(cf.get("pkt_len_mean", 0.0) or 0.0),
                "iat_mean": float(cf.get("iat_mean", 10.0) or 10.0),
            }
            result: ClassifierResult = self.classifier.predict(features)
            flows.append(
                {
                    **cf,
                    "flow_id": flow_id,
                    "device_id": device_id,
                    "traffic_class": result.predicted_class,
                    "confidence": result.confidence,
                    "method": result.method,
                    "bytes_per_s": features["bytes_per_s"],
                }
            )
        return flows

    def _read_conntrack_flows(self) -> list[dict]:
        """Read active connections from /proc/net/nf_conntrack (requires root/CAP_NET_ADMIN)."""
        flows: list[dict] = []
        conntrack_paths = [
            "/proc/net/nf_conntrack",
            "/proc/net/nf_conntrack_bridge",
        ]
        for path in conntrack_paths:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        parsed = self._parse_conntrack_line(line)
                        if parsed:
                            flows.append(parsed)
            except OSError:
                continue
        return flows

    @staticmethod
    def _parse_conntrack_line(line: str) -> dict | None:
        """Parse a single /proc/net/nf_conntrack line into flow features."""
        # Example: ipv4     2 tcp      6 431989 ESTABLISHED src=10.0.0.10 dst=198.51.100.1 sport=54321 dport=443 packets=1234 bytes=98765 ...
        parts = line.split()
        if len(parts) < 8:
            return None
        proto = parts[2] if len(parts) > 2 else "tcp"
        src_match = re.search(r"src=(\S+)", line)
        dst_match = re.search(r"dst=(\S+)", line)
        sport_match = re.search(r"sport=(\d+)", line)
        dport_match = re.search(r"dport=(\d+)", line)
        packets_match = re.search(r"packets=(\d+)", line)
        bytes_match = re.search(r"bytes=(\d+)", line)
        if not src_match or not dst_match:
            return None
        return {
            "protocol": proto,
            "src_ip": src_match.group(1),
            "dst_ip": dst_match.group(1),
            "src_port": int(sport_match.group(1)) if sport_match else 0,
            "dst_port": int(dport_match.group(1)) if dport_match else 0,
            "packets": int(packets_match.group(1)) if packets_match else 0,
            "bytes": int(bytes_match.group(1)) if bytes_match else 0,
            "bytes_per_s": 0.0,
            "duration_s": 1.0,
            "burstiness": 0.0,
            "pkt_len_mean": 0.0,
            "iat_mean": 10.0,
        }

    def _device_for_ip(self, ip: str, devices: list[dict]) -> str | None:
        """Match a source IP (IPv4 or IPv6) to a known device."""
        for d in devices:
            if d.get("ipv4") == ip or d.get("ipv6") == ip:
                return d["device_id"]
        return None

    def _probe_throughput(self) -> float:
        """Return a real throughput probe sample (Mbps) from /proc/net/dev."""
        if not self._wan_iface:
            return 0.0
        counters = _read_interface_counters(self._wan_iface)
        if counters is None:
            return 0.0
        now = time.time()
        if self._last_rx_bytes is None or self._last_probe_ts is None:
            self._last_rx_bytes = counters["rx_bytes"]
            self._last_tx_bytes = counters["tx_bytes"]
            self._last_probe_ts = now
            return 0.0
        elapsed = max(0.001, now - self._last_probe_ts)
        rx_delta = max(0, counters["rx_bytes"] - self._last_rx_bytes)
        tx_delta = max(0, counters["tx_bytes"] - self._last_tx_bytes)
        total_bytes = rx_delta + tx_delta
        mbps = (total_bytes * 8) / (elapsed * 1_000_000.0)
        self._last_rx_bytes = counters["rx_bytes"]
        self._last_tx_bytes = counters["tx_bytes"]
        self._last_probe_ts = now
        return round(mbps, 4)

    def _estimate_capacity(self, now: float):
        """Run a real throughput probe and update the Kalman estimator."""
        observation = self._probe_throughput()
        # Floor at a small positive value so the Kalman filter stays active.
        observation = max(observation, 0.1)
        return step_change_detected(self.estimator, observation, self.cusum, dt=1.0)[0]

    def _compute_qoe(
        self,
        devices: list[dict],
        flows: list[dict],
        estimate,
        now: float,
    ) -> dict[str, object]:
        """Compute per-device QoE from real measurements (RTT/jitter/throughput)."""

        rtt_ms, jitter_ms, loss_pct = self._probe_network_quality()
        self._last_quality = (rtt_ms, jitter_ms, loss_pct)

        # Prefer per-flow RTT from the kernel when available (ss reports it).
        flow_rtts = [float(f.get("rtt_ms", 0.0)) for f in flows if f.get("rtt_ms")]
        flow_jitters = [float(f.get("jitter_ms", 0.0)) for f in flows if f.get("jitter_ms")]
        if flow_rtts:
            rtt_ms = float(statistics.median(flow_rtts))
        if flow_jitters:
            jitter_ms = float(statistics.median(flow_jitters))
        flow_losses = [float(f.get("loss_pct", 0.0)) for f in flows if f.get("segs_out")]
        if flow_losses:
            loss_pct = float(statistics.median(flow_losses))

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
                flow_rtt = float(f.get("rtt_ms", 0.0) or rtt_ms)
                flow_jitter = float(f.get("jitter_ms", 0.0) or jitter_ms)
                flow_loss = float(f.get("loss_pct", 0.0) or loss_pct)
                score: float | None
                method = tc
                if tc == "call":
                    score = call_qoe(rtt_ms=flow_rtt, jitter_ms=flow_jitter, loss_pct=flow_loss)
                elif tc == "game":
                    score = game_qoe(rtt_ms=flow_rtt, jitter_ms=flow_jitter, loss_pct=flow_loss)
                elif tc == "stream":
                    score = (
                        stream_qoe(
                            throughput_mbps=rate,
                            target_bitrate_mbps=6.0,
                            cov=0.1,
                            rebuffer_fraction=0.0,
                        )
                        if rate > 0
                        else None
                    )
                elif tc == "bulk":
                    score = (
                        bulk_qoe(
                            rate_mbps=rate,
                            fair_expected_mbps=capacity / max(1, len(dev_flows)),
                        )
                        if rate > 0
                        else None
                    )
                else:
                    # Unknown traffic still has a measured responsiveness.
                    # Use the game/RTT formula as a transparent proxy rather
                    # than guessing a class.
                    score = game_qoe(rtt_ms=flow_rtt, jitter_ms=flow_jitter, loss_pct=flow_loss)
                    method = "responsiveness_proxy"
                flow_qoes.append(
                    {
                        "flow_id": f["flow_id"],
                        "device_id": did,
                        "traffic_class": tc,
                        "qoe": score,
                        "band": qoe_band(score),
                        "confidence": f.get("confidence", 0.0),
                        "method": method,
                        "bytes_per_s": f.get("bytes_per_s", 0.0),
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
            "rtt_ms": rtt_ms,
            "jitter_ms": jitter_ms,
            "loss_pct": loss_pct,
            "queue_pkts": self._last_queue_pkts,
            "throughput_mbps": self._last_throughput_mbps,
        }

    def _probe_network_quality(self) -> tuple[float, float, float]:
        """Measure real RTT/jitter/loss via ping to the default gateway or 1.1.1.1."""
        import subprocess

        target = "1.1.1.1"
        try:
            gw = self._default_gateway()
            if gw:
                target = gw
        except Exception:
            pass
        try:
            result = subprocess.run(
                ["ping", "-c", "3", "-W", "2", target],
                capture_output=True,
                text=True,
                timeout=8,
            )
            if result.returncode != 0:
                return 0.0, 0.0, 100.0
            output = result.stdout
            rtt_ms = 0.0
            jitter_ms = 0.0
            loss_pct = 100.0
            for line in output.splitlines():
                if "rtt min/avg/max" in line:
                    try:
                        parts = line.split("=")[1].split("/")
                        rtt_ms = float(parts[1])  # avg
                        jitter_ms = float(parts[2]) - float(parts[1])  # max - avg as jitter proxy
                        if jitter_ms < 0:
                            jitter_ms = 0.0
                    except (IndexError, ValueError):
                        pass
                if "packet loss" in line:
                    try:
                        loss_pct = float(line.split("%")[0].split()[-1])
                    except (ValueError, IndexError):
                        loss_pct = 0.0
            return round(rtt_ms, 2), round(jitter_ms, 2), round(loss_pct, 2)
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            return 0.0, 0.0, 100.0

    def _default_gateway(self) -> str | None:
        """Read the default gateway from /proc/net/route."""
        try:
            with open("/proc/net/route", "r", encoding="utf-8") as fh:
                for line in fh:
                    fields = line.split()
                    if len(fields) < 3:
                        continue
                    if fields[1] == "00000000":
                        # Gateway is stored as hex in little-endian
                        gw_hex = fields[2]
                        if gw_hex == "00000000":
                            continue
                        import struct

                        gw_bytes = bytes.fromhex(gw_hex)
                        gw = ".".join(str(b) for b in struct.unpack("BBBB", gw_bytes[::-1]))
                        return gw
        except OSError:
            return None
        return None

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

    def _record_policy_decision(self, policy: dict, qoe: dict, estimate) -> None:
        """Record a real policy recomputation in the hash-chained log.

        The daemon runs in monitor mode: it computes allocations from measured
        demand but does not alter a qdisc.  The decision evidence says so, so
        the log never implies a network change that did not happen.
        """
        if not self.decision_log:
            return
        allocations = {
            str(a.get("flow_id")): round(float(a.get("rate_mbps", 0.0)), 3)
            for a in policy.get("allocations", [])
            if a.get("flow_id")
        }
        signature = tuple(sorted(allocations.items()))
        current_qoe = qoe.get("household")
        current_qoe = float(current_qoe) if isinstance(current_qoe, (int, float)) else None
        capacity = float(estimate.capacity_mbps) if estimate else 0.0
        if signature == self._last_policy_signature:
            return
        initial = self._last_policy_signature is None
        before = {
            "qoe": self._last_policy_qoe if self._last_policy_qoe is not None else (current_qoe or 0.0),
            "capacity": self._last_policy_capacity if self._last_policy_capacity is not None else capacity,
        }
        after = {
            "qoe": current_qoe or 0.0,
            "capacity": capacity,
        }
        if not allocations and initial:
            self._last_policy_signature = signature
            return
        self.decision_log.append(
            trigger="policy.baseline" if initial else "policy.recompute",
            reason=(
                "Initial max-min allocation computed from measured demand. "
                "Monitor-only: no qdisc was changed."
                if initial
                else "Allocation recomputed after demand changed. "
                "Monitor-only: no qdisc was changed."
            ),
            before_qoe=before,
            after_qoe=after,
            evidence={
                "mode": "monitor-only",
                "allocations": allocations,
                "rationale": policy.get("rationale", ""),
                "jain": qoe.get("jain"),
                "rtt_ms": qoe.get("rtt_ms"),
                "loss_pct": qoe.get("loss_pct"),
            },
            verdict="kept",
        )
        self._last_policy_signature = signature
        self._last_policy_qoe = current_qoe
        self._last_policy_capacity = capacity

    def _refresh_intents(self, now: float) -> list[dict]:
        if not self.intent_service:
            return []
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
            "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
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

    def _persist_metrics_raw(
        self, now: float, metric: str, value: float, device_id: str = "gateway"
    ) -> None:
        self.store.execute(
            "INSERT INTO metrics_raw(ts, device_id, flow_id, metric, value, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (now, device_id, "wan", metric, float(value), "proc_net_dev"),
        )
        self.store.commit()

    def _persist_link_metrics(self, qoe: dict, now: float) -> None:
        for metric, value, source in (
            ("latency_ms", qoe.get("rtt_ms", 0.0), "ping"),
            ("jitter_ms", qoe.get("jitter_ms", 0.0), "ping"),
            ("loss_pct", qoe.get("loss_pct", 0.0), "ping"),
            ("queue_pkts", qoe.get("queue_pkts", 0), "ss"),
            ("fairness", qoe.get("jain", 1.0), "qoe"),
        ):
            if value is None:
                continue
            self.store.execute(
                "INSERT INTO metrics_raw(ts, device_id, flow_id, metric, value, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now, "gateway", "wan", metric, float(value), source),
            )
        self.store.commit()

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
        print("agent start")
    elif cmd == "stop":
        print("agent stop")
    elif cmd == "status":
        print("agent status: running")
    elif cmd == "logs":
        print("agent logs")
    elif cmd == "export-diagnostics":
        print(json.dumps({"diagnostics": "redacted", "emulated": False}))
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
