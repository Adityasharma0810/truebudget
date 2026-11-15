"""Dev control service.

Real backend runtime control for the development panel.  These flags
simulate fault conditions and tuning knobs against the live agent -- they
do not fabricate metrics or demo data.  Use only in lab/dev contexts.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class DevControl:
    offline: bool = False
    stale: bool = False
    slow: bool = False
    rate_limit: bool = False
    speed: int = 10
    seed: int = 4242
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "offline": self.offline,
            "stale": self.stale,
            "slow": self.slow,
            "rateLimit": self.rate_limit,
            "speed": self.speed,
            "seed": self.seed,
            "updatedAt": self.updated_at,
        }


class DevControlService:
    def __init__(self) -> None:
        self.state = DevControl()

    def get(self) -> DevControl:
        return self.state

    def patch(self, patch: dict) -> DevControl:
        if "offline" in patch:
            self.state.offline = bool(patch["offline"])
        if "stale" in patch:
            self.state.stale = bool(patch["stale"])
        if "slow" in patch:
            self.state.slow = bool(patch["slow"])
        if "rateLimit" in patch:
            self.state.rate_limit = bool(patch["rateLimit"])
        if "speed" in patch:
            self.state.speed = max(1, min(60, int(patch["speed"])))
        if "seed" in patch:
            self.state.seed = int(patch["seed"])
        self.state.updated_at = time.time()
        return self.state

    def reset(self) -> DevControl:
        self.state = DevControl()
        return self.state


_dev_control = DevControlService()


def get_dev_control_service() -> DevControlService:
    return _dev_control
