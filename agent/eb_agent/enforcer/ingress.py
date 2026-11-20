"""Ingress traffic-control: IFB + mirred + CAKE ingress.

Linux cannot shape ingress directly on a physical interface, so ingress traffic
is redirected to an IFB pseudo-device via
``tc filter ... matchall action mirred egress redirect dev ifb0`` and then
shaped with CAKE in ``ingress`` mode on the IFB device.
"""

from __future__ import annotations

from dataclasses import dataclass

from eb_agent.enforcer.catalog import (
    EnforceOp,
    FilterSpec,
    LinkSpec,
    QdiscSpec,
)

DEFAULT_IFB = "ifb0"


class IngressError(ValueError):
    """Ingress configuration is invalid."""


@dataclass(frozen=True)
class IngressPlan:
    """A CAKE ingress plan via IFB + mirred."""

    wan_interface: str
    ifb_interface: str = DEFAULT_IFB
    bandwidth_mbit: float = 100.0

    def validate(self) -> None:
        if self.bandwidth_mbit < 1.0:
            raise IngressError(
                f"ingress bandwidth {self.bandwidth_mbit} below 1.0 mbit/s minimum"
            )
        if self.wan_interface == self.ifb_interface:
            raise IngressError("WAN and IFB interfaces must differ")

    def __post_init__(self) -> None:
        self.validate()


def ifb_setup_ops(op_id_prefix: str, ifb_interface: str = DEFAULT_IFB) -> list[EnforceOp]:
    """Build an idempotent op that creates/brings up an IFB device."""

    return [
        EnforceOp(
            op_id=f"{op_id_prefix}-ifb-up",
            links=[LinkSpec(interface=ifb_interface, kind="ifb", up=True)],
        )
    ]


def mirred_filter_op(
    op_id: str,
    wan_interface: str,
    ifb_interface: str = DEFAULT_IFB,
) -> EnforceOp:
    """Build the clsact qdisc + matchall mirred redirect op (WAN -> IFB)."""

    return EnforceOp(
        op_id=op_id,
        qdisc=[QdiscSpec(kind="clsact", interface=wan_interface, parent="root")],
        filters=[
            FilterSpec(
                interface=wan_interface,
                parent="ingress",
                protocol="all",
                priority=1,
                kind="matchall",
                target=ifb_interface,
            )
        ],
    )


def cake_ingress_op(op_id: str, plan: IngressPlan) -> EnforceOp:
    """Build a CAKE ingress-mode qdisc op on the IFB device."""

    plan.validate()
    params: dict[str, str] = {
        "diffserv4": "",
        "nat": "",
        "dual-dsthost": "",
        "ingress": "",
    }
    return EnforceOp(
        op_id=op_id,
        qdisc=[
            QdiscSpec(
                kind="cake",
                interface=plan.ifb_interface,
                parent="root",
                bandwidth_mbit=plan.bandwidth_mbit,
                params=params,
            )
        ],
    )


def ingress_ops(
    op_id_prefix: str,
    plan: IngressPlan,
) -> list[EnforceOp]:
    """Convenience: IFB setup, mirred redirect, then CAKE ingress shaping."""

    return [
        *ifb_setup_ops(op_id_prefix, plan.ifb_interface),
        mirred_filter_op(
            f"{op_id_prefix}-mirred", plan.wan_interface, plan.ifb_interface
        ),
        cake_ingress_op(f"{op_id_prefix}-cake-ingress", plan),
    ]
