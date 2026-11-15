"""Allow-listed, bounded traffic-control operation schemas.

Every operation the enforcer may apply is described as plain data.  Bounds
(rates, MACs, IPs, interface names, op ids) are re-enforced here before
anything touches the kernel -- the controller is never trusted.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Sequence

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
IFACE_RE = re.compile(r"^[a-zA-Z0-9_.:-]{1,15}$")
OP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# ---- hard bounds (never trust the controller) ------------------------------

MIN_RATE_MBIT = 1.0
MAX_RATE_MBIT = 10_000.0
MAX_CLASSES_PER_QDISC = 64
MAX_NFT_ENTRIES = 256
ALLOWED_QDISC_KINDS = frozenset({"cake", "htb", "tbf", "fq_codel", "netem"})


class BoundsError(ValueError):
    """An operation violated an allow-list bound."""


# ---- operation schemas -----------------------------------------------------


@dataclass(frozen=True)
class QdiscSpec:
    """A single ``tc qdisc`` operation."""

    kind: str
    interface: str
    parent: str = "root"
    handle: str | None = None
    bandwidth_mbit: float | None = None
    params: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class HtbClass:
    """One HTB class: classid, rate, ceil, priority."""

    classid: str
    rate_mbit: float
    ceil_mbit: float | None = None
    priority: int = 0
    quantum: int | None = None


@dataclass(frozen=True)
class HtbPlan:
    """A complete HTB qdisc + class + leaf plan for one interface."""

    interface: str
    root_rate_mbit: float
    classes: list[HtbClass] = field(default_factory=list)
    leaf_qdisc: str = "fq_codel"


@dataclass(frozen=True)
class NftEntry:
    """One allow-listed nftables rule / set entry."""

    table: str
    chain: str
    handle: int | None = None
    expr: str = ""


@dataclass(frozen=True)
class NftPlan:
    """nftables operations keyed by MAC/IP (v4 + v6) -> DSCP / fwmark."""

    table: str = "eb_agent"
    chain: str = "mark"
    entries: list[NftEntry] = field(default_factory=list)


@dataclass(frozen=True)
class EnforceOp:
    """One idempotent, bounded enforcement operation."""

    op_id: str
    qdisc: list[QdiscSpec] = field(default_factory=list)
    htb: HtbPlan | None = None
    nft: NftPlan | None = None
    dry_run: bool = False


# ---- validation ------------------------------------------------------------


def validate_op_id(op_id: str) -> str:
    if not OP_ID_RE.match(op_id or ""):
        raise BoundsError(f"invalid op_id {op_id!r}: must match {OP_ID_RE.pattern}")
    return op_id


def validate_interface(interface: str, available: Sequence[str]) -> str:
    if not IFACE_RE.match(interface or ""):
        raise BoundsError(f"invalid interface name {interface!r}")
    if interface not in list(available):
        raise BoundsError(
            f"interface {interface!r} not in discovered interfaces: {list(available)}"
        )
    return interface


def _rate_mbit(value: float | None) -> float | None:
    if value is None:
        return None
    rate = float(value)
    if not (MIN_RATE_MBIT <= rate <= MAX_RATE_MBIT):
        raise BoundsError(
            f"rate {rate} mbit/s outside allowed bounds [{MIN_RATE_MBIT}, {MAX_RATE_MBIT}]"
        )
    return rate


def validate_mac(mac: str) -> str:
    if not MAC_RE.match(mac or ""):
        raise BoundsError(f"invalid MAC address {mac!r}")
    return mac.lower()


def validate_ip(ip: str) -> str:
    try:
        ipaddress.ip_address(ip or "")
    except ValueError as error:
        raise BoundsError(f"invalid IP address {ip!r}") from error
    return ip


def validate_qdisc_spec(spec: QdiscSpec, available_interfaces: Sequence[str]) -> QdiscSpec:
    if spec.kind not in ALLOWED_QDISC_KINDS:
        raise BoundsError(
            f"qdisc kind {spec.kind!r} not allow-listed: {sorted(ALLOWED_QDISC_KINDS)}"
        )
    validate_interface(spec.interface, available_interfaces)
    _rate_mbit(spec.bandwidth_mbit)
    return spec


def validate_htb_plan(plan: HtbPlan, available_interfaces: Sequence[str]) -> HtbPlan:
    validate_interface(plan.interface, available_interfaces)
    _rate_mbit(plan.root_rate_mbit)
    if len(plan.classes) > MAX_CLASSES_PER_QDISC:
        raise BoundsError(f"too many HTB classes ({len(plan.classes)} > {MAX_CLASSES_PER_QDISC})")
    for cls in plan.classes:
        _rate_mbit(cls.rate_mbit)
        _rate_mbit(cls.ceil_mbit)
    return plan


def validate_nft_plan(plan: NftPlan) -> NftPlan:
    if len(plan.entries) > MAX_NFT_ENTRIES:
        raise BoundsError(f"too many nft entries ({len(plan.entries)} > {MAX_NFT_ENTRIES})")
    return plan


def validate_enforce_op(op: EnforceOp, available_interfaces: Sequence[str]) -> EnforceOp:
    validate_op_id(op.op_id)
    qdisc = (
        [validate_qdisc_spec(s, available_interfaces) for s in op.qdisc] if op.qdisc else []
    )
    htb = validate_htb_plan(op.htb, available_interfaces) if op.htb is not None else None
    nft = validate_nft_plan(op.nft) if op.nft is not None else None
    return EnforceOp(
        op_id=op.op_id, qdisc=qdisc, htb=htb, nft=nft, dry_run=op.dry_run
    )
