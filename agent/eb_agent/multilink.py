"""Multilink: aggregate multiple WAN links with per-link health and ECMP-ish
load distribution.  Each link tracked independently; traffic steered to the
healthiest link within a flow (consistent hashing on 5-tuple).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class Link:
    link_id: str
    name: str
    capacity_mbps: float
    state: str = "up"  # up, degraded, down
    rtt_ms: float = 0.0
    loss_pct: float = 0.0
    used_mbps: float = 0.0

    @property
    def available_mbps(self) -> float:
        return max(0.0, self.capacity_mbps - self.used_mbps)

    @property
    def health_score(self) -> float:
        if self.state == "down":
            return 0.0
        score = 1.0
        score -= min(0.5, self.loss_pct / 10.0)
        score -= min(0.3, max(0.0, self.rtt_ms - 20.0) / 200.0)
        score -= min(0.2, self.used_mbps / max(0.001, self.capacity_mbps))
        return max(0.0, score)


@dataclass
class FlowSteering:
    flow_id: str
    link_id: str
    reason: str


class MultilinkSteerer:
    """Consistent-hash steering of flows across multiple links."""

    def __init__(self, links: Sequence[Link]) -> None:
        self.links: dict[str, Link] = {l.link_id: l for l in links}
        self._flow_link: dict[str, str] = {}

    def steer(self, flow_id: str, five_tuple: str) -> FlowSteering | None:
        """Steer a flow to a link. Returns None if all links are down."""

        healthy = [l for l in self.links.values() if l.health_score > 0]
        if not healthy:
            return None

        # If already pinned to a healthy link, keep it (flow stickiness).
        current = self._flow_link.get(flow_id)
        if current and current in self.links and self.links[current].health_score > 0:
            return FlowSteering(flow_id, current, "sticky")

        # Consistent hash over eligible links sorted by health desc.
        eligible = sorted(healthy, key=lambda l: -l.health_score)
        digest = hashlib.sha256(five_tuple.encode()).hexdigest()
        idx = int(digest, 16) % len(eligible)
        chosen = eligible[idx]
        self._flow_link[flow_id] = chosen.link_id
        return FlowSteering(flow_id, chosen.link_id, "consistent_hash")

    def update_link(self, link_id: str, **updates) -> None:
        link = self.links.get(link_id)
        if not link:
            return
        for k, v in updates.items():
            if hasattr(link, k):
                setattr(link, k, v)
        # Unpin flows from degraded/down links so they re-steer.
        if link.health_score == 0:
            self._flow_link = {
                f: lid for f, lid in self._flow_link.items() if lid != link_id
            }

    def link_report(self) -> list[dict]:
        return [
            {
                "link_id": l.link_id,
                "name": l.name,
                "state": l.state,
                "capacity_mbps": l.capacity_mbps,
                "available_mbps": round(l.available_mbps, 2),
                "rtt_ms": l.rtt_ms,
                "loss_pct": l.loss_pct,
                "health_score": round(l.health_score, 3),
            }
            for l in self.links.values()
        ]
