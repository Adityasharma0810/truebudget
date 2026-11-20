"""Hardening tests for Phase 2: signatures, idempotency, read-back, rendering."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import tempfile
import time

import pytest

from eb_agent.enforcer.catalog import (
    EnforceOp,
    FilterSpec,
    HtbClass,
    HtbPlan,
    LinkSpec,
    NftEntry,
    NftPlan,
    QdiscSpec,
    format_command,
)
from eb_agent.enforcer.client import EnforcerClient
from eb_agent.enforcer.daemon import Enforcerd, EnforcerdConfig
from eb_agent.enforcer.ingress import IngressPlan, ingress_ops
from eb_agent.enforcer.nft_policy import (
    ClassMark,
    DeviceEntry,
    NftPolicyPlan,
    nft_policy_op,
)
from eb_agent.enforcer.tc import FakeTcRunner, TcController, render_commands


def _make_daemon(runner: FakeTcRunner, **kwargs) -> Enforcerd:
    config = EnforcerdConfig(
        token="test-token",
        available_interfaces=["eth0", "ifb0"],
        dead_man_ttl=5.0,
        **kwargs,
    )
    return Enforcerd(config=config, controller=TcController(runner))


def _signed(daemon: Enforcerd, request: dict) -> dict:
    signed = dict(request)
    signed["ts"] = time.time()
    signed["nonce"] = secrets.token_hex(8)
    signed["sig"] = daemon._signature(signed)  # noqa: SLF001 - test helper
    return signed


def _apply_request(op_id: str = "op-1", bandwidth: float = 50.0) -> dict:
    return {
        "action": "apply",
        "token": "test-token",
        "op_id": op_id,
        "qdisc": [
            {"kind": "cake", "interface": "eth0", "bandwidth_mbit": bandwidth}
        ],
    }


# ------------------------------------------------------------------ read-back


def test_empty_readback_is_not_success() -> None:
    runner = FakeTcRunner()  # no qdisc state: the kernel reports nothing
    controller = TcController(runner)
    op = EnforceOp(
        op_id="op-empty",
        qdisc=[QdiscSpec(kind="cake", interface="eth0", bandwidth_mbit=50.0)],
    )
    result = controller.apply(op, ["eth0"])
    assert result.rolled_back is True
    assert result.readback is not None
    assert result.readback["interfaces"]["eth0"]["qdiscs"] == []


def test_readback_rejects_wrong_rate() -> None:
    runner = FakeTcRunner()
    runner.qdisc_state["eth0"] = [
        {"kind": "cake", "root": True, "options": {"bandwidth": 1_000_000}}
    ]
    controller = TcController(runner)
    op = EnforceOp(
        op_id="op-rate",
        qdisc=[QdiscSpec(kind="cake", interface="eth0", bandwidth_mbit=50.0)],
    )
    result = controller.apply(op, ["eth0"])
    assert result.rolled_back is True
    assert "verification failed" in result.stdout


# ------------------------------------------------------------------ rendering


def test_dry_run_renders_exact_bounded_commands() -> None:
    op = EnforceOp(
        op_id="op-dry-render",
        qdisc=[
            QdiscSpec(
                kind="cake",
                interface="eth0",
                bandwidth_mbit=95.0,
                params={"diffserv4": "", "nat": "", "dual-srchost": ""},
            )
        ],
    )
    rendered = "\n".join(format_command(c) for c in render_commands(op))
    assert (
        "tc qdisc replace dev eth0 root cake bandwidth 95mbit "
        "diffserv4 nat dual-srchost"
    ) in rendered

    setup, mirred, cake = ingress_ops(
        "op-ing", IngressPlan(wan_interface="eth0", ifb_interface="ifb0",
                              bandwidth_mbit=30.0)
    )
    rendered = "\n".join(
        format_command(c)
        for item in (setup, mirred, cake)
        for c in render_commands(item)
    )
    assert "ip link add dev ifb0 type ifb" in rendered
    assert (
        "tc filter add dev eth0 ingress protocol all pref 1 matchall "
        "action mirred egress redirect dev ifb0"
    ) in rendered
    assert "tc qdisc replace dev ifb0 root cake bandwidth 30mbit" in rendered
    assert "ingress" in rendered.split("bandwidth 30mbit", 1)[1]


def test_htb_and_nft_rendering_is_bounded_and_structured() -> None:
    htb = EnforceOp(
        op_id="op-htb-render",
        htb=HtbPlan(
            interface="eth0",
            root_rate_mbit=50.0,
            classes=[
                HtbClass(classid="1:1", rate_mbit=20.0, ceil_mbit=40.0, mark=1),
                HtbClass(classid="1:2", rate_mbit=10.0, mark=2),
            ],
            default_classid="1:2",
        ),
    )
    rendered = "\n".join(format_command(c) for c in render_commands(htb))
    assert "tc qdisc replace dev eth0 root handle 1: htb default 2" in rendered
    assert "tc class replace dev eth0 parent 1: classid 1:1 htb rate 20mbit" in rendered
    assert "tc filter add dev eth0 parent 1: protocol all pref 1 handle 1 fw flowid 1:1" in rendered

    nft = EnforceOp(
        op_id="op-nft-render",
        nft=NftPlan(
            entries=[
                NftEntry(mac="aa:bb:cc:dd:ee:ff", dscp=46, fwmark=1),
            ]
        ),
    )
    rendered = "\n".join(format_command(c) for c in render_commands(nft))
    assert "nft delete table inet eb_agent" in rendered
    assert "nft add chain inet eb_agent eb_mark" in rendered
    assert "ether saddr aa:bb:cc:dd:ee:ff ip dscp set ef" in rendered
    assert "ether saddr aa:bb:cc:dd:ee:ff ip6 dscp set ef" in rendered
    assert "meta mark set 1" in rendered


# ------------------------------------------------------------------ signatures


@pytest.mark.asyncio
async def test_requires_signature_and_rejects_replay() -> None:
    runner = FakeTcRunner()
    runner.state["eth0"] = {"kind": "cake"}
    daemon = _make_daemon(runner)

    unsigned = await daemon._dispatch(_apply_request(), require_signature=True)
    assert unsigned["error"] == "unauthorized"
    assert "signature" in unsigned["detail"]

    request = _signed(daemon, _apply_request())
    first = await daemon._dispatch(request, require_signature=True)
    assert first["ok"] is True
    replay = await daemon._dispatch(request, require_signature=True)
    assert replay["error"] == "unauthorized"
    assert replay["detail"].endswith("replayed_request")


@pytest.mark.asyncio
async def test_bad_signature_rejected() -> None:
    runner = FakeTcRunner()
    daemon = _make_daemon(runner)
    request = _signed(daemon, _apply_request())
    request["qdisc"][0]["bandwidth_mbit"] = 99.0
    response = await daemon._dispatch(request, require_signature=True)
    assert response["error"] == "unauthorized"
    assert response["detail"].endswith("invalid_signature")


# ------------------------------------------------------------------ idempotency


@pytest.mark.asyncio
async def test_idempotent_replay_does_not_execute_again() -> None:
    runner = FakeTcRunner()
    runner.state["eth0"] = {"kind": "cake"}
    daemon = _make_daemon(runner)
    request = _apply_request(op_id="op-idem")
    first = await daemon._dispatch(_signed(daemon, request), require_signature=True)
    assert first["ok"] is True
    commands_after_first = len(runner.commands)
    second = await daemon._dispatch(_signed(daemon, request), require_signature=True)
    assert second["verdict"] == "idempotent_replay"
    assert len(runner.commands) == commands_after_first

    conflicting = _apply_request(op_id="op-idem", bandwidth=25.0)
    response = await daemon._dispatch(
        _signed(daemon, conflicting), require_signature=True
    )
    assert response["error"] == "idempotency_conflict"


@pytest.mark.asyncio
async def test_safe_state_is_persisted_before_change(tmp_path) -> None:
    runner = FakeTcRunner()
    runner.state["eth0"] = {"kind": "netem"}
    safe_path = tmp_path / "safe.json"
    daemon = _make_daemon(runner, safe_state_path=str(safe_path))
    response = await daemon._dispatch(
        _signed(daemon, _apply_request(op_id="op-safe")), require_signature=True
    )
    # The fake kernel keeps reporting netem, so the change is verified as a
    # mismatch and rolled back; the important invariant is that the safe-state
    # snapshot was written *before* the attempted change.
    assert response["verdict"] == "rolled_back"
    payload = json.loads(safe_path.read_text(encoding="utf-8"))
    assert payload["interfaces"]["eth0"]["qdiscs"][0]["kind"] == "netem"


# ------------------------------------------------------------------ nft policy


def test_mac_only_device_emits_v4_and_v6_rules() -> None:
    plan = NftPolicyPlan(
        classes=[ClassMark(name="call", dscp=46, fwmark=1)],
        devices=[DeviceEntry(mac="aa:bb:cc:dd:ee:01", class_name="call")],
    )
    op = nft_policy_op("op-mac", plan)
    assert op.nft is not None
    assert len(op.nft.entries) == 2
    assert {entry.l3 for entry in op.nft.entries} == {"ip", "ip6"}
    assert all(entry.dscp == 46 and entry.fwmark == 1 for entry in op.nft.entries)


def test_nft_policy_rejects_undeclared_device_class() -> None:
    from eb_agent.enforcer.nft_policy import NftPolicyError

    with pytest.raises(NftPolicyError):
        NftPolicyPlan(
            classes=[ClassMark(name="call", dscp=46)],
            devices=[DeviceEntry(ipv4="10.0.0.2", class_name="bulk")],
        )


# ------------------------------------------------------------------ client log


class _FakeDecisionLog:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def append(self, **kwargs) -> None:
        self.entries.append(kwargs)


@pytest.mark.asyncio
async def test_client_writes_decision_log_entries() -> None:
    runner = FakeTcRunner()
    runner.state["eth0"] = {"kind": "cake"}
    daemon = _make_daemon(runner)
    sock_path = os.path.join(tempfile.mkdtemp(), "eb-hardening.sock")
    server = await asyncio.start_unix_server(daemon._handle_client, path=sock_path)
    log = _FakeDecisionLog()
    try:
        client = EnforcerClient(socket_path=sock_path, token="test-token",
                                decision_log=log)
        result = await client.apply(
            "op-logged",
            [{"kind": "cake", "interface": "eth0", "bandwidth_mbit": 50.0}],
        )
        assert result.ok is True
        assert len(log.entries) == 1
        entry = log.entries[0]
        assert entry["trigger"] == "enforcer_apply"
        assert entry["verdict"] == "kept"
        assert entry["evidence"]["op_id"] == "op-logged"
    finally:
        server.close()
        await server.wait_closed()
        try:
            os.unlink(sock_path)
        except OSError:
            pass
