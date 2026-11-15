"""Phase 9: Federated learning (stub).

Keeps model updates local -- nothing leaves the device.  The server only
aggregates anonymised deltas when the owner opts in.  Marked experimental.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class FederatedState:
    enabled: bool = False
    rounds: int = 0
    last_aggregate: float | None = None
    participants: int = 0


class FederatedService:
    def __init__(self) -> None:
        self.state = FederatedState()

    def opt_in(self) -> FederatedState:
        self.state.enabled = True
        return self.state

    def opt_out(self) -> FederatedState:
        self.state.enabled = False
        return self.state

    def status(self) -> FederatedState:
        return self.state

    def record_round(self, participants: int) -> FederatedState:
        self.state.rounds += 1
        self.state.participants = participants
        self.state.last_aggregate = time.time()
        return self.state


_federated = FederatedService()


def get_federated_service() -> FederatedService:
    return _federated
