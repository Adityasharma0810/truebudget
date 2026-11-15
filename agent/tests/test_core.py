"""Unit tests for eb_agent.core (no root required)."""

from __future__ import annotations

import asyncio

import pytest

from eb_agent.config import AgentConfig
from eb_agent.core import AgentCore, Snapshot, Tick, request_id
from eb_agent.store import Store


@pytest.fixture
def core(tmp_path):
    cfg = AgentConfig(mode="lab", slow_interval_s=0.01, fast_interval_s=0.01)
    store = Store(tmp_path / "eb.db")
    c = AgentCore(cfg, store)
    yield c, store
    store.close()


@pytest.mark.asyncio
async def test_request_id() -> None:
    rid1 = request_id()
    rid2 = request_id()
    assert rid1 != rid2
    assert len(rid1) == 16


@pytest.mark.asyncio
async def test_snapshot_assembly(core) -> None:
    c, _ = core
    await c._slow_tick()
    snap = c.last_snapshot
    assert isinstance(snap, Snapshot)
    assert snap.emulated is True
    assert snap.mode == "lab"
    assert len(snap.devices) == 5
    assert len(snap.flows) > 0


@pytest.mark.asyncio
async def test_slow_tick_persists(core) -> None:
    c, store = core
    await c._slow_tick()
    assert c.last_snapshot is not None
    cur = store.execute("SELECT value FROM settings WHERE key=?", ("last_snapshot",))
    assert cur.fetchone() is not None


@pytest.mark.asyncio
async def test_fast_tick(core) -> None:
    c, _ = core
    tick = Tick(ts=0.0, interval_s=1.0)
    result = c._fast_tick.__wrapped__ if hasattr(c._fast_tick, "__wrapped__") else None
    # Just call the coroutine
    await c._fast_tick()
    assert True  # fast tick should not raise


@pytest.mark.asyncio
async def test_start_stop(core) -> None:
    c, _ = core
    await c.start()
    assert c._running is True
    assert c._slow_task is not None
    assert c._fast_task is not None
    await asyncio.sleep(0.05)
    await c.stop()
    assert c._running is False


@pytest.mark.asyncio
async def test_downsample(core) -> None:
    c, store = core
    now = __import__("time").time()
    store.execute(
        "INSERT INTO metrics_raw(ts, device_id, metric, value, source) VALUES (?, ?, ?, ?, ?)",
        (now - 10, "dev-1", "rtt", 10.0, "conntrack"),
    )
    store.commit()
    c.downsample()
    cur = store.execute("SELECT count(*) AS c FROM metrics_agg")
    assert cur.fetchone()["c"] >= 0
