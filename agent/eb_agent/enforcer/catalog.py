"""Allow-listed, bounded traffic-control operation schemas.

Every operation the enforcer can execute is described as plain data.  The
catalog is the *only* place commands are built, and the privileged daemon
re-validates every field against freshly discovered interfaces and hard
bounds before anything reaches the kernel.  Controllers are never trusted.
"""

from __future__ import annotations

import ipaddress
import math
import re
from dataclasses import dataclass, field
from typing import Sequence

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
IFACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,14}$")
OP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
CLASSID_RE = re.compile(r"^[0-9A-Fa-f]{1,4}:[0-9A-Fa-f]{0,4}$")
HANDLE_RE = re.compile(r"^[0-9A-Fa-f]{1,4}:$")
TABLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,14}$")
PARAM_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:%,/-]{1,32}$")
EXPR_RE = re.compile(r"^[A-Za-z0-9_.:<>=/()!,\s-]{1,512}$")

# ---- hard bounds (never trust the controller) ------------------------------

MIN_RATE_MBIT = 1.0
MAX_RATE_MBIT = 10_000.0
MAX_CLASSES_PER_QDISC = 64
MAX_NFT_ENTRIES = 256
MAX_TTL_S = 86_400
MIN_SHAPING_FACTOR = 0.5
MAX_SHAPING_FACTOR = 1.0
ALLOWED_QDISC_KINDS = frozenset(
    {"cake", "htb", "tbf", "fq_codel", "netem", "clsact", "ingress", "sfq", "pfifo_fast"}
)
ALLOWED_FILTER_KINDS = frozenset({"matchall", "fw", "u32"})
ALLOWED_LINK_KINDS = frozenset({"ifb", "dummy"})
ALLOWED_PROTOCOLS = frozenset({"all", "ip", "ipv6", "802.1q"})
MAX_CLEANUP_S = 120.0

# Bare CAKE/queue flags.  They are rendered as flags, never as key=value.
FLAG_PARAMS = frozenset(
    {
        "diffserv3",
        "diffserv4",
        "nat",
        "dual-srchost",
        "dual-dsthost",
        "ack-filter",
        "ingress",
        "wash",
        "besteffort",
        "no-atm",
        "split-gso",
        "ifb-up",
    }
)
VALUE_PARAMS = frozenset(
    {
        "delay",
        "loss",
        "limit",
        "burst",
        "latency",
        "rate",
        "overhead",
        "mpu",
        "rtt",
        "memlimit",
        "quantum",
        "fwmark",
    }
)

DSCP_BY_CLASS: dict[str, int] = {
    "call": 46,
    "game": 34,
    "stream": 26,
    "bulk": 8,
    "unknown": 0,
}
FW_MARK_BY_CLASS: dict[str, int] = {
    "call": 1,
    "game": 2,
    "stream": 3,
    "bulk": 4,
    "unknown": 5,
}


class BoundsError(ValueError):
    """An operation violated an allow-list bound."""


# ---- operation schemas -----------------------------------------------------


@dataclass(frozen=True)
class QdiscSpec:
    """A single ``tc qdisc`` / ingress-redirect operation."""

    kind: str
    interface: str
    parent: str = "root"
    handle: str | None = None
    bandwidth_mbit: float | None = None
    params: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FilterSpec:
    """A bounded ``tc filter`` operation.

    ``matchall`` is used for the IFB ingress redirect; ``fw`` maps an
    nftables mark to an HTB class; ``u32`` is retained for explicit matches.
    """

    interface: str
    parent: str = "ingress"
    protocol: str = "all"
    priority: int = 1
    kind: str = "matchall"
    target: str | None = None
    mark: int | None = None
    flowid: str | None = None
    match: str = "u32 0 0"


@dataclass(frozen=True)
class LinkSpec:
    """An idempotent virtual-link setup step (IFB or dummy probe)."""

    interface: str
    kind: str = "ifb"
    up: bool = True


@dataclass(frozen=True)
class HtbClass:
    """One HTB class: classid, rate, ceil, priority, optional fwmark."""

    classid: str
    rate_mbit: float
    ceil_mbit: float | None = None
    priority: int = 0
    quantum: int | None = None
    mark: int | None = None
    leaf_qdisc: str = "fq_codel"


@dataclass(frozen=True)
class HtbPlan:
    """A complete HTB qdisc + class + leaf plan for one interface."""

    interface: str
    root_rate_mbit: float
    classes: list[HtbClass] = field(default_factory=list)
    leaf_qdisc: str = "fq_codel"
    default_classid: str | None = None


