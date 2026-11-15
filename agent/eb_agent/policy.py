"""Max-min policy engine with stability guards.

Inputs: capacity estimate (lower CI bound), per-flow demand + QoE-vs-rate curves,
floors, rate bounds, active intents + weights.
Output: per-flow/per-device allocation + DSCP/mark assignments + CAKE bandwidth
+ plain-language rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from eb_agent.qoe_server import bulk_qoe, call_qoe, game_qoe, stream_qoe

DEFAULT_HEADROOM = 0.05
DEFAULT_MAX_CHANGE_PCT = 20.0
DEFAULT_DWELL_S = 10.0


@dataclass
class FlowDemand:
    flow_id: str
    device_id: str
    traffic_class: str  # call, game, stream, bulk
    min_rate_mbps: float
    max_rate_mbps: float
    weight: float = 1.0


@dataclass
class Allocation:
    flow_id: str
    rate_mbps: float
    dscp: int | None = None
    fwmark: int | None = None


@dataclass
class Policy:
    allocations: list[Allocation]
    cake_bandwidth_mbit: float
    rationale: str
    infeasible: bool = False
    degradation_proposal: str | None = None
    worst_device_qoe: float | None = None
    jain_index: float | None = None
    starvation_count: int = 0


@dataclass
class PolicyState:
    """Tracks stability state between policy runs."""

    last_rates: dict[str, float] = field(default_factory=dict)
    last_apply_time: float = 0.0
    dwell_s: float = DEFAULT_DWELL_S
    max_change_pct: float = DEFAULT_MAX_CHANGE_PCT


def _qoe_for_class(
    traffic_class: str,
    rate_mbps: float,
    capacity_mbps: float,
) -> float:
    """Approximate QoE for a traffic class at a given rate."""

    if traffic_class == "call":
        # Call QoE depends on latency, which improves with rate up to a point
        # Use simple model: good QoE above ~1 Mbps
        return call_qoe(rtt_ms=max(10, 200 - rate_mbps * 10), jitter_ms=5.0, loss_pct=0.1)
    if traffic_class == "game":
        return game_qoe(rtt_ms=max(10, 200 - rate_mbps * 20), jitter_ms=2.0, loss_pct=0.05)
    if traffic_class == "stream":
        return stream_qoe(
            throughput_mbps=rate_mbps,
            target_bitrate_mbps=6.0,
            cov=0.1,
            rebuffer_fraction=0.0,
        ) or 0.0
    if traffic_class == "bulk":
        return bulk_qoe(rate_mbps=rate_mbps, fair_expected_mbps=capacity_mbps / 4.0) or 0.0
    return 0.0


def max_min_allocate(
    flows: Sequence[FlowDemand],
    capacity_mbps: float,
    headroom: float = DEFAULT_HEADROOM,
    state: PolicyState | None = None,
) -> Policy:
    """Max-min allocation: maximize minimum QoE subject to rate bounds."""

    state = state or PolicyState()
    usable = capacity_mbps * (1.0 - headroom)
    total_min = sum(f.min_rate_mbps for f in flows)

    if total_min > usable:
        return Policy(
            allocations=[],
            cake_bandwidth_mbit=usable,
            rationale=(
                f"Infeasible: total minimum rates ({total_min:.1f} Mbps) exceed "
                f"usable capacity ({usable:.1f} Mbps). Reduce floors or intents."
            ),
            infeasible=True,
            degradation_proposal="Lower min_service_floor for bulk flows or cancel non-urgent intents.",
            starvation_count=len(flows),
        )

    # Bisection on target QoE level t
    lo, hi = 0.0, 1.0
    best_allocation: dict[str, float] = {}

    for _ in range(40):  # bisection iterations
        t = (lo + hi) / 2.0
        # Progressive filling: give each flow enough rate to reach QoE >= t
        rates: dict[str, float] = {}
        remaining = usable

        # First pass: assign min rates
        for f in flows:
            rates[f.flow_id] = f.min_rate_mbps
            remaining -= f.min_rate_mbps

        # Second pass: raise rates to reach target QoE t
        changed = True
        while changed and remaining > 0.001:
            changed = False
            for f in flows:
                current = rates[f.flow_id]
                current_qoe = _qoe_for_class(f.traffic_class, current, capacity_mbps)
                if current_qoe < t and remaining > 0.001:
                    step = min(remaining, 0.5)
                    rates[f.flow_id] = min(f.max_rate_mbps, current + step)
                    remaining -= step
                    changed = True

        # Check if all flows reach QoE t
        all_reach = all(
            _qoe_for_class(f.traffic_class, rates[f.flow_id], capacity_mbps) >= t - 0.01
            for f in flows
        )

        if all_reach:
            best_allocation = dict(rates)
            lo = t
        else:
            hi = t

    # Apply stability constraints
    allocations: list[Allocation] = []
    now = __import__("time").time()
    time_since_last = now - state.last_apply_time

    for f in flows:
        rate = best_allocation.get(f.flow_id, f.min_rate_mbps)
        # Max rate change per step
        if f.flow_id in state.last_rates:
            last = state.last_rates[f.flow_id]
            max_change = last * (state.max_change_pct / 100.0)
            rate = max(last - max_change, min(last + max_change, rate))
        rate = max(f.min_rate_mbps, min(f.max_rate_mbps, rate))

        # Dwell: don't change too soon
        if time_since_last < state.dwell_s:
            rate = state.last_rates.get(f.flow_id, rate)

        allocations.append(
            Allocation(
                flow_id=f.flow_id,
                rate_mbps=round(rate, 4),
                dscp=_dscp_for_class(f.traffic_class),
                fwmark=None,
            )
        )
        state.last_rates[f.flow_id] = rate

    # Compute metrics
    qoes = [
        _qoe_for_class(f.traffic_class, a.rate_mbps, capacity_mbps)
        for f, a in zip(flows, allocations)
    ]
    worst = min(qoes) if qoes else None
    jain = _jain([a.rate_mbps for a in allocations])
    starvation = sum(1 for q in qoes if q < 0.5)

    rationale = (
        f"Allocated {usable:.1f} Mbps usable capacity across {len(flows)} flows "
        f"using max-min fairness. Worst device QoE: {worst:.2f}" if worst is not None else ""
    )

    state.last_apply_time = now

    return Policy(
        allocations=allocations,
        cake_bandwidth_mbit=usable,
        rationale=rationale,
        infeasible=False,
        worst_device_qoe=worst,
        jain_index=jain,
        starvation_count=starvation,
    )


def _dscp_for_class(traffic_class: str) -> int:
    return {"call": 46, "game": 34, "stream": 32, "bulk": 10}.get(traffic_class, 0)


def _jain(values: Sequence[float]) -> float:
    if not values or all(v == 0 for v in values):
        return 1.0
    total = sum(values)
    return total * total / (len(values) * sum(v * v for v in values))
