"""Regression tests for frontend-visible API contracts.

These cover bugs where the API returned 200 but the console could not observe
the change: device renames overwritten by discovery, demand deletes that only
disabled rows, safe state that never surfaced in the snapshot, and the static
scenario stop route being shadowed by the dynamic scenario start route.
"""

from __future__ import annotations

import tempfile
import time

import pytest
from fastapi.testclient import TestClient

from eb_agent.api import create_app
from eb_agent.config import AgentConfig
from eb_agent.core import AgentCore, Snapshot
from eb_agent.negotiation import NegotiationService
from eb_agent.store import Store


@pytest.fixture
def api():
    cfg = AgentConfig(mode="lab", shaping_factor=0.8)
    store = Store(tempfile.mktemp(suffix=".db"))
    core = AgentCore(cfg, store)
    core._last_snapshot = Snapshot(
        ts=time.time(),
        mode=cfg.mode,
        devices=[
            {
                "device_id": "host-eth0",
                "name": "This host · eth0",
                "kind": "Host",
                "last_seen": time.time(),
            }
        ],
        flows=[],
        qoe={},
        capacity_mbps=100.0,
        decisions=[],
        intents=[],
        emulated=False,
    )
    app = create_app(cfg, core, store, negotiation=NegotiationService())
    client = TestClient(app)
    yield client, core, store
    store.close()


def test_owner_device_name_survives_discovery(api):
    client, core, _ = api
    r = client.patch("/api/v1/devices/host-eth0", json={"name": "Desk PC"})
    assert r.status_code == 200

    body = client.get("/api/v1/devices").json()
    assert body[0]["name"] == "Desk PC"

    # The next discovery tick writes its own label back into core's snapshot;
    # the owner's override must still win in the API output.
    core._last_snapshot.devices[0]["name"] = "This host · eth0"
    body = client.get("/api/v1/devices").json()
    assert body[0]["name"] == "Desk PC"


def test_demand_schedule_delete_removes_row(api):
    client, _, _ = api
    created = client.post(
        "/api/v1/demand/schedules",
        json={
            "name": "Morning stand-up",
            "device": "host-eth0",
            "time": "09:30",
            "duration": "30 min",
        },
    ).json()
    assert any(s["id"] == created["id"] for s in client.get("/api/v1/demand/schedules").json())

    r = client.delete(f"/api/v1/demand/schedules?id={created['id']}")
    assert r.status_code == 200
    assert not any(
        s["id"] == created["id"] for s in client.get("/api/v1/demand/schedules").json()
    )


def test_safe_state_is_visible_and_restores_previous_mode(api):
    client, core, _ = api
    assert client.post("/api/v1/safe-state/reset").status_code == 200
    snap = client.get("/api/v1/state/snapshot").json()
    assert snap["mode"] == "safe_state"

    assert (
        client.post("/api/v1/safe-state/reset", json={"restore": True}).status_code
        == 200
    )
    snap = client.get("/api/v1/state/snapshot").json()
    assert snap["mode"] == "adaptive"
    assert core.config.mode == "lab"


def test_scenario_stop_is_not_shadowed_by_start_route(api):
    client, _, _ = api
    started = client.post("/api/v1/scenarios/iso-during-call")
    assert started.status_code == 200
    assert client.get("/api/v1/state/snapshot").json()["scenario"] == "iso-during-call"

    stopped = client.post("/api/v1/scenarios/stop")
    assert stopped.status_code == 200
    assert stopped.json()["verdict"] == "scenarios_stopped"
    assert client.get("/api/v1/state/snapshot").json()["scenario"] is None


def test_conflict_get_is_stable_and_resolvable(api):
    client, core, _ = api
    snap = core._last_snapshot
    snap.capacity_mbps = 3.0
    snap.flows = [{"device_id": "host-eth0", "bytes_per_s": 5_000_000.0}]

    first = client.get("/api/v1/conflicts").json()
    second = client.get("/api/v1/conflicts").json()
    assert len(first) == 1
    assert first[0]["status"] == "pending"
    assert first[0]["id"] == second[0]["id"]

    accepted = client.post(f"/api/v1/conflicts/{first[0]['id']}/accept", json={"option": 0})
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    # Once resolved, a later read may surface a fresh pending conflict if the
    # measured pressure is still present, but never a duplicate of the old one.
    later = client.get("/api/v1/conflicts").json()
    if later:
        assert later[0]["id"] != first[0]["id"]
