"""Server-side QoE scorers mirroring frontend/src/lib/qoe/index.ts.

Deterministic proxies (0-1 scale). Missing measurements return None so
callers can surface "unknown" instead of inventing data.
"""

from __future__ import annotations

from statistics import fmean
from typing import Iterable, Sequence

QoE_BANDS = {"green": 0.75, "amber": 0.5}


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return min(hi, max(lo, float(value)))


def qoe_band(qoe: float | None) -> str:
    if qoe is None:
        return "idle"
    if qoe >= 0.75:
        return "green"
    if qoe >= 0.5:
        return "amber"
    return "red"


def _f(x: float, good: float, bad: float) -> float:
    """1 at/below good, 0 at/above bad, linear between."""
    return clamp((bad - x) / (bad - good))


def call_qoe(
    rtt_ms: float,
    jitter_ms: float,
    loss_pct: float,
    codec_delay_ms: float = 20.0,
    ie: float = 0.0,
    bpl: float = 25.0,
) -> float:
    """Simplified ITU-T G.107 E-model for an Opus-like codec."""

    d = rtt_ms / 2 + 2 * jitter_ms + codec_delay_ms
    id_delay = 0.024 * d + 0.11 * max(0.0, d - 177.3)
    ie_eff = ie + (95 - ie) * loss_pct / (loss_pct + bpl)
    r = clamp(93.2 - id_delay - ie_eff, 0.0, 100.0)
    mos = clamp(1 + 0.035 * r + 7e-6 * r * (r - 60) * (100 - r), 1.0, 4.5)
    return (mos - 1) / 3.5


def game_qoe(rtt_ms: float, jitter_ms: float, loss_pct: float) -> float:
    return (
        0.55 * _f(rtt_ms, 30, 150)
        + 0.30 * _f(jitter_ms, 5, 40)
        + 0.15 * _f(loss_pct, 0, 2)
    )


def stream_qoe(
    throughput_mbps: float | None,
    target_bitrate_mbps: float,
    cov: float | None,
    rebuffer_fraction: float | None,
) -> float | None:
    if throughput_mbps is None or cov is None or rebuffer_fraction is None:
        return None
    return (
        0.60 * clamp(throughput_mbps / target_bitrate_mbps)
        + 0.25 * (1 - clamp(cov / 0.5))
        + 0.15 * (1 - clamp(rebuffer_fraction))
    )


def bulk_qoe(rate_mbps: float | None, fair_expected_mbps: float | None) -> float | None:
    if rate_mbps is None or fair_expected_mbps is None or fair_expected_mbps <= 0:
        return None
    return clamp(rate_mbps / fair_expected_mbps)


def fairness(values: Sequence[float]) -> float:
    if not values or all(v == 0 for v in values):
        return 1.0
    total = sum(values)
    return total * total / (len(values) * sum(v * v for v in values))


def weighted_device_qoe(scores: Iterable[tuple[float, float]]) -> float | None:
    """Weighted-min device QoE from (score, priority_weight) pairs."""

    pairs = [(score, max(0.0, weight)) for score, weight in scores]
    if not pairs:
        return None
    best = min(pairs, key=lambda pair: pair[0])
    weight = best[1]
    if weight <= 0:
        return best[0]
    weighted = sum(score * (1 + w) for score, w in pairs) / sum(1 + w for _, w in pairs)
    return clamp(0.75 * best[0] + 0.25 * weighted)


def household_qoe(device_scores: Iterable[float | None]) -> float | None:
    active = [score for score in device_scores if score is not None]
    if not active:
        return None
    return clamp(fmean(active))
