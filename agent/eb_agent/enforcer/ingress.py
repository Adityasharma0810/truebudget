"""Ingress traffic-control: IFB + mirred + CAKE ingress.

Linux cannot shape ingress directly on a physical interface, so ingress traffic
is redirected to an IFB pseudo-device via ``tc filter ... action mirred egress
redirect dev ifb0`` and then shaped with CAKE in ``ingress`` mode on IFB.
"""

from __future__ import annotations

from dataclasses import dataclass

from eb_agent.enforcer.catalog import EnforceOp, QdiscSpec

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

    def __post_init__(self) -> None:
        self.validate()


def ifb_setup_ops(op_id_prefix: str, ifb_interface: str = DEFAULT_IFB) -> list[EnforceOp]:
    """Build idempotent ops to bring up an IFB device (link set up)."""

    ops: list[EnforceOp] = []
    # IFB link-up is expressed as a netem/qdisc no-op placeholder that the
    # enforcer client translates to ``ip link set ifb0 up``.  Here we record
    # the intent as a qdisc spec so the catalog bounds-check the interface.
    ops.append(
        EnforceOp(
            op_id=f"{op_id_prefix}-ifb-up",
            qdisc=[
                QdiscSpec(
                    kind="netem",
                    interface=ifb_interface,
                    parent="root",
                    bandwidth_mbit=None,
                    params={"ifb-up": ""},
                )
            ],
        )
    )
    return ops


def mirred_filter_op(
    op_id: str,
    wan_interface: str,
    ifb_interface: str = DEFAULT_IFB,
) -> EnforceOp:
    """Build the mirred redirect filter op (ingress -> IFB)."""

    return EnforceOp(
        op_id=op_id,
        qdisc=[
            QdiscSpec(
                kind="tbf",
                interface=wan_interface,
                parent="ingress",
                bandwidth_mbit=None,
                params={
                    "mirred-egress-redirect": ifb_interface,
                    "handle": "1:",
                },
            )
        ],
    )


def cake_ingress_op(op_id: str, plan: IngressPlan) -> EnforceOp:
    """Build a CAKE ingress qdisc op on the IFB device."""

    plan.validate()
    return EnforceOp(
        op_id=op_id,
        qdisc=[
            QdiscSpec(
                kind="cake",
                interface=plan.ifb_interface,
                parent="ingress",
                bandwidth_mbit=plan.bandwidth_mbit,
                params={
                    "diffserv4": "",
                    "nat": "",
                    "dual-srchost": "",
                    "dual-dsthost": "",
                },
            )
        ],
    )
