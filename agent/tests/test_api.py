"""Unit tests for eb_agent.api (no root required)."""

from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient

from eb_agent.api import create_app
from eb_agent.config import AgentConfig
from eb_agent.core import AgentCore
from eb_agent.store import Store


@pytest.fixture
def test_client():
    cfg = AgentConfig(mode="gateway", shaping_factor=0.8)
    store = Store(tempfile.mktemp(suffix=".db"))
    core = AgentCore(cfg, store)
    app = create_app(cfg, core, store)
    client = TestClient(app)
    yield client, store
    store.close()


def test_capabilities(test_client):
    client, _ = test_client
    r = client.get("/api/v1/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert "flags" in body
    assert "classes" in body
    assert "call" in body["classes"]
    assert "formulas" in body


def test_snapshot_etag(test_client):
    client, _ = test_client
    r = client.get("/api/v1/state/snapshot")
    assert r.status_code == 404  # no snapshot yet until a slow tick runs


def test_snapshot_after_tick(test_client):
    client, store = test_client
    from eb_agent.core import AgentCore
    from eb_agent.config import AgentConfig
    from eb_agent.api import create_app
    cfg = AgentConfig(mode="lab")
    core = AgentCore(cfg, store)
    import asyncio
    asyncio.run(core._slow_tick())
    app = create_app(cfg, core, store)
    c = TestClient(app)
    r = c.get("/api/v1/state/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "adaptive"
    assert "link" in body and "household" in body
    assert isinstance(body["devices"], list)


def test_devices(test_client):
    client, _ = test_client
    r = client.get("/api/v1/devices")
    assert r.status_code == 200
    assert r.json() == []


def test_flows(test_client):
    client, _ = test_client
    r = client.get("/api/v1/flows")
    assert r.status_code == 200
    assert r.json() == []


def test_intents(test_client):
    client, _ = test_client
    r = client.get("/api/v1/intents")
    assert r.status_code == 200
    assert r.json() == []


def test_decisions(test_client):
    client, _ = test_client
    r = client.get("/api/v1/decisions")
    assert r.status_code == 200
    assert r.json() == []


def test_settings(test_client):
    client, _ = test_client
    r = client.get("/api/v1/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["rateChangePct"] == 20
    assert body["floorMbps"] == 2
    assert "flags" in body


def test_put_settings(test_client):
    client, _ = test_client
    r = client.put("/api/v1/settings", json={"mode": "bridge"})
    assert r.status_code == 200
    body = r.json()
    assert body["floorMbps"] == 2
    assert body["rateChangePct"] == 20
    assert "flags" in body


def test_safe_state_reset(test_client):
    client, _ = test_client
    r = client.post("/api/v1/safe-state/reset")
    assert r.status_code == 200
    assert r.json()["verdict"] == "reset_to_safe_state"


def test_ml_metrics(test_client):
    client, _ = test_client
    r = client.get("/api/v1/ml/metrics")
    assert r.status_code == 200


def test_qoe_formulas(test_client):
    client, _ = test_client
    r = client.get("/api/v1/qoe/formulas")
    assert r.status_code == 200
    assert "call" in r.json()


def test_agent_info(test_client):
    client, _ = test_client
    r = client.get("/api/v1/agent/info")
    assert r.status_code == 200
    assert r.json()["version"] == "0.1.0"


def test_health_diagnosis(test_client):
    client, _ = test_client
    r = client.get("/api/v1/health/diagnosis")
    assert r.status_code == 200


def test_internal_metrics(test_client):
    client, _ = test_client
    r = client.get("/api/v1/metrics/internal")
    assert r.status_code == 200


def test_create_intent(test_client):
    client, _ = test_client
    r = client.post(
        "/api/v1/intents",
        json={"device_id": "dev-1", "traffic_class": "call", "goal": "boost", "priority": 5},
    )
    assert r.status_code == 200
    assert "id" in r.json()


def test_links(test_client):
    client, _ = test_client
    r = client.get("/api/v1/links")
    assert r.status_code == 200


def test_paths_evaluate(test_client):
    client, _ = test_client
    r = client.post("/api/v1/paths/evaluate")
    assert r.status_code == 200
