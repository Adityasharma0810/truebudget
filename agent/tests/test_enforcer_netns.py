"""Real-kernel enforcer integration tests (run under ``unshare -Urn``).

These tests create throw-away dummy/IFB devices inside a user+network
namespace, then exercise the same code the privileged daemon uses:
render -> apply -> ``tc -j``/``nft list`` read-back -> rollback.
"""

from __future__ import annotations

import subprocess

import pytest

from eb_agent.enforcer.catalog import (
    EnforceOp,
    HtbClass,
    HtbPlan,
    LinkSpec,
    NftEntry,
    NftPlan,
    QdiscSpec,
)
from eb_agent.enforcer.ingress import IngressPlan, ingress_ops
from eb_agent.enforcer.tc import SubprocessRunner, TcController
from eb_agent.lab.topology import require_lab_namespace
from eb_agent.safety import safe_state


def _run(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), text=True, capture_output=True)


def _must(*command: str) -> None:
    result = _run(*command)
    assert result.returncode == 0, f"{' '.join(command)} failed: {result.stderr}"


def _mk_dummy(name: str) -> None:
    _run("ip", "link", "del", name)
    _must("ip", "link", "add", name, "type", "dummy")
    _must("ip", "link", "set", name, "up")


def _mk_ifb(name: str) -> None:
    _run("ip", "link", "del", name)
    _must("ip", "link", "add", name, "type", "ifb")
    _must("ip", "link", "set", name, "up")


def _cleanup(*names: str) -> None:
    for name in names:
        _run("ip", "link", "del", name)
    _run("nft", "delete", "table", "inet", "eb_agent")


@pytest.mark.netns
def test_cake_apply_readback_and_rollback_restores_netem() -> None:
    require_lab_namespace()
    runner = SubprocessRunner()
    controller = TcController(runner)
    _mk_dummy("ebd0")
    try:
        _must("tc", "qdisc", "add", "dev", "ebd0", "root", "netem", "delay", "5ms")
        op = EnforceOp(
            op_id="netns-cake-1",
            qdisc=[
                QdiscSpec(
                    kind="cake",
                    interface="ebd0",
                    bandwidth_mbit=50.0,
                    params={"diffserv4": "", "dual-srchost": ""},
                )
            ],
        )
        prior = controller.capture_state(op)
        assert prior["interfaces"]["ebd0"]["qdiscs"][0]["kind"] == "netem"

        result = controller.apply(op, ["ebd0"], prior_state=prior)
        assert result.ok, result.stderr
        assert result.rolled_back is False
        root = runner.qdisc_show_json("ebd0")
        assert root["kind"] == "cake"
        assert root["options"]["bandwidth"] == pytest.approx(6_250_000)

        restored = controller._rollback(prior)  # noqa: SLF001 - integration check
        assert restored.ok, restored.stderr
        assert runner.qdisc_show_json("ebd0")["kind"] == "netem"
    finally:
        _cleanup("ebd0")


@pytest.mark.netns
def test_mismatch_auto_rolls_back_to_prior_qdisc() -> None:
    require_lab_namespace()
    runner = SubprocessRunner()
    controller = TcController(runner)
    _mk_dummy("ebd1")
    try:
        _must("tc", "qdisc", "add", "dev", "ebd1", "root", "tbf",
              "rate", "10mbit", "burst", "32kbit", "latency", "400ms")
        op = EnforceOp(
            op_id="netns-cake-2",
            qdisc=[QdiscSpec(kind="cake", interface="ebd1", bandwidth_mbit=5.0)],
        )
        prior = controller.capture_state(op)
        # A real prior tbf is captured and restored if the new op is rejected.
        bad = EnforceOp(
            op_id="netns-bad-1",
            qdisc=[QdiscSpec(kind="cake", interface="ebd1", bandwidth_mbit=0.5)],
        )
        rejected = controller.apply(bad, ["ebd1"], prior_state=prior)
        assert rejected.ok is False
        assert rejected.returncode == 2
        assert runner.qdisc_show_json("ebd1")["kind"] == "tbf"
    finally:
        _cleanup("ebd1")


