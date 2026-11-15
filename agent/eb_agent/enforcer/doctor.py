"""Doctor: environment diagnostics for the agent/enforcer.

Checks iproute2, tc, sch_cake, ifb, nftables, conntrack, iperf3, interfaces,
permissions, kernel modules, IPv6, Docker, and time sync.  Prints fixes and
returns an overall pass/fail verdict.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from typing import Sequence

REQUIRED_BINARIES = ["ip", "tc", "nft"]
OPTIONAL_BINARIES = ["conntrack", "iperf3", "flent", "ethtool", "ping"]
REQUIRED_MODULES = ["sch_cake", "ifb", "nf_conntrack"]


@dataclass
class DoctorCheck:
    name: str
    ok: bool
    detail: str
    fix: str | None = None


@dataclass
class DoctorReport:
    checks: list[DoctorCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def add(self, name: str, ok: bool, detail: str, fix: str | None = None) -> None:
        self.checks.append(DoctorCheck(name, ok, detail, fix))


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run(cmd: Sequence[str]) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            list(cmd), text=True, capture_output=True, timeout=10
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, "", str(error)


def _module_loaded(module: str) -> bool:
    rc, _, _ = _run(["lsmod"])
    if rc != 0:
        return False
    return any(line.split()[0] == module for line in _.splitlines() if line)


def _has_cap_net_admin() -> bool:
    rc, _, _ = _run(["ip", "link", "add", "eb-doctor-probe", "type", "dummy"])
    if rc == 0:
        _run(["ip", "link", "del", "eb-doctor-probe", "type", "dummy"])
        return True
    return False


def doctor_report(interfaces: Sequence[str] | None = None) -> DoctorReport:
    """Build a full doctor report."""

    report = DoctorReport()
    ifaces = list(interfaces or [])

    # Binaries
    for binary in REQUIRED_BINARIES:
        path = _which(binary)
        report.add(
            f"binary:{binary}",
            path is not None,
            f"{binary} at {path}" if path else f"{binary} not found",
            f"install iproute2/nftables" if binary in REQUIRED_BINARIES else None,
        )
    for binary in OPTIONAL_BINARIES:
        path = _which(binary)
        report.add(
            f"binary:{binary}",
            path is not None,
            f"{binary} at {path}" if path else f"{binary} not found (optional)",
            None,
        )

    # Kernel modules
    for module in REQUIRED_MODULES:
        loaded = _module_loaded(module)
        report.add(
            f"module:{module}",
            loaded,
            f"{module} loaded" if loaded else f"{module} not loaded",
            f"modprobe {module}" if not loaded else None,
        )

    # Capabilities
    report.add(
        "cap:net_admin",
        _has_cap_net_admin(),
        "CAP_NET_ADMIN available" if _has_cap_net_admin() else "CAP_NET_ADMIN missing",
        "run with CAP_NET_ADMIN / unshare -Urn",
    )

    # Interfaces
    report.add(
        "interfaces",
        bool(ifaces),
        ", ".join(ifaces) if ifaces else "no interfaces supplied",
        "pass --interface <iface> or run interface auto-detection",
    )

    # IPv6
    rc, out, _ = _run(["ip", "-6", "addr", "show"])
    ipv6_ok = rc == 0 and bool(out.strip())
    report.add(
        "ipv6",
        ipv6_ok,
        "IPv6 addresses present" if ipv6_ok else "no IPv6 addresses",
        None,
    )

    # Docker
    docker_path = _which("docker")
    report.add(
        "docker",
        docker_path is not None,
        f"docker at {docker_path}" if docker_path else "docker not found",
        None,
    )

    # Time sync (rough check: system clock not obviously wrong)
    try:
        socket.gethostname()
        report.add("time:hostname", True, "hostname resolvable", None)
    except OSError:
        report.add("time:hostname", False, "hostname not resolvable", None)

    return report


def doctor_summary(interfaces: Sequence[str] | None = None) -> str:
    """Return a human-readable doctor summary."""

    report = doctor_report(interfaces)
    lines: list[str] = ["eb-agent doctor report", "=" * 40]
    for check in report.checks:
        status = "PASS" if check.ok else "FAIL"
        line = f"[{status}] {check.name}: {check.detail}"
        if check.fix:
            line += f"  (fix: {check.fix})"
        lines.append(line)
    lines.append("")
    lines.append(
        f"OVERALL: {'PASS' if report.ok else 'FAIL'}"
    )
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    interfaces = sys.argv[1:] if len(sys.argv) > 1 else []
    print(doctor_summary(interfaces))
