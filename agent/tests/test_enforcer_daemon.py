"""Unit tests for eb-enforcerd daemon (no root required; uses FakeTcRunner)."""

from __future__ import annotations

import asyncio
import time

import pytest

from eb_agent.enforcer.catalog import BoundsError, EnforceOp, QdiscSpec
from eb_agent.enforcer.daemon import Enforcerd, EnforcerdConfig, PendingOp
from eb_agent.enforcer.tc import FakeTcRunner, TcController


def _make_daemon(runner: FakeTcRunner | None = None) -> Enforcerd:
    runner = runner or FakeTcRunner()
    controller = TcController(runner)
    config = EnforcerdConfig(
        token="test-token",
        available_interfaces=["eth0", "wan0"],
        dead_man_ttl=5.0,
    )
    return Enforcerd(config=config, controller=controller)


def test_config_defaults() -> None:
    config = EnforcerdConfig()
    assert config.socket_path == "/run/eb-enforcerd.sock"
    assert config.dead_man_ttl == 30.0
    assert config.available_interfaces == []


def test_config_custom() -> None:
    config = EnforcerdConfig(
        token="my-secret",
        available_interfaces=["eth0"],
        dead_man_ttl=10.0,
    )
    assert config.token == "my-secret"
    assert config.available_interfaces == ["eth0"]
    assert config.dead_man_ttl == 10.0


@pytest.mark.asyncio
async def test_apply_succeeds_with_valid_token_and_matching_readback() -> None:
    runner = FakeTcRunner()
    runner.state["eth0"] = {"kind": "cake"}
    daemon = _make_daemon(runner)

    request = {
        "action": "apply",
        "token": "test-token",
        "op_id": "op-1",
        "qdisc": [
            {
                "kind": "cake",
                "interface": "eth0",
                "bandwidth_mbit": 50.0,
                "params": {"diffserv4": "", "dual-srchost": "", "dual-dsthost": ""},
            }
        ],
    }
    response = await daemon._dispatch(request)
    assert response["ok"] is True
    assert response["op_id"] == "op-1"
    assert response["verdict"] in {"applied", "applied_unverified"}


@pytest.mark.asyncio
async def test_apply_rejects_invalid_token() -> None:
    daemon = _make_daemon()
    request = {
        "action": "apply",
        "token": "wrong-token",
        "op_id": "op-1",
        "qdisc": [{"kind": "cake", "interface": "eth0"}],
    }
    response = await daemon._dispatch(request)
    assert response["ok"] is False
    assert response["error"] == "unauthorized"


@pytest.mark.asyncio
async def test_apply_rejects_bounds_violation() -> None:
    daemon = _make_daemon()
    request = {
        "action": "apply",
        "token": "test-token",
        "op_id": "op-1",
        "qdisc": [
            {
                "kind": "cake",
                "interface": "eth99",  # not in available_interfaces
                "bandwidth_mbit": 50.0,
            }
        ],
    }
    response = await daemon._dispatch(request)
    assert response["ok"] is False
    assert response["error"] == "bounds_violation"


@pytest.mark.asyncio
async def test_apply_dry_run_executes_nothing() -> None:
    runner = FakeTcRunner()
    daemon = _make_daemon(runner)
    request = {
        "action": "apply",
        "token": "test-token",
        "op_id": "op-dry",
        "dry_run": True,
        "qdisc": [{"kind": "cake", "interface": "eth0", "bandwidth_mbit": 50.0}],
    }
    response = await daemon._dispatch(request)
    assert response["ok"] is True
    assert response["verdict"] == "dry_run"
    assert runner.commands == []  # dry-run: no commands to runner


