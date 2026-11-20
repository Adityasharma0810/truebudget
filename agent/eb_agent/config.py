"""Agent configuration loader.

Loads /etc/eb-agent/config.toml (root:eb-agent 0640) with dev fallback
~/.local/share/eb-agent/config.toml.  Validates and provides defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - fallback for older Python
    import tomli as tomllib  # type: ignore


SYSTEM_CONFIG = Path("/etc/eb-agent/config.toml")
DEV_CONFIG = Path.home() / ".local/share/eb-agent/config.toml"


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


def _default_data_dir() -> Path:
    return Path("/var/lib/eb-agent") if _is_root() else DEV_CONFIG.parent


def _default_config_path() -> Path:
    return SYSTEM_CONFIG if _is_root() else DEV_CONFIG


@dataclass
class AgentConfig:
    mode: str = "lab"
    shaping_factor: float = 0.95
    ack_filter: bool = False
    slow_interval_s: float = 30.0
    fast_interval_s: float = 1.5
    dwell_s: float = 10.0
    max_rate_change_pct: float = 20.0
    rollback_cooldown_s: float = 30.0
    classifier_threshold: float = 0.6
    estimator_mape_target: float = 0.075
    forecaster_confidence_threshold: float = 0.7
    raw_retention_s: int = 86400
    agg_retention_d: int = 30
    data_dir: Path = field(default_factory=_default_data_dir)
    config_path: Path = field(default_factory=_default_config_path)
    socket_path: str = "/run/eb-enforcerd.sock"
    enforcer_token: str | None = None
    dead_man_ttl: float = 30.0
    available_interfaces: list[str] = field(default_factory=list)
    log_level: str = "info"
    agent_id: str = ""
    relay_url: str = ""
    relay_token: str = ""
    site_url: str = "https://example.com"
    tls: bool = False
    https_port: int = 7443
    mdns: bool = True

    def __post_init__(self) -> None:
        self._validate()

    @classmethod
    def load(
        cls,
        path: Path | str | None = None,
        *,
        env_prefix: str = "EB_",
    ) -> "AgentConfig":
        cfg = cls()
        config_path = Path(path) if path else None
        if config_path is None:
            if SYSTEM_CONFIG.exists():
                config_path = SYSTEM_CONFIG
            elif DEV_CONFIG.exists():
                config_path = DEV_CONFIG
            else:
                config_path = _default_config_path()
        if config_path and config_path.exists():
            cfg = _merge_toml(cfg, config_path)
        cfg = _merge_env(cfg, env_prefix)
        cfg._validate()
        return cfg

    def _validate(self) -> None:
        if self.slow_interval_s <= 0:
            raise ValueError("slow_interval_s must be > 0")
        if self.fast_interval_s <= 0:
            raise ValueError("fast_interval_s <= 0")
        if not (0.0 < self.shaping_factor <= 1.0):
            raise ValueError("shaping_factor must be in (0, 1]")
        if not (0.0 <= self.max_rate_change_pct <= 100.0):
            raise ValueError("max_rate_change_pct must be in [0, 100]")
        if not (0.0 <= self.classifier_threshold <= 1.0):
            raise ValueError("classifier_threshold must be in [0, 1]")

    def save(self, path: Path | str | None = None) -> None:
        target = Path(path) if path else self.config_path
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = ["[agent]",
            f'mode = "{self.mode}"',
            f'shaping_factor = {self.shaping_factor}',
            f'ack_filter = {str(self.ack_filter).lower()}',
            f'slow_interval_s = {self.slow_interval_s}',
            f'fast_interval_s = {self.fast_interval_s}',
            f'dwell_s = {self.dwell_s}',
            f'max_rate_change_pct = {self.max_rate_change_pct}',
            f'rollback_cooldown_s = {self.rollback_cooldown_s}',
            f'classifier_threshold = {self.classifier_threshold}',
            f'estimator_mape_target = {self.estimator_mape_target}',
            f'forecaster_confidence_threshold = {self.forecaster_confidence_threshold}',
            f'raw_retention_s = {self.raw_retention_s}',
            f'agg_retention_d = {self.agg_retention_d}',
            f'data_dir = "{self.data_dir}"',
            f'socket_path = "{self.socket_path}"',
            f'dead_man_ttl = {self.dead_man_ttl}',
            f'interfaces = [{", ".join(repr(i) for i in self.available_interfaces)}]',
            f'log_level = "{self.log_level}"',
            f'relay_url = "{self.relay_url}"',
            f'relay_token = "{self.relay_token}"',
            f'site_url = "{self.site_url}"',
            f'tls = {str(self.tls).lower()}',
            f'https_port = {self.https_port}',
            f'mdns = {str(self.mdns).lower()}',
        ]
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            target.chmod(0o640)
        except OSError:
            pass


def _merge_toml(cfg: AgentConfig, path: Path) -> AgentConfig:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    agent = data.get("agent", {})
    for key, value in agent.items():
        attr = key.replace("-", "_")
        if hasattr(cfg, attr):
            setattr(cfg, attr, value)
    return cfg


def _merge_env(cfg: AgentConfig, prefix: str) -> AgentConfig:
    env_map = {
        f"{prefix}MODE": ("mode", str),
        f"{prefix}SLOW_INTERVAL_S": ("slow_interval_s", float),
        f"{prefix}FAST_INTERVAL_S": ("fast_interval_s", float),
        f"{prefix}SHAPING_FACTOR": ("shaping_factor", float),
        f"{prefix}ACK_FILTER": ("ack_filter", lambda v: v.lower() == "true"),
        f"{prefix}SOCKET_PATH": ("socket_path", str),
        f"{prefix}ENFORCER_TOKEN": ("enforcer_token", str),
        f"{prefix}DEAD_MAN_TTL": ("dead_man_ttl", float),
        f"{prefix}LOG_LEVEL": ("log_level", str),
        f"{prefix}INTERFACES": ("available_interfaces", lambda v: [s.strip() for s in v.split(",") if s.strip()]),
        f"{prefix}DATA_DIR": ("data_dir", lambda v: Path(v)),
        f"{prefix}RELAY_URL": ("relay_url", str),
        f"{prefix}RELAY_TOKEN": ("relay_token", str),
        f"{prefix}SITE_URL": ("site_url", str),
        f"{prefix}TLS": ("tls", lambda v: v.lower() in {"1", "true", "yes", "on"}),
        f"{prefix}HTTPS_PORT": ("https_port", int),
        f"{prefix}MDNS": ("mdns", lambda v: v.lower() in {"1", "true", "yes", "on"}),
    }
    for env_name, (attr, conv) in env_map.items():
        raw = os.environ.get(env_name)
        if raw is not None:
            setattr(cfg, attr, conv(raw))
    return cfg


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "config":
        sub = sys.argv[2] if len(sys.argv) > 2 else ""
        key = sys.argv[3] if len(sys.argv) > 3 else ""
        value = sys.argv[4] if len(sys.argv) > 4 else None
        cfg = AgentConfig.load()
        if sub == "get":
            print(getattr(cfg, key, "unknown key"))
        elif sub == "set" and value is not None:
            current = getattr(cfg, key, None)
            try:
                if isinstance(current, bool):
                    value = value.lower() in {"1", "true", "yes", "on"}
                elif isinstance(current, int):
                    value = int(value)
                elif isinstance(current, float):
                    value = float(value)
                elif isinstance(current, Path):
                    value = Path(value).expanduser()
                elif isinstance(current, list):
                    value = [item.strip() for item in value.split(",") if item.strip()]
            except ValueError:
                print(f"invalid value for {key}: {value}")
                sys.exit(1)
            setattr(cfg, key, value)
            cfg.save()
            print(f"set {key}={value}")
        else:
            print("usage: python -m eb_agent.config config get|set <key> [value]")
    elif cmd == "mode":
        sub = sys.argv[2] if len(sys.argv) > 2 else ""
        mode = sys.argv[3] if len(sys.argv) > 3 else ""
        if sub == "set" and mode:
            if mode not in {"gateway", "bridge", "lab", "monitor"}:
                print("mode must be gateway, bridge, lab, or monitor")
                sys.exit(1)
            cfg = AgentConfig.load()
            cfg.mode = mode
            cfg.save()
            print(f"mode set to {mode}")
        else:
            print("usage: python -m eb_agent.config mode set <gateway|bridge|lab|monitor>")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
