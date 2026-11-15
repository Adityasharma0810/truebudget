"""Rootless Linux lab topology.

The lab creates real Linux network namespaces, veth pairs, a bridge, and a
Linux ``tc``/``netem`` bottleneck.  It runs inside a user+network namespace so
an unprivileged developer can exercise real kernel shaping without sudo:

    unshare -Urn .venv/bin/python -m eb_agent.lab.harness ...

The WAN namespace holds the shaped veth.  The router namespace holds a bridge
that connects the household devices to the WAN endpoint.  Device workloads run
inside their own network namespaces.  Everything here is emulated lab traffic;
it is not the host's real WAN.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

LAB_NET_PREFIX = "10.200.0."
ROUTER_IP = f"{LAB_NET_PREFIX}1"
WAN_IP = f"{LAB_NET_PREFIX}2"
DEVICE_BASE = 10
DEFAULT_WAN_CAPACITY_MBPS = 100.0
DEFAULT_WAN_DELAY_MS = 10.0
DEFAULT_WAN_LOSS_PCT = 0.0
STATE_DIR = Path("/tmp")
STATE_GLOB = "eb-lab-state-*.json"
AGENT_DIR = Path(__file__).resolve().parents[2]


class LabError(RuntimeError):
    """A reproducible lab setup failure with a fix hint."""


def _run(
    command: Sequence[str],
    *,
    check: bool = True,
    timeout: float = 20.0,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        text=True,
        capture_output=True,
        timeout=timeout,
        env={**os.environ, **(env or {})},
    )
    if check and result.returncode != 0:
        raise LabError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
        )
    return result


def _nsenter(pid: int, command: Sequence[str]) -> list[str]:
    return ["nsenter", "-t", str(pid), "-n", *command]


def require_lab_namespace() -> None:
    """Fail early with an actionable message when CAP_NET_ADMIN is missing."""

    if not shutil.which("ip") or not shutil.which("tc"):
        raise LabError("iproute2 is required (ip, tc)")
    probe = _run(["ip", "link", "add", "eb-probe0", "type", "dummy"], check=False)
    if probe.returncode != 0:
        raise LabError(
            "network-admin capability is missing. Run the lab inside a user+network "
            "namespace, e.g. `unshare -Urn .venv/bin/python -m eb_agent.lab.harness ...` "
            "or `make lab-demo`."
        )
    _run(["ip", "link", "del", "eb-probe0"], check=False)


class NetnsProcess:
    """A long-lived process that pins a network namespace."""

    def __init__(self, label: str) -> None:
        self.label = label
        process = subprocess.Popen(
            ["unshare", "-n", "sleep", "86400"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.process = process
        self.pid = process.pid
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if Path(f"/proc/{self.pid}/ns/net").exists():
                return
            if process.poll() is not None:
                raise LabError(f"could not create {label} netns: {process.stderr.read()}")
            time.sleep(0.02)
        raise LabError(f"timed out creating {label} netns")

    def run(
        self,
        *command: str,
        check: bool = True,
        timeout: float = 20.0,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return _run(_nsenter(self.pid, command), check=check, timeout=timeout, env=env)

    def popen(
        self,
        *command: str,
        env: dict[str, str] | None = None,
        stdout: int | None = subprocess.PIPE,
        stderr: int | None = subprocess.PIPE,
    ) -> subprocess.Popen[str]:
        return subprocess.Popen(
            _nsenter(self.pid, command),
            stdout=stdout,
            stderr=stderr,
            text=True,
            env={**os.environ, **(env or {})},
        )

    def read_text(self, path: str) -> str:
        return self.run("cat", path).stdout.strip()

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=2)


@dataclass
class Topology:
    devices: int = 4
    wan_capacity_mbps: float = DEFAULT_WAN_CAPACITY_MBPS
    wan_delay_ms: float = DEFAULT_WAN_DELAY_MS
    wan_loss_pct: float = DEFAULT_WAN_LOSS_PCT
    sink_port: int = 9100
    router: NetnsProcess | None = field(default=None, init=False)
    wan: NetnsProcess | None = field(default=None, init=False)
    device_netns: list[NetnsProcess] = field(default_factory=list, init=False)
    sink_process: subprocess.Popen[str] | None = field(default=None, init=False)
    udp_sink_process: subprocess.Popen[str] | None = field(default=None, init=False)
    state_path: Path | None = field(default=None, init=False)
    _suffix: str = field(default_factory=lambda: secrets.token_hex(3), init=False)
    _started: bool = field(default=False, init=False)

    @property
    def device_ips(self) -> list[str]:
        return [f"{LAB_NET_PREFIX}{DEVICE_BASE + i}" for i in range(self.devices)]

    @property
    def wan_veth(self) -> str:
        return "eth0"

    @property
    def udp_sink_port(self) -> int:
        return self.sink_port + 1

    def __enter__(self) -> "Topology":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    def start(self) -> None:
        if self._started:
            return
        require_lab_namespace()
        self.router = NetnsProcess("router")
        self.wan = NetnsProcess("wan")
        self.device_netns = [NetnsProcess(f"device-{i + 1}") for i in range(self.devices)]
        self._write_state()
        try:
            self._configure_router()
            self._configure_wan()
            for index in range(self.devices):
                self._configure_device(index)
            self._start_sink()
            self.reset_link()
            self._started = True
        except Exception:
            self.stop()
            raise

    def _write_state(self) -> None:
        pids = {
            "router": self.router.pid if self.router else None,
            "wan": self.wan.pid if self.wan else None,
            "devices": [netns.pid for netns in self.device_netns],
            "started_at": time.time(),
        }
        self.state_path = STATE_DIR / f"eb-lab-state-{os.getpid()}.json"
        self.state_path.write_text(json.dumps(pids, indent=2) + "\n", encoding="utf-8")

    def _configure_router(self) -> None:
        assert self.router
        self.router.run("ip", "link", "set", "lo", "up")
        self.router.run("ip", "link", "add", "br0", "type", "bridge")
        self.router.run("ip", "addr", "add", f"{ROUTER_IP}/24", "dev", "br0")
        self.router.run("ip", "link", "set", "br0", "up")

    def _bridge_port(self, router_name: str) -> None:
        assert self.router
        self.router.run("ip", "link", "set", router_name, "master", "br0")
        self.router.run("ip", "link", "set", router_name, "up")

    @staticmethod
    def _disable_offloads(netns: NetnsProcess, interface: str) -> None:
        """Make tc see normal-sized packets, not 64 KiB GSO super-packets."""

        if not shutil.which("ethtool"):
            return
        netns.run(
            "ethtool", "-K", interface, "gso", "off", "tso", "off", "gro", "off",
            check=False,
        )

    def _configure_wan(self) -> None:
        assert self.router and self.wan
        router_end = f"ebw{self._suffix}r"
        wan_end = f"ebw{self._suffix}w"
        _run(["ip", "link", "add", router_end, "type", "veth", "peer", "name", wan_end])
        _run(["ip", "link", "set", router_end, "netns", str(self.router.pid)])
        _run(["ip", "link", "set", wan_end, "netns", str(self.wan.pid)])
        self.router.run("ip", "link", "set", router_end, "name", "wan0")
        self.wan.run("ip", "link", "set", wan_end, "name", self.wan_veth)
        self._bridge_port("wan0")
        self.wan.run("ip", "link", "set", "lo", "up")
        self.wan.run("ip", "addr", "add", f"{WAN_IP}/24", "dev", self.wan_veth)
        self.wan.run("ip", "link", "set", self.wan_veth, "up")
        self._disable_offloads(self.wan, self.wan_veth)

    def _configure_device(self, index: int) -> None:
        assert self.router
        device = self.device_netns[index]
        router_end = f"ebd{self._suffix}{index}"
        device_end = f"ebd{self._suffix}{index}x"
        _run(["ip", "link", "add", router_end, "type", "veth", "peer", "name", device_end])
        _run(["ip", "link", "set", router_end, "netns", str(self.router.pid)])
        _run(["ip", "link", "set", device_end, "netns", str(device.pid)])
        self.router.run("ip", "link", "set", router_end, "name", f"p{index + 1}")
        device.run("ip", "link", "set", device_end, "name", "eth0")
        self._bridge_port(f"p{index + 1}")
        device.run("ip", "link", "set", "lo", "up")
        device.run("ip", "addr", "add", f"{self.device_ips[index]}/24", "dev", "eth0")
        device.run("ip", "link", "set", "eth0", "up")
        self._disable_offloads(device, "eth0")

    def _start_sink(self) -> None:
        assert self.wan
        env = {"PYTHONPATH": str(AGENT_DIR), "PYTHONUNBUFFERED": "1"}
        self.sink_process = self.wan.popen(
            sys.executable, "-m", "eb_agent.lab.workloads", "sink",
            "--port", str(self.sink_port), "--mode", "tcp", env=env,
        )
        self.udp_sink_process = self.wan.popen(
            sys.executable, "-m", "eb_agent.lab.workloads", "sink",
            "--port", str(self.udp_sink_port), "--mode", "udp", env=env,
        )
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            for process, port, protocol, socktype in (
                (self.sink_process, self.sink_port, "tcp", "SOCK_STREAM"),
                (self.udp_sink_process, self.udp_sink_port, "udp", "SOCK_DGRAM"),
            ):
                if process.poll() is not None:
                    stderr = process.stderr.read() if process.stderr else ""
                    raise LabError(f"WAN {protocol} sink failed to start: {stderr}")
                probe = self.wan.run(
                    sys.executable,
                    "-c",
                    (
                        "import socket,sys; s=socket.socket(socket.AF_INET, "
                        f"socket.{socktype}); s.settimeout(0.2); "
                        f"sys.exit(0 if s.connect_ex(('127.0.0.1',{port}))==0 else 1)"
                    ),
                    check=False,
                )
                if probe.returncode != 0:
                    break
            else:
                return
            time.sleep(0.1)
        raise LabError("timed out waiting for WAN sinks")

    # ------------------------------------------------------------------ link
    def _wan_tc(self, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        assert self.wan
        return self.wan.run("tc", *args, check=check)

    def reset_link(self) -> None:
        """Restore the emulated bottleneck: netem rate + delay + loss."""

        self._wan_tc("qdisc", "del", "dev", self.wan_veth, "root", check=False)
        result = self._wan_tc(
            "qdisc",
            "replace",
            "dev",
            self.wan_veth,
            "root",
            "netem",
            "rate",
            f"{self.wan_capacity_mbps:g}mbit",
            "delay",
            f"{self.wan_delay_ms:g}ms",
            "loss",
            f"{self.wan_loss_pct:g}%",
            "limit",
            "1000",
        )
        if result.returncode != 0:
            raise LabError(f"could not install netem: {result.stderr.strip()}")

    def apply_cake(self, bandwidth_mbps: float) -> str:
        """Install CAKE with the given bandwidth; fall back to TBF if needed."""

        bandwidth = f"{max(1.0, bandwidth_mbps):g}mbit"
        self._wan_tc("qdisc", "del", "dev", self.wan_veth, "root", check=False)
        cake = self._wan_tc(
            "qdisc",
            "replace",
            "dev",
            self.wan_veth,
            "root",
            "cake",
            "bandwidth",
            bandwidth,
            "diffserv4",
            "dual-srchost",
            "dual-dsthost",
        )
        if cake.returncode == 0:
            return "cake"
        tbf = self._wan_tc(
            "qdisc",
            "replace",
            "dev",
            self.wan_veth,
            "root",
            "tbf",
            "rate",
            bandwidth,
            "burst",
            "32kb",
            "latency",
            "50ms",
        )
        if tbf.returncode != 0:
            raise LabError(f"neither cake nor tbf could be installed: {tbf.stderr.strip()}")
        return "tbf"

    def remove_shaping(self) -> None:
        self._wan_tc("qdisc", "del", "dev", self.wan_veth, "root", check=False)

    def qdisc(self) -> str:
        assert self.wan
        return self.wan.run("tc", "qdisc", "show", "dev", self.wan_veth).stdout.strip()

    def stats(self) -> dict[str, int]:
        assert self.wan
        # /sys is not remounted per network namespace, so read namespace-local
        # counters through iproute2's JSON interface instead.
        raw = self.wan.run(
            "ip", "-s", "-j", "link", "show", "dev", self.wan_veth
        ).stdout
        try:
            entry = json.loads(raw)[0]
            stats = entry.get("stats64") or entry.get("stats") or {}
            rx = stats.get("rx", {})
            tx = stats.get("tx", {})
            return {
                "rx_packets": int(rx.get("packets", 0)),
                "tx_packets": int(tx.get("packets", 0)),
                "rx_bytes": int(rx.get("bytes", 0)),
                "tx_bytes": int(tx.get("bytes", 0)),
            }
        except (IndexError, KeyError, TypeError, ValueError) as error:
            raise LabError(f"could not parse interface counters: {error}: {raw[:200]}") from error

    def ping(self, device_index: int = 0, count: int = 2) -> tuple[bool, float]:
        device = self.device_netns[device_index]
        result = device.run("ping", "-c", str(count), "-W", "2", WAN_IP, check=False)
        if result.returncode != 0:
            return False, 0.0
        for line in result.stdout.splitlines():
            if "rtt min/avg/max" in line:
                try:
                    avg = float(line.split("=")[1].split("/")[1])
                    return True, avg
                except (IndexError, ValueError):
                    break
        return True, 0.0

    def run_workload(
        self,
        device_index: int,
        traffic_class: str,
        duration_s: float,
        seed: int,
        *,
        target_mbps: float | None = None,
    ) -> subprocess.Popen[str]:
        device = self.device_netns[device_index]
        command = [
            sys.executable,
            "-m",
            "eb_agent.lab.workloads",
            "run",
            "--kind",
            traffic_class,
            "--host",
            WAN_IP,
            "--port",
            str(self.sink_port),
            "--udp-port",
            str(self.udp_sink_port),
            "--duration",
            f"{duration_s:g}",
            "--seed",
            str(seed),
            "--device",
            str(device_index + 1),
        ]
        if target_mbps is not None:
            command.extend(["--target-mbps", f"{target_mbps:g}"])
        return device.popen(*command, env={"PYTHONPATH": str(AGENT_DIR), "PYTHONUNBUFFERED": "1"})

    def stop(self) -> None:
        for process in (self.sink_process, self.udp_sink_process):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
        self.sink_process = None
        self.udp_sink_process = None
        if self.wan:
            self.wan.close()
        if self.router:
            self.router.close()
        for device in self.device_netns:
            device.close()
        self.device_netns = []
        self.wan = None
        self.router = None
        self._started = False
        if self.state_path and self.state_path.exists():
            self.state_path.unlink(missing_ok=True)

    # ---------------------------------------------------------------- cleanup
    def device_pid(self, index: int) -> int:
        return self.device_netns[index].pid


def reset_stale_labs() -> int:
    """Terminate netns holders recorded by interrupted lab runs."""

    removed = 0
    for path in STATE_DIR.glob(STATE_GLOB):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
            continue
        pids: list[int] = []
        for value in (state.get("router"), state.get("wan")):
            if isinstance(value, int):
                pids.append(value)
        for value in state.get("devices", []):
            if isinstance(value, int):
                pids.append(value)
        for pid in pids:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                continue
            removed += 1
        path.unlink(missing_ok=True)
    return removed


def available() -> bool:
    probe = _run(["ip", "link", "add", "eb-probe0", "type", "dummy"], check=False)
    if probe.returncode != 0:
        return False
    _run(["ip", "link", "del", "eb-probe0"], check=False)
    return True


def network_summary(topology: Topology) -> dict[str, object]:
    return {
        "emulated": True,
        "routerIp": ROUTER_IP,
        "wanIp": WAN_IP,
        "deviceIps": topology.device_ips,
        "wanCapacityMbps": topology.wan_capacity_mbps,
        "wanDelayMs": topology.wan_delay_ms,
        "wanLossPct": topology.wan_loss_pct,
        "qdisc": topology.qdisc(),
        "interfaces": ["br0", "wan0", *[f"p{i + 1}" for i in range(topology.devices)]],
    }


def summarize_devices(topology: Topology) -> Iterable[str]:
    assert topology.router
    return [
        line
        for line in topology.router.run("ip", "-brief", "addr", "show").stdout.splitlines()
        if line.startswith("p")
    ]
