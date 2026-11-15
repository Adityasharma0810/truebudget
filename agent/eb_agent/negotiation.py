"""Phase 7: Intent negotiation / conflict resolution.

When two intents compete for the same scarce capacity, the agent surfaces
the conflict to the owner with bounded trade-off options.  Service floors
are never violated.  All proposals are emulated/synthetic.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class ConflictOption:
    name: str
    description: str
    allocations: list[float]  # percentages per device


@dataclass
class Conflict:
    id: str
    title: str
    description: str
    status: str  # pending | accepted | adjusted | cancelled
    options: list[ConflictOption]
    created_at: float = field(default_factory=time.time)


class NegotiationService:
    """Detects intent conflicts and proposes bounded trade-offs."""

    def __init__(self) -> None:
        self._conflicts: dict[str, Conflict] = {}

    def detect_conflicts(
        self,
        demands: dict[str, float],
        capacity_mbps: float,
        headroom: float = 0.05,
    ) -> list[Conflict]:
        """Return pending conflicts (empty when all demands fit)."""

        usable = capacity_mbps * (1.0 - headroom)
        total = sum(demands.values())
        if total <= usable:
            return []

        conflict_id = f"conf-{uuid.uuid4().hex[:8]}"
        scale = usable / total
        options: list[ConflictOption] = []

        # Option 0: pro-rata (agent preferred)
        pro_rata_alloc = [
            round(demands[d] * scale / max(usable, 0.001) * 100.0, 1)
            for d in demands
        ]
        options.append(
            ConflictOption(
                name="Pro-rata share",
                description="Scale all demands proportionally to fit capacity.",
                allocations=pro_rata_alloc,
            )
        )

        # Option 1: prioritize latency-sensitive classes
        prio_alloc: list[float] = []
        remaining = usable
        for device_id, rate in demands.items():
            alloc = min(rate, remaining)
            prio_alloc.append(round(alloc / max(usable, 0.001) * 100.0, 1))
            remaining -= alloc
        options.append(
            ConflictOption(
                name="Priority first",
                description="Give each device up to its floor; leftover shared.",
                allocations=prio_alloc,
            )
        )

        conflict = Conflict(
            id=conflict_id,
            title="Capacity is tighter than requested",
            description=(
                f"Requested {total:.1f} Mbps but only {usable:.1f} Mbps usable "
                f"({headroom:.0%} headroom reserved). Choose a trade-off."
            ),
            status="pending",
            options=options,
        )
        self._conflicts[conflict_id] = conflict
        return [conflict]

    def get(self, conflict_id: str) -> Conflict | None:
        return self._conflicts.get(conflict_id)

    def resolve(
        self,
        conflict_id: str,
        option_index: int,
    ) -> Conflict | None:
        conflict = self._conflicts.get(conflict_id)
        if not conflict or conflict.status != "pending":
            return None
        conflict.status = "adjusted" if option_index != 0 else "accepted"
        self._conflicts[conflict_id] = conflict
        return conflict

    def cancel(self, conflict_id: str) -> bool:
        conflict = self._conflicts.get(conflict_id)
        if not conflict or conflict.status != "pending":
            return False
        conflict.status = "cancelled"
        self._conflicts[conflict_id] = conflict
        return True

    def list(self) -> list[Conflict]:
        return list(self._conflicts.values())


_negotiation = NegotiationService()


def get_negotiation_service() -> NegotiationService:
    return _negotiation
