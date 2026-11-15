"""Known-safe traffic-control configuration.

Persisted before every change so the agent can restore a conservative CAKE
(or no-shaping) baseline after a failure, rollback, or manual reset.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from eb_agent.enforcer.catalog import QdiscSpec, validate_qdisc_spec


@dataclass
class SafeState:
    """Snapshot of a known-safe traffic-control configuration."""

    qdiscs: dict[str, list[QdiscSpec]] = field(default_factory=dict)
    nft_ruleset: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(
            {
                "qdiscs": {
                    iface: [
                        {
                            "kind": s.kind,
                            "interface": s.interface,
                            "parent": s.parent,
                            "handle": s.handle,
                            "bandwidth_mbit": s.bandwidth_mbit,
                            "params": s.params,
                        }
                        for s in specs
                    ]
                    for iface, specs in self.qdiscs.items()
                },
                "nft_ruleset": self.nft_ruleset,
                "updated_at": self.updated_at,
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, data: str) -> "SafeState":
        payload = json.loads(data)
        qdiscs: dict[str, list[QdiscSpec]] = {}
        for iface, specs in payload.get("qdiscs", {}).items():
            qdiscs[iface] = [
                QdiscSpec(
                    kind=s["kind"],
                    interface=s["interface"],
                    parent=s.get("parent", "root"),
                    handle=s.get("handle"),
                    bandwidth_mbit=s.get("bandwidth_mbit"),
                    params=s.get("params", {}),
                )
                for s in specs
            ]
        return cls(
            qdiscs=qdiscs,
            nft_ruleset=payload.get("nft_ruleset", ""),
            updated_at=payload.get("updated_at", time.time()),
        )


def default_safe_state(interfaces: Sequence[str]) -> SafeState:
    """Plain conservative CAKE on each interface (or no shaping if CAKE unavailable)."""

    specs: dict[str, list[QdiscSpec]] = {}
    for iface in interfaces:
        specs[iface] = [
            QdiscSpec(
                kind="cake",
                interface=iface,
                parent="root",
                bandwidth_mbit=None,
                params={"diffserv4": "", "nat": "", "dual-srchost": "", "dual-dsthost": ""},
            )
        ]
    return SafeState(qdiscs=specs)


def snapshot(
    runner,
    interfaces: Sequence[str],
    available_interfaces: Sequence[str],
) -> SafeState:
    """Capture current qdisc state per interface as a SafeState snapshot."""

    qdiscs: dict[str, list[QdiscSpec]] = {}
    for iface in interfaces:
        readback = runner.qdisc_show_json(iface)
        specs: list[QdiscSpec] = []
        if readback:
            kind = readback.get("kind", "cake")
            if kind not in {"cake", "htb", "tbf", "fq_codel", "netem"}:
                kind = "cake"
            specs.append(
                QdiscSpec(
                    kind=kind,
                    interface=iface,
                    parent=readback.get("parent", "root"),
                    handle=readback.get("handle"),
                    bandwidth_mbit=None,
                    params={},
                )
            )
        qdiscs[iface] = specs
    return SafeState(qdiscs=qdiscs, updated_at=time.time())


def save(safe: SafeState, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(safe.to_json() + "\n", encoding="utf-8")


def load(path: Path) -> SafeState:
    return SafeState.from_json(path.read_text(encoding="utf-8"))


def validate(safe: SafeState, available_interfaces: Sequence[str]) -> SafeState:
    """Re-validate every qdisc spec against allow-list bounds."""

    qdiscs: dict[str, list[QdiscSpec]] = {}
    for iface, specs in safe.qdiscs.items():
        qdiscs[iface] = [
            validate_qdisc_spec(s, available_interfaces) for s in specs
        ]
    return SafeState(qdiscs=qdiscs, nft_ruleset=safe.nft_ruleset, updated_at=safe.updated_at)


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "reset":
        print("safe-state reset (stub)")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
