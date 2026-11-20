"""``eb-agentd`` enforcer client: talks to ``eb-enforcerd`` over a Unix socket.

The unprivileged agent daemon uses this client to send *signed* enforcement
requests to the privileged ``eb-enforcerd`` daemon.  Every request carries an
idempotency ``op_id``, a nonce, a timestamp and an HMAC-SHA256 signature over
the canonical request body; the daemon verifies the token and signature in
constant time and rejects replays/stale requests.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from eb_agent.enforcer.catalog import EnforceOp

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
    nft: dict[str, str] = field(default_factory=dict)
    error: str | None = None


def _canonical(request: dict) -> bytes:
    body = {k: v for k, v in request.items() if k not in {"sig", "signature"}}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


class EnforcerClient:
    """Client for the ``eb-enforcerd`` Unix-socket protocol."""

    def __init__(
        self,
        socket_path: str = DEFAULT_SOCKET_PATH,
        token: str = "",
        connect_timeout: float = CONNECT_TIMEOUT,
        request_timeout: float = REQUEST_TIMEOUT,
        decision_log: Any | None = None,
    ) -> None:
        self.socket_path = socket_path
        self.token = token
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self.decision_log = decision_log

    # ------------------------------------------------------------------ request

    def _sign(self, request: dict) -> None:
        request["token"] = self.token
        request["ts"] = time.time()
        request["nonce"] = secrets.token_hex(16)
        request["sig"] = hmac.new(
            self.token.encode("utf-8"), _canonical(request), hashlib.sha256
        ).hexdigest()

    async def _request(self, request: dict) -> dict:
        self._sign(request)

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

    # ------------------------------------------------------------------ decision log

    def _log(
        self,
        *,
        trigger: str,
        reason: str,
        result: ApplyResult,
        before_qoe: dict | None = None,
        after_qoe: dict | None = None,
    ) -> None:
        if self.decision_log is None:
            return
        verdict = {
            "applied": "kept",
            "reverted": "kept",
            "rolled_back": "rolled_back",
            "failed": "escalated",
        }.get(result.verdict, "escalated")
        try:
            self.decision_log.append(
                trigger=trigger,
                reason=reason,
                before_qoe=before_qoe or {},
                after_qoe=after_qoe or {},
                evidence={
                    "op_id": result.op_id,
                    "verdict": result.verdict,
                    "error": result.error,
                    "detail": result.detail,
                    "evidence": result.evidence,
                },
                verdict=verdict,
            )
        except Exception:  # noqa: BLE001 - logging must not break enforcement
            pass

    # ------------------------------------------------------------------ apply

    async def apply(
        self,
        op_id: str,
        qdisc: Sequence[dict],
        *,
        htb: dict | None = None,
        nft: dict | None = None,
        filters: Sequence[dict] | None = None,
        links: Sequence[dict] | None = None,
        dry_run: bool = False,
        ttl_s: int | None = None,
        before_qoe: dict | None = None,
        after_qoe: dict | None = None,
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
        if filters:
            request["filters"] = list(filters)
        if links:
            request["links"] = list(links)
        if ttl_s is not None:
            request["ttl_s"] = int(ttl_s)

        try:
            response = await self._request(request)
        except EnforcerClientError as error:
            result = ApplyResult(ok=False, op_id=op_id, verdict="error", error=str(error))
            self._log(
                trigger="enforcer_apply",
                reason=f"enforcer unreachable while applying {op_id}",
                result=result,
                before_qoe=before_qoe,
                after_qoe=after_qoe,
            )
            return result

        result = ApplyResult(
            ok=bool(response.get("ok")),
            op_id=str(response.get("op_id", op_id)),
            verdict=str(response.get("verdict", "unknown")),
            error=response.get("error"),
            detail=response.get("detail"),
            evidence=response.get("evidence", {}),
        )
        self._log(
            trigger="enforcer_apply",
            reason=f"apply {op_id}: {result.verdict}",
            result=result,
            before_qoe=before_qoe,
            after_qoe=after_qoe,
        )
        return result

    async def apply_op(self, op: EnforceOp, **kwargs: Any) -> ApplyResult:
        """Convenience wrapper that converts an :class:`EnforceOp` to JSON."""

        payload = asdict(op)
        return await self.apply(
            op.op_id,
            payload.get("qdisc", []),
            htb=payload.get("htb"),
            nft=payload.get("nft"),
            filters=payload.get("filters", []),
            links=payload.get("links", []),
            dry_run=bool(payload.get("dry_run", False)),
            ttl_s=payload.get("ttl_s"),
            **kwargs,
        )

    # ------------------------------------------------------------------ snapshot

    async def snapshot(self) -> SnapshotResult:
        try:
            response = await self._request({"action": "snapshot", "op_id": "snapshot"})
        except EnforcerClientError as error:
            return SnapshotResult(ok=False, error=str(error))
        evidence = response.get("evidence", {})
        return SnapshotResult(
            ok=bool(response.get("ok")),
            state=evidence.get("state", {}),
            nft=evidence.get("nft", {}),
            error=response.get("error"),
        )

    # ------------------------------------------------------------------ revert

    async def revert(
        self,
        op_id: str,
        before_qoe: dict | None = None,
        after_qoe: dict | None = None,
    ) -> ApplyResult:
        try:
            response = await self._request(
                {"action": "revert", "op_id": op_id, "op_id_to_revert": op_id}
            )
        except EnforcerClientError as error:
            result = ApplyResult(ok=False, op_id=op_id, verdict="error", error=str(error))
            self._log(
                trigger="enforcer_revert",
                reason=f"enforcer unreachable while reverting {op_id}",
                result=result,
                before_qoe=before_qoe,
                after_qoe=after_qoe,
            )
            return result
        result = ApplyResult(
            ok=bool(response.get("ok")),
            op_id=str(response.get("op_id", op_id)),
            verdict=str(response.get("verdict", "unknown")),
            error=response.get("error"),
            detail=response.get("detail"),
            evidence=response.get("evidence", {}),
        )
        self._log(
            trigger="enforcer_revert",
            reason=f"revert {op_id}: {result.verdict}",
            result=result,
            before_qoe=before_qoe,
            after_qoe=after_qoe,
        )
        return result

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
        filters: Sequence[dict] | None = None,
        links: Sequence[dict] | None = None,
        dry_run: bool = False,
        ttl_s: int | None = None,
    ) -> ApplyResult:
        return asyncio.run(
            self.apply(
                op_id,
                qdisc,
                htb=htb,
                nft=nft,
                filters=filters,
                links=links,
                dry_run=dry_run,
                ttl_s=ttl_s,
            )
        )

    def snapshot_sync(self) -> SnapshotResult:
        return asyncio.run(self.snapshot())

    def revert_sync(self, op_id: str) -> ApplyResult:
        return asyncio.run(self.revert(op_id))

    def heartbeat_sync(self, pending_op_id: str) -> bool:
        return asyncio.run(self.heartbeat(pending_op_id))
