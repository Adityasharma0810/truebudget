"""Phase 9: Ask / RAG (stub).

Answers questions about the Experience Budget agent using only local,
public documentation.  Never sends user data anywhere.  Clearly marked
experimental with citations.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AskAnswer:
    text: str
    citations: list[str]
    disclaimer: str


class AskService:
    """Answers questions from local knowledge only."""

    def __init__(self) -> None:
        self._knowledge = {
            "pairing": (
                "Pairing uses an Ed25519/X25519 identity plus a short "
                "EB-XXXX-XXXX-XXXX key.  The agreement public key travels "
                "in the #pk= fragment and never reaches a server."
            ),
            "qoe": (
                "QoE is a formula-based proxy (0-1): call uses a simplified "
                "ITU-T G.107 E-model; game weighs RTT/jitter/loss; stream "
                "uses throughput vs target plus CoV and rebuffer; bulk is "
                "rate vs fair share."
            ),
            "policy": (
                "Max-min allocation distributes usable capacity with per-class "
                "floors.  Stability windows prevent oscillation; the verifier "
                "rolls back changes that don't improve QoE beyond noise."
            ),
        }

    def answer(self, question: str) -> AskAnswer:
        q = question.lower()
        for key, text in self._knowledge.items():
            if key in q:
                return AskAnswer(
                    text=text,
                    citations=[f"docs/{key}.md"],
                    disclaimer=(
                        "Emulated local knowledge only. Not medical, legal, "
                        "or network-engineering advice."
                    ),
                )
        return AskAnswer(
            text=(
                "I don't have a local answer for that. Try asking about "
                "pairing, QoE, or policy."
            ),
            citations=[],
            disclaimer="Emulated local knowledge only.",
        )


_ask = AskService()


def get_ask_service() -> AskService:
    return _ask
