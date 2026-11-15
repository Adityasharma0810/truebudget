"""Capacity forecaster: Holt-Winters + confidence gate.

Forecast influences policy only if confidence >= threshold and recent error
within tolerance; else reactive_fallback mode (UI badge).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

try:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
except ImportError:  # pragma: no cover
    ExponentialSmoothing = None  # type: ignore


@dataclass
class Forecast:
    timesteps: list[float]
    capacity_mbps: list[float]
    lo_mbps: list[float]
    hi_mbps: list[float]
    confidence: float
    next_dip_start_s: float | None = None
    next_dip_depth_mbps: float | None = None
    next_dip_duration_s: float | None = None
    method: str = "holt_winters"


@dataclass
class ForecastGateResult:
    use_forecast: bool
    forecast: Forecast | None
    reason: str


class HoltWintersForecaster:
    """Holt-Winters triple exponential smoothing with daily seasonality."""

    def __init__(self, seasonal_periods: int = 24) -> None:
        self.seasonal_periods = seasonal_periods
        self._model = None
        self._fitted = False

    def fit(self, history: Sequence[float]) -> bool:
        """Fit on capacity history (one value per time step)."""

        if ExponentialSmoothing is None:
            return False
        if len(history) < self.seasonal_periods * 2:
            return False
        try:
            self._model = ExponentialSmoothing(
                np.asarray(history, dtype=float),
                seasonal_periods=self.seasonal_periods,
                trend="add",
                seasonal="add",
            ).fit()
            self._fitted = True
            return True
        except Exception:
            self._fitted = False
            return False

    def predict(self, steps: int = 12) -> Forecast | None:
        """Predict `steps` ahead."""

        if not self._fitted or self._model is None:
            return None
        try:
            forecast_values = self._model.forecast(steps)
            sigma = float(np.std(forecast_values)) if len(forecast_values) > 1 else 5.0
            lo = forecast_values - 1.96 * sigma
            hi = forecast_values + 1.96 * sigma
            timesteps = list(range(1, steps + 1))
            return Forecast(
                timesteps=[float(t) for t in timesteps],
                capacity_mbps=[float(v) for v in forecast_values],
                lo_mbps=[float(max(0.0, v)) for v in lo],
                hi_mbps=[float(v) for v in hi],
                confidence=0.7,
                method="holt_winters",
            )
        except Exception:
            return None


class ForecasterGate:
    """Confidence gate: forecast used only if confidence and recent error OK."""

    def __init__(
        self,
        confidence_threshold: float = 0.7,
        error_tolerance: float = 0.15,
        good_windows_to_reenter: int = 3,
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.error_tolerance = error_tolerance
        self.good_windows_to_reenter = good_windows_to_reenter
        self._consecutive_good = 0
        self._in_fallback = False
        self._last_error = 0.0

    def evaluate(
        self,
        forecast: Forecast | None,
        recent_error: float,
    ) -> ForecastGateResult:
        """Decide whether to use forecast or reactive fallback."""

        self._last_error = recent_error

        if forecast is None or forecast.confidence < self.confidence_threshold:
            self._in_fallback = True
            self._consecutive_good = 0
            return ForecastGateResult(
                use_forecast=False,
                forecast=None,
                reason="forecast_unavailable_or_low_confidence",
            )

        if recent_error > self.error_tolerance:
            self._in_fallback = True
            self._consecutive_good = 0
            return ForecastGateResult(
                use_forecast=False,
                forecast=None,
                reason="recent_error_exceeds_tolerance",
            )

        self._consecutive_good += 1
        if self._in_fallback and self._consecutive_good < self.good_windows_to_reenter:
            return ForecastGateResult(
                use_forecast=False,
                forecast=forecast,
                reason="reentering_adaptive_after_fallback",
            )

        self._in_fallback = False
        return ForecastGateResult(
            use_forecast=True,
            forecast=forecast,
            reason="forecast_active",
        )

    @property
    def in_fallback(self) -> bool:
        return self._in_fallback
