"""Closed-loop verifier: verify each applied change, rollback if not helpful.

After each applied change: pre-window metrics → settle (2-3 fast intervals) →
post-window. Keep only if target metric improved beyond noise margin and no
floor/safety violation; else rollback or escalate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass
class VerificationResult:
    verdict: str  # kept | rolled_back | escalated
    evidence: dict
    metric_before: float | None
    metric_after: float | None
    improved: bool


class Verifier:
    """Closed-loop verification of traffic-control changes."""

    def __init__(self, noise_margin: float = 0.05, settle_intervals: int = 3) -> None:
        self.noise_margin = noise_margin
        self.settle_intervals = settle_intervals

    def verify(
        self,
        metric_before: float | None,
        metric_after: float | None,
        floor_violated: bool = False,
        safety_violated: bool = False,
        samples: Sequence[float] = (),
    ) -> VerificationResult:
        """Verify a change. Returns verdict + evidence."""

        improved = False
        if metric_before is not None and metric_after is not None:
            improved = metric_after > metric_before + self.noise_margin

        evidence = {
            "noise_margin": self.noise_margin,
            "settle_intervals": self.settle_intervals,
            "samples_count": len(samples),
        }

        if floor_violated or safety_violated:
            return VerificationResult(
                verdict="rolled_back",
                evidence={**evidence, "reason": "floor_or_safety_violation"},
                metric_before=metric_before,
                metric_after=metric_after,
                improved=False,
            )

        if not improved:
            return VerificationResult(
                verdict="rolled_back",
                evidence={**evidence, "reason": "no_improvement_beyond_noise"},
                metric_before=metric_before,
                metric_after=metric_after,
                improved=False,
            )

        # Paired statistical check when enough samples
        if len(samples) >= 10:
            # Simple bootstrap-style check: mean improvement > noise margin
            diffs = [s for s in samples if s > self.noise_margin]
            if len(diffs) / len(samples) < 0.7:
                return VerificationResult(
                    verdict="escalated",
                    evidence={**evidence, "reason": "statistical_significance_low"},
                    metric_before=metric_before,
                    metric_after=metric_after,
                    improved=improved,
                )

        return VerificationResult(
            verdict="kept",
            evidence={**evidence, "reason": "improvement_verified"},
            metric_before=metric_before,
            metric_after=metric_after,
            improved=True,
        )
