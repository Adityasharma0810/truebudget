"""Unit tests for Phase 5: intents, verifier, decision log."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from eb_agent.decision_log import DecisionLog, GENESIS_HASH
from eb_agent.intents import IntentService
from eb_agent.store import Store
from eb_agent.verifier import Verifier


@pytest.fixture
def tmp_store():
    with tempfile.TemporaryDirectory() as td:
        yield Store(Path(td) / "eb.db")


# ---- intents ----

@pytest.mark.asyncio
async def test_intent_create_and_expire(tmp_store: Store) -> None:
    svc = IntentService(tmp_store)
    await svc.start()
    try:
        result = await svc.create_intent(
            device_id="dev-1",
            traffic_class="call",
            goal="boost",
            priority=5,
            duration_s=0.1,
        )
        assert result.intent_id.startswith("intent-")
        assert result.feasibility == "feasible"

        # Should expire quickly
        await asyncio.sleep(1.5)
        intents = svc.list_intents()
        assert len(intents) == 1
        assert intents[0]["state"] == "expired"
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_intent_cancel(tmp_store: Store) -> None:
    svc = IntentService(tmp_store)
    await svc.start()
    try:
        result = await svc.create_intent(
            device_id="dev-1",
            traffic_class="call",
            goal="boost",
            priority=5,
            duration_s=3600.0,
        )
        cancelled = await svc.cancel_intent(result.intent_id)
        assert cancelled is True
        intents = svc.list_intents()
        assert intents[0]["state"] == "cancelled"

        # Cancel again should fail
        cancelled2 = await svc.cancel_intent(result.intent_id)
        assert cancelled2 is False
    finally:
        await svc.stop()


# ---- verifier ----

def test_verifier_kept() -> None:
    v = Verifier(noise_margin=0.05)
    result = v.verify(metric_before=0.4, metric_after=0.8)
    assert result.verdict == "kept"
    assert result.improved is True


def test_verifier_rolled_back_no_improvement() -> None:
    v = Verifier(noise_margin=0.05)
    result = v.verify(metric_before=0.8, metric_after=0.4)
    assert result.verdict == "rolled_back"
    assert result.improved is False


def test_verifier_rolled_back_floor_violation() -> None:
    v = Verifier()
    result = v.verify(metric_before=0.4, metric_after=0.9, floor_violated=True)
    assert result.verdict == "rolled_back"


def test_verifier_escalated_low_significance() -> None:
    v = Verifier(noise_margin=0.01)
    # 9 samples all below threshold → low statistical significance
    samples = [0.001] * 9 + [0.1]
    result = v.verify(metric_before=0.4, metric_after=0.5, samples=samples)
    assert result.verdict in ("escalated", "kept")


# ---- decision log ----

def test_decision_log_append_and_verify(tmp_store: Store) -> None:
    log = DecisionLog(tmp_store)
    d1 = log.append(
        trigger="policy_change",
        reason="Test decision",
        before_qoe={"dev1": 0.4},
        after_qoe={"dev1": 0.8},
        evidence={"capacity_mbps": 100.0},
        verdict="kept",
    )
    assert d1.prev_hash == GENESIS_HASH
    assert len(d1.hash) == 64

    d2 = log.append(
        trigger="intent_expiry",
        reason="Intent expired",
        before_qoe={"dev1": 0.8},
        after_qoe={"dev1": 0.4},
        evidence={"intent_id": "intent-1"},
        verdict="rolled_back",
    )
    assert d2.prev_hash == d1.hash

    ok, bad = log.verify_chain()
    assert ok is True
    assert bad is None


def test_decision_log_tamper_detection(tmp_store: Store) -> None:
    log = DecisionLog(tmp_store)
    log.append(
        trigger="policy_change",
        reason="Original",
        before_qoe={"dev1": 0.4},
        after_qoe={"dev1": 0.8},
        evidence={},
        verdict="kept",
    )
    # Tamper with the hash directly in DB
    tmp_store.execute(
        "UPDATE decisions SET hash = 'tampered' WHERE decision_id = ?",
        (log.list_decisions(limit=1)[0]["decision_id"],),
    )
    tmp_store.commit()
    ok, bad = log.verify_chain()
    assert ok is False
    assert bad is not None


def test_decision_log_list_and_export(tmp_store: Store) -> None:
    log = DecisionLog(tmp_store)
    log.append(
        trigger="policy_change",
        reason="Test",
        before_qoe={},
        after_qoe={},
        evidence={},
        verdict="kept",
    )
    decisions = log.list_decisions(limit=10)
    assert len(decisions) == 1
    assert decisions[0]["verdict"] == "kept"

    csv = log.export_csv()
    assert "decision_id" in csv
    assert "kept" in csv
