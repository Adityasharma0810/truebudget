"""Capacity estimator: hand-written Kalman filter + CUSUM step-change detector.

State = capacity (+trend).  Observations = noisy usable throughput + RTT-derived
signals.  Output: estimate + variance → uncertainty band (1.96σ).
CUSUM/innovation detector on fast loop → immediate reactive shaping.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Estimate:
    capacity_mbps: float
    variance: float
    lo_mbps: float
    hi_mbps: float
    trend_mbps_per_s: float = 0.0


@dataclass
class CUSUMResult:
    triggered: bool
    statistic: float
    threshold: float


class KalmanEstimator:
    """Hand-written 1D Kalman filter for link capacity estimation.

    State: [capacity, trend].  Observation: usable throughput sample.
    Process noise models capacity drift; measurement noise models probe error.
    """

    def __init__(
        self,
        initial_capacity_mbps: float = 100.0,
        initial_variance: float = 400.0,
        process_noise: float = 1.0,
        measurement_noise: float = 25.0,
    ) -> None:
        self.capacity = initial_capacity_mbps
        self.trend = 0.0
        # Covariance matrix [[var_cap, cov], [cov, var_trend]]
        self.P = [[initial_variance, 0.0], [0.0, initial_variance / 100.0]]
        self.Q = [[process_noise, 0.0], [0.0, process_noise / 100.0]]
        self.R = measurement_noise

    def predict(self, dt: float = 1.0) -> None:
        """Predict state forward by dt seconds."""

        self.capacity += self.trend * dt
        # Covariance prediction: P = F * P * F' + Q
        F = [[1.0, dt], [0.0, 1.0]]
        P00, P01, P11 = self.P[0][0], self.P[0][1], self.P[1][1]
        self.P[0][0] = max(
            0.0,
            F[0][0] * (F[0][0] * P00 + F[0][1] * P11)
            + F[0][1] * (F[0][0] * P01 + F[0][1] * P11)
            + self.Q[0][0],
        )
        self.P[0][1] = (
            F[1][0] * (F[0][0] * P00 + F[0][1] * P11)
            + F[1][1] * (F[0][0] * P01 + F[0][1] * P11)
        )
        self.P[1][1] = max(0.0, P11 + self.Q[1][1])

    def update(self, measurement_mbps: float) -> Estimate:
        """Update with a new throughput measurement."""

        # Observation: H = [1, 0] (observe capacity directly)
        S = self.P[0][0] + self.R  # innovation variance
        K0 = self.P[0][0] / S  # Kalman gain for capacity
        K1 = self.P[1][0] / S  # Kalman gain for trend

        innovation = measurement_mbps - self.capacity
        self.capacity += K0 * innovation
        self.trend += K1 * innovation

        # Covariance update: P = (I - K*H) * P
        P00, P01, P11 = self.P[0][0], self.P[0][1], self.P[1][1]
        self.P[0][0] = max(0.0, (1.0 - K0) * P00 - K0 * P01)
        self.P[0][1] = (1.0 - K0) * P01 - K0 * P11
        self.P[1][0] = self.P[0][1]
        self.P[1][1] = max(0.0, P11 - K1 * P01)

        variance = max(0.0, self.P[0][0])
        sigma = variance ** 0.5
        return Estimate(
            capacity_mbps=max(1.0, self.capacity),
            variance=variance,
            lo_mbps=max(1.0, self.capacity - 1.96 * sigma),
            hi_mbps=self.capacity + 1.96 * sigma,
            trend_mbps_per_s=self.trend,
        )

    def estimate(self) -> Estimate:
        """Return current estimate without a new measurement."""

        variance = max(0.0, self.P[0][0])
        sigma = variance ** 0.5
        return Estimate(
            capacity_mbps=max(1.0, self.capacity),
            variance=variance,
            lo_mbps=max(1.0, self.capacity - 1.96 * sigma),
            hi_mbps=self.capacity + 1.96 * sigma,
            trend_mbps_per_s=self.trend,
        )


class CUSUMDetector:
    """CUSUM step-change detector on the innovation sequence.

    Triggers when the cumulative sum of normalized innovations exceeds a
    threshold, indicating a step change in capacity.
    """

    def __init__(self, threshold: float = 5.0, drift: float = 0.5) -> None:
        self.threshold = threshold
        self.drift = drift
        self._plus = 0.0
        self._minus = 0.0

    def update(self, innovation: float, sigma: float) -> CUSUMResult:
        """Feed a new innovation (measurement - prediction)."""

        if sigma <= 0:
            sigma = 1.0
        s = innovation / sigma  # normalized innovation
        self._plus = max(0.0, self._plus + s - self.drift)
        self._minus = max(0.0, self._minus - s - self.drift)
        triggered = self._plus >= self.threshold or self._minus >= self.threshold
        return CUSUMResult(
            triggered=triggered,
            statistic=max(self._plus, self._minus),
            threshold=self.threshold,
        )

    def reset(self) -> None:
        self._plus = 0.0
        self._minus = 0.0


def step_change_detected(
    estimator: KalmanEstimator,
    measurement_mbps: float,
    cusum: CUSUMDetector,
    dt: float = 1.0,
) -> tuple[Estimate, CUSUMResult]:
    """Predict → update → CUSUM check.  Returns estimate + detection result."""

    estimator.predict(dt)
    estimate = estimator.update(measurement_mbps)
    result = cusum.update(measurement_mbps - estimate.capacity_mbps, estimate.variance ** 0.5)
    return estimate, result
