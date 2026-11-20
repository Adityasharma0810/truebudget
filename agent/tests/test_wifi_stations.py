"""Hotspot client discovery: Wi-Fi station parsing and the full refresh path.

The parsers run against the exact text ``iw`` and the lease files produce on a
real hotspot; no synthetic device rows are ever injected into production
state by these tests — they exercise the parsing/merge logic only.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from eb_agent.core import AgentCore, _read_wifi_stations


IW_DEV_OUT = """phy#0
\tInterface wlan0
\t\tifindex 4
\t\twdev 0x1
\t\taddr de:ad:be:ef:00:01
\tInterface ap0
\t\tifindex 5
\t\twdev 0x2
\t\taddr de:ad:be:ef:00:02
"""

IW_STATION_OUT = """Station aa:bb:cc:dd:ee:21 (on ap0)
\tinactive time:\t120 ms
\trx bytes:\t12000
\ttx bytes:\t48000
\tsignal:\t\t-47 dBm
\ttx bitrate:\t144.4 MBit/s

Station aa:bb:cc:dd:ee:22 (on ap0)
\tinactive time:\t3000 ms
\tsignal:\t\t-61 dBm
"""


def _patch_run(monkeypatch: pytest.MonkeyPatch, outputs: dict[str, str]) -> None:
    def fake_run(cmd, **kwargs):
        key = " ".join(cmd)
        return SimpleNamespace(returncode=0, stdout=outputs.get(key, ""))

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_wifi_station_dump_parses_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(
        monkeypatch,
        {
            "iw dev": IW_DEV_OUT,
            "iw dev wlan0 station dump": "",
            "iw dev ap0 station dump": IW_STATION_OUT,
        },
    )
    stations = _read_wifi_stations()
    macs = {s["mac"] for s in stations}
    assert macs == {"aa:bb:cc:dd:ee:21", "aa:bb:cc:dd:ee:22"}
    by_mac = {s["mac"]: s for s in stations}
    assert by_mac["aa:bb:cc:dd:ee:21"]["signal"] == "-47 dBm"
    assert by_mac["aa:bb:cc:dd:ee:21"]["ifname"] == "ap0"


def test_wifi_station_absent_without_iw(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_oserror(cmd, **kwargs):
        raise FileNotFoundError("iw")

    monkeypatch.setattr(subprocess, "run", raise_oserror)
    assert _read_wifi_stations() == []


def test_hotspot_station_becomes_a_device(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A station associated to the hotspot shows up even with no IP yet."""

    from eb_agent.config import AgentConfig
    from eb_agent.store import Store

    _patch_run(
        monkeypatch,
        {
            "iw dev": IW_DEV_OUT,
            "iw dev wlan0 station dump": "",
            "iw dev ap0 station dump": IW_STATION_OUT,
        },
    )
    db = Store(str(tmp_path / "agent.db"))
    config = AgentConfig(data_dir=str(tmp_path))
    core = AgentCore(config, db)
    devices = core._refresh_devices()
    wifi_devices = [d for d in devices if d.get("source") == "wifi"]
    macs = {d.get("mac") for d in wifi_devices}
    assert "aa:bb:cc:dd:ee:21" in macs
    assert all(d["kind"] == "Wi-Fi client" for d in wifi_devices)
