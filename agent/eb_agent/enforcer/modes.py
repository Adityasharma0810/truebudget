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
