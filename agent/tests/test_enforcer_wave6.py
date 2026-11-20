"""Unit tests for Phase 2 wave 6 enforcer modules (no root required)."""

from __future__ import annotations

from eb_agent.enforcer.catalog import BoundsError, QdiscSpec
from eb_agent.enforcer.doctor import DoctorReport, doctor_report, doctor_summary
from eb_agent.enforcer.egress import (
    DEFAULT_SHAPING_FACTOR,
    EgressError,
    EgressPlan,
    cake_egress_op,
)
from eb_agent.enforcer.fallback import (
    FallbackError,
    HtbClass,
    HtbFallbackPlan,
    htb_fallback_op,
)
from eb_agent.enforcer.ingress import (
    DEFAULT_IFB,
    IngressError,
    IngressPlan,
    cake_ingress_op,
    ifb_setup_ops,
    mirred_filter_op,
)
from eb_agent.enforcer.modes import (
    ALL_MODES,
    MODE_BRIDGE,
    MODE_GATEWAY,
    MODE_LAB,
    MODE_MONITOR,
    TopologyMode,
    get_mode,
    is_read_only,
    is_shaping_mode,
    MODE_REGISTRY,
)
from eb_agent.enforcer.nft_policy import (
    ClassMark,
    DeviceEntry,
    NftPolicyError,
    NftPolicyPlan,
    nft_policy_op,
)


# ---- modes ----

def test_modes_registry() -> None:
    assert MODE_GATEWAY in ALL_MODES
    assert MODE_BRIDGE in ALL_MODES
    assert MODE_LAB in ALL_MODES
    assert MODE_MONITOR in ALL_MODES
    assert len(ALL_MODES) == 4


def test_mode_properties() -> None:
    gateway = get_mode("gateway")
    assert gateway.shapes is True
    assert gateway.forwards is True
    assert gateway.read_only is False

    monitor = get_mode("monitor")
    assert monitor.shapes is False
    assert monitor.read_only is True

    lab = get_mode("lab")
    assert lab.shapes is True
    assert lab.read_only is False


def test_get_mode_unknown() -> None:
    try:
        get_mode("bogus")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_is_shaping_mode() -> None:
    assert is_shaping_mode("gateway") is True
    assert is_shaping_mode("monitor") is False


def test_is_read_only() -> None:
    assert is_read_only("monitor") is True
    assert is_read_only("gateway") is False


# ---- egress ----

def test_egress_plan_defaults() -> None:
    plan = EgressPlan(interface="eth0", capacity_mbps=100.0)
    assert plan.shaping_factor == DEFAULT_SHAPING_FACTOR
    assert plan.bandwidth_mbit == 95.0


def test_egress_plan_bounds() -> None:
    try:
        EgressPlan(interface="eth0", capacity_mbps=100.0, shaping_factor=1.5)
    except EgressError:
        pass
    else:
        raise AssertionError("expected EgressError")

    try:
        EgressPlan(interface="eth0", capacity_mbps=0.5)
    except EgressError:
        pass
    else:
        raise AssertionError("expected EgressError")


def test_cake_egress_op() -> None:
    op = cake_egress_op("op-1", EgressPlan(interface="eth0", capacity_mbps=100.0))
    assert op.op_id == "op-1"
    assert len(op.qdisc) == 1
    spec = op.qdisc[0]
    assert spec.kind == "cake"
    assert spec.interface == "eth0"
    assert spec.bandwidth_mbit == 95.0
    assert "diffserv4" in spec.params
    assert "dual-srchost" in spec.params


def test_cake_egress_op_ack_filter() -> None:
    op = cake_egress_op(
        "op-1", EgressPlan(interface="eth0", capacity_mbps=100.0, ack_filter=True)
    )
    assert "ack-filter" in op.qdisc[0].params


# ---- ingress ----

def test_ifb_setup_ops() -> None:
    ops = ifb_setup_ops("prefix", "ifb0")
    assert len(ops) == 1
    assert ops[0].op_id == "prefix-ifb-up"
    assert ops[0].links[0].interface == "ifb0"
    assert ops[0].links[0].kind == "ifb"


def test_mirred_filter_op() -> None:
    op = mirred_filter_op("op-1", "eth0")
    assert op.op_id == "op-1"
    assert op.qdisc[0].kind == "clsact"
    assert op.filters[0].target == "ifb0"
    assert op.filters[0].parent == "ingress"


def test_cake_ingress_op() -> None:
    op = cake_ingress_op("op-1", IngressPlan(wan_interface="eth0", bandwidth_mbit=50.0))
    assert op.qdisc[0].kind == "cake"
    assert op.qdisc[0].interface == "ifb0"
    assert op.qdisc[0].bandwidth_mbit == 50.0


def test_ingress_bounds() -> None:
    try:
        IngressPlan(wan_interface="eth0", bandwidth_mbit=0.5)
    except IngressError:
        pass
    else:
        raise AssertionError("expected IngressError")


# ---- fallback ----

def test_htb_fallback_op() -> None:
    plan = HtbFallbackPlan(
        interface="eth0",
        root_rate_mbit=100.0,
        classes=[
            HtbClass(classid="1:1", rate_mbit=50.0),
            HtbClass(classid="1:2", rate_mbit=30.0),
        ],
    )
    op = htb_fallback_op("op-1", plan)
    assert op.htb is not None
    assert op.htb.interface == "eth0"
    assert op.htb.root_rate_mbit == 100.0
    assert len(op.htb.classes) == 2


def test_htb_fallback_bounds() -> None:
    try:
        HtbFallbackPlan(interface="eth0", root_rate_mbit=0.5, classes=[])
    except FallbackError:
        pass
    else:
        raise AssertionError("expected FallbackError")


# ---- nft policy ----

def test_nft_policy_plan() -> None:
    plan = NftPolicyPlan(
        classes=[ClassMark(name="call", dscp=46)],
        devices=[DeviceEntry(mac="aa:bb:cc:dd:ee:ff", class_name="call")],
    )
    op = nft_policy_op("op-1", plan)
    assert op.nft is not None
    assert len(op.nft.entries) >= 1


def test_nft_policy_dscp_bounds() -> None:
    try:
        NftPolicyPlan(classes=[ClassMark(name="call", dscp=70)])
    except NftPolicyError:
        pass
    else:
        raise AssertionError("expected NftPolicyError")


def test_nft_policy_duplicate_class() -> None:
    try:
        NftPolicyPlan(
            classes=[
                ClassMark(name="call", dscp=46),
                ClassMark(name="call", fwmark=1),
            ]
        )
    except NftPolicyError:
        pass
    else:
        raise AssertionError("expected NftPolicyError")


# ---- doctor ----

def test_doctor_report_structure() -> None:
    report = doctor_report(["eth0", "wan0"])
    assert isinstance(report, DoctorReport)
    assert len(report.checks) > 0
    names = {c.name for c in report.checks}
    assert "binary:ip" in names
    assert "binary:tc" in names
    assert "cap:net_admin" in names


def test_doctor_summary() -> None:
    summary = doctor_summary(["eth0"])
    assert "eb-agent doctor report" in summary
    assert "OVERALL:" in summary


# ---- catalog bounds re-check ----

def test_validate_qdisc_spec_rejects_bad_rate() -> None:
    from eb_agent.enforcer.catalog import validate_qdisc_spec

    spec = QdiscSpec(kind="cake", interface="eth0", bandwidth_mbit=0.5)
    try:
        validate_qdisc_spec(spec, ["eth0"])
    except BoundsError:
        pass
    else:
        raise AssertionError("expected BoundsError")
