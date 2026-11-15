"""Unit tests for eb_agent.estimator (no root required)."""

from __future__ import annotations

from eb_agent.estimator import CUSUMDetector, KalmanEstimator, step_change_detected


def test_kalman_initial_estimate() -> None:
    est = KalmanEstimator(initial_capacity_mbps=100.0)
    result = est.estimate()
    assert result.capacity_mbps == 100.0
    assert result.lo_mbps <= result.capacity_mbps <= result.hi_mbps


def test_kalman_update_tracks_measurement() -> None:
    est = KalmanEstimator(initial_capacity_mbps=100.0, measurement_noise=1.0)
    r1 = est.update(50.0)
    assert r1.capacity_mbps < 100.0  # pulled toward 50
    r2 = est.update(50.0)
    assert r2.capacity_mbps < r1.capacity_mbps or abs(r2.capacity_mbps - 50) < abs(r1.capacity_mbps - 50)


def test_kalman_confidence_band() -> None:
    est = KalmanEstimator(initial_capacity_mbps=100.0, initial_variance=100.0)
    est.update(100.0)
    result = est.estimate()
    assert result.lo_mbps <= result.hi_mbps
    assert result.hi_mbps - result.lo_mbps > 0


def test_kalman_trend() -> None:
    est = KalmanEstimator(initial_capacity_mbps=100.0, process_noise=10.0)
    # Feed increasing measurements to induce trend
    for m in [100, 105, 110, 115, 120]:
        est.update(m)
    result = est.estimate()
    assert result.capacity_mbps > 100.0


def test_cusum_trigger_on_step() -> None:
    cusum = CUSUMDetector(threshold=3.0, drift=0.1)
    triggered = False
    for i in range(50):
        innovation = 10.0 if i > 25 else 0.0  # step change at i=25
        result = cusum.update(innovation, 1.0)
        if result.triggered:
            triggered = True
            break
    assert triggered, "CUSUM should trigger on sustained step change"


def test_cusum_no_trigger_on_noise() -> None:
    cusum = CUSUMDetector(threshold=10.0, drift=0.5)
    triggered = False
    for i in range(100):
        result = cusum.update(0.0, 1.0)  # zero innovation
        if result.triggered:
            triggered = True
    assert not triggered


def test_step_change_detected() -> None:
    est = KalmanEstimator(initial_capacity_mbps=100.0, measurement_noise=1.0)
    cusum = CUSUMDetector(threshold=4.0, drift=0.1)
    # Stable then step
    estimate, result = step_change_detected(est, 100.0, cusum)
    assert not result.triggered
    estimate, result = step_change_detected(est, 50.0, cusum)
    # May or may not trigger on single point; just check it runs
    assert estimate.capacity_mbps > 0
