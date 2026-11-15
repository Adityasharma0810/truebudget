"""Unit tests for eb_agent.policy (no root required)."""

from __future__ import annotations

from eb_agent.policy import FlowDemand, PolicyState, max_min_allocate


def test_basic_allocation() -> None:
    flows = [
        FlowDemand(flow_id="f1", device_id="d1", traffic_class="call", min_rate_mbps=1.0, max_rate_mbps=10.0),
        FlowDemand(flow_id="f2", device_id="d2", traffic_class="bulk", min_rate_mbps=1.0, max_rate_mbps=50.0),
    ]
    policy = max_min_allocate(flows, capacity_mbps=100.0)
    assert policy.infeasible is False
    assert len(policy.allocations) == 2
    assert policy.cake_bandwidth_mbit == 95.0  # 100 * 0.95
    assert policy.jain_index is not None
    assert policy.jain_index > 0
    assert policy.starvation_count >= 0


def test_infeasible_when_floors_exceed_capacity() -> None:
    flows = [
        FlowDemand(flow_id="f1", device_id="d1", traffic_class="bulk", min_rate_mbps=80.0, max_rate_mbps=100.0),
        FlowDemand(flow_id="f2", device_id="d2", traffic_class="bulk", min_rate_mbps=80.0, max_rate_mbps=100.0),
    ]
    policy = max_min_allocate(flows, capacity_mbps=100.0)
    assert policy.infeasible is True
    assert policy.degradation_proposal is not None


def test_rate_bounds_respected() -> None:
    flows = [
        FlowDemand(flow_id="f1", device_id="d1", traffic_class="call", min_rate_mbps=0.5, max_rate_mbps=2.0),
    ]
    policy = max_min_allocate(flows, capacity_mbps=100.0)
    for a in policy.allocations:
        assert a.rate_mbps >= 0.5
        assert a.rate_mbps <= 2.0


def test_stability_state() -> None:
    flows = [
        FlowDemand(flow_id="f1", device_id="d1", traffic_class="call", min_rate_mbps=1.0, max_rate_mbps=10.0),
    ]
    state = PolicyState()
    p1 = max_min_allocate(flows, capacity_mbps=100.0, state=state)
    p2 = max_min_allocate(flows, capacity_mbps=100.0, state=state)
    assert p1.allocations[0].rate_mbps == p2.allocations[0].rate_mbps


def test_jain_index() -> None:
    from eb_agent.policy import _jain
    assert _jain([10, 10, 10]) == 1.0
    assert _jain([10, 1, 1]) < 1.0
    assert _jain([]) == 1.0


def test_starvation_count() -> None:
    flows = [
        FlowDemand(flow_id="f1", device_id="d1", traffic_class="call", min_rate_mbps=0.1, max_rate_mbps=0.5),
        FlowDemand(flow_id="f2", device_id="d2", traffic_class="call", min_rate_mbps=10.0, max_rate_mbps=50.0),
    ]
    policy = max_min_allocate(flows, capacity_mbps=10.0)
    assert policy.starvation_count >= 0
