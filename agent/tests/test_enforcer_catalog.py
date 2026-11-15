"""Unit tests for the enforcer operation catalog (no root required)."""

from __future__ import annotations

import pytest

from eb_agent.enforcer.catalog import (
    ALLOWED_QDISC_KINDS,
    BoundsError,
    EnforceOp,
    HtbClass,
    HtbPlan,
    NftEntry,
    NftPlan,
    QdiscSpec,
    MAX_CLASSES_PER_QDISC,
    MAX_NFT_ENTRIES,
    MIN_RATE_MBIT,
    MAX_RATE_MBIT,
    validate_enforce_op,
    validate_htb_plan,
    validate_interface,
    validate_ip,
    validate_mac,
    validate_nft_plan,
    validate_op_id,
    validate_qdisc_spec,
)


def test_validate_op_id_accepts_valid() -> None:
    assert validate_op_id("op-1") == "op-1"
    assert validate_op_id("abc_123-XYZ") == "abc_123-XYZ"


@pytest.mark.parametrize("bad", ["", "a" * 65, "bad id!", "op/id", "op.id"])
def test_validate_op_id_rejects_bad(bad: str) -> None:
    with pytest.raises(BoundsError):
        validate_op_id(bad)


def test_validate_interface_accepts_known() -> None:
    assert validate_interface("eth0", ["eth0", "wan0"]) == "eth0"


def test_validate_interface_rejects_unknown_and_bad() -> None:
    with pytest.raises(BoundsError):
        validate_interface("eth99", ["eth0"])
    with pytest.raises(BoundsError):
        validate_interface("bad name!", ["eth0"])


def test_validate_mac() -> None:
    assert validate_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"
    with pytest.raises(BoundsError):
        validate_mac("not-a-mac")


def test_validate_ip() -> None:
    assert validate_ip("10.0.0.1") == "10.0.0.1"
    assert validate_ip("::1") == "::1"
    with pytest.raises(BoundsError):
        validate_ip("999.999.999.999")


def test_rate_bounds() -> None:
    spec = QdiscSpec(kind="cake", interface="eth0", bandwidth_mbit=100.0)
    validate_qdisc_spec(spec, ["eth0"])
    low = QdiscSpec(kind="cake", interface="eth0", bandwidth_mbit=MIN_RATE_MBIT - 0.1)
    with pytest.raises(BoundsError):
        validate_qdisc_spec(low, ["eth0"])
    high = QdiscSpec(kind="cake", interface="eth0", bandwidth_mbit=MAX_RATE_MBIT + 0.1)
    with pytest.raises(BoundsError):
        validate_qdisc_spec(high, ["eth0"])


def test_qdisc_kind_allow_list() -> None:
    bad = QdiscSpec(kind="tbpfifo", interface="eth0")
    with pytest.raises(BoundsError):
        validate_qdisc_spec(bad, ["eth0"])
    for kind in ALLOWED_QDISC_KINDS:
        spec = QdiscSpec(kind=kind, interface="eth0")
        validate_qdisc_spec(spec, ["eth0"])


def test_htb_class_count_bound() -> None:
    classes = [HtbClass(classid=f"1:{i}", rate_mbit=10.0) for i in range(MAX_CLASSES_PER_QDISC + 1)]
    plan = HtbPlan(interface="eth0", root_rate_mbit=100.0, classes=classes)
    with pytest.raises(BoundsError):
        validate_htb_plan(plan, ["eth0"])


def test_nft_entry_count_bound() -> None:
    entries = [NftEntry(table="t", chain="c") for _ in range(MAX_NFT_ENTRIES + 1)]
    plan = NftPlan(entries=entries)
    with pytest.raises(BoundsError):
        validate_nft_plan(plan)


def test_enforce_op_idempotent_and_dry_run() -> None:
    op = EnforceOp(
        op_id="op-1",
        qdisc=[QdiscSpec(kind="cake", interface="eth0", bandwidth_mbit=50.0)],
    )
    validated = validate_enforce_op(op, ["eth0"])
    assert validated.op_id == "op-1"
    assert not validated.dry_run

    dry = EnforceOp(op_id="op-2", qdisc=[QdiscSpec(kind="cake", interface="eth0")], dry_run=True)
    validated_dry = validate_enforce_op(dry, ["eth0"])
    assert validated_dry.dry_run


def test_enforce_op_rejects_bad_op_id() -> None:
    op = EnforceOp(op_id="bad op!", qdisc=[QdiscSpec(kind="cake", interface="eth0")])
    with pytest.raises(BoundsError):
        validate_enforce_op(op, ["eth0"])


def test_validate_enforce_op_rejects_unknown_interface() -> None:
    op = EnforceOp(op_id="op-1", qdisc=[QdiscSpec(kind="cake", interface="eth99")])
    with pytest.raises(BoundsError):
        validate_enforce_op(op, ["eth0"])
