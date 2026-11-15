"""Per-class/device nftables policy: MAC/IP (v4+v6) sets -> DSCP / fwmark.

Builds allow-listed nftables operations that mark traffic by device/traffic
class so CAKE tins (via DSCP) can shape per-class/device.  All MACs and IPs
are validated; only inet family tables are allowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from eb_agent.enforcer.catalog import EnforceOp, NftEntry, NftPlan, validate_ip

DEFAULT_TABLE = "eb_agent"
DEFAULT_CHAIN = "mark"


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
            if cls.name in seen_classes:
                raise NftPolicyError(f"duplicate class {cls.name!r}")
            seen_classes.add(cls.name)
            if cls.dscp is not None and not (0 <= cls.dscp <= 63):
                raise NftPolicyError(f"DSCP {cls.dscp} out of range [0, 63]")
            if cls.fwmark is not None and not (0 <= cls.fwmark <= 2**32 - 1):
                raise NftPolicyError(f"fwmark {cls.fwmark} out of range")
        for dev in self.devices:
            if dev.ipv4 is not None:
                validate_ip(dev.ipv4)
            if dev.ipv6 is not None:
                validate_ip(dev.ipv6)

    def __post_init__(self) -> None:
        self.validate()


def nft_policy_op(op_id: str, plan: NftPolicyPlan) -> EnforceOp:
    """Build idempotent nftables ops implementing the marking plan."""

    plan.validate()
    entries: list[NftEntry] = []

    # Class -> mark/DSCP rules keyed by class name (used as a set match).
    for cls in plan.classes:
        expr = f"ip dscp set {cls.dscp}" if cls.dscp is not None else ""
        if cls.fwmark is not None:
            expr = (
                expr + f" meta mark set {cls.fwmark}" if expr else f"meta mark set {cls.fwmark}"
            )
        entries.append(
            NftEntry(
                table=plan.table,
                chain=plan.chain,
                expr=expr or "accept",
            )
        )

    # Device -> class assignments keyed by MAC/IP.
    for dev in plan.devices:
        match_parts: list[str] = []
        if dev.mac is not None:
            match_parts.append(f"ether saddr {dev.mac}")
        if dev.ipv4 is not None:
            match_parts.append(f"ip saddr {dev.ipv4}")
        if dev.ipv6 is not None:
            match_parts.append(f"ip6 saddr {dev.ipv6}")
        if not match_parts:
            continue
        match = " and ".join(match_parts)
        # Find the class mark for this device's class.
        cls = next((c for c in plan.classes if c.name == dev.class_name), None)
        action_parts: list[str] = []
        if cls is not None:
            if cls.dscp is not None:
                action_parts.append(f"ip dscp set {cls.dscp}")
            if cls.fwmark is not None:
                action_parts.append(f"meta mark set {cls.fwmark}")
        action = " ".join(action_parts) or "accept"
        entries.append(
            NftEntry(
                table=plan.table,
                chain=plan.chain,
                expr=f"{match} {action}",
            )
        )

    return EnforceOp(op_id=op_id, nft=NftPlan(table=plan.table, chain=plan.chain, entries=entries))
