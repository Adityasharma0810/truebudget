"""Unit tests for the SQLite WAL store (no root required)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from eb_agent.store import Store


@pytest.fixture
def tmp_store():
    with tempfile.TemporaryDirectory() as td:
        yield Store(Path(td) / "eb.db")


def test_schema_version(tmp_store: Store) -> None:
    cur = tmp_store.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
    assert cur.fetchone()["version"] == 1


def test_integrity_check(tmp_store: Store) -> None:
    assert tmp_store.integrity_check() is True


def test_settings_round_trip(tmp_store: Store) -> None:
    tmp_store.execute(
        "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
        ("shaping_factor", "0.95", 1234567890.0),
    )
    tmp_store.commit()
    cur = tmp_store.execute("SELECT value FROM settings WHERE key=?", ("shaping_factor",))
    assert cur.fetchone()["value"] == "0.95"


def test_devices_insert(tmp_store: Store) -> None:
    tmp_store.execute(
        "INSERT INTO devices(device_id, name, mac, ipv4, ipv6, assigned_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("dev-1", "Living Room TV", "aa:bb:cc:dd:ee:ff", "10.200.0.10", None, 1234567890.0),
    )
    tmp_store.commit()
    cur = tmp_store.execute("SELECT name FROM devices WHERE device_id=?", ("dev-1",))
    assert cur.fetchone()["name"] == "Living Room TV"


def test_decisions_hash_chain(tmp_store: Store) -> None:
    tmp_store.execute(
        "INSERT INTO decisions(decision_id, ts, trigger, reason, before_qoe, after_qoe, "
        "evidence, verdict, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("d-1", 1000.0, "policy_change", "test", "{}", "{}", "{}", "kept", "genesis", "hash-1"),
    )
    tmp_store.commit()
    cur = tmp_store.execute("SELECT hash FROM decisions WHERE decision_id=?", ("d-1",))
    assert cur.fetchone()["hash"] == "hash-1"


def test_intents_expiry(tmp_store: Store) -> None:
    tmp_store.execute(
        "INSERT INTO intents(intent_id, device_id, traffic_class, goal, priority, "
        "expires_at, state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("i-1", "dev-1", "call", "boost", 5, 9999999999.0, "active", 1000.0),
    )
    tmp_store.commit()
    cur = tmp_store.execute("SELECT state FROM intents WHERE intent_id=?", ("i-1",))
    assert cur.fetchone()["state"] == "active"


def test_backup_and_restore(tmp_store: Store) -> None:
    tmp_store.execute(
        "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
        ("k1", "v1", 1.0),
    )
    tmp_store.commit()
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "backup.db"
        tmp_store.backup(dest)
        assert dest.exists()
        # restore into a new store
        new_store = Store(Path(td) / "restored.db")
        new_store.restore(dest)
        cur = new_store.execute("SELECT value FROM settings WHERE key=?", ("k1",))
        assert cur.fetchone()["value"] == "v1"
        new_store.close()


def test_retention(tmp_store: Store) -> None:
    import time

    now = time.time()
    tmp_store.execute(
        "INSERT INTO metrics_raw(ts, device_id, metric, value, source) "
        "VALUES (?, ?, ?, ?, ?)", (now - 200000, "dev-1", "rtt", 10.0, "conntrack")
    )
    tmp_store.execute(
        "INSERT INTO metrics_raw(ts, device_id, metric, value, source) "
        "VALUES (?, ?, ?, ?, ?)", (now - 100, "dev-1", "rtt", 11.0, "conntrack")
    )
    tmp_store.commit()
    # retention with raw_seconds=86400 (1 day) should delete the older row
    tmp_store.retention(raw_seconds=86400)
    cur = tmp_store.execute("SELECT count(*) AS c FROM metrics_raw")
    assert cur.fetchone()["c"] == 1


def test_vacuum(tmp_store: Store) -> None:
    tmp_store.vacuum()  # should not raise


def test_close(tmp_store: Store) -> None:
    tmp_store.close()
    # executing after close should raise
    with pytest.raises(Exception):
        tmp_store.execute("SELECT 1")
