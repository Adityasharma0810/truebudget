"""Bandwidth demand forecasting for negotiation and self-healing.

Predicts upcoming per-class demand from recent telemetry so the policy
engine can preemptively reserve headroom before congestion.
"""

from __future__ import annotations

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

    def __init__(self, alpha: float = 0.3) -> None:
        self.alpha = alpha
        self._ewma: dict[str, float] = {}
        self._counts: dict[str, int] = {}

    def observe(self, traffic_class: str, rate_mbps: float) -> None:
        prev = self._ewma.get(traffic_class, rate_mbps)
        self._ewma[traffic_class] = (
            self.alpha * rate_mbps + (1.0 - self.alpha) * prev
        )
        self._counts[traffic_class] = self._counts.get(traffic_class, 0) + 1

    def forecast(self, traffic_class: str, horizon_s: float = 30.0) -> DemandForecast:
        ewma = self._ewma.get(traffic_class, 0.0)
        count = self._counts.get(traffic_class, 0)
        confidence = min(1.0, count / 20.0)  # full confidence after 20 samples
        return DemandForecast(
            traffic_class=traffic_class,
            horizon_s=horizon_s,
            predicted_mbps=max(0.0, ewma),
            confidence=confidence,
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
