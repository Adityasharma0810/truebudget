"""Egress traffic-control: CAKE on the WAN interface.

Egress shaping applies ``bandwidth = estimate × shaping_factor`` (default 0.95)
with CAKE ``diffserv4``, ``nat``, ``dual-srchost``, ``dual-dsthost`` flags and
an optional ``ack-filter``.  All operations are built from the allow-listed
catalog and validated by the enforcer before reaching the kernel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from eb_agent.enforcer.catalog import EnforceOp, QdiscSpec
from eb_agent.enforcer.modes import TopologyMode

DEFAULT_SHAPING_FACTOR = 0.95
MIN_SHAPING_FACTOR = 0.5
MAX_SHAPING_FACTOR = 1.0


class EgressError(ValueError):
    """Egress configuration is invalid."""


@dataclass(frozen=True)
class EgressPlan:
    """A CAKE egress plan for a single WAN interface."""

    interface: str
    capacity_mbps: float
    shaping_factor: float = DEFAULT_SHAPING_FACTOR
    ack_filter: bool = False
    mode: TopologyMode | None = None

    @property
    def bandwidth_mbit(self) -> float:
        return max(1.0, self.capacity_mbps * self.shaping_factor)

    def validate(self) -> None:
        if not (MIN_SHAPING_FACTOR <= self.shaping_factor <= MAX_SHAPING_FACTOR):
            raise EgressError(
                f"shaping_factor {self.shaping_factor} outside "
                f"[{MIN_SHAPING_FACTOR}, {MAX_SHAPING_FACTOR}]"
            )
        if self.capacity_mbps < 1.0:
            raise EgressError(
                f"capacity {self.capacity_mbps} mbit/s below 1.0 mbit/s minimum"
            )

    def __post_init__(self) -> None:
        self.validate()


def cake_egress_op(op_id: str, plan: EgressPlan) -> EnforceOp:
    """Build an idempotent CAKE egress ``EnforceOp``."""

    plan.validate()
    params: dict[str, str] = {
        "diffserv4": "",
        "nat": "",
        # CAKE accepts only one host-isolation mode; egress uses source hosts.
        "dual-srchost": "",
    }
    if plan.ack_filter:
        params["ack-filter"] = ""
    return EnforceOp(
        op_id=op_id,
        qdisc=[
            QdiscSpec(
                kind="cake",
                interface=plan.interface,
                parent="root",
                bandwidth_mbit=plan.bandwidth_mbit,
                params=params,
            )
        ],
    )