@dataclass(frozen=True)
class NftEntry:
    """One allow-listed nftables rule or device-to-class mapping.

    ``expr`` is used for class-level rules built by :mod:`nft_policy`.
    Structured fields (``mac``/``ipv4``/``ipv6`` + ``dscp``/``fwmark``) are
    rendered into agent-owned sets so read-back can prove each device is
    actually marked.
    """

    table: str = "eb_agent"
    chain: str = "eb_mark"
    handle: int | None = None
    expr: str = ""
    mac: str | None = None
    ipv4: str | None = None
    ipv6: str | None = None
    dscp: int | None = None
    fwmark: int | None = None
    direction: str = "src"
    device_id: str | None = None
    traffic_class: str | None = None
    l3: str | None = None  # "ip" | "ip6" to force a family for MAC-only rules


@dataclass(frozen=True)
class NftPlan:
    """nftables operations keyed by MAC/IP (v4 + v6) -> DSCP / fwmark."""

    table: str = "eb_agent"
    chain: str = "eb_mark"
    entries: list[NftEntry] = field(default_factory=list)


@dataclass(frozen=True)
class EnforceOp:
    """One idempotent, bounded enforcement operation."""

    op_id: str
    qdisc: list[QdiscSpec] = field(default_factory=list)
    htb: HtbPlan | None = None
    nft: NftPlan | None = None
    filters: list[FilterSpec] = field(default_factory=list)
    links: list[LinkSpec] = field(default_factory=list)
    dry_run: bool = False
    ttl_s: int | None = None


# ---- validation ------------------------------------------------------------


def validate_op_id(op_id: str) -> str:
    if not OP_ID_RE.match(op_id or ""):
        raise BoundsError(f"invalid op_id {op_id!r}: must match {OP_ID_RE.pattern}")
    return op_id


def validate_interface(interface: str, available: Sequence[str]) -> str:
    if not IFACE_RE.match(interface or "") or interface in {"all", "default"}:
        raise BoundsError(f"invalid interface name {interface!r}")
    if interface not in list(available):
        raise BoundsError(
            f"interface {interface!r} not in discovered interfaces: {list(available)}"
        )
    return interface


