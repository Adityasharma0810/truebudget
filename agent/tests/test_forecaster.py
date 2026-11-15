"""Unit tests for eb_agent.forecaster (no root required)."""

from __future__ import annotations

import math

from eb_agent.forecaster import ForecasterGate, HoltWintersForecaster


def test_forecaster_insufficient_history() -> None:
    fw = HoltWintersForecaster(seasonal_periods=4)
    assert fw.fit([1.0, 2.0, 3.0]) is False


def test_forecaster_fit_and_predict() -> None:
    fw = HoltWintersForecaster(seasonal_periods=4)
    base = 100.0
    history = [
        base + 10 * math.sin(2 * math.pi * i / 24) + (i % 5)
        for i in range(48)
    ]
    assert fw.fit(history) is True
    fc = fw.predict(steps=6)
    assert fc is not None
    assert len(fc.capacity_mbps) == 6
    assert fc.method == "holt_winters"


def test_gate_low_confidence() -> None:
    gate = ForecasterGate(confidence_threshold=0.8)
    from eb_agent.forecaster import Forecast
    fc = Forecast(
        timesteps=[1.0, 2.0],
        capacity_mbps=[90.0, 85.0],
        lo_mbps=[80.0, 75.0],
        hi_mbps=[100.0, 95.0],
        confidence=0.5,
    )
    result = gate.evaluate(fc, 0.05)
    assert result.use_forecast is False
    assert gate.in_fallback is True


def test_gate_high_error() -> None:
    gate = ForecasterGate(confidence_threshold=0.5, error_tolerance=0.1)
    from eb_agent.forecaster import Forecast
    fc = Forecast(
        timesteps=[1.0],
        capacity_mbps=[90.0],
        lo_mbps=[80.0],
        hi_mbps=[100.0],
        confidence=0.9,
    )
    result = gate.evaluate(fc, 0.2)
    assert result.use_forecast is False
    assert gate.in_fallback is True


def test_gate_forecast_active() -> None:
    gate = ForecasterGate(confidence_threshold=0.5, error_tolerance=0.15)
    from eb_agent.forecaster import Forecast
    fc = Forecast(
        timesteps=[1.0],
        capacity_mbps=[90.0],
        lo_mbps=[80.0],
        hi_mbps=[100.0],
        confidence=0.9,
    )
    result = gate.evaluate(fc, 0.05)
    assert result.use_forecast is True
    assert gate.in_fallback is False
