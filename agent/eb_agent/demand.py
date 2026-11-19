"""Bandwidth demand forecasting for negotiation and self-healing.

Predicts upcoming per-class demand from recent telemetry so the policy
engine can preemptively reserve headroom before congestion.
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass
class DemandForecast:
    traffic_class: str
    horizon_s: float
    predicted_mbps: float
    confidence: float
    samples: int


class DemandForecaster:
    """Simple EWMA-based demand forecaster per traffic class."""

    def __init__(self, alpha: float = 0.3, history: int = 20) -> None:
        self.alpha = alpha
        self._ewma: dict[str, float] = {}
        self._counts: dict[str, int] = {}
        self._history: dict[str, deque[float]] = {}
        self._history_len = max(2, history)

    def observe(self, traffic_class: str, rate_mbps: float) -> None:
        prev = self._ewma.get(traffic_class, rate_mbps)
        self._ewma[traffic_class] = (
            self.alpha * rate_mbps + (1.0 - self.alpha) * prev
        )
        self._counts[traffic_class] = self._counts.get(traffic_class, 0) + 1
        history = self._history.setdefault(
            traffic_class, deque(maxlen=self._history_len)
        )
        history.append(max(0.0, float(rate_mbps)))

    def forecast(self, traffic_class: str, horizon_s: float = 30.0) -> DemandForecast:
        ewma = self._ewma.get(traffic_class, 0.0)
        count = self._counts.get(traffic_class, 0)
        recent = list(self._history.get(traffic_class, ()))
        if count == 0 or not recent:
            confidence = 0.0
        elif all(value <= 1e-9 for value in recent):
            # No demand is a valid observation, but it is not evidence that a
            # future schedule will be useful.  Keep this low so schedules do
            # not claim confidence they have not earned.
            confidence = min(0.25, 0.05 + count * 0.01)
        else:
            mean = statistics.fmean(recent)
            std = statistics.pstdev(recent)
            cv = std / mean if mean > 0 else 1.0
            coverage = min(1.0, count / 12.0)
            stability = 1.0 / (1.0 + cv)
            confidence = min(0.9, coverage * stability * 0.9)
        return DemandForecast(
            traffic_class=traffic_class,
            horizon_s=horizon_s,
            predicted_mbps=max(0.0, ewma),
            confidence=round(float(confidence), 3),
            samples=count,
        )

    def forecast_all(self, horizon_s: float = 30.0) -> dict[str, DemandForecast]:
        return {
            tc: self.forecast(tc, horizon_s)
            for tc in self._ewma
        }


@dataclass
class NegotiationProposal:
    device_id: str
    traffic_class: str
    requested_mbps: float
    offered_mbps: float
    reason: str


def negotiate(
    demands: dict[str, float],
    capacity_mbps: float,
    headroom: float = 0.05,
) -> list[NegotiationProposal]:
    """Pro-rata negotiation: scale demands to fit usable capacity."""

    usable = capacity_mbps * (1.0 - headroom)
    total = sum(demands.values())
    proposals: list[NegotiationProposal] = []

    if total <= usable:
        for device_id, rate in demands.items():
            proposals.append(
                NegotiationProposal(
                    device_id=device_id,
                    traffic_class="bulk",
                    requested_mbps=rate,
                    offered_mbps=rate,
                    reason="full_demand_met",
                )
            )
    else:
        scale = usable / total
        for device_id, rate in demands.items():
            offered = rate * scale
            proposals.append(
                NegotiationProposal(
                    device_id=device_id,
                    traffic_class="bulk",
                    requested_mbps=rate,
                    offered_mbps=round(offered, 3),
                    reason="pro_rata_scaled",
                )
            )

    return proposals
