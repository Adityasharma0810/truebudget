"""Phase 7: Health diagnosis.

Ranked hypotheses with supporting signals and recommendations.  All
diagnoses are emulated/synthetic -- clearly marked so they are never
mistaken for real network diagnosis.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class Diagnosis:
    id: str
    title: str
    confidence: float
    signals: list[str]
    recommendation: str
    status: str = "observation"


class HealthService:
    """Generates emulated hypotheses about network health."""

    def __init__(self) -> None:
        self._hypotheses: list[Diagnosis] = []

    def diagnose(
        self,
        capacity_mbps: float,
        jitter_ms: float,
        loss_pct: float,
        qoe: float | None,
    ) -> list[Diagnosis]:
        hypotheses: list[Diagnosis] = []

        if loss_pct > 1.0:
            hypotheses.append(
                Diagnosis(
                    id=f"hyp-{uuid.uuid4().hex[:8]}",
                    title="Packet loss on the WAN path",
                    confidence=0.78,
                    signals=[
                        f"Loss {loss_pct:.1f}% observed (threshold 1%)",
                        "Likely congestion or link instability",
                    ],
                    recommendation="Reduce shaping rate or check the uplink.",
                    status="observation",
                )
            )

        if jitter_ms > 10.0:
            hypotheses.append(
                Diagnosis(
                    id=f"hyp-{uuid.uuid4().hex[:8]}",
                    title="Elevated jitter",
                    confidence=0.65,
                    signals=[
                        f"Jitter {jitter_ms:.0f} ms (threshold 10 ms)",
                        "May affect calls and games",
                    ],
                    recommendation="Verify CAKE is active; reduce burst.",
                    status="observation",
                )
            )

        if capacity_mbps < 20.0:
            hypotheses.append(
                Diagnosis(
                    id=f"hyp-{uuid.uuid4().hex[:8]}",
                    title="Low available capacity",
                    confidence=0.9,
                    signals=[f"Capacity {capacity_mbps:.0f} Mbps"],
                    recommendation="Prioritize latency-sensitive traffic.",
                    status="observation",
                )
            )

        if qoe is not None and qoe < 0.7:
            hypotheses.append(
                Diagnosis(
                    id=f"hyp-{uuid.uuid4().hex[:8]}",
                    title="Below-target device QoE",
                    confidence=0.72,
                    signals=[f"Household QoE {qoe:.2f}"],
                    recommendation="Review intent floors and traffic classes.",
                    status="observation",
                )
            )

        if not hypotheses:
            hypotheses.append(
                Diagnosis(
                    id=f"hyp-{uuid.uuid4().hex[:8]}",
                    title="No significant issues detected",
                    confidence=0.85,
                    signals=["Capacity, loss, and jitter within normal bounds"],
                    recommendation="Keep current policy.",
                    status="observation",
                )
            )

        self._hypotheses = hypotheses
        return hypotheses

    def list(self) -> list[Diagnosis]:
        return list(self._hypotheses)


_health = HealthService()


def get_health_service() -> HealthService:
    return _health
