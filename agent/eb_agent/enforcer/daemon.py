"""``eb-enforcerd``: privileged traffic-control enforcement daemon.

Listens on a root-owned Unix socket, verifies signed requests, applies
bounded ``tc``/``ip``/``nft`` operations through the allow-listed catalog,
read-back verifies every change, and auto-reverts on mismatch or failure.

Run as root (CAP_NET_ADMIN + CAP_NET_RAW only).  Never uses ``shell=True``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from eb_agent.enforcer.catalog import (
    BoundsError,
    EnforceOp,
    FilterSpec,
    HtbClass,
    HtbPlan,
    LinkSpec,
    NftEntry,
    NftPlan,
    QdiscSpec,
    validate_enforce_op,
)
from eb_agent.enforcer.tc import (
    SubprocessRunner,
    TcController,
    TcResult,
    render_commands,
)
from eb_agent.enforcer.modes import ALL_MODES, is_read_only

DEFAULT_SOCKET_PATH = "/run/eb-enforcerd.sock"
DEFAULT_SAFE_STATE_PATH = "/var/lib/eb-agent/enforcer-safe-state.json"
SOCKET_MODE = 0o660
TOKEN_BYTES = 32
DEAD_MAN_TTL_DEFAULT = 30.0
REQUEST_SKEW_S = 60.0


class EnforcerdError(RuntimeError):
    """Daemon-level error returned to the client."""


@dataclass
class PendingOp:
    """An applied operation tracked for dead-man revert."""

    op_id: str
    applied_at: float
    ttl: float
    interface: str
    prior_state: dict


@dataclass
class EnforcerdConfig:
    socket_path: str = DEFAULT_SOCKET_PATH
    token: str = field(default_factory=lambda: secrets.token_hex(TOKEN_BYTES))
    dead_man_ttl: float = DEAD_MAN_TTL_DEFAULT
    available_interfaces: Sequence[str] = field(default_factory=list)
    safe_state_path: str | None = None
    require_signature: bool = True
    mode: str = "lab"

    def __post_init__(self) -> None:
        if self.mode not in ALL_MODES:
            raise ValueError(f"unknown topology mode {self.mode!r}")


class Enforcerd:
    """Privileged enforcement daemon."""

    def __init__(
        self,
        config: EnforcerdConfig | None = None,
        controller: TcController | None = None,
    ) -> None:
        self.config = config or EnforcerdConfig()
        self._server: asyncio.AbstractServer | None = None
        self._controller = controller or TcController(SubprocessRunner())
        self._pending: dict[str, PendingOp] = {}
        self._lock = asyncio.Lock()
        self._dead_man_task: asyncio.Task | None = None
        self._seen_nonces: dict[str, float] = {}
        self._idempotent: dict[str, tuple[str, dict]] = {}

    # ------------------------------------------------------------------ start

    async def start(self) -> None:
        path = Path(self.config.socket_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        self._server = await asyncio.start_unix_server(
            self._handle_client, path=str(path)
        )
        os.chmod(path, SOCKET_MODE)
        if os.geteuid() == 0:
            try:
                os.chown(path, 0, -1)
            except OSError:
                pass
        self._dead_man_task = asyncio.create_task(self._dead_man_loop())

    async def stop(self) -> None:
        if self._dead_man_task:
            self._dead_man_task.cancel()
            try:
                await self._dead_man_task
            except asyncio.CancelledError:
                pass
            self._dead_man_task = None
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        path = Path(self.config.socket_path)
        path.unlink(missing_ok=True)

    async def serve_forever(self) -> None:
        await self.start()
        assert self._server
        async with self._server:
            await self._server.serve_forever()

    # ------------------------------------------------------------------ client handling

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            data = await reader.read(65536)
            if not data:
                return
            try:
                request = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as error:
                writer.write(self._envelope(ok=False, error="invalid_json", detail=str(error)))
                await writer.drain()
                return

            response = await self._dispatch(
                request, require_signature=self.config.require_signature
            )
            writer.write(self._envelope(**response))
            await writer.drain()
        finally:
            writer.close()

    def _envelope(
        self,
        *,
        ok: bool,
        op_id: str | None = None,
        verdict: str | None = None,
        error: str | None = None,
        detail: str | None = None,
        evidence: dict | None = None,
        **extra: object,
    ) -> bytes:
        envelope: dict[str, object] = {"ok": ok, "ts": time.time()}
        if op_id is not None:
            envelope["op_id"] = op_id
        if verdict is not None:
            envelope["verdict"] = verdict
        if error is not None:
            envelope["error"] = error
        if detail is not None:
            envelope["detail"] = detail
        if evidence is not None:
            envelope["evidence"] = evidence
        envelope.update(extra)
        return (json.dumps(envelope) + "\n").encode("utf-8")

    # ------------------------------------------------------------------ signatures

    @staticmethod
    def _canonical(request: dict) -> bytes:
        body = {
            key: value
            for key, value in request.items()
            if key not in {"sig", "signature"}
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _signature(self, request: dict) -> str:
        return hmac.new(
            self.config.token.encode("utf-8"),
            self._canonical(request),
            hashlib.sha256,
        ).hexdigest()

    def _verify_signature(self, request: dict) -> tuple[bool, str | None]:
        signature = str(request.get("sig") or request.get("signature") or "")
        if not signature:
            return False, "missing_signature"
        expected = self._signature(request)
        if not hmac.compare_digest(signature, expected):
            return False, "invalid_signature"
        nonce = str(request.get("nonce") or "")
        if not nonce:
            return False, "missing_nonce"
        try:
            ts = float(request.get("ts", 0))
        except (TypeError, ValueError):
            return False, "invalid_timestamp"
        now = time.time()
        if abs(now - ts) > REQUEST_SKEW_S:
            return False, "stale_request"
        if nonce in self._seen_nonces:
            return False, "replayed_request"
        # Prune old nonces so the cache stays bounded.
        cutoff = now - REQUEST_SKEW_S * 2
        for old, seen_at in list(self._seen_nonces.items()):
            if seen_at < cutoff:
                del self._seen_nonces[old]
        self._seen_nonces[nonce] = now
        return True, None

    # ------------------------------------------------------------------ dispatch

    async def _dispatch(self, request: dict, require_signature: bool = False) -> dict:
        if not isinstance(request, dict):
            return dict(ok=False, error="invalid_request", detail="request must be a JSON object")

        op_id = request.get("op_id")
        token = request.get("token", "")

        # Constant-time token check before anything else.
        if not hmac.compare_digest(str(token or ""), self.config.token):
            return dict(ok=False, op_id=op_id, error="unauthorized", detail="invalid token")

        if require_signature and self.config.require_signature:
            valid, reason = self._verify_signature(request)
            if not valid:
                return dict(
                    ok=False, op_id=op_id, error="unauthorized",
                    detail=f"request signature rejected: {reason}",
                )

        action = request.get("action")

        if action == "apply":
            return await self._do_apply(request, op_id)
        if action == "snapshot":
            return await self._do_snapshot(op_id)
        if action == "revert":
            return await self._do_revert(op_id, request.get("op_id_to_revert"))
        if action == "heartbeat":
            return await self._do_heartbeat(op_id, request.get("pending_op_id"))
        if action == "shutdown":
            return dict(ok=True, op_id=op_id, verdict="shutting_down")

        return dict(ok=False, op_id=op_id, error="unknown_action", detail=str(action))

    # ------------------------------------------------------------------ apply

    @staticmethod
    def _fingerprint(request: dict) -> str:
        body = {
            key: value
            for key, value in request.items()
            if key not in {"token", "sig", "signature", "nonce", "ts"}
        }
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _parse_op(self, request: dict, op_id: str | None) -> EnforceOp:
        qdisc_specs = [
            QdiscSpec(
                kind=item.get("kind", ""),
                interface=item.get("interface", ""),
                parent=item.get("parent", "root"),
                handle=item.get("handle"),
                bandwidth_mbit=item.get("bandwidth_mbit"),
                params=item.get("params", {}),
            )
            for item in request.get("qdisc", [])
        ]
        filters = [
            FilterSpec(
                interface=item.get("interface", ""),
                parent=item.get("parent", "ingress"),
                protocol=item.get("protocol", "all"),
                priority=item.get("priority", 1),
                kind=item.get("kind", "matchall"),
                target=item.get("target"),
                mark=item.get("mark"),
                flowid=item.get("flowid"),
                match=item.get("match", "u32 0 0"),
            )
            for item in request.get("filters", [])
        ]
        links = [
            LinkSpec(
                interface=item.get("interface", ""),
                kind=item.get("kind", "ifb"),
                up=bool(item.get("up", True)),
            )
            for item in request.get("links", [])
        ]
        htb_plan = None
        if request.get("htb") is not None:
            htb = request["htb"]
            htb_plan = HtbPlan(
                interface=htb.get("interface", ""),
                root_rate_mbit=htb.get("root_rate_mbit", 0.0),
                classes=[
                    HtbClass(
                        classid=c.get("classid", ""),
                        rate_mbit=c.get("rate_mbit", 0.0),
                        ceil_mbit=c.get("ceil_mbit"),
                        priority=c.get("priority", 0),
                        quantum=c.get("quantum"),
                        mark=c.get("mark"),
                        leaf_qdisc=c.get("leaf_qdisc", htb.get("leaf_qdisc", "fq_codel")),
                    )
                    for c in htb.get("classes", [])
                ],
                leaf_qdisc=htb.get("leaf_qdisc", "fq_codel"),
                default_classid=htb.get("default_classid"),
            )
        nft_plan = None
        if request.get("nft") is not None:
            nft = request["nft"]
            nft_plan = NftPlan(
                table=nft.get("table", "eb_agent"),
                chain=nft.get("chain", "eb_mark"),
                entries=[
                    NftEntry(
                        table=e.get("table", nft.get("table", "eb_agent")),
                        chain=e.get("chain", nft.get("chain", "eb_mark")),
                        handle=e.get("handle"),
                        expr=e.get("expr", ""),
                        mac=e.get("mac"),
                        ipv4=e.get("ipv4"),
                        ipv6=e.get("ipv6"),
                        dscp=e.get("dscp"),
                        fwmark=e.get("fwmark"),
                        direction=e.get("direction", "src"),
                        device_id=e.get("device_id"),
                        traffic_class=e.get("traffic_class"),
                        l3=e.get("l3"),
                    )
                    for e in nft.get("entries", [])
                ],
            )
        return EnforceOp(
            op_id=str(op_id or request.get("op_id", "enforcerd-apply")),
            qdisc=qdisc_specs,
            htb=htb_plan,
            nft=nft_plan,
            filters=filters,
            links=links,
            dry_run=bool(request.get("dry_run", False)),
            ttl_s=request.get("ttl_s"),
        )

    @staticmethod
    def _render(op: EnforceOp) -> list[list[str]]:
        return render_commands(op)

    def _interfaces(self) -> list[str]:
        configured = list(self.config.available_interfaces)
        if configured:
            return configured
        lister = getattr(self._controller.runner, "list_interfaces", None)
        if callable(lister):
            try:
                return list(lister())
            except Exception:  # noqa: BLE001 - discovery must not crash the daemon
                return []
        return []

    async def _do_apply(self, request: dict, op_id: str | None) -> dict:
        fingerprint = self._fingerprint(request)
        cache_key = str(op_id or request.get("op_id") or "")
        if cache_key and cache_key in self._idempotent:
            cached_fingerprint, cached_response = self._idempotent[cache_key]
            if cached_fingerprint != fingerprint:
                return dict(
                    ok=False, op_id=op_id, error="idempotency_conflict",
                    detail="same op_id was used with a different operation",
                )
            replay = dict(cached_response)
            replay["verdict"] = "idempotent_replay"
            return replay

        try:
            op = self._parse_op(request, op_id)
        except (TypeError, ValueError, KeyError) as error:
            return dict(ok=False, op_id=op_id, error="invalid_op", detail=str(error))

        interfaces = self._interfaces()
        try:
            op = validate_enforce_op(op, interfaces)
        except BoundsError as error:
            return dict(ok=False, op_id=op.op_id, error="bounds_violation", detail=str(error))

        if op.dry_run:
            commands = self._render(op)
            response = dict(
                ok=True,
                op_id=op.op_id,
                verdict="dry_run",
                evidence={"commands": commands},
            )
            if cache_key:
                self._idempotent[cache_key] = (fingerprint, response)
            return response

        if is_read_only(self.config.mode):
            return dict(
                ok=False, op_id=op.op_id, error="read_only_mode",
                detail=f"{self.config.mode} mode never changes traffic-control state",
            )

        prior_state = self._controller.capture_state(op)
        self._persist_safe_state(prior_state)

        result: TcResult = self._controller.apply(op, interfaces, prior_state=prior_state)

        if result.rolled_back:
            verdict = "rolled_back"
        elif not result.ok:
            verdict = "failed"
        elif result.readback is None:
            verdict = "applied_unverified"
        else:
            verdict = "applied"

        if result.ok and not result.rolled_back:
            interface = ""
            if op.qdisc:
                interface = op.qdisc[0].interface
            elif op.htb:
                interface = op.htb.interface
            elif op.filters:
                interface = op.filters[0].interface
            if interface:
                ttl = float(op.ttl_s if op.ttl_s is not None else self.config.dead_man_ttl)
                self._pending[op.op_id] = PendingOp(
                    op_id=op.op_id,
                    applied_at=time.time(),
                    ttl=ttl,
                    interface=interface,
                    prior_state=prior_state,
                )

        response = dict(
            ok=result.ok and not result.rolled_back,
            op_id=op.op_id,
            verdict=verdict,
            detail=result.stderr if not result.ok else None,
            evidence={
                "commands": result.all_commands or result.commands,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "readback": result.readback,
                "prior_state": prior_state,
                "rolled_back": result.rolled_back,
            },
        )
        if cache_key:
            self._idempotent[cache_key] = (fingerprint, response)
        return response

    def _persist_safe_state(self, prior_state: dict) -> None:
        path = self.config.safe_state_path
        if not path:
            return
        target = Path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".safe-state-", dir=str(target.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(prior_state, handle, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        except OSError:
            # A disk problem must not leave the daemon unable to revert.
            pass

    # ------------------------------------------------------------------ snapshot / revert / heartbeat

    async def _do_snapshot(self, op_id: str | None) -> dict:
        state: dict[str, object] = {}
        for iface in self._interfaces():
            runner = self._controller.runner
            state[iface] = {
                "root": runner.qdisc_show_json(iface),
                "qdiscs": runner.qdiscs(iface),
                "classes": runner.classes(iface),
                "filters": runner.filters(iface),
            }
        nft: dict[str, str] = {}
        # The agent-owned table is always safe to read.
        for table in ["eb_agent"]:
            text = self._controller.runner.nft_table(table)
            if text:
                nft[table] = text
        return dict(
            ok=True, op_id=op_id, verdict="snapshot",
            evidence={"state": state, "nft": nft},
        )

    async def _do_revert(self, op_id: str | None, op_id_to_revert: str | None) -> dict:
        target = op_id_to_revert or op_id
        pending = self._pending.get(target)
        if not pending:
            return dict(ok=False, op_id=target, error="not_found", detail="no pending op to revert")

        result = self._controller._rollback(pending.prior_state)  # noqa: SLF001
        if target in self._pending:
            del self._pending[target]
        return dict(
            ok=result.ok,
            op_id=target,
            verdict="reverted" if result.ok else "revert_failed",
            evidence={
                "commands": result.all_commands or result.commands,
                "stderr": result.stderr,
            },
        )

    async def _do_heartbeat(self, op_id: str | None, pending_op_id: str | None) -> dict:
        if not pending_op_id:
            return dict(ok=False, op_id=op_id, error="missing_pending_op_id")
        pending = self._pending.get(pending_op_id)
        if not pending:
            return dict(ok=False, op_id=pending_op_id, error="not_found")
        pending.applied_at = time.time()
        return dict(
            ok=True, op_id=pending_op_id, verdict="heartbeat_ok",
            remaining_ttl=pending.ttl,
        )

    # ------------------------------------------------------------------ dead-man sweep

    async def _dead_man_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(1.0)
                reverted = await asyncio.to_thread(self.sweep_dead_man)
                if reverted:
                    import logging

                    logging.getLogger("eb_agent.enforcer").warning(
                        "dead-man switch reverted expired ops: %s", reverted
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - sweep must keep running
                import logging

                logging.getLogger("eb_agent.enforcer").exception("dead-man sweep failed")

    def sweep_dead_man(self) -> list[str]:
        """Revert any pending ops whose TTL has expired.  Returns reverted op ids."""

        now = time.time()
        expired: list[str] = []
        for op_id, pending in list(self._pending.items()):
            if now - pending.applied_at > pending.ttl:
                self._controller._rollback(pending.prior_state)  # noqa: SLF001
                expired.append(op_id)
                del self._pending[op_id]
        return expired


def main() -> int:
    runner = SubprocessRunner()
    interfaces = [
        name for name in runner.list_interfaces()
        if name not in {"lo"} and not name.startswith("ifb")
    ]
    config = EnforcerdConfig(
        socket_path=os.environ.get("EB_ENFORCERD_SOCKET", DEFAULT_SOCKET_PATH),
        token=os.environ.get("EB_ENFORCERD_TOKEN", secrets.token_hex(TOKEN_BYTES)),
        dead_man_ttl=float(os.environ.get("EB_ENFORCERD_TTL", str(DEAD_MAN_TTL_DEFAULT))),
        available_interfaces=interfaces,
        safe_state_path=os.environ.get(
            "EB_ENFORCERD_SAFE_STATE", DEFAULT_SAFE_STATE_PATH
        ),
        mode=os.environ.get("EB_ENFORCERD_MODE", "lab"),
    )
    daemon = Enforcerd(config)
    try:
        asyncio.run(daemon.serve_forever())
    except KeyboardInterrupt:
        pass
    finally:
        asyncio.run(daemon.stop())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
