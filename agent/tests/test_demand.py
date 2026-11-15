"""Unit tests for eb_agent.demand (no root required)."""

from __future__ import annotations

from eb_agent.demand import DemandForecaster, negotiate


def test_forecaster_ewma() -> None:
    f = DemandForecaster(alpha=0.5)
    f.observe("call", 1.0)
    f.observe("call", 3.0)
    fc = f.forecast("call")
    assert fc.predicted_mbps > 0
    assert fc.confidence >= 0
    assert fc.samples == 2


def test_negotiate_full_demand() -> None:
    props = negotiate({"d1": 10.0, "d2": 20.0}, capacity_mbps=100.0)
    assert len(props) == 2
    for p in props:
        assert p.offered_mbps == p.requested_mbps


def test_negotiate_pro_rata() -> None:
    props = negotiate({"d1": 100.0, "d2": 100.0}, capacity_mbps=100.0)
    total_offered = sum(p.offered_mbps for p in props)
    assert abs(total_offered - 95.0) < 0.01  # 100 * 0.95 headroom
    for p in props:
        assert p.reason == "pro_rata_scaled"
