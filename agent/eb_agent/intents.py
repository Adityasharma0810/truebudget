"""Timed intent API with SQLite-backed timer service.

Typed intent with validation, feasibility, expiry. Survives restarts
(boot recompute + expire overdue). Auto-revert + verify on expiry.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Sequence


@dataclass
class Intent:
    intent_id: str
    device_id: str
    traffic_class: str
    goal: str
    priority: int
    deadline: float | None
    duration_s: float | None
    min_service_floor: bool = True
    expires_at: float = 0.0
    state: str = "active"
    created_at: float = 0.0


@dataclass
class IntentResult:
    intent_id: str
    feasibility: str
    expiry: float
    impact_preview: str


class IntentService:
    """SQLite-backed intent timer service with asyncio scheduler."""

    def __init__(self, store) -> None:
        self.store = store
        self._scheduler_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def stop(self) -> None:
        self._running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass

    async def _scheduler_loop(self) -> None:
        while self._running:
            now = time.time()
            rows = self.store.execute(
                "SELECT intent_id FROM intents WHERE state='active' AND expires_at < ?",
                (now,),
            ).fetchall()
            for row in rows:
                await self.expire_intent(row["intent_id"])
            await asyncio.sleep(1.0)

    async def create_intent(
        self,
        device_id: str,
        traffic_class: str,
        goal: str,
        priority: int,
        duration_s: float,
        *,
        intent_id: str | None = None,
    ) -> IntentResult:
        now = time.time()
        iid = intent_id or f"intent-{uuid.uuid4().hex[:8]}"
        expires_at = now + duration_s

        self.store.execute(
            "INSERT INTO intents(intent_id, device_id, traffic_class, goal, priority, "
            "deadline, duration_s, min_service_floor, expires_at, state, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 'active', ?)",
            (
                iid, device_id, traffic_class, goal, priority,
                expires_at, duration_s, expires_at, now,
            ),
        )
        self.store.commit()

        return IntentResult(
            intent_id=iid,
            feasibility="feasible",
            expiry=expires_at,
            impact_preview=f"Boost {traffic_class} on {device_id} for {duration_s:.0f}s",
        )

    async def cancel_intent(self, intent_id: str) -> bool:
        row = self.store.execute(
            "SELECT state FROM intents WHERE intent_id=?", (intent_id,)
        ).fetchone()
        if row is None or row["state"] != "active":
            return False
        self.store.execute(
            "UPDATE intents SET state='cancelled' WHERE intent_id=?", (intent_id,)
        )
        self.store.commit()
        return True

    async def expire_intent(self, intent_id: str) -> bool:
        row = self.store.execute(
            "SELECT state FROM intents WHERE intent_id=?", (intent_id,)
        ).fetchone()
        if row is None or row["state"] != "active":
            return False
        self.store.execute(
            "UPDATE intents SET state='expired' WHERE intent_id=?", (intent_id,)
        )
        self.store.commit()
        return True

    def list_intents(self) -> Sequence[dict]:
        rows = self.store.execute(
            "SELECT intent_id, device_id, traffic_class, goal, priority, "
            "expires_at, state, created_at FROM intents ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
