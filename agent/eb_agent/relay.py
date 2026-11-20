"""FastAPI WebSocket relay for Experience Budget.

Stateless, ciphertext-only relay.  Agents keep an outbound WSS connection
(NAT-friendly, no port forwarding).  Browsers join a room by agent_id.
The relay sees ciphertext only -- E2E crypto happens at the endpoints.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse

logger = logging.getLogger("eb_agent.relay")

MAX_MESSAGE_SIZE = 1024 * 1024  # 1 MiB — a 1 h /metrics history is ~150 KB sealed
HEARTBEAT_INTERVAL = 30.0
HEARTBEAT_TIMEOUT = 90.0
MAX_CONNECTIONS = 1000
# The agent streams a snapshot + tick event every fast interval (1 s by
# default), so one browser legitimately receives ~120-130 messages per
# minute.  The limit must sit comfortably above the designed event rate
# while still stopping real floods.
RATE_LIMIT_MSGS_PER_MIN = 600


@dataclass
class Presence:
    agent_id: str
    online: bool = False
    last_seen: float = field(default_factory=time.time)
    connected_at: float | None = None


class Connection:
    """One WebSocket connection (agent or browser)."""

    def __init__(self, ws: WebSocket, conn_id: str, kind: str, agent_id: Optional[str] = None) -> None:
        self.ws = ws
        self.conn_id = conn_id
        self.kind = kind  # "agent" or "browser"
        self.agent_id = agent_id
        self.connected_at = time.time()
        self.last_message = time.time()
        self.msg_count = 0
        self.window_start = time.time()
        self.e2e_session: Optional[str] = None


class Relay:
    """WebSocket relay with presence, rate limits, and heartbeat."""

    def __init__(self) -> None:
        self._connections: dict[str, Connection] = {}
        self._agent_conns: dict[str, set[str]] = defaultdict(set)  # agent_id -> conn_ids
        self._presence: dict[str, Presence] = {}
        self._lock = asyncio.Lock()
        self._heartbeat_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        async with self._lock:
            for conn in list(self._connections.values()):
                try:
                    await conn.ws.close(code=status.WS_1001_GOING_AWAY)
                except Exception:
                    pass
            self._connections.clear()
            self._agent_conns.clear()

    # ------------------------------------------------------------------ connection handling

    async def connect(
        self,
        ws: WebSocket,
        kind: str,
        agent_id: Optional[str] = None,
        token: Optional[str] = None,
    ) -> str:
        await ws.accept()
        expected = os.environ.get("RELAY_AGENT_TOKEN", "")
        if kind == "agent" and expected:
            provided = token or ""
            if not hmac.compare_digest(provided, expected):
                await ws.close(code=status.WS_1008_POLICY_VIOLATION)
                raise RuntimeError("invalid relay agent token")
        conn_id = uuid.uuid4().hex
        if len(self._connections) >= MAX_CONNECTIONS:
            await ws.close(code=status.WS_1013_TRY_AGAIN_LATER)
            raise RuntimeError("too many connections")
        conn = Connection(ws, conn_id, kind, agent_id)
        async with self._lock:
            self._connections[conn_id] = conn
            if kind == "agent" and agent_id:
                self._agent_conns[agent_id].add(conn_id)
                self._presence[agent_id] = Presence(
                    agent_id=agent_id,
                    online=True,
                    connected_at=time.time(),
                )
        logger.info(
            "relay connect",
            extra={"conn_id": conn_id, "kind": kind, "agent_id": agent_id},
        )
        if kind == "agent" and agent_id:
            await self._broadcast_presence(agent_id, True)
        return conn_id

    async def disconnect(self, conn_id: str) -> None:
        async with self._lock:
            conn = self._connections.pop(conn_id, None)
            if conn is None:
                return
            if conn.kind == "agent" and conn.agent_id:
                self._agent_conns[conn.agent_id].discard(conn_id)
                if not self._agent_conns[conn.agent_id]:
                    self._presence[conn.agent_id] = Presence(
                        agent_id=conn.agent_id, online=False, last_seen=time.time()
                    )
        try:
            await conn.ws.close(code=status.WS_1000_NORMAL_CLOSURE)
        except Exception:
            pass
        if conn.kind == "browser" and conn.agent_id and conn.e2e_session:
            await self._send_to_agent(
                conn.agent_id,
                json.dumps(
                    {
                        "type": "browser_closed",
                        "session": conn.e2e_session,
                        "ts": time.time(),
                    }
                ),
            )
        logger.info(
            "relay disconnect",
            extra={"conn_id": conn_id, "agent_id": conn.agent_id},
        )

    # ------------------------------------------------------------------ message routing

    async def route(self, conn_id: str, message: str) -> None:
        conn = self._connections.get(conn_id)
        if conn is None:
            return
        now = time.time()
        if now - conn.window_start > 60.0:
            conn.window_start = now
            conn.msg_count = 0
        conn.msg_count += 1
        if conn.msg_count > RATE_LIMIT_MSGS_PER_MIN:
            logger.warning(
                "relay rate limit kind=%s count=%d agent=%s",
                conn.kind,
                conn.msg_count,
                conn.agent_id,
            )
            return
        if len(message.encode("utf-8")) > MAX_MESSAGE_SIZE:
            logger.warning(
                "relay message too large",
                extra={"conn_id": conn_id, "agent_id": conn.agent_id},
            )
            return

        conn.last_message = now
        if conn.kind == "browser":
            # Remember the browser's E2E session id (e2e_hello is plaintext by
            # design) so the agent can be told when the socket disappears and
            # the bridge can drop the channel instead of sealing events for a
            # dead browser forever.
            if conn.e2e_session is None and message.startswith('{"type":"e2e_hello"'):
                try:
                    parsed = json.loads(message)
                    session = str(parsed.get("session") or "")
                    if session:
                        conn.e2e_session = session
                except ValueError:
                    pass
        if conn.kind == "agent":
            # Agent -> broadcast to all browsers in this agent's room
            await self._broadcast_to_agent_room(conn.agent_id, message, sender=conn_id)
        elif conn.kind == "browser":
            # Browser -> forward to agent's connection(s)
            if conn.agent_id:
                await self._forward_to_agent(conn.agent_id, message, sender=conn_id)

    async def _send_to_agent(self, agent_id: str, message: str) -> None:
        """Send a control message to the agent-kind connections of an agent."""

        for aid in self._agent_conn_ids(agent_id):
            conn = self._connections.get(aid)
            if conn:
                try:
                    await conn.ws.send_text(message)
                except Exception:
                    await self.disconnect(aid)

    async def _broadcast_to_agent_room(
        self, agent_id: Optional[str], message: str, sender: str
    ) -> None:
        if not agent_id:
            return
        async with self._lock:
            browser_ids = [
                c.conn_id
                for c in self._connections.values()
                if c.kind == "browser" and c.agent_id == agent_id
            ]
        for bid in browser_ids:
            conn = self._connections.get(bid)
            if conn:
                try:
                    await conn.ws.send_text(message)
                except Exception:
                    await self.disconnect(bid)

    async def _forward_to_agent(
        self, agent_id: str, message: str, sender: str
    ) -> None:
        for aid in self._agent_conn_ids(agent_id):
            conn = self._connections.get(aid)
            if conn:
                try:
                    await conn.ws.send_text(message)
                except Exception:
                    await self.disconnect(aid)

    # ------------------------------------------------------------------ presence

    async def _broadcast_presence(self, agent_id: str, online: bool) -> None:
        """Tell browsers in this room that the agent came online/offline."""

        payload = json.dumps(
            {
                "type": "presence",
                "agentId": agent_id,
                "online": online,
                "ts": time.time(),
            },
            separators=(",", ":"),
        )
        async with self._lock:
            browser_ids = [
                c.conn_id
                for c in self._connections.values()
                if c.kind == "browser" and c.agent_id == agent_id
            ]
        for bid in browser_ids:
            conn = self._connections.get(bid)
            if conn:
                try:
                    await conn.ws.send_text(payload)
                except Exception:
                    await self.disconnect(bid)

    def get_presence(self, agent_id: str) -> dict:
        p = self._presence.get(agent_id)
        if p is None:
            return {"agent_id": agent_id, "online": False, "last_seen": 0.0}
        return {
            "agent_id": p.agent_id,
            "online": p.online,
            "last_seen": p.last_seen,
            "connected_at": p.connected_at,
        }

    def online_agents(self) -> list[str]:
        """Agent ids that currently have at least one live agent connection."""

        return sorted(
            {
                c.agent_id
                for c in self._connections.values()
                if c.kind == "agent" and c.agent_id
            }
        )

    def attach_browser_to_agent(self, conn_id: str, agent_id: str) -> bool:
        """Late-bind an agent-less browser connection to an agent room.

        A fresh browser that has never paired does not know an agent id.  When
        exactly one agent is online, joining its room directly is the honest
        zero-config path; the E2E handshake still proves the agent's identity
        before any payload flows.  The browser connection is never added to
        ``_agent_conns``: that index must stay agent-only or the agent's
        replies would be routed back to the browser that sent the request.
        """

        conn = self._connections.get(conn_id)
        if conn is None or conn.kind != "browser":
            return False
        if conn.agent_id:
            return False
        conn.agent_id = agent_id
        return True

    def _agent_conn_ids(self, agent_id: str) -> list[str]:
        """Live agent-kind connection ids for an agent id."""

        return [
            c.conn_id
            for c in self._connections.values()
            if c.kind == "agent" and c.agent_id == agent_id
        ]

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            now = time.time()
            async with self._lock:
                conns = list(self._connections.values())
            for conn in conns:
                if now - conn.last_message > HEARTBEAT_TIMEOUT:
                    await self.disconnect(conn.conn_id)


_relay = Relay()


def create_app() -> FastAPI:
    app = FastAPI(title="EB Relay", version="0.1.0")

    @app.on_event("startup")
    async def _startup():
        await _relay.start()

    @app.on_event("shutdown")
    async def _shutdown():
        await _relay.stop()

    @app.websocket("/ws/{kind}")
    async def ws_endpoint(ws: WebSocket, kind: str, agent_id: Optional[str] = None):
        if kind not in ("agent", "browser"):
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        conn_id = await _relay.connect(ws, kind, agent_id)
        if kind == "browser" and not agent_id:
            # A browser that has not paired yet does not know an agent id.
            # With exactly one agent online, join its room and tell the
            # browser which agent it is talking to; otherwise publish the
            # online list so the UI can offer a choice.
            online = _relay.online_agents()
            if len(online) == 1:
                _relay.attach_browser_to_agent(conn_id, online[0])
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "presence",
                            "agentId": online[0],
                            "online": True,
                            "auto": True,
                            "ts": time.time(),
                        },
                        separators=(",", ":"),
                    )
                )
            elif len(online) > 1:
                await ws.send_text(
                    json.dumps(
                        {"type": "agents", "agents": online, "ts": time.time()},
                        separators=(",", ":"),
                    )
                )
        try:
            while True:
                message = await ws.receive_text()
                await _relay.route(conn_id, message)
        except WebSocketDisconnect:
            pass
        finally:
            await _relay.disconnect(conn_id)

    @app.get("/api/v1/presence/{agent_id}")
    async def presence(agent_id: str):
        return JSONResponse(content=_relay.get_presence(agent_id))

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok", "connections": len(_relay._connections)}

    return app


if __name__ == "__main__":
    import sys

    from uvicorn import Config, Server

    host = sys.argv[1] if len(sys.argv) > 1 else "0.0.0.0"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 7444
    app = create_app()
    import asyncio

    asyncio.run(Server(Config(app, host=host, port=port, log_level="info")).serve())
