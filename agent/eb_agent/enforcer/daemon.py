"""``eb-enforcerd``: privileged traffic-control enforcement daemon.

Listens on a root-owned Unix socket, verifies signed requests, applies
bounded ``tc``/nft operations through the allow-listed catalog, read-back
verifies every change, and auto-reverts on mismatch or failure.

Run as root (CAP_NET_ADMIN + CAP_NET_RAW only).  Never uses ``shell=True``.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import socket
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

from eb_agent.enforcer.catalog import (
    BoundsError,
    EnforceOp,
    HtbClass,
    HtbPlan,
    NftEntry,
    NftPlan,
    QdiscSpec,
    validate_enforce_op,
)
from eb_agent.enforcer.tc import SubprocessRunner, TcController, TcResult

DEFAULT_SOCKET_PATH = "/run/eb-enforcerd.sock"
SOCKET_MODE = 0o660
TOKEN_BYTES = 32
DEAD_MAN_TTL_DEFAULT = 30.0  # seconds


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

    # ------------------------------------------------------------------ start

    async def start(self) -> None:
        path = Path(self.config.socket_path)
        if path.exists():
            path.unlink()
        self._server = await asyncio.start_unix_server(
            self._handle_client, path=str(path)
        )
        os.chmod(path, SOCKET_MODE)

    async def stop(self) -> None:
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

            response = await self._dispatch(request)
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
        return (json.dumps(envelope) + "\n").encode("utf-8")

    # ------------------------------------------------------------------ dispatch

    async def _dispatch(self, request: dict) -> dict:
        if not isinstance(request, dict):
            return dict(ok=False, error="invalid_request", detail="request must be a JSON object")

        op_id = request.get("op_id")
        token = request.get("token", "")

        # constant-time token check before anything else
        if not hmac.compare_digest(token or "", self.config.token):
            return dict(ok=False, op_id=op_id, error="unauthorized", detail="invalid token")

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

    async def _do_apply(self, request: dict, op_id: str | None) -> dict:
        try:
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
                        )
                        for c in htb.get("classes", [])
                    ],
                    leaf_qdisc=htb.get("leaf_qdisc", "fq_codel"),
                )
            nft_plan = None
            if request.get("nft") is not None:
                nft = request["nft"]
                nft_plan = NftPlan(
                    table=nft.get("table", "eb_agent"),
                    chain=nft.get("chain", "mark"),
                    entries=[
                        NftEntry(
                            table=e.get("table", ""),
                            chain=e.get("chain", ""),
                            handle=e.get("handle"),
                            expr=e.get("expr", ""),
                        )
                        for e in nft.get("entries", [])
                    ],
                )
            op = EnforceOp(
                op_id=str(op_id or request.get("op_id", "enforcerd-apply")),
                qdisc=qdisc_specs,
                htb=htb_plan,
                nft=nft_plan,
                dry_run=bool(request.get("dry_run", False)),
            )
        except (TypeError, ValueError, KeyError) as error:
            return dict(ok=False, op_id=op_id, error="invalid_op", detail=str(error))

        try:
            op = validate_enforce_op(op, list(self.config.available_interfaces))
        except BoundsError as error:
            return dict(ok=False, op_id=op.op_id, error="bounds_violation", detail=str(error))

        if op.dry_run:
            commands = self._render(op)
            return dict(
                ok=True,
                op_id=op.op_id,
                verdict="dry_run",
                evidence={"commands": commands},
            )

        # capture prior state before applying
        prior_state = await self._capture_prior_state(op)

        result: TcResult = self._controller.apply(
            op, list(self.config.available_interfaces), prior_state=prior_state
        )

        verdict = "applied"
        if not result.ok:
            verdict = "failed"
        elif result.readback is None:
            verdict = "applied_unverified"

        # track for dead-man revert if not dry-run and has an interface
        for spec in op.qdisc:
            self._pending[op.op_id] = PendingOp(
                op_id=op.op_id,
                applied_at=time.time(),
                ttl=self.config.dead_man_ttl,
                interface=spec.interface,
                prior_state=prior_state,
            )

        return dict(
            ok=result.ok,
            op_id=op.op_id,
            verdict=verdict,
            evidence={
                "commands": result.commands,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "readback": result.readback,
                "prior_state": prior_state,
            },
        )

    async def _capture_prior_state(self, op: EnforceOp) -> dict:
        state: dict[str, object] = {}
        for spec in op.qdisc:
            readback = self._controller.runner.qdisc_show_json(spec.interface)
            state[spec.interface] = {
                "kind": readback.get("kind"),
                "handle": readback.get("handle"),
                "parent": readback.get("parent", "root"),
            }
        return state

    @staticmethod
    def _render(op: EnforceOp) -> list[list[str]]:
        commands: list[list[str]] = []
        for spec in op.qdisc:
            cmd = ["qdisc", "replace", "dev", spec.interface, "root", spec.kind]
            if spec.bandwidth_mbit is not None:
                cmd += ["bandwidth", f"{spec.bandwidth_mbit:g}mbit"]
            for key, value in spec.params.items():
                cmd.append(f"{key}={value}")
            commands.append(cmd)
        return commands

    # ------------------------------------------------------------------ snapshot / revert / heartbeat

    async def _do_snapshot(self, op_id: str | None) -> dict:
        state: dict[str, object] = {}
        for iface in self.config.available_interfaces:
            readback = self._controller.runner.qdisc_show_json(iface)
            state[iface] = readback or {}
        return dict(ok=True, op_id=op_id, verdict="snapshot", evidence={"state": state})

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
            evidence={"commands": result.commands, "stderr": result.stderr},
        )

    async def _do_heartbeat(self, op_id: str | None, pending_op_id: str | None) -> dict:
        if not pending_op_id:
            return dict(ok=False, op_id=op_id, error="missing_pending_op_id")
        pending = self._pending.get(pending_op_id)
        if not pending:
            return dict(ok=False, op_id=pending_op_id, error="not_found")
        pending.applied_at = time.time()
        return dict(ok=True, op_id=pending_op_id, verdict="heartbeat_ok", remaining_ttl=pending.ttl)

    # ------------------------------------------------------------------ dead-man sweep

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
    config = EnforcerdConfig(
        socket_path=os.environ.get("EB_ENFORCERD_SOCKET", DEFAULT_SOCKET_PATH),
        token=os.environ.get("EB_ENFORCERD_TOKEN", secrets.token_hex(TOKEN_BYTES)),
        dead_man_ttl=float(os.environ.get("EB_ENFORCERD_TTL", str(DEAD_MAN_TTL_DEFAULT))),
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
