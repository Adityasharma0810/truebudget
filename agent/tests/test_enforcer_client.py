"""Unit tests for the enforcer client (real Enforcerd + FakeTcRunner on temp socket)."""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from eb_agent.enforcer.client import ApplyResult, EnforcerClient, SnapshotResult
from eb_agent.enforcer.daemon import Enforcerd, EnforcerdConfig
from eb_agent.enforcer.tc import FakeTcRunner, TcController


@pytest.mark.asyncio
async def test_client_apply_through_daemon() -> None:
    runner = FakeTcRunner()
    runner.state["eth0"] = {"kind": "cake"}
    controller = TcController(runner)

    sock_path = os.path.join(tempfile.mkdtemp(), "eb-test.sock")
    config = EnforcerdConfig(
        socket_path=sock_path,
        token="client-token",
        available_interfaces=["eth0", "wan0"],
    )
    daemon = Enforcerd(config=config, controller=controller)

    # start daemon in background
    server = await asyncio.start_unix_server(daemon._handle_client, path=sock_path)
    try:
        client = EnforcerClient(socket_path=sock_path, token="client-token")
        result = await client.apply(
            "op-client-1",
            [{"kind": "cake", "interface": "eth0", "bandwidth_mbit": 50.0}],
        )
        assert isinstance(result, ApplyResult)
        assert result.ok is True
        assert result.op_id == "op-client-1"
    finally:
        server.close()
        await server.wait_closed()
        try:
            os.unlink(sock_path)
        except OSError:
            pass


@pytest.mark.asyncio
async def test_client_apply_wrong_token() -> None:
    runner = FakeTcRunner()
    controller = TcController(runner)

    sock_path = os.path.join(tempfile.mkdtemp(), "eb-test2.sock")
    config = EnforcerdConfig(
        socket_path=sock_path,
        token="server-token",
        available_interfaces=["eth0"],
    )
    daemon = Enforcerd(config=config, controller=controller)

    server = await asyncio.start_unix_server(daemon._handle_client, path=sock_path)
    try:
        client = EnforcerClient(socket_path=sock_path, token="wrong-token")
        result = await client.apply(
            "op-client-2",
            [{"kind": "cake", "interface": "eth0"}],
        )
        assert result.ok is False
        assert result.error == "unauthorized"
    finally:
        server.close()
        await server.wait_closed()
        try:
            os.unlink(sock_path)
        except OSError:
            pass


@pytest.mark.asyncio
async def test_client_snapshot_through_daemon() -> None:
    runner = FakeTcRunner()
    runner.state["eth0"] = {"kind": "cake", "handle": "1:"}
    controller = TcController(runner)

    sock_path = os.path.join(tempfile.mkdtemp(), "eb-test3.sock")
    config = EnforcerdConfig(
        socket_path=sock_path,
        token="tok",
        available_interfaces=["eth0"],
    )
    daemon = Enforcerd(config=config, controller=controller)

    server = await asyncio.start_unix_server(daemon._handle_client, path=sock_path)
    try:
        client = EnforcerClient(socket_path=sock_path, token="tok")
        snap = await client.snapshot()
        assert isinstance(snap, SnapshotResult)
        assert snap.ok is True
        assert "eth0" in snap.state
    finally:
        server.close()
        await server.wait_closed()
        try:
            os.unlink(sock_path)
        except OSError:
            pass


@pytest.mark.asyncio
async def test_client_connection_refused() -> None:
    sock_path = os.path.join(tempfile.mkdtemp(), "nonexistent.sock")
    client = EnforcerClient(socket_path=sock_path, token="tok")
    result = await client.apply("op-x", [{"kind": "cake", "interface": "eth0"}])
    assert result.ok is False
    assert result.verdict == "error"


@pytest.mark.asyncio
async def test_client_revert_through_daemon() -> None:
    runner = FakeTcRunner()
    controller = TcController(runner)

    sock_path = os.path.join(tempfile.mkdtemp(), "eb-test4.sock")
    config = EnforcerdConfig(
        socket_path=sock_path,
        token="tok",
        available_interfaces=["eth0"],
    )
    daemon = Enforcerd(config=config, controller=controller)

    server = await asyncio.start_unix_server(daemon._handle_client, path=sock_path)
    try:
        client = EnforcerClient(socket_path=sock_path, token="tok")

        # first apply an op so it's tracked as pending
        apply_result = await client.apply(
            "op-revert-client",
            [{"kind": "cake", "interface": "eth0", "bandwidth_mbit": 50.0}],
        )
        assert apply_result.ok is True

        # now revert it
        rev = await client.revert("op-revert-client")
        assert rev.ok is True
        assert rev.verdict == "reverted"
    finally:
        server.close()
        await server.wait_closed()
        try:
            os.unlink(sock_path)
        except OSError:
            pass


def test_client_sync_wrappers() -> None:
    """Sync wrappers should not crash when daemon is unavailable."""
    client = EnforcerClient(
        socket_path="/tmp/nonexistent-eb-agentd.sock", token="tok"
    )
    result = client.apply_sync("op-sync", [{"kind": "cake", "interface": "eth0"}])
    assert result.ok is False
    assert result.verdict == "error"

    snap = client.snapshot_sync()
    assert snap.ok is False

    rev = client.revert_sync("op-x")
    assert rev.ok is False

    hb = client.heartbeat_sync("op-x")
    assert hb is False
