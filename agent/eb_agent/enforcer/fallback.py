"""HTB + fq_codel fallback when sch_cake is unavailable.

Startup module check + dry run detect CAKE availability; if CAKE is missing,
the enforcer falls back to an HTB root qdisc with per-class HTB classes and
fq_codel leaves, plus ``tc filter fw`` rules keyed by fwmark.
"""

from __future__ import annotations

from dataclasses import dataclass

from eb_agent.enforcer.catalog import EnforceOp, HtbClass, HtbPlan, QdiscSpec


class FallbackError(ValueError):
    """Fallback configuration is invalid."""


@dataclass(frozen=True)
class HtbFallbackPlan:
    """HTB fallback plan for one interface."""

    interface: str
    root_rate_mbit: float
    classes: list[HtbClass]
    leaf_qdisc: str = "fq_codel"

    def validate(self) -> None:
        if self.root_rate_mbit < 1.0:
            raise FallbackError(
                f"HTB root rate {self.root_rate_mbit} below 1.0 mbit/s"
            )
        if not self.classes:
            raise FallbackError("HTB fallback needs at least one class")

    def __post_init__(self) -> None:
        self.validate()


def htb_fallback_op(op_id: str, plan: HtbFallbackPlan) -> EnforceOp:
    """Build an HTB root + class + fq_codel leaf EnforceOp."""

    plan.validate()
    return EnforceOp(
        op_id=op_id,
        htb=HtbPlan(
            interface=plan.interface,
            root_rate_mbit=plan.root_rate_mbit,
            classes=list(plan.classes),
            leaf_qdisc=plan.leaf_qdisc,
            default_classid=plan.classes[-1].classid,
        ),
    )


def cake_available(runner) -> bool:
    """Probe whether the kernel has sch_cake available (dry run, then clean up)."""

    result = runner.run(["qdisc", "add", "dev", "lo", "root", "cake"])
    try:
        runner.run(["qdisc", "del", "dev", "lo", "root"])
    except TypeError:
        pass
    return result.ok
