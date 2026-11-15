from __future__ import annotations

import pytest

from eb_agent.lab.harness import UnmanagedController, run_profile
from eb_agent.lab.topology import Topology, require_lab_namespace


@pytest.mark.netns
def test_topology_connects_devices_and_applies_cake() -> None:
    require_lab_namespace()
    with Topology(devices=2) as topology:
        ok, rtt = topology.ping(0, count=2)
        assert ok, "device cannot reach the WAN endpoint"
        assert rtt >= 0
        backend = topology.apply_cake(topology.wan_capacity_mbps * 0.95)
        assert backend in {"cake", "tbf"}
        assert "cake" in topology.qdisc() or "tbf" in topology.qdisc()
        stats = topology.stats()
        assert stats["rx_packets"] >= 0
        topology.reset_link()
        assert "netem" in topology.qdisc()


@pytest.mark.netns
def test_harness_produces_emulated_row() -> None:
    require_lab_namespace()
    row = run_profile(
        UnmanagedController(),
        seed=9,
        duration_s=3.0,
        devices=4,
        sink_port=9123,
    )
    assert row["emulated"] is True
    assert row["controller"] == "unmanaged"
    assert row["call_p95_rtt_ms"] >= 0
    assert row["bulk_throughput_mbps"] > 0
    assert row["worst_device_qoe"] is not None
    assert "netem" in str(row["qdisc"])
