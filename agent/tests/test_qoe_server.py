"""Unit tests for eb_agent.qoe_server (no root required)."""

from __future__ import annotations

from eb_agent.qoe_server import (
    bulk_qoe,
    call_qoe,
    fairness,
    game_qoe,
    household_qoe,
    qoe_band,
    stream_qoe,
    weighted_device_qoe,
)


def test_call_qoe() -> None:
    good = call_qoe(20, 1, 0)
    bad = call_qoe(220, 1, 0)
    assert 0 <= good <= 1
    assert good > bad
    assert qoe_band(good) == "green"


def test_game_qoe() -> None:
    assert game_qoe(10, 1, 0) == 1.0
    assert game_qoe(300, 100, 10) == 0.0


def test_stream_qoe() -> None:
    assert stream_qoe(10, 6, 0, 0) == 1.0
    assert stream_qoe(None, 6, 0, 0) is None


def test_bulk_qoe() -> None:
    assert bulk_qoe(10, 10) == 1.0
    assert bulk_qoe(5, 10) == 0.5
    assert bulk_qoe(None, 10) is None


def test_fairness() -> None:
    assert fairness([10, 10, 10]) == 1.0
    assert fairness([10, 1, 1]) < 1.0


def test_device_and_household() -> None:
    assert weighted_device_qoe([]) is None
    assert household_qoe([0.8, None, 0.6]) == 0.7
