"""Unit tests for eb_agent.multilink (no root required)."""

from __future__ import annotations

from eb_agent.multilink import Link, MultilinkSteerer


def test_steer_to_healthy_link() -> None:
    links = [
        Link(link_id="l1", name="WAN1", capacity_mbps=50.0),
        Link(link_id="l2", name="WAN2", capacity_mbps=50.0),
    ]
    steerer = MultilinkSteerer(links)
    result = steerer.steer("flow-1", "src=1.2.3.4:1234,dst=5.6.7.8:443,proto=TCP")
    assert result is not None
    assert result.link_id in ("l1", "l2")


def test_flow_stickiness() -> None:
    links = [Link(link_id="l1", name="WAN1", capacity_mbps=50.0)]
    steerer = MultilinkSteerer(links)
    r1 = steerer.steer("flow-1", "tuple-a")
    r2 = steerer.steer("flow-1", "tuple-b")
    assert r1.link_id == r2.link_id == "l1"


def test_all_links_down() -> None:
    links = [Link(link_id="l1", name="WAN1", capacity_mbps=50.0, state="down")]
    steerer = MultilinkSteerer(links)
    assert steerer.steer("flow-1", "tuple-a") is None


def test_link_degraded_unpins() -> None:
    links = [Link(link_id="l1", name="WAN1", capacity_mbps=50.0)]
    steerer = MultilinkSteerer(links)
    steerer.steer("flow-1", "tuple-a")
    steerer.update_link("l1", state="down")
    # After link down, flow unpinned
    assert "flow-1" not in steerer._flow_link


def test_link_report() -> None:
    links = [Link(link_id="l1", name="WAN1", capacity_mbps=100.0, rtt_ms=10.0, loss_pct=0.5)]
    steerer = MultilinkSteerer(links)
    report = steerer.link_report()
    assert len(report) == 1
    assert report[0]["health_score"] > 0
