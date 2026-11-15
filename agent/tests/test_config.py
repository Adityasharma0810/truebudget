"""Unit tests for eb_agent.config."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from eb_agent.config import AgentConfig


def test_defaults() -> None:
    cfg = AgentConfig()
    assert cfg.mode == "lab"
    assert cfg.shaping_factor == 0.95
    assert cfg.slow_interval_s == 30.0
    assert cfg.fast_interval_s == 1.5


def test_env_overrides() -> None:
    os.environ["EB_MODE"] = "gateway"
    os.environ["EB_SHAPING_FACTOR"] = "0.8"
    os.environ["EB_SLOW_INTERVAL_S"] = "60"
    os.environ["EB_ACK_FILTER"] = "true"
    os.environ["EB_INTERFACES"] = "eth0, wan0"
    try:
        cfg = AgentConfig.load()
        assert cfg.mode == "gateway"
        assert cfg.shaping_factor == 0.8
        assert cfg.slow_interval_s == 60.0
        assert cfg.ack_filter is True
        assert cfg.available_interfaces == ["eth0", "wan0"]
    finally:
        for k in ("EB_MODE", "EB_SHAPING_FACTOR", "EB_SLOW_INTERVAL_S", "EB_ACK_FILTER", "EB_INTERFACES"):
            os.environ.pop(k, None)


def test_toml_load(tmp_path: Path) -> None:
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        '[agent]\nmode = "bridge"\nshaping_factor = 0.7\nslow_interval_s = 45\n',
        encoding="utf-8",
    )
    cfg = AgentConfig.load(toml_path)
    assert cfg.mode == "bridge"
    assert cfg.shaping_factor == 0.7
    assert cfg.slow_interval_s == 45.0


def test_validation() -> None:
    try:
        AgentConfig(shaping_factor=1.5)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")

    try:
        AgentConfig(slow_interval_s=0)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_save_and_reload(tmp_path: Path) -> None:
    cfg = AgentConfig(mode="gateway", shaping_factor=0.85, slow_interval_s=60.0)
    path = tmp_path / "saved.toml"
    cfg.save(path)
    reloaded = AgentConfig.load(path)
    assert reloaded.mode == "gateway"
    assert reloaded.shaping_factor == 0.85
    assert reloaded.slow_interval_s == 60.0
