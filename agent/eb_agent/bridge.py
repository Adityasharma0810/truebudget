"""Outbound WebSocket relay bridge.

The agent keeps a single outbound WSS connection to the relay (NAT-friendly,
no port forwarding).  Browsers join the agent's room by ``agent_id``.  Every
browser message is sealed with X25519 + HKDF + AES-256-GCM before it reaches
the relay; the bridge decrypts locally, forwards the request to the loopback
API, and seals the response.  The relay never sees plaintext payloads.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from eb_agent.config import AgentConfig
from eb_agent.e2e import E2EError, SecureChannel, derive_key, fingerprint, generate_keypair
from eb_agent.security import AgentIdentity

logger = logging.getLogger("eb_agent.bridge")

APP_VERSION = "0.1.0"
MAX_FRAME_BYTES = 128 * 1024
REQUEST_TIMEOUT_S = 20.0
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SENSITIVE_GETS = (
    "/sessions",
    "/audit",
    "/agent/logs",
    "/metrics/internal",
    "/dev/",
)


def _normalise_api_path(path: str) -> str:
    value = path if path.startswith("/") else f"/{path}"
    if not value.startswith("/api/v1"):
        value = "/api/v1" + value
    return value


def _relay_ws_url(relay_url: str, agent_id: str, token: str = "") -> str:
    raw = relay_url.strip()
    if not raw:
        return ""
    if raw.startswith("https://"):
        raw = "wss://" + raw[len("https://") :]
    elif raw.startswith("http://"):
        raw = "ws://" + raw[len("http://") :]
    elif not raw.startswith(("ws://", "wss://")):
        raw = "wss://" + raw
    parsed = urllib.parse.urlparse(raw)
    path = parsed.path.rstrip("/")
    if not path.endswith("/ws/agent"):
        path = f"{path}/ws/agent" if path else "/ws/agent"
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if key not in {"agent_id", "token"}]
    query.append(("agent_id", agent_id))
    if token:
        query.append(("token", token))
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, path, "", urllib.parse.urlencode(query), "")
    )


class RelayBridge:
    """Maintains the outbound relay connection and per-browser E2E channels."""

    def __init__(
        self,
        config: AgentConfig,
        identity: AgentIdentity,
        *,
        local_url: str | None = None,
        relay_url: str | None = None,
        relay_token: str | None = None,
    ) -> None:
        self.config = config
        self.identity = identity
        self.relay_url = relay_url if relay_url is not None else config.relay_url
        self.relay_token = relay_token if relay_token is not None else config.relay_token
        self.local_url = (
            local_url
            or f"http://127.0.0.1:{getattr(config, 'https_port', 7443)}"
        ).rstrip("/")
        self._channels: dict[str, SecureChannel] = {}
        self._roles: dict[str, str] = {}
        self._tokens: dict[str, str] = {}
        self._ws: Any | None = None
        self._send_lock = asyncio.Lock()
        self._run_task: asyncio.Task | None = None
        self._event_task: asyncio.Task | None = None
        self._stopped = asyncio.Event()
        self._connected = False
        self._last_error: str | None = None

    # ------------------------------------------------------------------ lifecycle

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        if not self.relay_url:
            logger.info("relay bridge disabled (no RELAY_URL configured)")
            return
        self._stopped.clear()
        self._run_task = asyncio.create_task(self._run_forever(), name="eb-relay-bridge")
        self._event_task = asyncio.create_task(self._event_forever(), name="eb-relay-events")
        logger.info(
            "relay bridge starting",
            extra={"agent_id": self.identity.agent_id, "relay": self.relay_url},
        )

    async def stop(self) -> None:
        self._stopped.set()
        for task in (self._run_task, self._event_task):
            if task:
                task.cancel()
        ws = self._ws
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._channels.clear()
        self._connected = False

    async def _run_forever(self) -> None:
        attempt = 0
        while not self._stopped.is_set():
            url = _relay_ws_url(self.relay_url, self.identity.agent_id, self.relay_token)
            if not url:
                await asyncio.sleep(5.0)
                continue
            try:
                import websockets

                async with websockets.connect(
                    url,
                    max_size=MAX_FRAME_BYTES,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    attempt = 0
                    self._last_error = None
                    await self._send_json(
                        {
                            "type": "agent_hello",
                            "agentId": self.identity.agent_id,
                            "agentPub": self.identity.agreement_public_hex(),
                            "version": APP_VERSION,
                        }
                    )
                    logger.info(
                        "relay connected",
                        extra={"agent_id": self.identity.agent_id},
                    )
                    async for raw in ws:
                        await self._handle_relay_message(raw)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - reconnect on any failure
                self._last_error = str(error)
                logger.warning(
                    "relay connection failed: %s",
                    error,
                    extra={"agent_id": self.identity.agent_id},
                )
            finally:
                self._connected = False
                self._ws = None
                self._channels.clear()
                self._roles.clear()
                self._tokens.clear()
            if self._stopped.is_set():
                break
            attempt += 1
            delay = min(30.0, 0.5 * (2 ** min(attempt, 6)))
            delay *= 0.5 + random.random()
            logger.info(
                "relay reconnect in %.1fs",
                delay,
                extra={"agent_id": self.identity.agent_id, "attempt": attempt},
            )
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------ relay messages

    async def _send_json(self, payload: dict) -> None:
        ws = self._ws
        if ws is None:
            return
        text = json.dumps(payload, separators=(",", ":"))
        async with self._send_lock:
            await ws.send(text)

    async def _handle_relay_message(self, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
            return
        try:
            message = json.loads(raw)
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        kind = message.get("type")
        if kind == "ping":
            await self._send_json({"type": "pong", "ts": time.time()})
            return
        if kind == "e2e_hello":
            await self._handle_hello(message)
            return
        if kind == "e2e":
            await self._handle_sealed(message)
            return
        if kind == "presence":
            return
        # Unknown plaintext types are ignored; only sealed payloads may carry
        # requests or state.

    async def _handle_hello(self, message: dict) -> None:
        session = str(message.get("session") or "")
        browser_pub = str(message.get("browserPub") or "")
        if not session or not browser_pub:
            return
        try:
            peer = bytes.fromhex(browser_pub)
            key = derive_key(self.identity.agreement_private_bytes(), peer)
            channel = SecureChannel(key)
        except (ValueError, E2EError):
            logger.warning(
                "invalid E2E hello",
                extra={"agent_id": self.identity.agent_id, "session": session[:12]},
            )
            return
        self._channels[session] = channel
        agent_pub = self.identity.agreement_public_hex()
        await self._send_json(
            {
                "type": "e2e_ready",
                "session": session,
                "agentId": self.identity.agent_id,
                "agentPub": agent_pub,
                "fingerprint": fingerprint(bytes.fromhex(agent_pub)),
            }
        )
        logger.info(
            "E2E channel established",
            extra={"agent_id": self.identity.agent_id, "session": session[:12]},
        )

    async def _handle_sealed(self, message: dict) -> None:
        session = str(message.get("session") or "")
        envelope = message.get("envelope")
        channel = self._channels.get(session)
        if channel is None or not isinstance(envelope, dict):
            return
        try:
            payload = channel.open(envelope)
        except E2EError as error:
            logger.warning(
                "E2E envelope rejected: %s",
                error,
                extra={"agent_id": self.identity.agent_id, "session": session[:12]},
            )
            return
        if not isinstance(payload, dict) or payload.get("type") != "request":
            return
        request_id = str(payload.get("id") or "")
        method = str(payload.get("method") or "GET").upper()
        path = _normalise_api_path(str(payload.get("path") or "/"))
        body = payload.get("body")
        token = str(payload.get("accessToken") or "")

        status, response = await asyncio.to_thread(
            self._local_request, method, path, body, token
        )
        if path == "/api/v1/pair" and status == 200 and isinstance(response, dict):
            role = str(response.get("role") or "owner")
            self._roles[session] = role
            access = response.get("accessToken")
            if access:
                self._tokens[session] = str(access)
        await self._send_sealed(
            session,
            {"type": "response", "id": request_id, "status": status, "body": response},
        )

    async def _send_sealed(self, session: str, payload: dict) -> None:
        channel = self._channels.get(session)
        if channel is None:
            return
        try:
            envelope = channel.seal(payload)
        except E2EError:
            return
        await self._send_json(
            {"type": "e2e", "session": session, "envelope": envelope}
        )

    # ------------------------------------------------------------------ local API

    def _local_request(
        self,
        method: str,
        path: str,
        body: Any,
        token: str,
    ) -> tuple[int, Any]:
        """Forward one request to the loopback API (blocking, run in a thread)."""

        if method != "GET" and path != "/api/v1/pair":
            if not token:
                return 401, {
                    "code": "auth_required",
                    "message": "Pair this browser before changing agent state.",
                }
            session = self._verify_token(token)
            if session is None:
                return 401, {
                    "code": "invalid_session",
                    "message": "The session token is invalid, expired, or revoked.",
                }
            if session.get("role") == "viewer":
                return 403, {
                    "code": "read_only_session",
                    "message": "This session is read-only.",
                }
        elif any(path.startswith(prefix) for prefix in SENSITIVE_GETS):
            if token and self._verify_token(token) is None:
                return 401, {
                    "code": "invalid_session",
                    "message": "The session token is invalid, expired, or revoked.",
                }

        url = f"{self.local_url}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                status = response.status
                raw = response.read()
        except urllib.error.HTTPError as error:
            status = error.code
            raw = error.read()
        except OSError as error:
            return 503, {
                "code": "agent_unreachable",
                "message": f"Local agent API is unreachable: {error}",
            }
        if not raw:
            return status, None
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, {"raw": raw.decode("utf-8", errors="replace")[:2000]}

    def _verify_token(self, token: str) -> dict | None:
        status, body = self._local_request("GET", "/api/v1/session", None, token)
        if status == 200 and isinstance(body, dict):
            return body
        return None

    # ------------------------------------------------------------------ local events

    async def _event_forever(self) -> None:
        while not self._stopped.is_set():
            try:
                await self._event_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                logger.debug("relay event stream failed: %s", error)
            if self._stopped.is_set():
                break
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass

    async def _event_once(self) -> None:
        import websockets

        scheme = "wss" if self.local_url.startswith("https://") else "ws"
        host = urllib.parse.urlparse(self.local_url).netloc
        url = f"{scheme}://{host}/api/v1/stream"
        async with websockets.connect(url, max_size=MAX_FRAME_BYTES) as ws:
            async for raw in ws:
                if self._stopped.is_set():
                    return
                if not self._channels:
                    continue
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                event = str(message.get("event") or "message")
                if event in {"hello", "pong"}:
                    continue
                if event == "snapshot":
                    payload = message.get("snapshot")
                elif event == "tick":
                    payload = {
                        "ts": message.get("ts"),
                        "counters": message.get("counters") or {},
                        "connected": message.get("connected", True),
                    }
                else:
                    payload = message.get("payload", message)
                for session in list(self._channels):
                    await self._send_sealed(
                        session,
                        {"type": "event", "event": event, "payload": payload},
                    )


def bridge_status(bridge: RelayBridge | None) -> dict[str, Any]:
    if bridge is None:
        return {"enabled": False, "connected": False}
    return {
        "enabled": bool(bridge.relay_url),
        "connected": bridge.connected,
        "agentId": bridge.identity.agent_id,
        "channels": len(bridge._channels),
        "lastError": bridge._last_error,
    }
