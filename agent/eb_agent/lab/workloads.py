"""Synthetic household workloads for the lab.

All traffic is generated locally against the lab WAN sink.  No real user
payloads are captured, stored, or inspected.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import socket
import socketserver
import statistics
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

EMULATED = True
PACKET_HEADER = struct.Struct("!Id")


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    index = (len(ordered) - 1) * min(1.0, max(0.0, fraction))
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return float(ordered[lower])
    weight = index - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def mean(values: Sequence[float]) -> float:
    return float(statistics.fmean(values)) if values else 0.0


def jitter(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    deltas = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
    return mean(deltas)


def coefficient_of_variation(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = mean(values)
    if avg <= 0:
        return 0.0
    return float(statistics.pstdev(values) / avg)


class _ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _ReusableUDPServer(socketserver.UDPServer):
    # A single thread is enough for call/game echo traffic and avoids the
    # thread-per-datagram overhead that would otherwise add artificial latency.
    allow_reuse_address = True


class _TcpSink(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(10)
        chunk = b"\0" * 65_536
        try:
            self.request.recv(1024)
            while True:
                self.request.sendall(chunk)
        except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
            return


class _UdpSink(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        data, sock = self.request
        try:
            sock.sendto(data, self.client_address)
        except OSError:
            return


def run_sink(port: int, mode: str = "both") -> None:
    """Run a TCP bulk sink and/or UDP echo sink.

    TCP and UDP are normally started as separate processes by the lab topology
    so a saturating Python TCP sender cannot starve the UDP echo thread through
    the GIL.  That keeps call/game latency measurements meaningful.
    """

    if mode not in {"tcp", "udp", "both"}:
        raise ValueError(f"unknown sink mode: {mode}")
    servers: list[socketserver.BaseServer] = []
    threads: list[threading.Thread] = []
    if mode in {"tcp", "both"}:
        servers.append(_ReusableTCPServer(("0.0.0.0", port), _TcpSink))
    if mode in {"udp", "both"}:
        servers.append(_ReusableUDPServer(("0.0.0.0", port), _UdpSink))
    for server in servers:
        threads.append(
            threading.Thread(target=server.serve_forever, name=f"eb-{mode}-sink", daemon=True)
        )
    for thread in threads:
        thread.start()
    print(json.dumps({"ready": True, "port": port, "mode": mode, "emulated": True}), flush=True)
    try:
        while all(thread.is_alive() for thread in threads):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


def ping_rtt(host: str, count: int = 3) -> dict[str, object] | None:
    """Optional ICMP RTT probe using the system ping binary."""

    if not shutil.which("ping"):
        return None
    result = subprocess.run(
        ["ping", "-c", str(count), "-W", "2", host],
        text=True,
        capture_output=True,
        timeout=max(3, count * 3),
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if "rtt min/avg/max" in line:
            values = line.split("=")[1].split("/")
            return {
                "host": host,
                "min_ms": float(values[0]),
                "avg_ms": float(values[1]),
                "max_ms": float(values[2]),
                "emulated": EMULATED,
            }
    return None


def iperf3_bulk(host: str, duration_s: float, port: int = 5201) -> dict[str, object] | None:
    """Optional iperf3 bulk download wrapper (not bundled by default)."""

    if not shutil.which("iperf3"):
        return None
    result = subprocess.run(
        [
            "iperf3",
            "-c",
            host,
            "-p",
            str(port),
            "-t",
            str(max(1, int(duration_s))),
            "-J",
        ],
        text=True,
        capture_output=True,
        timeout=duration_s + 15,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
        bps = float(payload["end"]["sum_received"]["bits_per_second"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return {
        "host": host,
        "throughput_mbps": bps / 1_000_000,
        "emulated": EMULATED,
        "tool": "iperf3",
    }


def flent_rrul(host: str, duration_s: float) -> dict[str, object] | None:
    """Optional flent RRUL wrapper; requires flent + iperf/netperf on PATH."""

    if not shutil.which("flent"):
        return None
    # flent owns its own iperf servers and test protocol.  This wrapper is a
    # thin availability check + invocation, not a replacement for the built-in
    # deterministic workloads.
    result = subprocess.run(
        [
            "flent",
            "rrul",
            "--host",
            host,
            "--length",
            str(max(1, int(duration_s))),
            "--test-parameter",
            "duration",
            str(max(1, int(duration_s))),
        ],
        text=True,
        capture_output=True,
        timeout=duration_s + 30,
    )
    return {
        "host": host,
        "returncode": result.returncode,
        "emulated": EMULATED,
        "tool": "flent-rrul",
        "note": "RRUL is an external test; inspect flent output for series data.",
    }


@dataclass
class WorkloadResult:
    kind: str
    device: int
    seed: int
    duration_s: float
    sent: int = 0
    received: int = 0
    bytes: int = 0
    rtt_samples: list[float] | None = None
    throughput_mbps: float = 0.0
    cov: float | None = None
    rebuffer_fraction: float | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        rtts = self.rtt_samples or []
        return {
            "kind": self.kind,
            "device": self.device,
            "seed": self.seed,
            "duration_s": round(self.duration_s, 3),
            "sent": self.sent,
            "received": self.received,
            "loss_pct": round(
                100.0 * (1.0 - self.received / self.sent) if self.sent else 0.0, 4
            ),
            "bytes": self.bytes,
            "throughput_mbps": round(self.throughput_mbps, 4),
            "rtt_mean_ms": round(mean(rtts), 4),
            "rtt_p95_ms": round(percentile(rtts, 0.95), 4),
            "jitter_mean_ms": round(jitter(rtts), 4),
            "jitter_p95_ms": round(percentile([abs(rtts[i] - rtts[i - 1]) for i in range(1, len(rtts))], 0.95), 4)
            if len(rtts) > 1
            else 0.0,
            "jitter_p99_ms": round(percentile([abs(rtts[i] - rtts[i - 1]) for i in range(1, len(rtts))], 0.99), 4)
            if len(rtts) > 1
            else 0.0,
            "cov": None if self.cov is None else round(self.cov, 4),
            "rebuffer_fraction": None
            if self.rebuffer_fraction is None
            else round(self.rebuffer_fraction, 4),
            "error": self.error,
            "emulated": EMULATED,
        }


def _run_udp(result: WorkloadResult, host: str, port: int, duration: float, interval_ms: float, size: int, seed: int) -> None:
    rng = random.Random(seed)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    deadline = time.monotonic() + duration
    next_send = time.monotonic()
    pending: dict[int, float] = {}
    rtts: list[float] = []
    seq = 0
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_send:
            payload = PACKET_HEADER.pack(seq, time.perf_counter()) + bytes(rng.randrange(256) for _ in range(max(0, size - PACKET_HEADER.size)))
            try:
                sock.sendto(payload, (host, port))
                pending[seq] = time.perf_counter()
                result.sent += 1
                seq += 1
            except OSError:
                pass
            next_send += max(0.001, interval_ms / 1000.0) * rng.uniform(0.98, 1.02)
        while True:
            try:
                data, _ = sock.recvfrom(2048)
            except BlockingIOError:
                break
            except OSError:
                break
            if len(data) >= PACKET_HEADER.size:
                got_seq, _sent_at = PACKET_HEADER.unpack(data[: PACKET_HEADER.size])
                sent_at = pending.pop(got_seq, None)
                if sent_at is not None:
                    result.received += 1
                    rtts.append((time.perf_counter() - sent_at) * 1000.0)
        time.sleep(0.001)
    # Drain late replies briefly.
    drain_until = time.monotonic() + 0.15
    while time.monotonic() < drain_until:
        try:
            data, _ = sock.recvfrom(2048)
        except BlockingIOError:
            time.sleep(0.005)
            continue
        except OSError:
            break
        if len(data) >= PACKET_HEADER.size:
            got_seq, _ = PACKET_HEADER.unpack(data[: PACKET_HEADER.size])
            sent_at = pending.pop(got_seq, None)
            if sent_at is not None:
                result.received += 1
                rtts.append((time.perf_counter() - sent_at) * 1000.0)
    sock.close()
    result.rtt_samples = rtts
    result.bytes = result.sent * size


def _run_tcp(result: WorkloadResult, host: str, port: int, duration: float, target_mbps: float | None) -> None:
    try:
        sock = socket.create_connection((host, port), timeout=3)
    except OSError as error:
        result.error = f"connect failed: {error}"
        return
    sock.settimeout(1.0)
    start = time.monotonic()
    deadline = start + duration
    total = 0
    window_bytes = 0
    windows: list[float] = []
    window_started = start
    target_bps = None if target_mbps is None else max(0.001, target_mbps) * 1_000_000 / 8.0
    try:
        sock.sendall(b"eb\n")
        while time.monotonic() < deadline:
            chunk = sock.recv(65_536)
            if not chunk:
                break
            received_at = time.monotonic()
            total += len(chunk)
            window_bytes += len(chunk)
            if target_bps is not None:
                expected = (received_at - start) * target_bps
                ahead = total - expected
                if ahead > 0:
                    # Client-side pacing lets TCP backpressure produce a
                    # realistic streaming rate rather than a full-speed crawl.
                    time.sleep(min(0.2, ahead / target_bps))
            elapsed = received_at - window_started
            if elapsed >= 1.0:
                windows.append(window_bytes / elapsed / 1_000_000)
                window_bytes = 0
                window_started = received_at
    except (socket.timeout, OSError):
        pass
    finally:
        elapsed = max(0.001, time.monotonic() - start)
        sock.close()
    if window_bytes and time.monotonic() - window_started > 0.1:
        windows.append(window_bytes / max(0.001, time.monotonic() - window_started) / 1_000_000)
    result.bytes = total
    result.throughput_mbps = total * 8 / elapsed / 1_000_000
    if result.kind == "stream":
        result.cov = coefficient_of_variation(windows) if len(windows) >= 2 else 0.0
        if target_mbps is None:
            result.rebuffer_fraction = 0.0
        else:
            result.rebuffer_fraction = (
                sum(1 for value in windows if value < target_mbps) / len(windows)
                if windows
                else 1.0
            )


def run_workload(
    kind: str,
    host: str,
    port: int,
    duration: float,
    seed: int,
    device: int,
    target_mbps: float | None,
    *,
    udp_port: int | None = None,
) -> WorkloadResult:
    if kind not in {"call", "game", "stream", "bulk", "noise"}:
        raise ValueError(f"unknown workload kind: {kind}")
    result = WorkloadResult(kind=kind, device=device, seed=seed, duration_s=duration)
    udp_target = udp_port if udp_port is not None else port
    if kind == "call":
        _run_udp(result, host, udp_target, duration, 20.0, 160, seed)
    elif kind == "game":
        _run_udp(result, host, udp_target, duration, 33.0, 120, seed)
    elif kind == "stream":
        _run_tcp(result, host, port, duration, target_mbps or 4.0)
    elif kind == "bulk":
        _run_tcp(result, host, port, duration, None)
    else:
        _run_udp(result, host, udp_target, duration, 5.0, 320, seed)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eb-lab-workload")
    sub = parser.add_subparsers(dest="command", required=True)
    sink = sub.add_parser("sink")
    sink.add_argument("--port", type=int, default=9100)
    sink.add_argument("--mode", choices=("tcp", "udp", "both"), default="both")
    run = sub.add_parser("run")
    run.add_argument("--kind", required=True)
    run.add_argument("--host", required=True)
    run.add_argument("--port", type=int, default=9100)
    run.add_argument("--udp-port", type=int, default=None)
    run.add_argument("--duration", type=float, default=10.0)
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--device", type=int, default=1)
    run.add_argument("--target-mbps", type=float, default=None)
    run.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.command == "sink":
        run_sink(args.port, args.mode)
        return 0

    result = run_workload(
        args.kind,
        args.host,
        args.port,
        args.duration,
        args.seed,
        args.device,
        args.target_mbps,
        udp_port=args.udp_port,
    )
    payload = result.as_dict()
    text = json.dumps(payload, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
