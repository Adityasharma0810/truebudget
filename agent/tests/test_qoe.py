from __future__ import annotations

from eb_agent.qoe import (
    bulk_qoe,
    call_qoe,
    fairness,
    game_qoe,
    household_qoe,
    qoe_band,
    stream_qoe,
    weighted_device_qoe,
)


def test_call_qoe_is_monotonic_in_latency_and_loss() -> None:
    good = call_qoe(20, 1, 0)
    bad_latency = call_qoe(220, 1, 0)
    bad_loss = call_qoe(20, 1, 20)
    assert 0 <= good <= 1
    assert good > bad_latency
    assert good > bad_loss
    assert qoe_band(good) == "green"
    assert qoe_band(bad_loss) in {"amber", "red"}


def test_game_qoe_boundaries() -> None:
    assert game_qoe(10, 1, 0) == 1.0
    assert game_qoe(300, 100, 10) == 0.0
    assert 0.0 < game_qoe(80, 20, 1) < 1.0


def test_stream_qoe_components() -> None:
    assert stream_qoe(10, 6, 0, 0) == 1.0
    assert stream_qoe(None, 6, 0, 0) is None
    low = stream_qoe(1, 6, 0.5, 1.0)
    assert low is not None and low < 0.5


def test_bulk_qoe_and_fairness() -> None:
    assert bulk_qoe(10, 10) == 1.0
    assert bulk_qoe(5, 10) == 0.5
    assert bulk_qoe(None, 10) is None
    assert fairness([10, 10, 10]) == 1.0
    assert fairness([10, 1, 1]) < 1.0
    assert fairness([]) == 1.0


def test_device_and_household_aggregation() -> None:
    assert weighted_device_qoe([]) is None
    assert weighted_device_qoe([(0.4, 1), (0.9, 1)]) is not None
    assert household_qoe([0.8, None, 0.6]) == 0.7
    assert household_qoe([]) is None