@pytest.mark.asyncio
async def test_apply_rolls_back_on_readback_mismatch() -> None:
    runner = FakeTcRunner()
    runner.state["eth0"] = {"kind": "netem"}  # does not match cake
    daemon = _make_daemon(runner)
    request = {
        "action": "apply",
        "token": "test-token",
        "op_id": "op-1",
        "qdisc": [{"kind": "cake", "interface": "eth0", "bandwidth_mbit": 50.0}],
    }
    response = await daemon._dispatch(request)
    assert response["verdict"] in {"applied", "applied_unverified", "failed", "rolled_back"}
    assert response["ok"] is False
    # rollback commands should have been issued
    assert any(c[:3] == ["qdisc", "del", "dev"] for c in runner.commands)


@pytest.mark.asyncio
async def test_snapshot_returns_state() -> None:
    runner = FakeTcRunner()
    runner.state["eth0"] = {"kind": "cake", "handle": "1:"}
    daemon = _make_daemon(runner)
    response = await daemon._dispatch({"action": "snapshot", "token": "test-token", "op_id": "snap-1"})
    assert response["ok"] is True
    assert response["verdict"] == "snapshot"
    assert "eth0" in response["evidence"]["state"]


@pytest.mark.asyncio
async def test_heartbeat_updates_applied_at() -> None:
    daemon = _make_daemon()
    # manually add a pending op
    pending = PendingOp(
        op_id="pending-1",
        applied_at=time.time() - 10,
        ttl=30.0,
        interface="eth0",
        prior_state={},
    )
    daemon._pending["pending-1"] = pending
    old_at = pending.applied_at

    response = await daemon._dispatch(
        {"action": "heartbeat", "token": "test-token", "pending_op_id": "pending-1"}
    )
    assert response["ok"] is True
    assert pending.applied_at > old_at


@pytest.mark.asyncio
async def test_revert_pending_op() -> None:
    runner = FakeTcRunner()
    daemon = _make_daemon(runner)
    # add pending op
    daemon._pending["op-rev"] = PendingOp(
        op_id="op-rev",
        applied_at=time.time(),
        ttl=30.0,
        interface="eth0",
        prior_state={"eth0": {"tc_args": ["netem"]}},
    )
    response = await daemon._dispatch(
        {"action": "revert", "token": "test-token", "op_id_to_revert": "op-rev"}
    )
    assert response["ok"] is True
    assert response["verdict"] == "reverted"
    assert "op-rev" not in daemon._pending


def test_sweep_dead_man_reverts_expired() -> None:
    runner = FakeTcRunner()
    daemon = _make_daemon(runner)
    # add expired pending op
    old = PendingOp(
        op_id="expired-1",
        applied_at=time.time() - 100,  # well past ttl
        ttl=5.0,
        interface="eth0",
        prior_state={"eth0": {"tc_args": ["netem"]}},
    )
    daemon._pending["expired-1"] = old
    # add fresh pending op
    fresh = PendingOp(
        op_id="fresh-1",
        applied_at=time.time(),
        ttl=30.0,
        interface="eth0",
        prior_state={},
    )
    daemon._pending["fresh-1"] = fresh

    expired = daemon.sweep_dead_man()
    assert "expired-1" in expired
    assert "fresh-1" not in expired
    assert "expired-1" not in daemon._pending
    assert "fresh-1" in daemon._pending


@pytest.mark.asyncio
async def test_unknown_action_rejected() -> None:
    daemon = _make_daemon()
    response = await daemon._dispatch(
        {"action": "frobnicate", "token": "test-token", "op_id": "op-1"}
    )
    assert response["ok"] is False
    assert response["error"] == "unknown_action"


@pytest.mark.asyncio
async def test_constant_time_token_rejects_partial_match() -> None:
    daemon = _make_daemon()
    # token "test-token" vs "test-tokX" - should fail constant-time compare
    request = {
        "action": "apply",
        "token": "test-tokX",
        "op_id": "op-1",
        "qdisc": [{"kind": "cake", "interface": "eth0"}],
    }
    response = await daemon._dispatch(request)
    assert response["ok"] is False
    assert response["error"] == "unauthorized"
