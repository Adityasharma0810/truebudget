"""Phase 6: A/B experiment runner.

Runs reproducible scenario comparisons through the lab harness using
deterministic seeds.  All results are emulated/synthetic -- clearly marked
so they are never mistaken for real measurements.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Sequence

from eb_agent.lab.harness import CONTROLLERS, run_profile


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


def run_experiment(store: ExperimentStore, exp: Experiment) -> None:
    """Run an A/B experiment: unmanaged vs cake_static vs experience_budget.

    Mutates ``exp`` in place.  Called synchronously from the API handler;
    for a real deployment this would be a background task.
    """

    exp.status = "running"
    exp.progress = 0
    store.update(exp)

    try:
        scenario_name = exp.scenario
        # Determine capacity from scenario-like defaults.
        capacity_map = {
            "saturating_call": 50.0,
            "gaming_download": 100.0,
            "mixed_household": 75.0,
            "bulk_backfill": 40.0,
        }
        capacity = capacity_map.get(scenario_name, 50.0)

        controllers = ["unmanaged", "cake_static", "experience_budget"]
        rows: dict[str, list[dict]] = {c: [] for c in controllers}

        for trial in range(1, exp.trials + 1):
            for ctrl in controllers:
                row = run_profile(
                    CONTROLLERS[ctrl],
                    seed=exp.seed + trial - 1,
                    duration_s=10.0,
                    trial=trial,
                    devices=4,
                    capacity_mbps=capacity,
                )
                rows[ctrl].append(row)
            exp.progress = int(trial / exp.trials * 100)
            store.update(exp)

        # Aggregate metrics across trials.
        call_rtt = {
            c: _average(
                [float(r.get("call_p95_rtt_ms", 0.0)) for r in rows[c] if r.get("call_p95_rtt_ms") is not None]
            )
            for c in controllers
        }
        bulk = {
            c: _average(
                [float(r.get("bulk_throughput_mbps", 0.0)) for r in rows[c] if r.get("bulk_throughput_mbps") is not None]
            )
            for c in controllers
        }
        qoe = {
            c: _average(
                [float(r.get("worst_device_qoe", 0.0)) for r in rows[c] if r.get("worst_device_qoe") is not None]
            )
            for c in controllers
        }

        call_rtt_values = [call_rtt[c] for c in controllers]
        bulk_values = [bulk[c] for c in controllers]
        qoe_values = [qoe[c] for c in controllers]

        exp.results = [
            ExperimentResult(
                metric="Call p95 RTT",
                unit="ms",
                unmanaged=call_rtt["unmanaged"],
                cake=call_rtt["cake_static"],
                eb=call_rtt["experience_budget"],
                ci=_ci(call_rtt_values),
            ),
            ExperimentResult(
                metric="Bulk throughput",
                unit="Mbps",
                unmanaged=bulk["unmanaged"],
                cake=bulk["cake_static"],
                eb=bulk["experience_budget"],
                ci=_ci(bulk_values),
            ),
            ExperimentResult(
                metric="Worst-device QoE",
                unit="proxy",
                unmanaged=qoe["unmanaged"] if qoe["unmanaged"] is not None else 0.0,
                cake=qoe["cake_static"] if qoe["cake_static"] is not None else 0.0,
                eb=qoe["experience_budget"] if qoe["experience_budget"] is not None else 0.0,
                ci=_ci(qoe_values),
            ),
        ]

        exp.status = "completed"
        exp.progress = 100
    except Exception as error:  # noqa: BLE001
        exp.status = "failed"
        exp.error = str(error)
    finally:
        store.update(exp)
