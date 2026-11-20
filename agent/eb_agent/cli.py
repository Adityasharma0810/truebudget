"""Operator CLI helpers used by the ``eb-agent`` Node wrapper.

Every command operates on the same config, SQLite store and identity files as
the daemon.  Nothing here uses ``sudo`` implicitly and no command downloads or
installs an artifact without an explicit, verified step.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from eb_agent.config import AgentConfig
from eb_agent.security import (
    PairingManager,
    SessionManager,
    generate_identity,
    load_or_create_identity,
    make_pairing_link,
)
from eb_agent.store import Store

APP_VERSION = "0.1.0"


def _config() -> AgentConfig:
    return AgentConfig.load()


def _store(config: AgentConfig) -> Store:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    return Store(config.data_dir / "eb.db")


def _api_url(path: str) -> str:
    host = os.environ.get("EB_AGENT_HOST", "127.0.0.1")
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    port = int(os.environ.get("EB_AGENT_PORT", "7443"))
    return f"http://{host}:{port}{path}"


def _post(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode("utf-8")
    request = urllib.request.Request(
        _api_url(path),
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"agent returned HTTP {error.code}: {detail}") from error
    except OSError as error:
        raise SystemExit(f"agent is not reachable at {_api_url('')}: {error}") from error


def _print_pairing(config: AgentConfig, store: Store, identity) -> None:
    pairing = PairingManager(store)
    secret = pairing.rotate(identity.agent_id, identity.agreement_public_hex())
    site = os.environ.get("EB_SITE_URL", "https://example.com")
    link = (
        f"{site.rstrip('/')}/connect?key={secret.key}"
        f"#pk={identity.agreement_public_hex()}"
    )
    print(f"Agent ID:     {identity.agent_id}")
    print(f"Data dir:     {config.data_dir}")
    print(f"Pairing key:  {secret.key}")
    print(f"Pairing link: {link}")
    print(f"Expires:      {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(secret.expires_at))}")
    print("Single use. The key is shown once and is never written to logs.")


def cmd_init(args: argparse.Namespace) -> int:
    config = _config()
    if args.mode:
        config.mode = args.mode
        config.save()
    if args.docker:
        config.mode = "lab"
        config.save()
    store = _store(config)
    identity = load_or_create_identity(config.data_dir)
    _print_pairing(config, store, identity)
    if args.docker:
        print("Docker/Windows: lab/monitor only; no Windows traffic is shaped.")
    store.close()
    return 0


def cmd_pair(args: argparse.Namespace) -> int:
    config = _config()
    store = _store(config)
    identity = load_or_create_identity(config.data_dir)
    _print_pairing(config, store, identity)
    store.close()
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    config = _config()
    store = _store(config)
    manager = SessionManager(store)
    identity = load_or_create_identity(config.data_dir)
    rows = manager.list_sessions(identity.agent_id)
    if not rows:
        print("No active sessions.")
    for session in rows:
        print(
            f"{session.session_id[:12]:12}  {session.role:6}  "
            f"created={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session.created_at))}  "
            f"expires={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session.expires_at))}"
        )
    store.close()
    return 0


def cmd_rotate_keys(args: argparse.Namespace) -> int:
    config = _config()
    store = _store(config)
    previous = load_or_create_identity(config.data_dir)
    identity = generate_identity(previous.agent_id)
    identity.save(config.data_dir)
    _print_pairing(config, store, identity)
    print("Agreement keys rotated; existing sessions should be revoked from the console.")
    store.close()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    pid_path = _config().data_dir / "eb-agentd.pid"
    pid = None
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid = None
    running = False
    if pid:
        try:
            os.kill(pid, 0)
            running = True
        except OSError:
            running = False
    payload: dict = {
        "running": running,
        "pid": pid,
        "version": APP_VERSION,
        "mode": _config().mode,
        "dataDir": str(_config().data_dir),
    }
    try:
        with urllib.request.urlopen(_api_url("/api/v1/capabilities"), timeout=2.0) as response:
            payload["api"] = "reachable"
            payload["httpStatus"] = response.status
    except OSError:
        payload["api"] = "unreachable"
    print(json.dumps(payload, indent=2))
    return 0 if running else 1


def cmd_stop(args: argparse.Namespace) -> int:
    config = _config()
    pid_path = config.data_dir / "eb-agentd.pid"
    if not pid_path.exists():
        print("Agent is not running (no PID file).")
        return 0
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pid_path.unlink(missing_ok=True)
        print("Removed a corrupt PID file; agent was not signalled.")
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pid_path.unlink(missing_ok=True)
        print("Agent process was already gone.")
        return 0
    deadline = time.time() + 10.0
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            pid_path.unlink(missing_ok=True)
            print(f"Stopped agent (pid {pid}).")
            return 0
        time.sleep(0.2)
    print(f"Agent pid {pid} did not exit within 10s; inspect logs before retrying.")
    return 1


def cmd_safe_reset(args: argparse.Namespace) -> int:
    result = _post("/api/v1/safe-state/reset", {"restore": bool(args.restore)})
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def cmd_export_diagnostics(args: argparse.Namespace) -> int:
    config = _config()
    store = _store(config)
    identity = load_or_create_identity(config.data_dir)
    bundle = {
        "version": APP_VERSION,
        "generatedAt": time.time(),
        "mode": config.mode,
        "dataDir": str(config.data_dir),
        "agentId": identity.agent_id,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "counts": {},
        "redacted": [
            "identity private keys",
            "pairing secrets",
            "session tokens",
            "raw flow payloads",
        ],
    }
    for table in (
        "devices",
        "flows",
        "decisions",
        "intents",
        "metrics_raw",
        "metrics_agg",
        "audit_log",
    ):
        try:
            row = store.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            bundle["counts"][table] = int(row["n"]) if row else 0
        except Exception:  # noqa: BLE001
            bundle["counts"][table] = None
    destination = Path(args.output or (config.data_dir / "reports" / "diagnostics.json"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    store.close()
    print(str(destination))
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    print(
        json.dumps(
            {
                "currentVersion": APP_VERSION,
                "updateAvailable": False,
                "note": (
                    "No update channel is configured. Model updates are queued "
                    "through POST /api/v1/models/updates and verified locally."
                ),
            },
            indent=2,
        )
    )
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    config = _config()
    if not args.yes:
        print("This would reset traffic-control state, stop the agent, and remove local data.")
        print("Re-run with --yes to confirm (no implicit sudo).")
        return 0
    try:
        _post("/api/v1/safe-state/reset", {"restore": False})
        print("Requested a safe-state reset before removal.")
    except SystemExit as error:
        print(f"Could not reach the agent to revert shaping: {error}")
    pid_path = config.data_dir / "eb-agentd.pid"
    if pid_path.exists():
        cmd_stop(argparse.Namespace())
    if args.purge:
        import shutil

        shutil.rmtree(config.data_dir, ignore_errors=True)
        print(f"Removed {config.data_dir}")
    print("Remove the systemd unit manually if it was installed (see docs/INSTALL.md).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eb-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--mode", choices=["gateway", "bridge", "lab", "monitor"])
    init.add_argument("--docker", action="store_true")
    init.set_defaults(func=cmd_init)

    pair = sub.add_parser("pair")
    pair.set_defaults(func=cmd_pair)

    sessions = sub.add_parser("sessions")
    sessions.set_defaults(func=cmd_sessions)

    rotate = sub.add_parser("rotate-keys")
    rotate.set_defaults(func=cmd_rotate_keys)

    status = sub.add_parser("status")
    status.set_defaults(func=cmd_status)

    stop = sub.add_parser("stop")
    stop.set_defaults(func=cmd_stop)

    safe_reset = sub.add_parser("safe-reset")
    safe_reset.add_argument("--restore", action="store_true")
    safe_reset.set_defaults(func=cmd_safe_reset)

    export = sub.add_parser("export-diagnostics")
    export.add_argument("--output")
    export.set_defaults(func=cmd_export_diagnostics)

    update = sub.add_parser("update")
    update.set_defaults(func=cmd_update)

    uninstall = sub.add_parser("uninstall")
    uninstall.add_argument("--purge", action="store_true")
    uninstall.add_argument("--yes", action="store_true")
    uninstall.set_defaults(func=cmd_uninstall)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