def _rate_mbit(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        rate = float(value)
    except (TypeError, ValueError) as error:
        raise BoundsError(f"rate {value!r} is not a number") from error
    if not math.isfinite(rate) or not (MIN_RATE_MBIT <= rate <= MAX_RATE_MBIT):
        raise BoundsError(
            f"rate {rate} mbit/s outside allowed bounds [{MIN_RATE_MBIT}, {MAX_RATE_MBIT}]"
        )
    return rate


def _dscp(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value <= 63):
        raise BoundsError(f"DSCP {value!r} outside [0, 63]")
    return value


def _fwmark(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= 65535):
        raise BoundsError(f"fwmark {value!r} outside [1, 65535]")
    return value


def validate_mac(mac: str) -> str:
    if not MAC_RE.match(mac or ""):
        raise BoundsError(f"invalid MAC address {mac!r}")
    lowered = mac.lower()
    if lowered in {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"}:
        raise BoundsError(f"invalid MAC address {mac!r}")
    return lowered


def validate_ip(ip: str) -> str:
    try:
        address = ipaddress.ip_address(ip or "")
    except ValueError as error:
        raise BoundsError(f"invalid IP address {ip!r}") from error
    if address.is_multicast or address.is_unspecified:
        raise BoundsError(f"IP address is not a device address: {ip!r}")
    return str(address)


def validate_qdisc_spec(spec: QdiscSpec, available_interfaces: Sequence[str]) -> QdiscSpec:
    if spec.kind not in ALLOWED_QDISC_KINDS:
        raise BoundsError(
            f"qdisc kind {spec.kind!r} not allow-listed: {sorted(ALLOWED_QDISC_KINDS)}"
        )
    validate_interface(spec.interface, available_interfaces)
    _rate_mbit(spec.bandwidth_mbit)
    if spec.parent not in {"root", "ingress"} and not CLASSID_RE.match(spec.parent or ""):
        raise BoundsError(f"invalid qdisc parent {spec.parent!r}")
    if spec.handle is not None and not HANDLE_RE.match(spec.handle):
        raise BoundsError(f"invalid qdisc handle {spec.handle!r}")
    if not isinstance(spec.params, dict):
        raise BoundsError("qdisc params must be an object")
    if spec.kind in {"ingress", "clsact"} and spec.params:
        raise BoundsError(f"{spec.kind} qdisc takes no parameters")
    for key, value in spec.params.items():
        if key in FLAG_PARAMS:
            if value not in {"", None}:
                raise BoundsError(f"flag param {key!r} must have an empty value")
            continue
        if key in VALUE_PARAMS:
            if not isinstance(value, str) or not value:
                raise BoundsError(f"value param {key!r} needs a value")
            if not PARAM_VALUE_RE.match(value):
                raise BoundsError(f"value param {key!r} has unsafe value {value!r}")
            continue
        raise BoundsError(f"param {key!r} is not allow-listed")
    return spec


def validate_filter_spec(spec: FilterSpec, available_interfaces: Sequence[str]) -> FilterSpec:
    validate_interface(spec.interface, available_interfaces)
    if spec.kind not in ALLOWED_FILTER_KINDS:
        raise BoundsError(f"filter kind {spec.kind!r} not allow-listed")
    if spec.protocol not in ALLOWED_PROTOCOLS:
        raise BoundsError(f"filter protocol {spec.protocol!r} not allow-listed")
    if isinstance(spec.priority, bool) or not (1 <= int(spec.priority) <= 65535):
        raise BoundsError("filter priority must be 1..65535")
    if spec.parent not in {"ingress", "egress"} and not CLASSID_RE.match(spec.parent or ""):
        raise BoundsError(f"invalid filter parent {spec.parent!r}")
    if spec.kind == "matchall":
        if not spec.target:
            raise BoundsError("matchall filter needs a redirect target")
        validate_interface(spec.target, available_interfaces)
        if spec.target == spec.interface:
            raise BoundsError("filter cannot redirect an interface to itself")
    elif spec.kind == "fw":
        _fwmark(spec.mark)
        if spec.mark is None:
            raise BoundsError("fw filter needs a mark")
        if not CLASSID_RE.match(spec.flowid or ""):
            raise BoundsError(f"invalid flowid {spec.flowid!r}")
        if spec.parent in {"ingress", "egress"}:
            raise BoundsError("fw filter belongs on an HTB class parent")
    else:  # u32
        if not spec.target:
            raise BoundsError("u32 filter needs a redirect target")
        validate_interface(spec.target, available_interfaces)
        if not re.match(r"^[A-Za-z0-9 .:/-]{1,64}$", spec.match or ""):
            raise BoundsError(f"unsafe u32 match {spec.match!r}")
    return spec


def validate_link_spec(spec: LinkSpec) -> LinkSpec:
    if not IFACE_RE.match(spec.interface or "") or spec.interface in {"all", "default"}:
        raise BoundsError(f"invalid link interface {spec.interface!r}")
    if spec.kind not in ALLOWED_LINK_KINDS:
        raise BoundsError(f"link kind {spec.kind!r} not allow-listed")
    return spec


def validate_htb_plan(plan: HtbPlan, available_interfaces: Sequence[str]) -> HtbPlan:
    validate_interface(plan.interface, available_interfaces)
    root = _rate_mbit(plan.root_rate_mbit)
    if len(plan.classes) > MAX_CLASSES_PER_QDISC:
        raise BoundsError(f"too many HTB classes ({len(plan.classes)} > {MAX_CLASSES_PER_QDISC})")
    if not plan.classes:
        raise BoundsError("HTB plan needs at least one class")
    classids: set[str] = set()
    marks: set[int] = set()
    total = 0.0
    for cls in plan.classes:
        if not CLASSID_RE.match(cls.classid or ""):
            raise BoundsError(f"invalid HTB classid {cls.classid!r}")
        if cls.classid in classids:
            raise BoundsError(f"duplicate HTB classid {cls.classid!r}")
        classids.add(cls.classid)
        rate = _rate_mbit(cls.rate_mbit) or 0.0
        ceil = _rate_mbit(cls.ceil_mbit) if cls.ceil_mbit is not None else rate
        if ceil is not None and ceil < rate:
            raise BoundsError("HTB ceil must be >= rate")
        if ceil is not None and root is not None and ceil > root * 1.001:
            raise BoundsError("HTB class ceil exceeds the root rate")
        if isinstance(cls.priority, bool) or not (0 <= int(cls.priority) <= 7):
            raise BoundsError("HTB priority must be 0..7")
        if cls.quantum is not None and not (1000 <= int(cls.quantum) <= 200_000):
            raise BoundsError("HTB quantum outside [1000, 200000]")
        if cls.mark is not None:
            mark = _fwmark(cls.mark)
            if mark in marks:
                raise BoundsError(f"duplicate HTB fwmark {mark}")
            marks.add(mark)
        if cls.leaf_qdisc not in {"fq_codel", "cake"}:
            raise BoundsError("HTB leaf must be fq_codel or cake")
        total += rate
    if root is not None and total > root * 1.001:
        raise BoundsError("sum of HTB class rates exceeds the root rate")
    if plan.default_classid is not None and plan.default_classid not in classids:
        raise BoundsError("HTB default classid is not one of the declared classes")
    if plan.leaf_qdisc not in {"fq_codel", "cake"}:
        raise BoundsError("HTB leaf_qdisc must be fq_codel or cake")
    return plan


def validate_nft_plan(plan: NftPlan) -> NftPlan:
    if not TABLE_RE.match(plan.table or ""):
        raise BoundsError(f"invalid nft table {plan.table!r}")
    if not TABLE_RE.match(plan.chain or ""):
        raise BoundsError(f"invalid nft chain {plan.chain!r}")
    if len(plan.entries) > MAX_NFT_ENTRIES:
        raise BoundsError(f"too many nft entries ({len(plan.entries)} > {MAX_NFT_ENTRIES})")
    seen: set[tuple[str, str, int | None, int | None, str]] = set()
    for entry in plan.entries:
        _dscp(entry.dscp)
        _fwmark(entry.fwmark)
        if entry.direction not in {"src", "dst"}:
            raise BoundsError("nft direction must be src or dst")
        if entry.l3 not in {None, "ip", "ip6"}:
            raise BoundsError("nft l3 must be ip, ip6 or None")
        if entry.expr and not EXPR_RE.match(entry.expr):
            raise BoundsError(f"unsafe nft expression {entry.expr!r}")
        if entry.mac is not None:
            validate_mac(entry.mac)
        if entry.ipv4 is not None:
            address = validate_ip(entry.ipv4)
            if ipaddress.ip_address(address).version != 4:
                raise BoundsError(f"{entry.ipv4!r} is not IPv4")
        if entry.ipv6 is not None:
            address = validate_ip(entry.ipv6)
            if ipaddress.ip_address(address).version != 6:
                raise BoundsError(f"{entry.ipv6!r} is not IPv6")
        structured = any(
            value is not None for value in (entry.mac, entry.ipv4, entry.ipv6)
        )
        if structured and entry.dscp is None and entry.fwmark is None:
            raise BoundsError("structured nft entry needs a DSCP or fwmark action")
        if not structured and not entry.expr:
            raise BoundsError("nft entry needs an expression or a device match")
        key = (
            str(entry.mac or entry.ipv4 or entry.ipv6 or entry.expr),
            entry.direction,
            entry.dscp,
            entry.fwmark,
            str(entry.l3 or ""),
        )
        if key in seen:
            raise BoundsError(f"duplicate nft entry {key[0]!r}")
        seen.add(key)
    return plan


def validate_enforce_op(op: EnforceOp, available_interfaces: Sequence[str]) -> EnforceOp:
    validate_op_id(op.op_id)
    qdisc = (
        [validate_qdisc_spec(s, available_interfaces) for s in op.qdisc] if op.qdisc else []
    )
    htb = validate_htb_plan(op.htb, available_interfaces) if op.htb is not None else None
    nft = validate_nft_plan(op.nft) if op.nft is not None else None
    filters = (
        [validate_filter_spec(f, available_interfaces) for f in op.filters]
        if op.filters
        else []
    )
    links = [validate_link_spec(link) for link in op.links] if op.links else []
    if not qdisc and htb is None and nft is None and not filters and not links and not op.dry_run:
        raise BoundsError("operation has no qdisc, htb, nft, filter or link component")
    if op.ttl_s is not None:
        if isinstance(op.ttl_s, bool) or not (1 <= int(op.ttl_s) <= MAX_TTL_S):
            raise BoundsError(f"ttl_s must be 1..{MAX_TTL_S}")
    return EnforceOp(
        op_id=op.op_id,
        qdisc=qdisc,
        htb=htb,
        nft=nft,
        filters=filters,
        links=links,
        dry_run=op.dry_run,
        ttl_s=op.ttl_s,
    )


def dscp_name(value: int) -> str:
    names = {
        0: "cs0",
        8: "cs1",
        26: "af31",
        32: "cs4",
        34: "af41",
        46: "ef",
    }
    return names.get(int(value), str(int(value)))


def format_command(command: Sequence[str]) -> str:
    import shlex

    return shlex.join(list(command))
