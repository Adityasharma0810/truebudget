"""Decision log: append-only, hash-chained record of every control action.

Every policy change/revert/intent/mode-switch/safe-state event → Decision
with trigger, templated reason, before/after QoE, evidence JSON, verdict.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Sequence


GENESIS_HASH = "0" * 64


@dataclass
class Decision:
    decision_id: str
    ts: float
    trigger: str
    reason: str
    before_qoe: dict
    after_qoe: dict
    evidence: dict
    verdict: str  # kept | rolled_back | escalated
    prev_hash: str
    hash: str


class DecisionLog:
    """Append-only, hash-chained decision log."""

    def __init__(self, store) -> None:
        self.store = store
        self._last_hash = GENESIS_HASH
        self._init_chain()

    def _init_chain(self) -> None:
        row = self.store.execute(
            "SELECT hash FROM decisions ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if row:
            self._last_hash = row["hash"]

    def _compute_hash(self, prev_hash: str, ts: float, trigger: str, reason: str,
                      before_qoe: dict, after_qoe: dict, evidence: dict, verdict: str) -> str:
        payload = json.dumps(
            {
                "prev_hash": prev_hash,
                "ts": ts,
                "trigger": trigger,
                "reason": reason,
                "before_qoe": before_qoe,
                "after_qoe": after_qoe,
                "evidence": evidence,
                "verdict": verdict,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def append(
        self,
        trigger: str,
        reason: str,
        before_qoe: dict,
        after_qoe: dict,
        evidence: dict,
        verdict: str,
    ) -> Decision:
        """Append a new decision to the hash chain."""

        ts = time.time()
        decision_id = f"dec-{int(ts * 1000)}-{uuid.uuid4().hex[:6]}"
        h = self._compute_hash(
            self._last_hash, ts, trigger, reason, before_qoe, after_qoe, evidence, verdict
        )
        prev_hash = self._last_hash

        self.store.execute(
            "INSERT INTO decisions(decision_id, ts, trigger, reason, before_qoe, "
            "after_qoe, evidence, verdict, prev_hash, hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision_id, ts, trigger, reason,
                json.dumps(before_qoe), json.dumps(after_qoe),
                json.dumps(evidence), verdict, self._last_hash, h,
            ),
        )
        self.store.commit()

        self._last_hash = h

        return Decision(
            decision_id=decision_id,
            ts=ts,
            trigger=trigger,
            reason=reason,
            before_qoe=before_qoe,
            after_qoe=after_qoe,
            evidence=evidence,
            verdict=verdict,
            prev_hash=prev_hash,
            hash=h,
        )

    def verify_chain(self) -> tuple[bool, str | None]:
        """Verify hash chain integrity. Returns (ok, first_bad_decision_id)."""

        prev = GENESIS_HASH
        rows = self.store.execute(
            "SELECT decision_id, prev_hash, hash, ts, trigger, reason, before_qoe, "
            "after_qoe, evidence, verdict FROM decisions ORDER BY ts ASC"
        ).fetchall()

        for row in rows:
            expected = self._compute_hash(
                prev, row["ts"], row["trigger"], row["reason"],
                json.loads(row["before_qoe"]), json.loads(row["after_qoe"]),
                json.loads(row["evidence"]), row["verdict"],
            )
            if row["prev_hash"] != prev:
                return False, row["decision_id"]
            if row["hash"] != expected:
                return False, row["decision_id"]
            prev = row["hash"]

        return True, None

    def list_decisions(
        self,
        limit: int = 50,
        offset: int = 0,
        verdict: str | None = None,
        trigger: str | None = None,
    ) -> Sequence[dict]:
        query = (
            "SELECT decision_id, ts, trigger, reason, verdict FROM decisions "
            "WHERE 1=1"
        )
        params: list = []
        if verdict:
            query += " AND verdict=?"
            params.append(verdict)
        if trigger:
            query += " AND trigger=?"
            params.append(trigger)
        query += " ORDER BY ts DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self.store.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def export_csv(self) -> str:
        """Export decisions as CSV."""

        rows = self.store.execute(
            "SELECT decision_id, ts, trigger, reason, verdict, prev_hash, hash "
            "FROM decisions ORDER BY ts ASC"
        ).fetchall()
        lines = ["decision_id,ts,trigger,reason,verdict,prev_hash,hash"]
        for r in rows:
            lines.append(
                f"{r['decision_id']},{r['ts']},{r['trigger']},"
                f"{r['reason'].replace(',', ' ')},{r['verdict']},"
                f"{r['prev_hash']},{r['hash']}"
            )
        return "\n".join(lines)
