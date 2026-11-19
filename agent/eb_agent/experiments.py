"""Phase 6: A/B experiment runner.

Runs reproducible scenario comparisons through the lab harness using
deterministic seeds.  All results are emulated/synthetic -- clearly marked
so they are never mistaken for real measurements.

The harness needs a user+network namespace to build its virtual household.
When the agent itself is not running inside one (the common case) the
harness re-executes itself via ``unshare -Urn``; the experiment therefore
runs in a subprocess instead of blocking the API event loop.
"""

from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

AGENT_DIR = Path(__file__).resolve().parents[1]


@dataclass
class ExperimentResult:
    metric: str
    unit: str
    unmanaged: float
    cake: float
    eb: float
    ci: float


@dataclass
class Experiment:
    id: str
    scenario: str
    seed: int
    trials: int
    progress: int
    status: str  # queued | running | completed | failed
    results: list[ExperimentResult] = field(default_factory=list)
    error: str | None = None
    emulated: bool = True
    created_at: float = field(default_factory=time.time)


class ExperimentStore:
    """In-memory experiment registry (single-process agent)."""

    def __init__(self) -> None:
        self._experiments: dict[str, Experiment] = {}

    def create(
        self,
        scenario: str,
        seed: int,
        trials: int,
    ) -> Experiment:
        exp = Experiment(
            id=f"exp-{uuid.uuid4().hex[:8]}",
            scenario=scenario,
            seed=seed,
            trials=max(1, min(trials, 100)),
            progress=0,
            status="queued",
        )
        self._experiments[exp.id] = exp
        return exp

    def get(self, exp_id: str) -> Experiment | None:
        return self._experiments.get(exp_id)

    def list(self) -> list[Experiment]:
        return sorted(
            self._experiments.values(), key=lambda e: e.created_at, reverse=True
        )

    def update(self, exp: Experiment) -> None:
        self._experiments[exp.id] = exp


def _average(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _ci(values: Sequence[float]) -> float:
    """Illustrative 95% CI half-width (emulated)."""
    if len(values) < 2:
        return 0.0
    mean = _average(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = variance**0.5
    return round(1.96 * std / (len(values) ** 0.5), 4)


def _capacity_for(scenario_name: str) -> float:
    return {
        "saturating_call": 50.0,
        "gaming_download": 100.0,
        "mixed_household": 75.0,
        "bulk_backfill": 40.0,
    }.get(scenario_name, 50.0)


def _run_harness(
    store: ExperimentStore,
    exp: Experiment,
    *,
    duration_s: float,
    capacity_mbps: float,
) -> list[dict[str, object]]:
    """Run the harness CLI and return its per-controller rows.

    The CLI fans every trial out over the three controllers so the
    measurements share a seed, workload and link trace.
    """
    controllers = ["unmanaged", "cake_static", "experience_budget"]
    planned = exp.trials * len(controllers)
    with tempfile.TemporaryDirectory(prefix="eb-experiment-") as tmp:
        json_path = Path(tmp) / "rows.json"
        csv_path = Path(tmp) / "rows.csv"
        command = [
            sys.executable,
            "-m",
            "eb_agent.lab.harness",
            "--controllers",
            ",".join(controllers),
            "--seed",
            str(exp.seed),
            "--duration",
            f"{duration_s:g}",
            "--trials",
            str(exp.trials),
            "--capacity-mbps",
            f"{capacity_mbps:g}",
            "--out",
            str(csv_path),
            "--json",
            str(json_path),
        ]
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                [str(AGENT_DIR), os.environ.get("PYTHONPATH", "")]
            ).strip(os.pathsep),
            "PYTHONUNBUFFERED": "1",
        }
        process = subprocess.Popen(
            command,
            cwd=str(AGENT_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        tail: collections.deque[str] = collections.deque(maxlen=12)
        completed = 0
        assert process.stderr is not None
        for line in process.stderr:
            line = line.rstrip()
            if not line:
                continue
            tail.append(line)
            if "controller=" in line:
                completed += 1
                exp.progress = min(99, int(completed / planned * 100))
                store.update(exp)
        returncode = process.wait()
        if returncode != 0:
            detail = " | ".join(tail) or f"harness exited with {returncode}"
            raise RuntimeError(detail)
        if not json_path.exists():
            detail = " | ".join(tail) or "harness produced no JSON"
            raise RuntimeError(detail)
        rows = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(rows, list) or not rows:
            raise RuntimeError("harness produced no rows")
        return rows


def run_experiment(store: ExperimentStore, exp: Experiment) -> None:
    """Run an A/B experiment: unmanaged vs cake_static vs experience_budget.

    Mutates ``exp`` in place.  Blocking; the API schedules it on a worker
    thread so the request can return while the lab run is in progress.
    """

    exp.status = "running"
    exp.progress = 1
    store.update(exp)

    try:
        capacity = _capacity_for(exp.scenario)
        rows = _run_harness(store, exp, duration_s=2.0, capacity_mbps=capacity)
        by_controller: dict[str, list[dict[str, object]]] = {
            c: [r for r in rows if r.get("controller") == c]
            for c in ("unmanaged", "cake_static", "experience_budget")
        }
        missing = [name for name, values in by_controller.items() if not values]
        if missing:
            raise RuntimeError(
                "harness did not return rows for: " + ", ".join(missing)
            )

        def metric(source: str) -> dict[str, float]:
            return {
                name: _average(
                    [
                        float(row.get(source) or 0.0)
                        for row in values
                        if row.get(source) is not None
                    ]
                )
                for name, values in by_controller.items()
            }

        call_rtt = metric("call_p95_rtt_ms")
        bulk = metric("bulk_throughput_mbps")
        qoe = metric("worst_device_qoe")

        exp.results = [
            ExperimentResult(
                metric="Call p95 RTT",
                unit="ms",
                unmanaged=call_rtt["unmanaged"],
                cake=call_rtt["cake_static"],
                eb=call_rtt["experience_budget"],
                ci=_ci([call_rtt[c] for c in by_controller]),
            ),
            ExperimentResult(
                metric="Bulk throughput",
                unit="Mbps",
                unmanaged=bulk["unmanaged"],
                cake=bulk["cake_static"],
                eb=bulk["experience_budget"],
                ci=_ci([bulk[c] for c in by_controller]),
            ),
            ExperimentResult(
                metric="Worst-device QoE",
                unit="proxy",
                unmanaged=qoe["unmanaged"],
                cake=qoe["cake_static"],
                eb=qoe["experience_budget"],
                ci=_ci([qoe[c] for c in by_controller]),
            ),
        ]

        exp.status = "completed"
        exp.progress = 100
        exp.error = None
    except Exception as error:  # noqa: BLE001 - the UI needs the real reason
        exp.status = "failed"
        exp.error = str(error)
    finally:
        store.update(exp)
