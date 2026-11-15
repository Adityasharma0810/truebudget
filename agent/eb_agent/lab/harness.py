"""A/B lab harness: identical workloads, seeds, and link traces per profile.

Run locally on Linux without sudo:

    unshare -Urn .venv/bin/python -m eb_agent.lab.harness \
      --controllers unmanaged,cake_static --seed 42 --duration 10 \
      --out reports/lab/baseline.csv

or simply ``make lab-demo``.  Every row is marked EMULATED.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import resource
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from eb_agent.qoe import (
    bulk_qoe,
    call_qoe,
    game_qoe,
    household_qoe,
    stream_qoe,
)
from eb_agent.lab.topology import (
    AGENT_DIR,
    DEFAULT_WAN_CAPACITY_MBPS,
    LabError,
    Topology,
    require_lab_namespace,
)

EMULATED = True


@dataclass(frozen=True)
class ControllerResult:
    name: str
    backend: str
    requested_mbps: float | None


class Controller:
    name = "base"

    def apply(self, topology: Topology) -> ControllerResult:
        raise NotImplementedError


class UnmanagedController(Controller):
    name = "unmanaged"

    def apply(self, topology: Topology) -> ControllerResult:
        topology.reset_link()
        return ControllerResult(self.name, "netem", topology.wan_capacity_mbps)


class CakeStaticController(Controller):
    name = "cake_static"

    def __init__(self, shaping_factor: float = 0.95) -> None:
        self.shaping_factor = shaping_factor

    def apply(self, topology: Topology) -> ControllerResult:
        rate = topology.wan_capacity_mbps * self.shaping_factor
        backend = topology.apply_cake(rate)
        return ControllerResult(self.name, backend, rate)


CONTROLLERS: dict[str, Controller] = {
    "unmanaged": UnmanagedController(),
    "cake_static": CakeStaticController(),
}


def _read_child_result(
    process: subprocess.Popen[str],
    kind: str,
    device: int,
    seed: int,
    duration_s: float,
) -> dict[str, object]:
    try:
        stdout, stderr = process.communicate(timeout=max(5.0, duration_s + 8.0))
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                if isinstance(payload, dict):
                    return payload
            except json.JSONDecodeError:
                continue
    return {
        "kind": kind,
        "device": device,
        "seed": seed,
        "duration_s": duration_s,
        "error": (stderr or "workload produced no JSON").strip()[-500:],
        "emulated": True,
    }


def _cpu_percent(before_cpu_s: float, after_cpu_s: float, duration_s: float) -> float:
    return max(0.0, (after_cpu_s - before_cpu_s) / max(0.001, duration_s) * 100.0)


def _usage() -> tuple[float, float]:
    self_usage = resource.getrusage(resource.RUSAGE_SELF)
    child_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return (
        self_usage.ru_utime + self_usage.ru_stime + child_usage.ru_utime + child_usage.ru_stime,
        self_usage.ru_maxrss,
    )


def _qoe_for(kind: str, data: dict[str, object], capacity: float) -> float | None:
    try:
        if kind == "call":
            return call_qoe(
                float(data.get("rtt_mean_ms", 0.0)),
                float(data.get("jitter_p95_ms", 0.0)),
                float(data.get("loss_pct", 0.0)),
            )
        if kind == "game":
            return game_qoe(
                float(data.get("rtt_mean_ms", 0.0)),
                float(data.get("jitter_p95_ms", 0.0)),
                float(data.get("loss_pct", 0.0)),
            )
        if kind == "stream":
            return stream_qoe(
                float(data.get("throughput_mbps", 0.0)),
                6.0,
                float(data.get("cov") or 0.0),
                float(data.get("rebuffer_fraction") or 0.0),
            )
        if kind == "bulk":
            return bulk_qoe(float(data.get("throughput_mbps", 0.0)), capacity / 4.0)
    except (TypeError, ValueError):
        return None
    return None


def run_profile(
    controller: Controller,
    *,
    seed: int,
    duration_s: float,
    trial: int = 1,
    devices: int = 4,
    capacity_mbps: float = DEFAULT_WAN_CAPACITY_MBPS,
    sink_port: int = 9100,
) -> dict[str, object]:
    """Run one controller profile over the standard four-device workload."""

    with Topology(
        devices=max(4, devices),
        wan_capacity_mbps=capacity_mbps,
        wan_delay_ms=10.0,
        wan_loss_pct=0.0,
        sink_port=sink_port,
    ) as topology:
        applied = controller.apply(topology)
        before_stats = topology.stats()
        usage_before = _usage()
        started = time.monotonic()
        assignments = [
            ("call", 6.0),
            ("game", 6.0),
            ("stream", 6.0),
            ("bulk", 6.0),
        ]
        processes: list[tuple[str, int, int, subprocess.Popen[str]]] = []
        for index, (kind, target) in enumerate(assignments[: topology.devices]):
            # Stagger starts by 50 ms so queue build-up is deterministic.
            time.sleep(0.05)
            process = topology.run_workload(
                index,
                kind,
                duration_s,
                seed + index,
                target_mbps=target if kind == "stream" else None,
            )
            processes.append((kind, index, seed + index, process))
        results: list[dict[str, object]] = []
        for kind, index, workload_seed, process in processes:
            results.append(_read_child_result(process, kind, index + 1, workload_seed, duration_s))
        elapsed = max(0.001, time.monotonic() - started)
        after_stats = topology.stats()
        usage_after = _usage()
        qdisc = topology.qdisc()

    by_kind = {str(item.get("kind")): item for item in results}
    call = by_kind.get("call", {})
    game = by_kind.get("game", {})
    stream = by_kind.get("stream", {})
    bulk = by_kind.get("bulk", {})
    qoe_scores = [_qoe_for(kind, data, capacity_mbps) for kind, data in by_kind.items()]
    worst = min((score for score in qoe_scores if score is not None), default=None)
    packets = max(0, after_stats["rx_packets"] - before_stats["rx_packets"])
    packets += max(0, after_stats["tx_packets"] - before_stats["tx_packets"])
    row: dict[str, object] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "emulated": EMULATED,
        "controller": applied.name,
        "backend": applied.backend,
        "seed": seed,
        "trial": trial,
        "duration_s": round(elapsed, 3),
        "capacity_mbps": capacity_mbps,
        "call_p95_rtt_ms": round(float(call.get("rtt_p95_ms", 0.0)), 4),
        "call_jitter_p95_ms": round(float(call.get("jitter_p95_ms", 0.0)), 4),
        "call_jitter_p99_ms": round(float(call.get("jitter_p99_ms", 0.0)), 4),
        "call_loss_pct": round(float(call.get("loss_pct", 0.0)), 4),
        "game_p95_rtt_ms": round(float(game.get("rtt_p95_ms", 0.0)), 4),
        "game_jitter_p95_ms": round(float(game.get("jitter_p95_ms", 0.0)), 4),
        "stream_throughput_mbps": round(float(stream.get("throughput_mbps", 0.0)), 4),
        "bulk_throughput_mbps": round(float(bulk.get("throughput_mbps", 0.0)), 4),
        "bulk_progress_mbps": round(float(bulk.get("throughput_mbps", 0.0)), 4),
        "worst_device_qoe": None if worst is None else round(worst, 4),
        "household_qoe": None
        if not qoe_scores
        else round(household_qoe(qoe_scores) or 0.0, 4),
        "cpu_percent": round(_cpu_percent(usage_before[0], usage_after[0], elapsed), 4),
        "packets_per_second": round(packets / elapsed, 2),
        "qdisc": qdisc,
        "errors": " | ".join(
            str(item["error"]) for item in results if item.get("error")
        ),
    }
    return row


def write_rows(rows: Iterable[dict[str, object]], path: Path) -> Path:
    materialised = list(rows)
    if not materialised:
        raise ValueError("no lab rows to write")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(materialised[0]))
        writer.writeheader()
        writer.writerows(materialised)
    return path


def _reexec_in_namespace(argv: Sequence[str]) -> int:
    env = {
        **os.environ,
        "EB_LAB_NS": "1",
        "PYTHONPATH": os.pathsep.join(
            [str(AGENT_DIR), os.environ.get("PYTHONPATH", "")]
        ).strip(os.pathsep),
    }
    command = [
        "unshare",
        "-Urn",
        "--",
        sys.executable,
        "-m",
        "eb_agent.lab.harness",
        *argv,
    ]
    print(
        "[eb-lab] re-executing inside an unprivileged user+network namespace "
        "(emulated lab; no host traffic is shaped)",
        file=sys.stderr,
    )
    return subprocess.call(command, env=env)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eb-lab")
    parser.add_argument(
        "--controllers",
        default="unmanaged,cake_static",
        help="comma-separated controller names",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--devices", type=int, default=4)
    parser.add_argument("--capacity-mbps", type=float, default=DEFAULT_WAN_CAPACITY_MBPS)
    parser.add_argument("--sink-port", type=int, default=9100)
    parser.add_argument("--out", type=Path, default=Path("reports/lab/baseline.csv"))
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    if not os.environ.get("EB_LAB_NS"):
        try:
            require_lab_namespace()
        except LabError:
            return _reexec_in_namespace(sys.argv[1:] if argv is None else argv)

    names = [name.strip() for name in args.controllers.split(",") if name.strip()]
    unknown = [name for name in names if name not in CONTROLLERS]
    if unknown:
        parser.error(f"unknown controller(s): {', '.join(unknown)}")
    if args.duration <= 0 or args.trials <= 0:
        parser.error("--duration and --trials must be positive")

    rows: list[dict[str, object]] = []
    for trial in range(1, args.trials + 1):
        for name in names:
            print(
                f"[eb-lab] controller={name} seed={args.seed} trial={trial} "
                f"duration={args.duration:g}s (EMULATED)",
                file=sys.stderr,
            )
            row = run_profile(
                CONTROLLERS[name],
                seed=args.seed + trial - 1,
                duration_s=args.duration,
                trial=trial,
                devices=args.devices,
                capacity_mbps=args.capacity_mbps,
                sink_port=args.sink_port,
            )
            rows.append(row)
            print(
                f"[eb-lab]   call p95={row['call_p95_rtt_ms']}ms "
                f"jitter p95={row['call_jitter_p95_ms']}ms "
                f"bulk={row['bulk_throughput_mbps']}Mbps "
                f"worstQoE={row['worst_device_qoe']}",
                file=sys.stderr,
            )
    path = write_rows(rows, args.out)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"[eb-lab] wrote {len(rows)} row(s) to {path}", file=sys.stderr)
    print(f"EMULATED lab CSV: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
