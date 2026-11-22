"""End-to-end integration test: full closed-loop pipeline.

Store → classifier → estimator → forecaster → policy → verifier →
decision log.  No root required (uses FakeTcRunner-equivalent fakes).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from eb_agent.classifier import FlowClassifier
from tests.test_classifier import _real_rows
from eb_agent.decision_log import DecisionLog, GENESIS_HASH
from eb_agent.estimator import KalmanEstimator, CUSUMDetector, step_change_detected
from eb_agent.forecaster import HoltWintersForecaster, ForecasterGate
from eb_agent.policy import FlowDemand, max_min_allocate, PolicyState
from eb_agent.qoe_server import call_qoe
from eb_agent.store import Store
from eb_agent.verifier import Verifier


@pytest.fixture
def tmp_store():
    with tempfile.TemporaryDirectory() as td:
        yield Store(Path(td) / "eb.db")


def test_full_closed_loop(tmp_store: Store) -> None:
    """Exercise the complete pipeline end to end."""

    # 1. Store: persist a capacity measurement
    tmp_store.execute(
        "INSERT INTO capacity_history(ts, capacity_mbps, variance, source) "
        "VALUES (?, ?, ?, ?)",
        (1000.0, 100.0, 4.0, "kalman"),
    )
    tmp_store.commit()

    # 2. Classifier: train on real-shaped labelled rows, then classify a flow
    clf = FlowClassifier()
    gate = clf.train([])
    assert gate["trained"] is False  # refuses without real labelled rows
    clf.train(_real_rows())
    result = clf.predict({
        "dst_port": 443,
        "protocol": "TCP",
        "direction_ratio": 0.2,
        "bytes_per_s": 5_000_000,
        "duration_s": 300,
    })
    assert result.predicted_class in ("call", "game", "stream", "bulk", "unknown")

    # 3. Estimator: Kalman filter + CUSUM
    est = KalmanEstimator(initial_capacity_mbps=100.0)
    cusum = CUSUMDetector(threshold=4.0, drift=0.1)
    estimate, cusum_result = step_change_detected(est, 90.0, cusum)
    assert estimate.capacity_mbps > 0

    # 4. Forecaster: Holt-Winters
    fw = HoltWintersForecaster(seasonal_periods=4)
    history = [100.0 + 5 * (i % 4) for i in range(16)]
    assert fw.fit(history) is True
    fc = fw.predict(steps=4)
    assert fc is not None

    # 5. Forecaster gate
    gate = ForecasterGate(confidence_threshold=0.5, error_tolerance=0.15)
    gate_result = gate.evaluate(fc, recent_error=0.05)
    assert gate_result.use_forecast is True

    # 6. Policy: max-min allocation
    flows = [
        FlowDemand(flow_id="f1", device_id="d1", traffic_class="call", min_rate_mbps=1.0, max_rate_mbps=10.0),
        FlowDemand(flow_id="f2", device_id="d2", traffic_class="bulk", min_rate_mbps=1.0, max_rate_mbps=50.0),
    ]
    state = PolicyState()
    policy = max_min_allocate(flows, capacity_mbps=estimate.capacity_mbps, state=state)
    assert policy.infeasible is False
    assert len(policy.allocations) == 2

    # 7. Verifier: verify the change improved QoE
    verifier = Verifier(noise_margin=0.01)
    qoe_before = call_qoe(rtt_ms=100, jitter_ms=30, loss_pct=2.0)
    qoe_after = call_qoe(rtt_ms=20, jitter_ms=5, loss_pct=0.1)
    assert qoe_after > qoe_before + 0.01  # sanity: improvement exists
    verification = verifier.verify(qoe_before, qoe_after)
    assert verification.verdict == "kept"

    # 8. Decision log: append the decision
    log = DecisionLog(tmp_store)
    decision = log.append(
        trigger="policy_change",
        reason=f"Allocated {policy.cake_bandwidth_mbit} Mbps usable capacity",
        before_qoe={"d1": qoe_before, "d2": 0.5},
        after_qoe={"d1": qoe_after, "d2": 0.9},
        evidence={"policy_rationale": policy.rationale},
        verdict=verification.verdict,
    )
    assert decision.prev_hash == GENESIS_HASH

    # Verify chain integrity
    ok, bad = log.verify_chain()
    assert ok is True
    assert bad is None


def test_rollback_pipeline(tmp_store: Store) -> None:
    """When verification fails, decision log records rollback."""

    log = DecisionLog(tmp_store)
    decision = log.append(
        trigger="policy_change",
        reason="QoE did not improve",
        before_qoe={"d1": 0.8},
        after_qoe={"d1": 0.4},
        evidence={"reason": "no_improvement"},
        verdict="rolled_back",
    )
    assert decision.verdict == "rolled_back"

    ok, bad = log.verify_chain()
    assert ok is True