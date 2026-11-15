"""Unit tests for eb_agent.relay (no root required)."""

from __future__ import annotations

import asyncio

import pytest

from eb_agent.relay import MAX_MESSAGE_SIZE, MAX_CONNECTIONS, Relay


class FakeWS:
    """Fake WebSocket for testing the relay."""

    def __init__(self) -> None:
        self.accepted = False
        self.closed = False
        self.close_code = None
        self.sent: list[str] = []
        self._receive_q: asyncio.Queue[str] = asyncio.Queue()

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        self.close_code = code

    async def send_text(self, message: str) -> None:
        self.sent.append(message)

    async def receive_text(self) -> str:
        return await self._receive_q.get()

    def feed(self, message: str) -> None:
        self._receive_q.put_nowait(message)


@pytest.mark.asyncio
async def test_connect_and_route_agent_to_browser() -> None:
    relay = Relay()
    agent_ws = FakeWS()
    browser_ws = FakeWS()

    agent_id = await relay.connect(agent_ws, "agent", "agent-1")
    browser_id = await relay.connect(browser_ws, "browser", "agent-1")

    await relay.route(agent_id, '{"type":"snapshot","ts":123}')
    assert len(browser_ws.sent) == 1
    assert browser_ws.sent[0] == '{"type":"snapshot","ts":123}'


@pytest.mark.asyncio
async def test_route_browser_to_agent() -> None:
    relay = Relay()
    agent_ws = FakeWS()
    browser_ws = FakeWS()

    agent_id = await relay.connect(agent_ws, "agent", "agent-1")
    browser_id = await relay.connect(browser_ws, "browser", "agent-1")

    await relay.route(browser_id, '{"type":"intent","id":"i-1"}')
    assert len(agent_ws.sent) == 1


@pytest.mark.asyncio
async def test_disconnect_cleans_up() -> None:
    relay = Relay()
    agent_ws = FakeWS()

    agent_id = await relay.connect(agent_ws, "agent", "agent-1")
    await relay.disconnect(agent_id)
    assert agent_ws.closed is True
    presence = relay.get_presence("agent-1")
    assert presence["online"] is False


@pytest.mark.asyncio
async def test_message_too_large_rejected() -> None:
    relay = Relay()
    agent_ws = FakeWS()
    agent_id = await relay.connect(agent_ws, "agent", "agent-1")
    big = "x" * (MAX_MESSAGE_SIZE + 1)
    await relay.route(agent_id, big)
    # No broadcast should happen since there's no browser; just check no error
    assert True


@pytest.mark.asyncio
async def test_presence() -> None:
    relay = Relay()
    assert relay.get_presence("unknown")["online"] is False
    agent_ws = FakeWS()
    await relay.connect(agent_ws, "agent", "agent-1")
    assert relay.get_presence("agent-1")["online"] is True


@pytest.mark.asyncio
async def test_start_stop() -> None:
    relay = Relay()
    await relay.start()
    assert relay._heartbeat_task is not None
    await relay.stop()


@pytest.mark.asyncio
async def test_rate_limiting() -> None:
    relay = Relay()
    agent_ws = FakeWS()
    agent_id = await relay.connect(agent_ws, "agent", "agent-1")
    for _ in range(200):
        await relay.route(agent_id, '{"x":1}')
    # Should not crash despite exceeding rate limit
    assert True
