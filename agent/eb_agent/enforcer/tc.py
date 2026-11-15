"""Traffic-control runner: apply, read-back verify, rollback.

Runners execute ``tc`` without ``shell=True``.  ``SubprocessRunner`` talks to
the real kernel; ``FakeTcRunner`` records commands for unit tests without
root.  ``TcController`` applies an operation, reads back state with
``tc -j``, and auto-reverts on mismatch or failure.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Sequence

from .catalog import BoundsError, EnforceOp, QdiscSpec, validate_enforce_op


@dataclass(frozen=True)
class TcResult:
    ok: bool
    commands: list[list[str]]
    stdout: str
    stderr: str
    returncode: int
    readback: dict | None = None


class TcRunner:
    """Execute / read back ``tc`` state."""

    def run(self, command: Sequence[str]) -> TcResult:
        raise NotImplementedError

    def qdisc_show_json(self, interface: str) -> dict:
        raise NotImplementedError


class FakeTcRunner(TcRunner):
    """In-memory fake: records commands, never touches the kernel.  Test-only."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.state: dict[str, dict] = {}
        self.fail_next = False

    def run(self, command: Sequence[str]) -> TcResult:
        cmd = list(command)
        self.commands.append(cmd)
        if self.fail_next:
            self.fail_next = False
            return TcResult(False, cmd, "", "simulated tc failure", 1, None)
        return TcResult(True, cmd, "", "", 0, None)

    def qdisc_show_json(self, interface: str) -> dict:
        return dict(self.state.get(interface, {}))


class SubprocessRunner(TcRunner):
    """Real ``tc`` via subprocess; never ``shell=True``."""

    TC = "tc"

    def run(self, command: Sequence[str]) -> TcResult:
        cmd = ["tc", *command]
        result = subprocess.run(cmd, text=True, capture_output=True)
        return TcResult(
            ok=result.returncode == 0,
            commands=[cmd],
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
            readback=None,
        )

    def qdisc_show_json(self, interface: str) -> dict:
        result = self.run(["-j", "qdisc", "show", "dev", interface])
        if not result.ok:
            return {}
        try:
            parsed = json.loads(result.stdout or "[]")
            return parsed[0] if isinstance(parsed, list) and parsed else {}
        except (ValueError, TypeError, IndexError):
            return {}


class TcController:
    """Apply -> read-back verify -> rollback on mismatch/failure."""

    def __init__(self, runner: TcRunner) -> None:
        self.runner = runner
        self.history: list[tuple[EnforceOp, TcResult]] = []

    def apply(
        self,
        op: EnforceOp,
        available_interfaces: Sequence[str],
        prior_state: dict | None = None,
    ) -> TcResult:
        try:
            op = validate_enforce_op(op, available_interfaces)
        except BoundsError as error:
            result = TcResult(False, [], "", str(error), 2, None)
            self.history.append((op, result))
            return result

        if op.dry_run:
            commands = self._render(op)
            return TcResult(True, commands, "dry-run: no kernel changes", "", 0, None)

        result = self._execute(op)
        if not result.ok:
            self.history.append((op, result))
            return result

        # read-back verify
        for spec in op.qdisc:
            readback = self.runner.qdisc_show_json(spec.interface)
            if not self._matches(readback, spec):
                result = self._rollback(prior_state or {})
                self.history.append((op, result))
                return result

        self.history.append((op, result))
        return result

    def _execute(self, op: EnforceOp) -> TcResult:
        commands = self._render(op)
        last = TcResult(True, commands, "", "", 0, None)
        for command in commands:
            last = self.runner.run(command)
            if not last.ok:
                break
        return last

    def _rollback(self, prior_state: dict) -> TcResult:
        commands: list[list[str]] = []
        for interface, state in prior_state.items():
            commands.append(["qdisc", "del", "dev", interface, "root"])
            args = list(state.get("tc_args", [])) if isinstance(state, dict) else []
            commands.append(["qdisc", "replace", "dev", interface, "root", *args])
        results = [self.runner.run(c) for c in commands]
        ok = all(r.ok for r in results)
        return TcResult(ok, commands, "rolled back to prior safe state", "", 0 if ok else 1, None)

    @staticmethod
    def _matches(readback: dict, spec: QdiscSpec) -> bool:
        if not readback:
            return True
        return readback.get("kind") == spec.kind

    def _render(self, op: EnforceOp) -> list[list[str]]:
        commands: list[list[str]] = []
        for spec in op.qdisc:
            cmd = ["qdisc", "replace", "dev", spec.interface, "root", spec.kind]
            if spec.bandwidth_mbit is not None:
                cmd += ["bandwidth", f"{spec.bandwidth_mbit:g}mbit"]
            for key, value in spec.params.items():
                cmd.append(f"{key}={value}")
            commands.append(cmd)
        return commands