@pytest.mark.netns
def test_htb_fallback_marks_and_mirred_ingress_are_real() -> None:
    require_lab_namespace()
    runner = SubprocessRunner()
    controller = TcController(runner)
    _mk_dummy("ebd2")
    _mk_ifb("ebi2")
    try:
        htb = EnforceOp(
            op_id="netns-htb-1",
            htb=HtbPlan(
                interface="ebd2",
                root_rate_mbit=50.0,
                classes=[
                    HtbClass(classid="1:1", rate_mbit=20.0, ceil_mbit=40.0, mark=1),
                    HtbClass(classid="1:2", rate_mbit=10.0, ceil_mbit=20.0,
                             priority=4, mark=2),
                ],
                default_classid="1:2",
            ),
        )
        prior = controller.capture_state(htb)
        applied = controller.apply(htb, ["ebd2"], prior_state=prior)
        assert applied.ok, applied.stderr
        root = runner.qdisc_show_json("ebd2")
        assert root["kind"] == "htb"
        classids = {entry["handle"] for entry in runner.classes("ebd2")}
        assert {"1:1", "1:2"} <= classids
        filters = runner.filters("ebd2", "1:")
        marks = {
            int((entry.get("options") or {}).get("fw", {}).get("mark", "0"), 0)
            for entry in filters
            if entry.get("options")
        }
        assert {1, 2} <= marks

        setup, mirred, cake = ingress_ops(
            "netns-ing", IngressPlan(
                wan_interface="ebd2", ifb_interface="ebi2", bandwidth_mbit=30.0
            )
        )
        assert controller.apply(setup, ["ebd2", "ebi2"]).ok
        result = controller.apply(mirred, ["ebd2", "ebi2"])
        assert result.ok, result.stderr
        ingress_filters = runner.filters("ebd2", "ingress")
        assert any(
            action.get("to_dev") == "ebi2"
            for entry in ingress_filters
            for action in (entry.get("options") or {}).get("actions") or []
        )
        assert controller.apply(cake, ["ebd2", "ebi2"]).ok
        assert runner.qdisc_show_json("ebi2")["options"]["ingress"] is True
    finally:
        _cleanup("ebd2", "ebi2")


@pytest.mark.netns
def test_nft_device_markup_is_read_back_from_kernel() -> None:
    require_lab_namespace()
    runner = SubprocessRunner()
    controller = TcController(runner)
    _mk_dummy("ebd3")
    try:
        op = EnforceOp(
            op_id="netns-nft-1",
            nft=NftPlan(
                entries=[
                    NftEntry(mac="aa:bb:cc:dd:ee:01", ipv4="10.10.0.2",
                             dscp=46, fwmark=1, traffic_class="call"),
                    NftEntry(ipv6="fd00::2", dscp=8, fwmark=4,
                             traffic_class="bulk"),
                ]
            ),
        )
        prior = controller.capture_state(op)
        applied = controller.apply(op, ["ebd3"], prior_state=prior)
        assert applied.ok, applied.stderr
        ruleset = runner.nft_table("eb_agent")
        assert "10.10.0.2" in ruleset
        assert "fd00::2" in ruleset
        assert "dscp set ef" in ruleset
        assert "mark set 0x00000001" in ruleset
        assert "mark set 0x00000004" in ruleset
    finally:
        _cleanup("ebd3")


@pytest.mark.netns
def test_safe_state_round_trip_on_real_kernel() -> None:
    require_lab_namespace()
    runner = SubprocessRunner()
    controller = TcController(runner)
    _mk_dummy("ebd4")
    try:
        _must("tc", "qdisc", "add", "dev", "ebd4", "root", "netem", "delay", "2ms")
        captured = safe_state.snapshot(runner, ["ebd4"], ["ebd4"])
        assert captured.qdiscs["ebd4"][0].kind == "netem"

        safe = safe_state.default_safe_state(["ebd4"])
        result = safe_state.apply(safe, controller, ["ebd4"])
        assert result is not None and result.ok, getattr(result, "stderr", "")
        assert runner.qdisc_show_json("ebd4")["kind"] == "cake"
    finally:
        _cleanup("ebd4")
