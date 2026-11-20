"""Traffic-control topology modes: gateway, bridge, lab, monitor.

Each mode describes how the agent sits on the network path and what
interfaces it may shape.  Mode selection is persisted to the agent config
and enforced by the enforcer client/daemon boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

MODE_GATEWAY = "gateway"
MODE_BRIDGE = "bridge"
MODE_LAB = "lab"
MODE_MONITOR = "monitor"

ALL_MODES = frozenset({MODE_GATEWAY, MODE_BRIDGE, MODE_LAB, MODE_MONITOR})


class ModeError(ValueError):
    """Invalid or unsupported topology mode."""


@dataclass(frozen=True)
class TopologyMode:
    """A topology mode with its capabilities and constraints."""

    name: str
    # Whether this mode applies real traffic-control shaping.
    shapes: bool
    # Whether this mode forwards packets between WAN and LAN.
    forwards: bool
    # Whether this mode is read-only (monitoring only).
    read_only: bool
    # Interfaces that must be discovered/configured before shaping.
    required_interfaces: Sequence[str] = field(default_factory=list)
    # Human-readable description.
    description: str = ""

    def validate(self) -> None:
        if self.name not in ALL_MODES:
            raise ModeError(
                f"unknown topology mode {self.name!r}; allowed: {sorted(ALL_MODES)}"
            )


MODE_REGISTRY: dict[str, TopologyMode] = {
    MODE_GATEWAY: TopologyMode(
        name=MODE_GATEWAY,
        shapes=True,
        forwards=True,
        read_only=False,
        required_interfaces=["wan", "lan"],
        description="WAN + LAN interfaces with forwarding and NAT; real gateway.",
    ),
    MODE_BRIDGE: TopologyMode(
        name=MODE_BRIDGE,
        shapes=True,
        forwards=True,
        read_only=False,
        required_interfaces=["br0"],
        description="Transparent bridge between WAN and LAN; real shaping.",
    ),
    MODE_LAB: TopologyMode(
        name=MODE_LAB,
        shapes=True,
        forwards=False,
        read_only=False,
        required_interfaces=["wan0"],
        description="Emulated netns/veth/netem lab; not host traffic.",
    ),
    MODE_MONITOR: TopologyMode(
        name=MODE_MONITOR,
        shapes=False,
        forwards=False,
        read_only=True,
        required_interfaces=[],
        description="Read-only monitoring; no traffic-control changes.",
    ),
}


def get_mode(name: str) -> TopologyMode:
    mode = MODE_REGISTRY.get(name)
    if mode is None:
        raise ModeError(
            f"unknown topology mode {name!r}; allowed: {sorted(ALL_MODES)}"
        )
    return mode


def is_shaping_mode(name: str) -> bool:
    return get_mode(name).shapes


def is_read_only(name: str) -> bool:
    return get_mode(name).read_only


@dataclass(frozen=True)
class ModeSetupPlan:
    """Commands a user must explicitly approve to enable a topology mode.

    The enforcer never changes forwarding/NAT silently: ``eb-agent mode set``
    prints this plan and asks for confirmation first.
    """

    mode: str
    commands: list[list[str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    requires_confirmation: bool = True


def mode_setup(
    mode: str,
    wan_interface: str | None = None,
    lan_interface: str | None = None,
) -> ModeSetupPlan:
    """Build the bounded setup plan for a topology mode."""

    topology = get_mode(mode)
    if mode == MODE_MONITOR:
        return ModeSetupPlan(
            mode=mode,
            notes=["monitor mode is read-only; no qdisc, forwarding or NAT change"],
        )
    if mode == MODE_LAB:
        return ModeSetupPlan(
            mode=mode,
            notes=[
                "lab mode uses netns/veth/netem inside a namespace; "
                "it never shapes the host's real WAN",
            ],
        )
    if not wan_interface or not lan_interface:
        raise ModeError(f"{mode} mode needs both --wan and --lan interfaces")
    if wan_interface == lan_interface:
        raise ModeError("WAN and LAN interfaces must differ")

    if mode == MODE_BRIDGE:
        return ModeSetupPlan(
            mode=mode,
            notes=[
                f"attach {wan_interface} and {lan_interface} to a Linux bridge "
                "(brctl/ip link is operator-managed)",
                "shaping targets the bridge interface, not the physical ports",
            ],
        )

    commands = [
        ["sysctl", "-w", "net.ipv4.ip_forward=1"],
        ["sysctl", "-w", "net.ipv6.conf.all.forwarding=1"],
        ["nft", "add", "table", "ip", "eb_nat"],
        ["nft", "add", "chain", "ip", "eb_nat", "postrouting",
         "{ type nat hook postrouting priority srcnat ; policy accept ; }"],
        ["nft", "add", "rule", "ip", "eb_nat", "postrouting",
         "oifname", wan_interface, "masquerade"],
    ]
    return ModeSetupPlan(
        mode=mode,
        commands=commands,
        notes=[
            f"forwarding {lan_interface} -> {wan_interface}",
            "dnsmasq/hostapd are optional and operator-managed",
            "review every command before applying; use --dry-run to inspect",
        ],
    )


def render_mode_setup(plan: ModeSetupPlan) -> str:
    import shlex

    lines = [f"mode: {plan.mode}"]
    for command in plan.commands:
        lines.append(shlex.join(command))
    for note in plan.notes:
        lines.append(f"# {note}")
    return "\n".join(lines)
