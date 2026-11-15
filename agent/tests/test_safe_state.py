"""Unit tests for safety/safe_state.py (no root required)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from eb_agent.enforcer.catalog import BoundsError, QdiscSpec
from eb_agent.safety.safe_state import (
    SafeState,
    default_safe_state,
    load,
    save,
    snapshot,
    validate,
)


class _FakeRunner:
    """Minimal fake runner for safe_state snapshot tests."""

    def __init__(self, states: dict[str, dict]) -> None:
        self._states = states

    def qdisc_show_json(self, interface: str) -> dict:
        return dict(self._states.get(interface, {}))


def test_default_safe_state() -> None:
    safe = default_safe_state(["eth0", "wan0"])
    assert "eth0" in safe.qdiscs
    assert "wan0" in safe.qdiscs
    assert safe.qdiscs["eth0"][0].kind == "cake"
    assert safe.qdiscs["eth0"][0].interface == "eth0"


def test_safe_state_json_round_trip() -> None:
    safe = SafeState(
        qdiscs={
            "eth0": [QdiscSpec(kind="cake", interface="eth0", bandwidth_mbit=100.0)]
        },
        nft_ruleset="table inet eb_agent { }",
    )
    data = safe.to_json()
    restored = SafeState.from_json(data)
    assert restored.qdiscs["eth0"][0].kind == "cake"
    assert restored.qdiscs["eth0"][0].bandwidth_mbit == 100.0
    assert restored.nft_ruleset == "table inet eb_agent { }"


def test_save_and_load(tmp_path: Path) -> None:
    safe = SafeState(qdiscs={"eth0": [QdiscSpec(kind="tbf", interface="eth0")]})
    path = tmp_path / "safe.json"
    save(safe, path)
    assert path.exists()
    loaded = load(path)
    assert loaded.qdiscs["eth0"][0].kind == "tbf"


def test_snapshot_captures_state() -> None:
    runner = _FakeRunner({"eth0": {"kind": "cake", "handle": "1:"}})
    safe = snapshot(runner, ["eth0"], ["eth0", "wan0"])
    assert safe.qdiscs["eth0"][0].kind == "cake"


def test_validate_rejects_bad_spec() -> None:
    safe = SafeState(
        qdiscs={
            "eth99": [QdiscSpec(kind="cake", interface="eth99")]  # unknown interface
        }
    )
    with pytest.raises(BoundsError):
        validate(safe, ["eth0", "wan0"])


def test_validate_passes_good_specs() -> None:
    safe = SafeState(
        qdiscs={
            "eth0": [QdiscSpec(kind="cake", interface="eth0", bandwidth_mbit=50.0)]
        }
    )
    result = validate(safe, ["eth0", "wan0"])
    assert result.qdiscs["eth0"][0].kind == "cake"
