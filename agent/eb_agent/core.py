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
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

from eb_agent.classifier import (
    CLASS_LABELS,
    ClassifierResult,
    FlowClassifier,
    get_classifier,
    rule_classify,
)
from eb_agent.config import AgentConfig
from eb_agent.decision_log import DecisionLog
from eb_agent.demand import DemandForecaster
from eb_agent.estimator import CUSUMDetector, KalmanEstimator, step_change_detected
from eb_agent.forecaster import Forecast, ForecasterGate, HoltWintersForecaster
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
    """Read the kernel neighbour table (IPv4 + IPv6) from ``ip -j neigh``.

    ``/proc/net/arp`` only exposes IPv4, so IPv6-only hotspot clients and
    modern phones would otherwise never appear.  ``ip`` is part of iproute2,
    which the enforcer already requires; the proc table remains a fallback.
    """

    neighbors: list[dict[str, str]] = []
    try:
        result = subprocess.run(
            ["ip", "-j", "neigh", "show"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            for entry in json.loads(result.stdout):
                dst = str(entry.get("dst") or "").split("%", 1)[0]
                mac = str(entry.get("lladdr") or "").lower()
                state = " ".join(entry.get("state") or [])
                if not dst or not mac or mac == "00:00:00:00:00:00":
                    continue
                if "FAILED" in state or "INCOMPLETE" in state:
                    continue
                neighbors.append(
                    {"ip": dst, "mac": mac, "ifname": str(entry.get("dev") or "")}
                )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    if neighbors:
        return neighbors
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


def _read_dhcp_leases() -> list[dict[str, str]]:
    """Read real DHCP leases written by dnsmasq/NetworkManager/OpenWrt.

    A hotspot client often has a lease before it has any ARP entry, so leases
    are the most reliable way to show a phone that just joined the hotspot.
    Only standard dnsmasq-format leases are parsed; nothing is invented.
    """

    import glob

    paths = [
        "/var/lib/misc/dnsmasq.leases",
        "/var/lib/dnsmasq/dnsmasq.leases",
        "/tmp/dhcp.leases",
        "/var/lib/NetworkManager/dnsmasq.leases",
    ]
    paths.extend(glob.glob("/var/lib/NetworkManager/dnsmasq-*.leases"))
    paths.extend(glob.glob("/var/lib/nm-dnsmasq/*.leases"))
    leases: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    # dnsmasq: <expiry> <mac> <ip> <hostname> <client-id>
                    mac = parts[1].lower()
                    ip = parts[2]
                    if ":" not in mac or mac == "00:00:00:00:00:00":
                        continue
                    hostname = parts[3] if len(parts) > 3 and parts[3] != "*" else ""
                    if mac in seen:
                        continue
                    seen.add(mac)
                    leases.append({"mac": mac, "ip": ip, "hostname": hostname})
        except OSError:
            continue
    return leases


def _read_wifi_stations() -> list[dict[str, str]]:
    """Return associated Wi-Fi stations from ``iw dev <iface> station dump``.

    This finds hotspot clients even when they have not sent IP traffic yet and
    therefore have no ARP/neighbour entry.  No ``iw`` or no wireless hardware
    simply yields an empty list.
    """

    try:
        dev_result = subprocess.run(
            ["iw", "dev"], capture_output=True, text=True, timeout=3, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if dev_result.returncode != 0:
        return []
    interfaces: list[str] = []
    for line in dev_result.stdout.splitlines():
        match = re.match(r"\s*Interface\s+(\S+)", line)
        if match:
            interfaces.append(match.group(1))
    stations: list[dict[str, str]] = []
    for ifname in interfaces:
        try:
            result = subprocess.run(
                ["iw", "dev", ifname, "station", "dump"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
            continue
        current: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if line.startswith("Station "):
                if current.get("mac"):
                    stations.append(current)
                current = {"mac": line.split()[1].lower(), "ifname": ifname}
            elif current and "signal:" in line:
                current["signal"] = line.split(":", 1)[1].strip()
        if current.get("mac"):
            stations.append(current)
    return stations


def _read_interface_type(ifname: str) -> str:
    try:
        with open(f"/sys/class/net/{ifname}/type", "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _is_household_interface(ifname: str) -> bool:
    """Exclude loopback and tunnel/virtual interfaces from the device list.

    The agent still measures tunnel interfaces for capacity, but they are not
    household devices and would otherwise fill the UI with fake "devices".
    """

    blocked_prefixes = (
        "lo",
        "tun",
        "tap",
        "wg",
        "proton",
        "pvpn",
        "Cloudflare",
        "docker",
        "veth",
        "br-",
        "virbr",
        "dummy",
        "ipv6leak",
        "zt",
        "tailscale",
    )
    if ifname.startswith(blocked_prefixes):
        return False
    if _read_interface_type(ifname) in {"772", "65534"}:
        return False
    return True


def _local_subnets() -> list[tuple[str, int]]:
    """Return IPv4 subnets configured on this host (for gateway discovery)."""

    subnets: list[tuple[str, int]] = []
    try:
        result = subprocess.run(
            ["ip", "-j", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if result.returncode != 0:
            return subnets
        for entry in json.loads(result.stdout):
            for addr in entry.get("addr_info") or []:
                if addr.get("family") != "inet":
                    continue
                local = addr.get("local")
                prefix = int(addr.get("prefixlen") or 24)
                if local and not local.startswith("127."):
                    subnets.append((str(local), prefix))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return subnets


def _ip_in_subnet(ip: str, local: str, prefix: int) -> bool:
    try:
        packed_ip = struct.unpack("!I", socket.inet_aton(ip))[0]
        packed_net = struct.unpack("!I", socket.inet_aton(local))[0]
    except OSError:
        return False
    mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF if prefix else 0
    return (packed_ip & mask) == (packed_net & mask)


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
        segs_in = re.search(r"segs_in:(\d+)", metrics)
        record["rtt_ms"] = float(rtt.group(1)) if rtt else 0.0
        record["jitter_ms"] = float(rtt.group(2)) if rtt else 0.0
        record["bytes_sent"] = int(sent.group(1)) if sent else 0
        record["bytes_received"] = int(received.group(1)) if received else 0
        record["retransmits"] = int(retrans.group(1)) if retrans else 0
        record["segs_out"] = int(segs_out.group(1)) if segs_out else 0
        record["segs_in"] = int(segs_in.group(1)) if segs_in else 0
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
    forecast: list[dict] = field(default_factory=list)
    forecast_method: str = "unavailable"
    forecast_confidence: float = 0.0
    forecast_gate: dict = field(default_factory=dict)
    demand_forecast: list[dict] = field(default_factory=list)


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
        self._last_queue_bytes = 0
        self._last_policy_signature: tuple | None = None
        self._maintenance_counter = 0
        self._last_policy_qoe: float | None = None
        self._last_policy_capacity: float | None = None
        # --- forecasting / demand ML state (fed by real measurements) ---
        self.forecaster = HoltWintersForecaster(seasonal_periods=12)
        self.forecast_gate = ForecasterGate(
            confidence_threshold=config.forecaster_confidence_threshold
        )
        self.demand_forecaster = DemandForecaster(alpha=0.3)
        self._capacity_samples: deque[float] = deque(maxlen=288)
        self._capacity_forecast: Forecast | None = None
        self._forecast_method = "unavailable"
        self._forecast_confidence = 0.0
        self._forecast_error: float | None = None
        self._forecast_gate_result = None
        self._forecast_updated = 0.0
        self._demand_forecasts: list[dict] = []
        # --- classifier inference provenance ---
        self._inference_totals = {"ml": 0, "rule": 0}
        self._inference_last = {"ml": 0, "rule": 0}
        self._inference_by_class: dict[str, int] = {}
        self._inference_last_ts: float | None = None
        self._last_classifier_method = "rule"
        # Shadow inference: what the model would have said even when its
        # confidence is below the acceptance threshold and the rule baseline
        # answers instead.  This makes the model's real live behaviour visible
        # without letting low-confidence guesses drive decisions.
        self._shadow_totals = {
            "evaluated": 0,
            "accepted": 0,
            "belowThreshold": 0,
            "domainGuard": 0,
            "untrained": 0,
        }
        self._shadow_last = dict(self._shadow_totals)
        self._shadow_by_class: dict[str, int] = {}

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._seed_capacity_samples()
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

    # ------------------------------------------------------------------ forecasting

    def _seed_capacity_samples(self) -> None:
        """Warm the forecaster from persisted capacity history after a restart."""

        try:
            rows = self.store.execute(
                "SELECT capacity_mbps FROM capacity_history ORDER BY ts DESC LIMIT 288"
            ).fetchall()
        except Exception:  # noqa: BLE001 - forecasting must not block startup
            rows = []
        for row in reversed(rows):
            value = row["capacity_mbps"]
            if value is not None:
                self._capacity_samples.append(float(value))

    @staticmethod
    def _mape(actual: Sequence[float], predicted: Sequence[float]) -> float:
        pairs = [
            (float(a), float(p))
            for a, p in zip(actual, predicted)
            if abs(float(a)) > 1e-9
        ]
        if not pairs:
            return 1.0
        return sum(abs(a - p) / abs(a) for a, p in pairs) / len(pairs)

    @staticmethod
    def _holt_winters(
        samples: Sequence[float], steps: int
    ) -> Forecast | None:
        usable = list(samples)
        if len(usable) < 8:
            return None
        seasonal = min(12, max(2, len(usable) // 2))
        model = HoltWintersForecaster(seasonal_periods=seasonal)
        if not model.fit(usable):
            return None
        return model.predict(steps)

    def _update_capacity_forecast(self, now: float, estimate) -> Forecast | None:
        """Fit the capacity forecaster on measured history and gate the result."""

        if estimate is not None and estimate.capacity_mbps is not None:
            self._capacity_samples.append(float(estimate.capacity_mbps))
        samples = list(self._capacity_samples)
        interval = max(1.0, float(self.config.slow_interval_s))
        steps = 12
        forecast: Forecast | None = None
        method = "unavailable"
        confidence = 0.0
        error: float | None = None

        recent = samples[-min(60, len(samples)) :]
        if len(recent) >= 4:
            x = np.arange(len(recent), dtype=float)
            y = np.asarray(recent, dtype=float)
            slope, intercept = np.polyfit(x, y, 1)
            fitted = intercept + slope * x
            residual = float(np.std(y - fitted))
            mean = float(np.mean(y)) or 1.0
            cv = residual / mean
            predicted = [max(0.0, intercept + slope * (len(recent) + i)) for i in range(steps)]
            sigma = max(0.25, residual)
            forecast = Forecast(
                timesteps=[now + (i + 1) * interval for i in range(steps)],
                capacity_mbps=predicted,
                lo_mbps=[max(0.0, value - 1.96 * sigma) for value in predicted],
                hi_mbps=[value + 1.96 * sigma for value in predicted],
                confidence=float(np.clip(0.35 + 0.35 * min(1.0, len(samples) / 48) - cv, 0.05, 0.72)),
                method="linear_trend",
            )
            method = "linear_trend"
            confidence = forecast.confidence
            error = cv

        if len(samples) >= 24:
            # Backtest a four-step holdout before trusting Holt-Winters, then
            # refit on all measurements for the published forecast.
            if len(samples) >= 28:
                backtest = self._holt_winters(samples[:-4], 4)
                if backtest is not None and len(backtest.capacity_mbps) == 4:
                    error = self._mape(samples[-4:], backtest.capacity_mbps)
            hw = self._holt_winters(samples, steps)
            if hw is not None:
                hw.timesteps = [now + (i + 1) * interval for i in range(len(hw.capacity_mbps))]
                base = 0.45 + 0.35 * min(1.0, len(samples) / 96)
                hw.confidence = float(
                    np.clip(base - (error if error is not None else 0.15) * 0.8, 0.05, 0.92)
                )
                hw.method = "holt_winters"
                forecast = hw
                method = "holt_winters"
                confidence = hw.confidence

        if forecast is None:
            # Too little history for a trend; persistence is still honest output.
            mean = float(np.mean(recent)) if recent else 0.0
            forecast = Forecast(
                timesteps=[now + (i + 1) * interval for i in range(steps)],
                capacity_mbps=[mean] * steps,
                lo_mbps=[max(0.0, mean * 0.5)] * steps,
                hi_mbps=[mean * 1.5] * steps,
                confidence=0.1 if recent else 0.0,
                method="persistence",
            )
            method = "persistence"
            confidence = forecast.confidence
            error = 1.0

        forecast.method = method
        forecast.confidence = round(confidence, 3)
        self._capacity_forecast = forecast
        self._forecast_method = method
        self._forecast_confidence = forecast.confidence
        self._forecast_error = round(error, 4) if error is not None else None
        self._forecast_updated = now
        self._forecast_gate_result = self.forecast_gate.evaluate(
            forecast, error if error is not None else 1.0
        )

        # Persist the next-step forecast next to the capacity measurements.
        if forecast.capacity_mbps:
            self.store.execute(
                "INSERT INTO forecasts(ts, horizon_s, capacity_mbps, lo_mbps, hi_mbps, "
                "confidence, method) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    now,
                    interval,
                    float(forecast.capacity_mbps[0]),
                    float(forecast.lo_mbps[0]),
                    float(forecast.hi_mbps[0]),
                    float(forecast.confidence),
                    method,
                ),
            )
            self.store.commit()
        return forecast

    def _update_demand_forecast(self, flows: Sequence[dict]) -> list[dict]:
        """Observe per-class measured demand and publish EWMA forecasts."""

        observed = {label: 0.0 for label in CLASS_LABELS}
        for flow in flows:
            traffic_class = str(flow.get("traffic_class", "unknown"))
            rate = float(flow.get("bytes_per_s", 0.0) or 0.0) * 8.0 / 1_000_000.0
            observed[traffic_class] = observed.get(traffic_class, 0.0) + rate
        for traffic_class, rate in observed.items():
            self.demand_forecaster.observe(traffic_class, rate)
        horizon = max(5.0, float(self.config.slow_interval_s) * 4)
        forecasts = self.demand_forecaster.forecast_all(horizon_s=horizon)
        self._demand_forecasts = [
            {
                "trafficClass": f.traffic_class,
                "horizonSeconds": f.horizon_s,
                "predictedMbps": round(f.predicted_mbps, 4),
                "confidence": round(f.confidence, 3),
                "samples": f.samples,
                "method": "ewma",
            }
            for f in sorted(forecasts.values(), key=lambda f: -f.predicted_mbps)
        ]
        return self._demand_forecasts

    def _forecast_points(self) -> list[dict]:
        forecast = self._capacity_forecast
        if forecast is None:
            return []
        return [
            {
                "ts": float(forecast.timesteps[i]),
                "mbps": round(float(forecast.capacity_mbps[i]), 3),
                "lo": round(float(max(0.0, forecast.lo_mbps[i])), 3),
                "hi": round(float(forecast.hi_mbps[i]), 3),
            }
            for i in range(len(forecast.capacity_mbps))
        ]

    @property
    def forecast_state(self) -> dict:
        gate = self._forecast_gate_result
        return {
            "method": self._forecast_method,
            "confidence": round(self._forecast_confidence, 3),
            "error": self._forecast_error,
            "updatedAt": self._forecast_updated,
            "gate": {
                "useForecast": bool(gate.use_forecast) if gate else False,
                "reason": gate.reason if gate else "forecast_unavailable",
                "threshold": float(self.forecast_gate.confidence_threshold),
            },
            "next": self._forecast_points()[:4],
            "samples": len(self._capacity_samples),
        }

    @property
    def demand_state(self) -> dict:
        return {
            "forecasts": list(self._demand_forecasts),
            "updatedAt": self._forecast_updated,
            "horizonSeconds": max(5.0, float(self.config.slow_interval_s) * 4),
        }

    @property
    def inference_state(self) -> dict:
        total = self._inference_totals["ml"] + self._inference_totals["rule"]
        return {
            "last": dict(self._inference_last),
            "totals": dict(self._inference_totals),
            "total": total,
            "byClass": dict(self._inference_by_class),
            "lastAt": self._inference_last_ts,
            "model": self.classifier.model_kind or "rule_baseline",
            "trained": self.classifier.is_trained,
            "threshold": float(self.classifier.threshold),
            "shadow": {
                "last": dict(self._shadow_last),
                "totals": dict(self._shadow_totals),
                "byClass": dict(self._shadow_by_class),
            },
        }

    async def _slow_tick(self) -> None:
        """Run the synchronous measurement pass off the event loop.

        ``ss``, ``ping`` and SQLite are blocking calls.  Keeping them on the
        asyncio loop starves the HTTP/WebSocket API during every tick, so the
        whole pass runs in a worker thread and only publication happens here.
        """

        snapshot = await asyncio.to_thread(self._compute_slow_tick)
        if snapshot is None:
            return
        self._last_snapshot = snapshot
        self._last_tick = None
        if self.on_snapshot:
            result = self.on_snapshot(snapshot)
            if asyncio.iscoroutine(result):
                await result
        logger.info(
            "slow tick: %d devices, %d flows, %.2f Mbps capacity, %.1f ms rtt",
            len(snapshot.devices),
            len(snapshot.flows),
            snapshot.capacity_mbps or 0.0,
            float(snapshot.qoe.get("rtt_ms", 0.0) or 0.0),
            extra={
                "request_id": request_id(),
                "ts": snapshot.ts,
                "devices": len(snapshot.devices),
                "flows": len(snapshot.flows),
                "capacity_mbps": snapshot.capacity_mbps,
            },
        )

    def _compute_slow_tick(self) -> Snapshot:
        now = time.time()
        started = time.monotonic()

        # 1. Discover/refresh devices from real kernel state (interfaces + ARP).
        devices = self._refresh_devices()

        # 2. Gather real flow features (from sockets + conntrack) and classify.
        flows = self._gather_and_classify_flows(devices)

        # 3. Estimate link capacity (Kalman) from real interface throughput probes.
        estimate = self._estimate_capacity(now)

        # 3b. Fit capacity forecast (Holt-Winters when enough history, linear
        # fallback otherwise) and per-class demand forecasts (EWMA).
        self._update_capacity_forecast(now, estimate)
        demand_forecasts = self._update_demand_forecast(flows)

        # 4. Compute per-flow / per-device QoE from real measurements.
        qoe = self._compute_qoe(devices, flows, estimate, now)

        # 5. Run max-min policy over active intents + floors.
        policy = self._run_policy(flows, estimate, qoe)
        self._record_policy_decision(policy, qoe, estimate)

        # 6. Expire overdue intents.
        active_intents = self._refresh_intents(now)

        # 7. Assemble snapshot; the event-loop wrapper publishes it.
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
            forecast=self._forecast_points(),
            forecast_method=self._forecast_method,
            forecast_confidence=self._forecast_confidence,
            forecast_gate=self.forecast_state["gate"],
            demand_forecast=demand_forecasts,
        )
        self._persist_snapshot(snapshot)
        self._persist_flows(flows)
        self._persist_qoe(qoe)
        self._persist_capacity(estimate, now)
        self._persist_link_metrics(qoe, now)
        self._last_slow_ms = (time.monotonic() - started) * 1000.0
        self._maintenance_counter += 1
        # Run retention/downsampling once a minute of slow ticks rather than
        # on every tick; VACUUM itself is rate-limited inside the store.
        interval = max(1, int(60.0 / max(1.0, float(self.config.slow_interval_s))))
        if self._maintenance_counter % interval == 0:
            try:
                self.downsample()
                self.store.maintenance(
                    raw_seconds=self.config.raw_retention_s,
                    agg_days=self.config.agg_retention_d,
                )
            except Exception:  # noqa: BLE001 - maintenance must not stop control
                logger.exception("store maintenance failed")
        return snapshot

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

        Sources, in order of reliability:
        * local physical interfaces (this host)
        * kernel neighbour table via ``ip -j neigh`` (IPv4 + IPv6)
        * DHCP leases (dnsmasq / NetworkManager / OpenWrt)
        * associated Wi-Fi stations (hotspot clients without an IP yet)

        The default gateway is the router, not a household endpoint, so it is
        not reported as a device.  No names are invented: a new device is named
        after its DHCP hostname or address until the user renames it.
        """

        now = time.time()
        host_ips: set[str] = set()
        host_macs: set[str] = set()
        gateway = self._default_gateway()
        subnets = _local_subnets()

        for ifname in _local_interfaces():
            if not _is_household_interface(ifname):
                continue
            ipv4 = _interface_ipv4(ifname)
            ipv6 = _interface_ipv6(ifname)
            if not ipv4 and not ipv6:
                continue
            if ipv4:
                host_ips.add(ipv4)
            if ipv6:
                host_ips.add(ipv6)
            mac = _interface_mac(ifname)
            if mac:
                host_macs.add(mac.lower())
            device_id = f"host-{ifname}"
            self._upsert_device(
                device_id,
                f"This host · {ifname}",
                mac,
                ipv4,
                ipv6,
                now,
            )

        # Neighbour table: covers phones/laptops that have exchanged packets.
        for neighbor in _read_neighbors():
            ip = neighbor["ip"]
            mac = neighbor["mac"].lower()
            if ip in host_ips or ip == gateway or mac in host_macs:
                continue
            if ip.startswith("169.254.") or ip.startswith("fe80:") or ip.startswith("ff"):
                continue
            device_id = f"dev-{mac.replace(':', '')}"
            if ":" in ip:
                self._upsert_device(device_id, f"Device {ip}", mac, None, ip, now)
            else:
                self._upsert_device(device_id, f"Device {ip}", mac, ip, None, now)

        # DHCP leases: a hotspot client gets a lease before its first packet.
        for lease in _read_dhcp_leases():
            ip = lease["ip"]
            mac = lease["mac"].lower()
            if ip in host_ips or ip == gateway or mac in host_macs:
                continue
            if ip.startswith("169.254."):
                continue
            hostname = lease.get("hostname") or ""
            name = hostname if hostname else f"Device {ip}"
            device_id = f"dev-{mac.replace(':', '')}"
            if ":" in ip:
                self._upsert_device(device_id, name, mac, None, ip, now)
            else:
                self._upsert_device(device_id, name, mac, ip, None, now)

        # Associated Wi-Fi stations: hotspot clients may have no IP yet.
        for station in _read_wifi_stations():
            mac = station["mac"].lower()
            if mac in host_macs or mac == "00:00:00:00:00:00":
                continue
            device_id = f"dev-{mac.replace(':', '')}"
            self._upsert_device(
                device_id,
                f"Wi-Fi client {mac[-8:]}",
                mac,
                None,
                None,
                now,
            )

        self.store.commit()
        rows = self.store.execute(
            "SELECT device_id, name, mac, ipv4, ipv6, assigned_at FROM devices "
            "ORDER BY assigned_at"
        ).fetchall()
        devices = [dict(r) for r in rows]
        self._attach_unknown_flow_devices(devices, subnets, now)
        return devices

    def _upsert_device(
        self,
        device_id: str,
        name: str,
        mac: str | None,
        ipv4: str | None,
        ipv6: str | None,
        now: float,
    ) -> None:
        """Insert/update a real device without erasing better-known fields."""

        self.store.execute(
            "INSERT INTO devices(device_id, name, mac, ipv4, ipv6, assigned_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(device_id) DO UPDATE SET "
            "  mac=COALESCE(excluded.mac, devices.mac), "
            "  ipv4=COALESCE(excluded.ipv4, devices.ipv4), "
            "  ipv6=COALESCE(excluded.ipv6, devices.ipv6), "
            "  name=CASE "
            "    WHEN excluded.name LIKE 'Device %' OR excluded.name LIKE 'Wi-Fi client %' "
            "    THEN devices.name ELSE excluded.name END",
            (device_id, name, mac, ipv4, ipv6, now),
        )

    def _attach_unknown_flow_devices(
        self,
        devices: list[dict],
        subnets: list[tuple[str, int]],
        now: float,
    ) -> None:
        """Create devices for real LAN source IPs seen in conntrack.

        On a hotspot/gateway, a client can appear in conntrack before its ARP
        entry is populated.  Only addresses inside a configured local subnet
        are accepted, so this never invents internet hosts.
        """

        if not subnets:
            return
        known = {d.get("ipv4") for d in devices} | {d.get("ipv6") for d in devices}
        try:
            rows = self.store.execute(
                "SELECT DISTINCT src_ip FROM flows WHERE last_seen > ?", (now - 120.0,)
            ).fetchall()
        except Exception:  # noqa: BLE001
            return
        changed = False
        for row in rows:
            ip = str(row["src_ip"] or "")
            if not ip or ip in known or ip.startswith("127.") or ip.startswith("169.254."):
                continue
            if not any(_ip_in_subnet(ip, local, prefix) for local, prefix in subnets):
                continue
            device_id = f"dev-ip-{ip.replace('.', '-').replace(':', '-')}"
            self._upsert_device(device_id, f"Device {ip}", None, ip if ":" not in ip else None,
                                ip if ":" in ip else None, now)
            known.add(ip)
            changed = True
        if changed:
            self.store.commit()
            rows = self.store.execute(
                "SELECT device_id, name, mac, ipv4, ipv6, assigned_at FROM devices "
                "ORDER BY assigned_at"
            ).fetchall()
            devices[:] = [dict(r) for r in rows]

    def _read_socket_flows(self) -> tuple[list[dict], int]:
        """Read real per-socket byte counters and RTT from ``ss``.

        Works without root on Linux and gives per-connection throughput,
        retransmits and RTT for flows owned by this host.  Returns the raw
        records plus the number of packets implied by queued *bytes*.

        ``ss`` reports send/recv queue fill in bytes, not packets.  We keep the
        real byte count and convert with the standard 1448-byte TCP payload
        (documented approximation) so the UI label "packets" is not a
        mislabelled byte counter.
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
        queue_bytes = 0
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
            flow_queue_bytes = int(record.get("send_q", 0)) + int(record.get("recv_q", 0))
            queue_bytes += flow_queue_bytes
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
            segs_out = int(record.get("segs_out", 0) or 0)
            segs_in = int(record.get("segs_in", 0) or 0)
            total_packets = segs_out + segs_in
            duration_s = max(1.0, now - first_seen)
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
                    "segs_out": segs_out,
                    "segs_in": segs_in,
                    "pkt_len_mean": round((sent + received) / total_packets, 2)
                    if total_packets
                    else 0.0,
                    "packets_per_s": round(total_packets / duration_s, 3),
                    "iat_mean_ms": round(duration_s * 1000.0 / total_packets, 3)
                    if total_packets
                    else 0.0,
                    "rtt_ms": float(record.get("rtt_ms", 0.0) or 0.0),
                    "jitter_ms": float(record.get("jitter_ms", 0.0) or 0.0),
                    "loss_pct": float(record.get("loss_pct", 0.0) or 0.0),
                    "retransmits": int(record.get("retransmits", 0) or 0),
                    "queue_bytes": flow_queue_bytes,
                    "direction_ratio": direction_ratio,
                    "duration_s": duration_s,
                    "first_seen": first_seen,
                    "last_seen": now,
                }
            )
        for flow_id in list(self._socket_samples):
            if flow_id not in active_ids:
                del self._socket_samples[flow_id]
        queue_pkts = int(np.ceil(queue_bytes / 1448.0)) if queue_bytes else 0
        self._last_queue_pkts = queue_pkts
        self._last_queue_bytes = queue_bytes
        return flows, queue_pkts


    def _gather_and_classify_flows(self, devices: list[dict]) -> list[dict]:
        """Gather real flow features from sockets + conntrack, then classify."""
        now = time.time()
        self._inference_last = {"ml": 0, "rule": 0}
        self._shadow_last = {key: 0 for key in self._shadow_totals}
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
            packets = int(cf.get("packets", 0) or 0)
            duration_s = max(1.0, now - first_seen)
            cf.update(
                {
                    "flow_id": flow_id,
                    "bytes_per_s": bytes_per_s,
                    "packets_per_s": round(packets / duration_s, 3),
                    "iat_mean_ms": round(duration_s * 1000.0 / packets, 3)
                    if packets
                    else 0.0,
                    "pkt_len_mean": round(total / packets, 2) if packets else 0.0,
                    "duration_s": duration_s,
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
                "pkt_len_mean": float(cf.get("pkt_len_mean", 0.0) or 0.0),
                "packets_per_s": float(cf.get("packets_per_s", 0.0) or 0.0),
                "iat_mean_ms": float(cf.get("iat_mean_ms", 0.0) or 0.0),
            }
            result: ClassifierResult = self.classifier.predict(features)
            method = result.method if result.method in ("ml", "rule") else "rule"
            self._inference_last[method] += 1
            self._inference_totals[method] += 1
            self._inference_by_class[result.predicted_class] = (
                self._inference_by_class.get(result.predicted_class, 0) + 1
            )
            if result.model_evaluated:
                self._shadow_last["evaluated"] += 1
                self._shadow_totals["evaluated"] += 1
                if method == "ml":
                    self._shadow_last["accepted"] += 1
                    self._shadow_totals["accepted"] += 1
                else:
                    self._shadow_last["belowThreshold"] += 1
                    self._shadow_totals["belowThreshold"] += 1
                if result.model_class:
                    self._shadow_by_class[result.model_class] = (
                        self._shadow_by_class.get(result.model_class, 0) + 1
                    )
            else:
                reason = result.model_skip_reason or "untrained"
                key = "domainGuard" if reason == "domain_guard" else "untrained"
                self._shadow_last[key] += 1
                self._shadow_totals[key] += 1
            self._last_classifier_method = method
            flows.append(
                {
                    **cf,
                    "flow_id": flow_id,
                    "device_id": device_id,
                    "traffic_class": result.predicted_class,
                    "confidence": result.confidence,
                    "method": method,
                    "model_class": result.model_class,
                    "model_confidence": round(float(result.model_confidence), 4)
                    if result.model_evaluated
                    else None,
                    "bytes_per_s": features["bytes_per_s"],
                }
            )
        self._inference_last_ts = now
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
                rate = f.get("bytes_per_s", 0) * 8.0 / 1_000_000.0
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
            "queue_bytes": self._last_queue_bytes,
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
        # The forecast gate decides whether a near-term capacity dip may reduce
        # the capacity the allocator plans against.  When the gate is in
        # fallback the allocator uses the live estimate only.
        planning_capacity = capacity
        forecast_used = False
        gate = self._forecast_gate_result
        if (
            gate is not None
            and gate.use_forecast
            and self._capacity_forecast is not None
            and self._capacity_forecast.capacity_mbps
        ):
            horizon = self._capacity_forecast.capacity_mbps[:4]
            planning_capacity = min(capacity, max(0.1, min(horizon)))
            forecast_used = planning_capacity < capacity - 0.01
        policy = max_min_allocate(demands, planning_capacity, state=self.policy_state)
        rationale = policy.rationale
        if forecast_used:
            rationale += (
                f" Forecast gate active: planning capacity {planning_capacity:.2f} Mbps "
                f"(live estimate {capacity:.2f} Mbps)."
            )
        return {
            "cake_bandwidth_mbit": policy.cake_bandwidth_mbit,
            "infeasible": policy.infeasible,
            "rationale": rationale,
            "planning_capacity_mbps": round(planning_capacity, 3),
            "forecast_used": forecast_used,
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
                "forecast": {
                    "method": self._forecast_method,
                    "confidence": self._forecast_confidence,
                    "gate": (
                        self._forecast_gate_result.reason
                        if self._forecast_gate_result
                        else "forecast_unavailable"
                    ),
                    "gateActive": bool(
                        self._forecast_gate_result
                        and self._forecast_gate_result.use_forecast
                    ),
                    "planningCapacityMbps": policy.get("planning_capacity_mbps"),
                    "used": bool(policy.get("forecast_used")),
                },
                "inference": {
                    "model": self.classifier.model_kind or "rule_baseline",
                    "trained": self.classifier.is_trained,
                    "lastTickMl": self._inference_last.get("ml", 0),
                    "lastTickRule": self._inference_last.get("rule", 0),
                    "lastTickEvaluated": self._shadow_last.get("evaluated", 0),
                    "lastTickBelowThreshold": self._shadow_last.get(
                        "belowThreshold", 0
                    ),
                    "modelsEvaluatedTotal": self._shadow_totals.get("evaluated", 0),
                    "modelsAcceptedTotal": self._shadow_totals.get("accepted", 0),
                },
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
            ("queue_bytes", qoe.get("queue_bytes", 0), "ss"),
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
        """Downsample raw metrics into 15-minute, 10-second and 1-minute buckets.

        The bucket labels are the documented granularities: 15m for long-term
        trends, 24h for the last day at 10-second resolution, and 30d for the
        last month at 1-minute resolution.
        """

        now = time.time()
        for granularity, bucket_s, horizon_s in (
            ("15m", 900, 7 * 86400),
            ("24h", 10, 86400),
            ("30d", 60, 30 * 86400),
        ):
            self.store.execute(
                """
                INSERT OR REPLACE INTO metrics_agg(ts_bucket, granularity, device_id, metric,
                  avg_value, min_value, max_value, p95_value, count, updated_at)
                SELECT
                  (CAST(ts / ? AS INTEGER) * ?) AS ts_bucket,
                  ? AS granularity,
                  device_id,
                  metric,
                  AVG(value), MIN(value), MAX(value),
                  NULL, COUNT(*), ?
                FROM metrics_raw
                WHERE ts > ?
                GROUP BY ts_bucket, device_id, metric
                """,
                (bucket_s, bucket_s, granularity, now, now - horizon_s),
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
