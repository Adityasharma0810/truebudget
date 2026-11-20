"""Traffic-control runner: render, apply, read-back verify, rollback.

Runners execute ``tc``/``ip``/``nft`` without ``shell=True``.
``SubprocessRunner`` talks to the real kernel; ``FakeTcRunner`` records
commands for unit tests without root.  ``TcController`` applies an operation,
reads the kernel state back (``tc -j``, ``ip -j``, ``nft list``), and
automatically restores the captured prior state on mismatch or failure.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from typing import Sequence

from .catalog import (
    BoundsError,
    EnforceOp,
    FilterSpec,
    HtbClass,
    NftEntry,
    NftPlan,
    QdiscSpec,
    dscp_name,
    format_command,
    validate_enforce_op,
)

TC_TIMEOUT_S = 10.0
RATE_TOLERANCE = 0.03


@dataclass(frozen=True)
class TcResult:
    ok: bool
    commands: list[list[str]]
    stdout: str
    stderr: str
    returncode: int
    readback: dict | None = None
    rolled_back: bool = False
    all_commands: list[list[str]] = field(default_factory=list)

    @property
    def rendered(self) -> str:
        """Human-readable, shell-quoted commands (never executed via shell)."""

        return "\n".join(format_command(c) for c in (self.all_commands or self.commands))


class TcRunner:
    """Execute and read back kernel traffic-control state."""

    def run(self, command: Sequence[str]) -> TcResult:
        raise NotImplementedError

    def run_ip(self, command: Sequence[str]) -> TcResult:
        raise NotImplementedError

    def run_nft(self, command: Sequence[str]) -> TcResult:
        raise NotImplementedError

    def run_nft_script(self, script: str) -> TcResult:
        raise NotImplementedError

    def qdisc_show_json(self, interface: str) -> dict:
        raise NotImplementedError

    def qdiscs(self, interface: str) -> list[dict]:
        raise NotImplementedError

    def classes(self, interface: str) -> list[dict]:
        raise NotImplementedError

    def filters(self, interface: str, parent: str | None = None) -> list[dict]:
        raise NotImplementedError

    def nft_table(self, table: str) -> str:
        raise NotImplementedError

    def link_exists(self, interface: str) -> bool:
        raise NotImplementedError


class FakeTcRunner(TcRunner):
    """In-memory fake: records commands, never touches the kernel.  Test-only.

    Read-back state is deliberately *not* updated by ``run``/``run_ip``: a
    test can therefore model a kernel that silently ignores or alters a
    command and assert that the controller notices the mismatch and restores
    the captured prior state.
    """

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.ip_commands: list[list[str]] = []
        self.nft_commands: list[list[str]] = []
        self.nft_scripts: list[str] = []
        self.state: dict[str, dict] = {}
        self.qdisc_state: dict[str, list[dict]] = {}
        self.class_state: dict[str, list[dict]] = {}
        self.filter_state: dict[str, list[dict]] = {}
        self.nft_state: dict[str, str] = {}
        self.existing_links: set[str] = set()
        self.fail_next = False

    def run(self, command: Sequence[str]) -> TcResult:
        cmd = list(command)
        self.commands.append(cmd)
        if self.fail_next:
            self.fail_next = False
            return TcResult(False, cmd, "", "simulated tc failure", 1, None)
        return TcResult(True, cmd, "", "", 0, None)

    def run_ip(self, command: Sequence[str]) -> TcResult:
        cmd = list(command)
        self.ip_commands.append(cmd)
        if self.fail_next:
            self.fail_next = False
            return TcResult(False, cmd, "", "simulated ip failure", 1, None)
        if len(cmd) >= 3 and cmd[0] == "link" and cmd[1] == "add":
            self.existing_links.add(cmd[2] if cmd[2] != "dev" else cmd[3])
        if len(cmd) >= 3 and cmd[0] == "link" and cmd[1] == "set":
            iface = cmd[3] if cmd[2] == "dev" else cmd[2]
            self.existing_links.add(iface)
        return TcResult(True, cmd, "", "", 0, None)

    def run_nft(self, command: Sequence[str]) -> TcResult:
        cmd = list(command)
        self.nft_commands.append(cmd)
        if self.fail_next:
            self.fail_next = False
            return TcResult(False, cmd, "", "simulated nft failure", 1, None)
        # Minimal simulation so read-back verification can be exercised.
        if len(cmd) >= 4 and cmd[0] == "add" and cmd[1] == "table":
            family, table = cmd[2], cmd[3]
            self.nft_state.setdefault(table, f"table {family} {table} {{\n}}\n")
        elif len(cmd) >= 4 and cmd[0] in {"flush", "delete"} and cmd[1] == "table":
            family, table = cmd[2], cmd[3]
            if cmd[0] == "delete":
                self.nft_state.pop(table, None)
            else:
                self.nft_state[table] = f"table {family} {table} {{\n}}\n"
        elif len(cmd) >= 5 and cmd[0] == "add" and cmd[1] == "chain":
            family, table, chain = cmd[2], cmd[3], cmd[4]
            self.nft_state.setdefault(table, f"table {family} {table} {{\n}}\n")
            if f"chain {chain}" not in self.nft_state[table]:
                self.nft_state[table] += (
                    f"\tchain {chain} {{ type filter hook postrouting priority mangle ; "
                    "policy accept ; }\n"
                )
        elif len(cmd) >= 5 and cmd[0] == "add" and cmd[1] == "rule":
            family, table, chain = cmd[2], cmd[3], cmd[4]
            expr = " ".join(cmd[5:])
            self.nft_state.setdefault(table, f"table {family} {table} {{\n}}\n")
            self.nft_state[table] += f"\t\t{expr}\n"
        return TcResult(True, cmd, "", "", 0, None)

    def run_nft_script(self, script: str) -> TcResult:
        self.nft_scripts.append(script)
        if self.fail_next:
            self.fail_next = False
            return TcResult(False, [], "", "simulated nft script failure", 1, None)
        return TcResult(True, [], "", "", 0, None)

    def qdisc_show_json(self, interface: str) -> dict:
        entries = self.qdiscs(interface)
        for entry in entries:
            if entry.get("root"):
                return dict(entry)
        return dict(entries[0]) if entries else dict(self.state.get(interface, {}))

    def qdiscs(self, interface: str) -> list[dict]:
        if interface in self.qdisc_state:
            return [dict(entry) for entry in self.qdisc_state[interface]]
        state = self.state.get(interface)
        if not state:
            return []
        entry = dict(state)
        entry.setdefault("root", True)
        return [entry]

    def classes(self, interface: str) -> list[dict]:
        return [dict(entry) for entry in self.class_state.get(interface, [])]

    def filters(self, interface: str, parent: str | None = None) -> list[dict]:
        entries = self.filter_state.get(interface, [])
        result: list[dict] = []
        for entry in entries:
            if parent is None or entry.get("parent") == parent:
                item = dict(entry)
                item.setdefault("_parent", parent or "root")
                result.append(item)
        return result

    def nft_table(self, table: str) -> str:
        return self.nft_state.get(table, "")

    def link_exists(self, interface: str) -> bool:
        return interface in self.existing_links


class SubprocessRunner(TcRunner):
    """Real ``tc``/``ip``/``nft`` via subprocess; never ``shell=True``."""

    TC = "tc"
    IP = "ip"
    NFT = "nft"

    def __init__(self, timeout: float = TC_TIMEOUT_S) -> None:
        self.timeout = timeout

    def _run(
        self,
        binary: str,
        command: Sequence[str],
        *,
        stdin: str | None = None,
    ) -> TcResult:
        cmd = [binary, *command]
        try:
            result = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                timeout=self.timeout,
                input=stdin,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return TcResult(False, cmd, "", str(error), 127, None)
        return TcResult(
            ok=result.returncode == 0,
            commands=cmd,
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
            readback=None,
        )

    def run(self, command: Sequence[str]) -> TcResult:
        return self._run(self.TC, command)

    def run_ip(self, command: Sequence[str]) -> TcResult:
        return self._run(self.IP, command)

    def run_nft(self, command: Sequence[str]) -> TcResult:
        return self._run(self.NFT, command)

    def run_nft_script(self, script: str) -> TcResult:
        return self._run(self.NFT, ["-f", "-"], stdin=script)

    @staticmethod
    def _json(result: TcResult) -> list[dict]:
        if not result.ok:
            return []
        try:
            parsed = json.loads(result.stdout or "[]")
        except (ValueError, TypeError):
            return []
        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return [entry for entry in parsed if isinstance(entry, dict)]
        return []

    def qdiscs(self, interface: str) -> list[dict]:
        return self._json(self.run(["-j", "qdisc", "show", "dev", interface]))

    def qdisc_show_json(self, interface: str) -> dict:
        entries = self.qdiscs(interface)
        for entry in entries:
            if entry.get("root"):
                return entry
        return entries[0] if entries else {}

    def classes(self, interface: str) -> list[dict]:
        return self._json(self.run(["-j", "class", "show", "dev", interface]))

    def filters(self, interface: str, parent: str | None = None) -> list[dict]:
        command = ["-j", "filter", "show", "dev", interface]
        if parent:
            if parent in {"ingress", "egress"}:
                command += [parent]
            else:
                command += ["parent", parent]
        entries = self._json(self.run(command))
        for entry in entries:
            entry.setdefault("_parent", parent or entry.get("parent") or "root")
        return entries

    def nft_table(self, table: str) -> str:
        result = self.run_nft(["list", "table", "inet", table])
        return result.stdout if result.ok else ""

    def link_exists(self, interface: str) -> bool:
        return self.run_ip(["-j", "link", "show", "dev", interface]).ok

    def list_interfaces(self) -> list[str]:
        result = self.run_ip(["-j", "link", "show"])
        names: list[str] = []
        for entry in self._json(result):
            name = entry.get("ifname")
            if isinstance(name, str) and name:
                names.append(name)
        return names


# --------------------------------------------------------------------------- rendering


def _format_rate(value: float) -> str:
    return f"{value:g}mbit"


def _qdisc_rate_from_kernel(entry: dict) -> float | None:
    options = entry.get("options") or {}
    raw = options.get("bandwidth")
    if raw is None:
        return None
    try:
        return round(float(raw) * 8.0 / 1_000_000.0, 4)
    except (TypeError, ValueError):
        return None


def _kernel_params(entry: dict) -> list[str]:
    """Best-effort reconstruction of qdisc args from ``tc -j`` options."""

    options = entry.get("options") or {}
    args: list[str] = []
    rate = _qdisc_rate_from_kernel(entry)
    if rate is not None:
        args += ["bandwidth", _format_rate(rate)]
    for flag in ("diffserv4", "diffserv3", "besteffort", "nat", "wash", "ingress", "ack-filter"):
        key = flag.replace("-", "_")
        value = options.get(flag, options.get(key))
        if value is True or (flag == "diffserv4" and options.get("diffserv") == "diffserv4"):
            if flag not in args:
                args.append(flag)
        elif flag == "diffserv3" and options.get("diffserv") == "diffserv3":
            args.append(flag)
        elif flag == "ack-filter" and value == "enabled":
            args.append(flag)
    flowmode = options.get("flowmode")
    if flowmode in {"dual-srchost", "dual-dsthost", "triple-isolate"}:
        args.append(str(flowmode))
    for key in ("rtt", "overhead", "mpu", "fwmark", "limit", "quantum", "latency"):
        value = options.get(key)
        if isinstance(value, (int, float)) and key in {"rtt", "overhead", "mpu", "fwmark"}:
            args += [key, str(value)]
        elif isinstance(value, str) and key in {"latency"}:
            args += [key, value]
    delay = options.get("delay")
    if isinstance(delay, (int, float)) and delay > 0:
        args += ["delay", f"{delay}us"]
    loss = options.get("loss")
    if isinstance(loss, (int, float)) and loss > 0:
        args += ["loss", f"{loss}%"]
    return args


def _render_qdisc(spec: QdiscSpec) -> list[str]:
    if spec.kind == "clsact":
        return ["qdisc", "replace", "dev", spec.interface, "clsact"]
    if spec.kind == "ingress":
        return [
            "qdisc", "replace", "dev", spec.interface,
            "handle", spec.handle or "ffff:", "ingress",
        ]

    command = ["qdisc", "replace", "dev", spec.interface]
    command += [spec.parent]
    if spec.handle:
        command += ["handle", spec.handle]
    command.append(spec.kind)
    if spec.bandwidth_mbit is not None:
        if spec.kind == "tbf" and "rate" not in spec.params:
            command += ["rate", _format_rate(spec.bandwidth_mbit)]
        else:
            command += ["bandwidth", _format_rate(spec.bandwidth_mbit)]
    for key, value in spec.params.items():
        if key in {"diffserv3", "diffserv4", "nat", "dual-srchost", "dual-dsthost",
                   "ack-filter", "ingress", "wash", "besteffort", "no-atm",
                   "split-gso", "ifb-up"}:
            command.append(key)
        else:
            command += [key, str(value)]
    return command


def _render_htb(plan) -> list[list[str]]:
    commands: list[list[str]] = []
    default_classid = plan.default_classid or plan.classes[-1].classid
    default_minor = default_classid.split(":", 1)[-1]
    commands.append(
        ["qdisc", "replace", "dev", plan.interface, "root", "handle", "1:", "htb",
         "default", default_minor]
    )
    for cls in plan.classes:
        ceil = cls.ceil_mbit if cls.ceil_mbit is not None else cls.rate_mbit
        command = [
            "class", "replace", "dev", plan.interface, "parent", "1:",
            "classid", cls.classid, "htb",
            "rate", _format_rate(cls.rate_mbit),
            "ceil", _format_rate(ceil),
            "prio", str(cls.priority),
        ]
        if cls.quantum is not None:
            command += ["quantum", str(cls.quantum)]
        commands.append(command)
        commands.append(
            ["qdisc", "replace", "dev", plan.interface, "parent", cls.classid,
             cls.leaf_qdisc]
        )
    return commands


def _render_filter(spec: FilterSpec) -> list[str]:
    if spec.kind == "matchall":
        return [
            "filter", "add", "dev", spec.interface, spec.parent,
            "protocol", spec.protocol, "pref", str(spec.priority),
            "matchall", "action", "mirred", "egress", "redirect", "dev", str(spec.target),
        ]
    if spec.kind == "fw":
        return [
            "filter", "add", "dev", spec.interface, "parent", spec.parent,
            "protocol", spec.protocol, "pref", str(spec.priority),
            "handle", str(spec.mark), "fw", "flowid", str(spec.flowid),
        ]
    return [
        "filter", "add", "dev", spec.interface, spec.parent,
        "protocol", spec.protocol, "pref", str(spec.priority),
        "u32", "match", *str(spec.match or "u32 0 0").split(),
        "action", "mirred", "egress", "redirect", "dev", str(spec.target),
    ]


def _nft_address_match(entry: NftEntry, l3: str | None) -> str | None:
    direction = "saddr" if entry.direction == "src" else "daddr"
    parts: list[str] = []
    if entry.mac:
        parts.append(f"ether {direction} {entry.mac}")
    if l3 == "ip" and entry.ipv4:
        parts.append(f"ip saddr {entry.ipv4}")
    elif l3 == "ip6" and entry.ipv6:
        parts.append(f"ip6 saddr {entry.ipv6}")
    elif l3 is None:
        if entry.ipv4:
            parts.append(f"ip saddr {entry.ipv4}")
        if entry.ipv6:
            parts.append(f"ip6 saddr {entry.ipv6}")
    return " ".join(parts) or None


def _nft_rules(plan: NftPlan) -> list[tuple[str, list[str]]]:
    """Return ``(rule_text, verification_tokens)`` pairs for an nft plan."""

    rules: list[tuple[str, list[str]]] = []
    for entry in plan.entries:
        if entry.expr:
            rules.append((entry.expr, [entry.expr]))
            continue
        families: list[str | None]
        if entry.l3:
            families = [entry.l3]
        elif entry.ipv4 or entry.ipv6:
            families = [None]
        else:
            families = ["ip", "ip6"]
        for family in families:
            match = _nft_address_match(entry, family)
            if not match:
                continue
            if entry.fwmark is not None:
                rules.append(
                    (f"{match} meta mark set {entry.fwmark}",
                     [match, f"mark set {entry.fwmark}"])
                )
            if entry.dscp is not None:
                if family == "ip6" or entry.l3 == "ip6" or entry.ipv6 is not None:
                    dscp_family = "ip6"
                else:
                    dscp_family = family or "ip"
                rules.append(
                    (f"{match} {dscp_family} dscp set {dscp_name(entry.dscp)}",
                     [match, f"{dscp_family} dscp set {dscp_name(entry.dscp)}"])
                )
    return rules


def render_commands(op: EnforceOp) -> list[list[str]]:
    """Render the exact argv for an operation, including the binary."""

    commands: list[list[str]] = []
    for link in op.links:
        commands.append(["ip", "link", "add", "dev", link.interface, "type", link.kind])
        if link.up:
            commands.append(["ip", "link", "set", "dev", link.interface, "up"])
    for spec in op.qdisc:
        commands.append(["tc", *_render_qdisc(spec)])
    if op.htb is not None:
        commands.extend(["tc", *command] for command in _render_htb(op.htb))
    explicit = {(f.interface, f.parent, f.mark) for f in op.filters}
    for spec in op.filters:
        commands.append(["tc", *_render_filter(spec)])
    if op.htb is not None:
        for cls in op.htb.classes:
            if cls.mark is None:
                continue
            key = (op.htb.interface, "1:", cls.mark)
            if key in explicit:
                continue
            commands.append(
                ["tc", *_render_filter(
                    FilterSpec(
                        interface=op.htb.interface,
                        parent="1:",
                        kind="fw",
                        mark=cls.mark,
                        flowid=cls.classid,
                    )
                )]
            )
    if op.nft is not None:
        commands.append(["nft", "delete", "table", "inet", op.nft.table])
        commands.append(["nft", "add", "table", "inet", op.nft.table])
        commands.append(
            ["nft", "add", "chain", "inet", op.nft.table, op.nft.chain,
             "{ type filter hook postrouting priority mangle ; policy accept ; }"]
        )
        for rule, _tokens in _nft_rules(op.nft):
            commands.append(["nft", "add", "rule", "inet", op.nft.table, op.nft.chain, rule])
    return commands


# --------------------------------------------------------------------------- verification


def _approx(actual: float, expected: float, tolerance: float = RATE_TOLERANCE) -> bool:
    if expected == 0:
        return abs(actual) <= tolerance
    return abs(actual - expected) / abs(expected) <= tolerance


def _root_qdisc(entries: Sequence[dict]) -> dict:
    for entry in entries:
        if entry.get("root"):
            return entry
    return {}


def _find_qdisc(entries: Sequence[dict], spec: QdiscSpec) -> dict:
    if spec.kind in {"clsact", "ingress"}:
        for entry in entries:
            if entry.get("kind") == spec.kind:
                return entry
        return {}
    if spec.parent == "root":
        root = _root_qdisc(entries)
        return root if root.get("kind") == spec.kind else {}
    for entry in entries:
        if entry.get("kind") == spec.kind and entry.get("parent") == spec.parent:
            return entry
    return {}


def _qdisc_matches(entry: dict, spec: QdiscSpec) -> bool:
    if not entry or entry.get("kind") != spec.kind:
        return False
    if spec.handle and entry.get("handle") and entry.get("handle") != spec.handle:
        return False
    if spec.bandwidth_mbit is not None:
        actual = _qdisc_rate_from_kernel(entry)
        if actual is not None and not _approx(actual, spec.bandwidth_mbit):
            return False
    return True


def _class_matches(entry: dict, cls: HtbClass) -> bool:
    if entry.get("handle") != cls.classid:
        return False
    try:
        actual = float(entry.get("rate", 0)) * 8.0 / 1_000_000.0
    except (TypeError, ValueError):
        return False
    return _approx(actual, cls.rate_mbit)


def _filter_matches(entry: dict, spec: FilterSpec) -> bool:
    if entry.get("kind") != spec.kind:
        return False
    if spec.kind == "matchall":
        actions = (entry.get("options") or {}).get("actions") or []
        return any(
            action.get("kind") == "mirred"
            and action.get("to_dev") == spec.target
            and action.get("direction") == "egress"
            for action in actions
        )
    if spec.kind == "fw":
        options = entry.get("options") or {}
        fw = options.get("fw") or {}
        mark = str(fw.get("mark", "")).lower()
        try:
            expected = int(str(spec.mark), 16) if mark.startswith("0x") else int(mark)
        except (TypeError, ValueError):
            return False
        return expected == spec.mark and options.get("classid") == spec.flowid
    return True


def _nft_verified(text: str, tokens: Sequence[str]) -> bool:
    if not text:
        return False
    import re

    normalised = re.sub(
        r"(mark set )0x0*([0-9a-fA-F]+)",
        lambda match: f"{match.group(1)}{int(match.group(2), 16)}",
        text,
    )
    return all(token in normalised for token in tokens)


def verify_op(runner: TcRunner, op: EnforceOp, available_interfaces: Sequence[str]) -> tuple[bool, dict, str]:
    """Read state back from the kernel and prove every requested change."""

    readback: dict = {"interfaces": {}, "nft": {}}
    errors: list[str] = []

    interfaces = sorted({
        *(spec.interface for spec in op.qdisc),
        *( [op.htb.interface] if op.htb else [] ),
        *(spec.interface for spec in op.filters),
    })
    for interface in interfaces:
        qdiscs = runner.qdiscs(interface)
        classes = runner.classes(interface)
        readback["interfaces"][interface] = {"qdiscs": qdiscs, "classes": classes}
        for spec in op.qdisc:
            if spec.interface != interface:
                continue
            entry = _find_qdisc(qdiscs, spec)
            if not _qdisc_matches(entry, spec):
                errors.append(f"qdisc {spec.kind} missing or mismatched on {interface}")
        if op.htb is not None and op.htb.interface == interface:
            root = _root_qdisc(qdiscs)
            if root.get("kind") != "htb":
                errors.append(f"HTB root qdisc missing on {interface}")
            for cls in op.htb.classes:
                if not any(_class_matches(entry, cls) for entry in classes):
                    errors.append(f"HTB class {cls.classid} missing or rate mismatch")
                leaf = [
                    entry for entry in qdiscs
                    if entry.get("parent") == cls.classid
                    and entry.get("kind") == cls.leaf_qdisc
                ]
                if not leaf:
                    errors.append(f"HTB leaf {cls.leaf_qdisc} missing for {cls.classid}")

    for spec in op.filters:
        entries = runner.filters(spec.interface, spec.parent)
        if not any(_filter_matches(entry, spec) for entry in entries):
            errors.append(
                f"filter {spec.kind} missing on {spec.interface} ({spec.parent})"
            )
    if op.htb is not None:
        for cls in op.htb.classes:
            if cls.mark is None:
                continue
            spec = FilterSpec(
                interface=op.htb.interface,
                parent="1:",
                kind="fw",
                mark=cls.mark,
                flowid=cls.classid,
            )
            entries = runner.filters(op.htb.interface, "1:")
            if not any(_filter_matches(entry, spec) for entry in entries):
                errors.append(f"fw filter for mark {cls.mark} missing")

    if op.nft is not None:
        text = runner.nft_table(op.nft.table)
        readback["nft"] = {"table": op.nft.table, "ruleset": text}
        for rule, tokens in _nft_rules(op.nft):
            if not _nft_verified(text, tokens):
                errors.append(f"nft rule not present: {rule}")

    return (not errors), readback, "; ".join(errors)


# --------------------------------------------------------------------------- prior state


def _legacy_restore(interface: str, state: dict) -> list[list[str]]:
    commands = [["qdisc", "del", "dev", interface, "root"]]
    args = list(state.get("tc_args", []))
    if args:
        commands.append(["qdisc", "replace", "dev", interface, "root", *args])
    return commands


def _restore_qdisc_commands(interface: str, entry: dict) -> list[list[str]]:
    kind = str(entry.get("kind") or "")
    if not kind or kind == "noqueue":
        return []
    if kind == "clsact":
        return [["qdisc", "replace", "dev", interface, "clsact"]]
    if kind == "ingress":
        return [
            ["qdisc", "replace", "dev", interface, "handle",
             str(entry.get("handle") or "ffff:"), "ingress"]
        ]
    parent = entry.get("parent") or "root"
    command = ["qdisc", "replace", "dev", interface, str(parent)]
    if entry.get("handle") and str(entry.get("handle")) != "0:":
        command += ["handle", str(entry["handle"])]
    command.append(kind)
    command += _kernel_params(entry)
    return [command]


def _restore_filter_commands(interface: str, entry: dict) -> list[list[str]]:
    kind = entry.get("kind")
    parent = str(entry.get("parent") or entry.get("_parent") or "ingress")
    priority = int(entry.get("pref") or 1)
    if kind == "matchall":
        actions = (entry.get("options") or {}).get("actions") or []
        for action in actions:
            if action.get("kind") == "mirred" and action.get("to_dev"):
                return [
                    ["filter", "add", "dev", interface, parent, "protocol", "all",
                     "pref", str(priority), "matchall", "action", "mirred", "egress",
                     "redirect", "dev", str(action["to_dev"])]
                ]
    if kind == "fw":
        options = entry.get("options") or {}
        fw = options.get("fw") or {}
        mark = str(fw.get("mark") or "")
        classid = str(options.get("classid") or "")
        if mark and classid:
            try:
                numeric = int(mark, 16)
            except ValueError:
                return []
            return [
                ["filter", "add", "dev", interface, "parent", parent,
                 "protocol", "all", "pref", str(priority), "handle", str(numeric),
                 "fw", "flowid", classid]
            ]
    return []


class TcController:
    """Apply -> read-back verify -> rollback on mismatch/failure."""

    def __init__(self, runner: TcRunner) -> None:
        self.runner = runner
        self.history: list[tuple[EnforceOp, TcResult]] = []

    # ------------------------------------------------------------------ public API

    def capture_state(self, op: EnforceOp) -> dict:
        """Capture the kernel state an operation may change."""

        state: dict = {"interfaces": {}, "nft": {}}
        interfaces = {
            *(spec.interface for spec in op.qdisc),
            *(spec.interface for spec in op.filters),
            *(link.interface for link in op.links),
        }
        if op.htb is not None:
            interfaces.add(op.htb.interface)
        for interface in sorted(interfaces):
            filters: list[dict] = []
            seen: set[str] = set()
            for parent in ("ingress", "egress", None):
                for entry in self.runner.filters(interface, parent):
                    key = json.dumps(entry, sort_keys=True, default=str)
                    if key in seen:
                        continue
                    seen.add(key)
                    item = dict(entry)
                    item.setdefault("_parent", parent or entry.get("parent") or "root")
                    filters.append(item)
            state["interfaces"][interface] = {
                "qdiscs": self.runner.qdiscs(interface),
                "classes": self.runner.classes(interface),
                "filters": filters,
            }
        if op.nft is not None:
            state["nft"] = {
                "table": op.nft.table,
                "ruleset": self.runner.nft_table(op.nft.table),
            }
        return state

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
            commands = self._render_all(op)
            return TcResult(
                True, [c[1:] for c in commands if c and c[0] == "tc"],
                "dry-run: no kernel changes", "", 0, None, False, commands,
            )

        captured = prior_state if prior_state is not None else self.capture_state(op)
        result = self._execute(op)
        if not result.ok:
            rolled = self._rollback(captured)
            self.history.append((op, rolled))
            return TcResult(
                rolled.ok,
                rolled.commands,
                f"{rolled.stdout}; apply failed: {result.stderr or result.stdout}",
                "; ".join(part for part in (result.stderr, rolled.stderr) if part),
                rolled.returncode if not rolled.ok else 3,
                None,
                rolled_back=True,
                all_commands=rolled.all_commands,
            )

        ok, readback, detail = verify_op(self.runner, op, available_interfaces)
        if not ok:
            rolled = self._rollback(captured)
            self.history.append((op, rolled))
            return TcResult(
                rolled.ok,
                rolled.commands,
                f"{rolled.stdout}; verification failed: {detail}",
                detail,
                rolled.returncode if not rolled.ok else 3,
                readback,
                rolled_back=True,
                all_commands=rolled.all_commands,
            )

        verified = TcResult(
            True, result.commands, result.stdout, result.stderr, 0, readback,
            False, result.all_commands,
        )
        self.history.append((op, verified))
        return verified

    # ------------------------------------------------------------------ execute

    @staticmethod
    def _render_all(op: EnforceOp) -> list[list[str]]:
        return render_commands(op)

    def _execute(self, op: EnforceOp) -> TcResult:
        commands: list[list[str]] = []
        all_commands: list[list[str]] = []
        for command in self._render_all(op):
            binary, args = command[0], command[1:]
            if binary == "ip" and args[:3] == ["link", "add", "dev"]:
                # ``ip link add`` is not idempotent: skip when it already exists.
                if self.runner.link_exists(args[3]):
                    all_commands.append(command)
                    continue
            if binary == "nft" and args[:2] == ["delete", "table"]:
                # Deleting an absent agent-owned table is a normal first run.
                result = self.runner.run_nft(args)
                all_commands.append(command)
                continue
            if binary == "ip":
                result = self.runner.run_ip(args)
            elif binary == "nft":
                result = self.runner.run_nft(args)
            else:
                result = self.runner.run(args)
                commands.append(args)
            all_commands.append(command)
            if not result.ok:
                return TcResult(
                    False, commands, result.stdout, result.stderr,
                    result.returncode, None, False, all_commands,
                )
        return TcResult(True, commands, "", "", 0, None, False, all_commands)

    # ------------------------------------------------------------------ rollback

    def _rollback(self, prior_state: dict) -> TcResult:
        commands: list[list[str]] = []
        all_commands: list[list[str]] = []
        errors: list[str] = []

        if not prior_state:
            return TcResult(
                True, [], "rolled back to prior safe state (no prior state)", "", 0,
                None, True,
            )

        # Legacy format used by early callers/tests: {"eth0": {"tc_args": [...]}}
        if "interfaces" not in prior_state and "nft" not in prior_state:
            for interface, state in prior_state.items():
                if not isinstance(state, dict):
                    continue
                for command in _legacy_restore(interface, state):
                    result = self.runner.run(command)
                    commands.append(command)
                    all_commands.append(["tc", *command])
                    if not result.ok and "Cannot find" not in result.stderr:
                        errors.append(result.stderr)
            return TcResult(
                not errors, commands, "rolled back to prior safe state",
                "; ".join(errors), 0 if not errors else 1, None, True, all_commands,
            )

        for interface, state in (prior_state.get("interfaces") or {}).items():
            # Remove everything the agent may have created on this interface.
            for cleanup in (
                ["qdisc", "del", "dev", interface, "root"],
                ["qdisc", "del", "dev", interface, "clsact"],
                ["qdisc", "del", "dev", interface, "handle", "ffff:", "ingress"],
            ):
                result = self.runner.run(cleanup)
                commands.append(cleanup)
                all_commands.append(["tc", *cleanup])
            for entry in reversed(state.get("qdiscs") or []):
                if entry.get("root") or entry.get("kind") in {"clsact", "ingress"}:
                    for command in _restore_qdisc_commands(interface, entry):
                        result = self.runner.run(command)
                        commands.append(command)
                        all_commands.append(["tc", *command])
                        if not result.ok:
                            errors.append(result.stderr)
            for entry in state.get("classes") or []:
                try:
                    rate = float(entry.get("rate", 0)) * 8.0 / 1_000_000.0
                    ceil = float(entry.get("ceil", entry.get("rate", 0))) * 8.0 / 1_000_000.0
                except (TypeError, ValueError):
                    continue
                if not entry.get("handle") or rate <= 0:
                    continue
                command = [
                    "class", "replace", "dev", interface, "parent",
                    str(entry.get("parent") or "1:"), "classid", str(entry["handle"]),
                    "htb", "rate", _format_rate(rate), "ceil", _format_rate(ceil),
                    "prio", str(entry.get("prio", 0)),
                ]
                result = self.runner.run(command)
                commands.append(command)
                all_commands.append(["tc", *command])
                if not result.ok:
                    errors.append(result.stderr)
            for entry in state.get("qdiscs") or []:
                if entry.get("root") or entry.get("kind") in {"clsact", "ingress"}:
                    continue
                for command in _restore_qdisc_commands(interface, entry):
                    result = self.runner.run(command)
                    commands.append(command)
                    all_commands.append(["tc", *command])
                    if not result.ok:
                        errors.append(result.stderr)
            for entry in state.get("filters") or []:
                for command in _restore_filter_commands(interface, entry):
                    result = self.runner.run(command)
                    commands.append(command)
                    all_commands.append(["tc", *command])
                    if not result.ok:
                        errors.append(result.stderr)

        nft_state = prior_state.get("nft") or {}
        if nft_state:
            table = str(nft_state.get("table") or "")
            if table:
                delete = ["delete", "table", "inet", table]
                self.runner.run_nft(delete)
                all_commands.append(["nft", *delete])
                ruleset = nft_state.get("ruleset") or ""
                if ruleset.strip():
                    result = self.runner.run_nft_script(ruleset + "\n")
                    all_commands.append(["nft", "-f", "-"])
                    if not result.ok:
                        errors.append(result.stderr)

        return TcResult(
            not errors, commands, "rolled back to prior safe state",
            "; ".join(errors), 0 if not errors else 1, None, True, all_commands,
        )

    # Kept for callers that used the old private helper.
    def _matches(self, readback: dict, spec: QdiscSpec) -> bool:
        if not readback:
            return False
        return _qdisc_matches(readback, spec)
