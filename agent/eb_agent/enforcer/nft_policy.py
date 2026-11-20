"""Per-class/device nftables policy: MAC/IP (v4+v6) -> DSCP / fwmark.

Builds allow-listed nftables operations that mark traffic by device/traffic
class so CAKE tins (via DSCP) can shape per-class/device, and HTB fallback can
match the fwmark.  All MACs and IPs are validated; only the agent-owned inet
table is touched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from eb_agent.enforcer.catalog import EnforceOp, NftEntry, NftPlan, validate_ip

DEFAULT_TABLE = "eb_agent"
DEFAULT_CHAIN = "eb_mark"
CLASS_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,15}$")


class NftPolicyError(ValueError):
    """Nftables policy configuration is invalid."""


@dataclass(frozen=True)
class ClassMark:
    """One traffic-class -> DSCP/fwmark mapping."""

    name: str  # e.g. "call", "game", "stream", "bulk"
    dscp: int | None = None
    fwmark: int | None = None


@dataclass(frozen=True)
class DeviceEntry:
    """One device entry: MAC/IP -> class assignment."""

    mac: str | None = None
    ipv4: str | None = None
    ipv6: str | None = None
    class_name: str = "bulk"


@dataclass(frozen=True)
class NftPolicyPlan:
    """Complete nftables marking plan for one interface."""

    table: str = DEFAULT_TABLE
    chain: str = DEFAULT_CHAIN
    classes: Sequence[ClassMark] = ()
    devices: Sequence[DeviceEntry] = ()

    def validate(self) -> None:
        seen_classes = set()
        for cls in self.classes:
            if not CLASS_NAME_RE.match(cls.name or ""):
                raise NftPolicyError(f"invalid class name {cls.name!r}")
            if cls.name in seen_classes:
                raise NftPolicyError(f"duplicate class {cls.name!r}")
            seen_classes.add(cls.name)
            if cls.dscp is None and cls.fwmark is None:
                raise NftPolicyError(
                    f"class {cls.name!r} needs a DSCP or fwmark action"
                )
            if cls.dscp is not None and not (0 <= cls.dscp <= 63):
                raise NftPolicyError(f"DSCP {cls.dscp} out of range [0, 63]")
            if cls.fwmark is not None and not (1 <= cls.fwmark <= 65535):
                raise NftPolicyError(f"fwmark {cls.fwmark} out of range [1, 65535]")
        class_names = {cls.name for cls in self.classes}
        for dev in self.devices:
            if dev.class_name not in class_names:
                raise NftPolicyError(
                    f"device class {dev.class_name!r} is not declared"
                )
            if dev.ipv4 is not None:
                validate_ip(dev.ipv4)
            if dev.ipv6 is not None:
                validate_ip(dev.ipv6)
            if dev.mac is None and dev.ipv4 is None and dev.ipv6 is None:
                raise NftPolicyError("device entry needs a MAC or IP address")

    def __post_init__(self) -> None:
        self.validate()


def nft_policy_op(op_id: str, plan: NftPolicyPlan) -> EnforceOp:
    """Build idempotent nftables ops implementing the marking plan.

    Each device gets a structured rule (one per address family for MAC-only
    devices) so read-back can prove that exact device is marked.  No rule is
    emitted without a device match: a rule that marks *all* traffic would be
    an unbounded policy change and is never generated.
    """

    plan.validate()
    by_name = {cls.name: cls for cls in plan.classes}
    entries: list[NftEntry] = []
    for dev in plan.devices:
        mark = by_name[dev.class_name]
        families: list[str | None]
        if dev.ipv4 is not None or dev.ipv6 is not None:
            families = [None]
        else:
            families = ["ip", "ip6"]
        for family in families:
            entries.append(
                NftEntry(
                    table=plan.table,
                    chain=plan.chain,
                    mac=dev.mac,
                    ipv4=dev.ipv4,
                    ipv6=dev.ipv6,
                    dscp=mark.dscp,
                    fwmark=mark.fwmark,
                    direction="src",
                    traffic_class=dev.class_name,
                    l3=family,
                )
            )
    return EnforceOp(
        op_id=op_id,
        nft=NftPlan(table=plan.table, chain=plan.chain, entries=entries),
    )
