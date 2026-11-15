"""``eb-agentd`` enforcer client: talks to ``eb-enforcerd`` over a Unix socket.

The unprivileged agent daemon uses this client to send signed enforcement
requests to the privileged ``eb-enforcerd`` daemon.  All requests carry an
idempotency op_id and a token verified in constant time by the daemon.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from eb_agent.enforcer.catalog import EnforceOp, QdiscSpec
from eb_agent.enforcer.tc import TcResult

DEFAULT_SOCKET_PATH = "/run/eb-enforcerd.sock"
CONNECT_TIMEOUT = 5.0
REQUEST_TIMEOUT = 30.0


class EnforcerClientError(RuntimeError):
    """Raised when the enforcer client cannot reach or talk to the daemon."""


@dataclass
class ApplyResult:
    ok: bool
    op_id: str
    verdict: str
    error: str | None = None
    detail: str | None = None
    evidence: dict = field(default_factory=dict)


@dataclass
class SnapshotResult:
    ok: bool
    state: dict[str, dict] = field(default_factory=dict)
    error: str | None = None


class EnforcerClient:
    """Client for the ``eb-enforcerd`` Unix-socket protocol."""

    def __init__(
        self,
        socket_path: str = DEFAULT_SOCKET_PATH,
        token: str = "",
        connect_timeout: float = CONNECT_TIMEOUT,
        request_timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        self.socket_path = socket_path
        self.token = token
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout

    # ------------------------------------------------------------------ request

    async def _request(self, request: dict) -> dict:
        request["token"] = self.token
        request["ts"] = time.time()

        reader: asyncio.StreamReader | None = None
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.socket_path),
                timeout=self.connect_timeout,
            )
            writer.write((json.dumps(request) + "\n").encode("utf-8"))
            await writer.drain()

            line = await asyncio.wait_for(reader.readline(), timeout=self.request_timeout)
            if not line:
                raise EnforcerClientError("daemon closed connection without response")
            return json.loads(line.decode("utf-8"))
        except (asyncio.TimeoutError, OSError, json.JSONDecodeError) as error:
            raise EnforcerClientError(f"enforcerd request failed: {error}") from error
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

    # ------------------------------------------------------------------ apply

    async def apply(
        self,
        op_id: str,
        qdisc: Sequence[dict],
        *,
        htb: dict | None = None,
        nft: dict | None = None,
        dry_run: bool = False,
    ) -> ApplyResult:
        request: dict = {
            "action": "apply",
            "op_id": op_id,
            "qdisc": list(qdisc),
            "dry_run": dry_run,
        }
        if htb is not None:
            request["htb"] = htb
        if nft is not None:
            request["nft"] = nft

        try:
            response = await self._request(request)
        except EnforcerClientError as error:
            return ApplyResult(ok=False, op_id=op_id, verdict="error", error=str(error))

        return ApplyResult(
            ok=bool(response.get("ok")),
            op_id=str(response.get("op_id", op_id)),
            verdict=str(response.get("verdict", "unknown")),
            error=response.get("error"),
            detail=response.get("detail"),
            evidence=response.get("evidence", {}),
        )

    # ------------------------------------------------------------------ snapshot

    async def snapshot(self) -> SnapshotResult:
        try:
            response = await self._request({"action": "snapshot", "op_id": "snapshot"})
        except EnforcerClientError as error:
            return SnapshotResult(ok=False, error=str(error))
        return SnapshotResult(
            ok=bool(response.get("ok")),
            state=response.get("evidence", {}).get("state", {}),
            error=response.get("error"),
        )

    # ------------------------------------------------------------------ revert

    async def revert(self, op_id: str) -> ApplyResult:
        try:
            response = await self._request(
                {"action": "revert", "op_id": op_id, "op_id_to_revert": op_id}
            )
        except EnforcerClientError as error:
            return ApplyResult(ok=False, op_id=op_id, verdict="error", error=str(error))
        return ApplyResult(
            ok=bool(response.get("ok")),
            op_id=str(response.get("op_id", op_id)),
            verdict=str(response.get("verdict", "unknown")),
            error=response.get("error"),
            detail=response.get("detail"),
            evidence=response.get("evidence", {}),
        )

    # ------------------------------------------------------------------ heartbeat

    async def heartbeat(self, pending_op_id: str) -> bool:
        try:
            response = await self._request(
                {"action": "heartbeat", "op_id": "heartbeat", "pending_op_id": pending_op_id}
            )
            return bool(response.get("ok"))
        except EnforcerClientError:
            return False

    # ------------------------------------------------------------------ sync wrappers

    def apply_sync(
        self,
        op_id: str,
        qdisc: Sequence[dict],
        *,
        htb: dict | None = None,
        nft: dict | None = None,
        dry_run: bool = False,
    ) -> ApplyResult:
        return asyncio.run(
            self.apply(op_id, qdisc, htb=htb, nft=nft, dry_run=dry_run)
        )

    def snapshot_sync(self) -> SnapshotResult:
        return asyncio.run(self.snapshot())

    def revert_sync(self, op_id: str) -> ApplyResult:
        return asyncio.run(self.revert(op_id))

    def heartbeat_sync(self, pending_op_id: str) -> bool:
        return asyncio.run(self.heartbeat(pending_op_id))
