"""Unit tests for the tc runner (FakeTcRunner only; no root required)."""

from __future__ import annotations

from eb_agent.enforcer.catalog import BoundsError, EnforceOp, QdiscSpec
from eb_agent.enforcer.tc import FakeTcRunner, SubprocessRunner, TcController, TcResult


def test_fake_runner_records_commands() -> None:
    runner = FakeTcRunner()
    result = runner.run(["qdisc", "replace", "dev", "eth0", "root", "cake"])
    assert result.ok
    assert runner.commands == [["qdisc", "replace", "dev", "eth0", "root", "cake"]]


def test_fake_runner_fail_next() -> None:
    runner = FakeTcRunner()
    runner.fail_next = True
    result = runner.run(["qdisc", "replace", "dev", "eth0", "root", "cake"])
    assert not result.ok
    assert runner.fail_next is False  # consumed


def test_controller_apply_succeeds_when_readback_matches() -> None:
    runner = FakeTcRunner()
    runner.state["eth0"] = {"kind": "cake"}
    controller = TcController(runner)
    op = EnforceOp(
        op_id="op-1",
        qdisc=[QdiscSpec(kind="cake", interface="eth0", bandwidth_mbit=50.0)],
    )
    result = controller.apply(op, ["eth0"])
    assert result.ok
    assert len(controller.history) == 1


def test_controller_apply_rolls_back_on_readback_mismatch() -> None:
    runner = FakeTcRunner()
    runner.state["eth0"] = {"kind": "netem"}  # does not match cake
    controller = TcController(runner)
    op = EnforceOp(
        op_id="op-1",
        qdisc=[QdiscSpec(kind="cake", interface="eth0", bandwidth_mbit=50.0)],
    )
    result = controller.apply(op, ["eth0"], prior_state={"eth0": {"tc_args": ["netem"]}})
    # apply returns the rollback result (ok because fake rollback succeeds)
    assert result.ok
    assert "rolled back" in result.stdout
    # rollback commands should be present: del then replace with prior args
    assert any(c[:3] == ["qdisc", "del", "dev"] for c in runner.commands)
    assert any(
        c[:5] == ["qdisc", "replace", "dev", "eth0", "root"] and "netem" in c
        for c in runner.commands
    )
    assert len(controller.history) == 1


def test_controller_apply_rejects_bounds_without_executing() -> None:
    runner = FakeTcRunner()
    controller = TcController(runner)
    op = EnforceOp(
        op_id="bad op!",
        qdisc=[QdiscSpec(kind="cake", interface="eth0", bandwidth_mbit=50.0)],
    )
    result = controller.apply(op, ["eth0"])
    assert not result.ok
    assert result.returncode == 2
    assert runner.commands == []  # nothing executed


def test_controller_dry_run_executes_nothing() -> None:
    runner = FakeTcRunner()
    controller = TcController(runner)
    op = EnforceOp(
        op_id="op-1",
        qdisc=[QdiscSpec(kind="cake", interface="eth0", bandwidth_mbit=50.0)],
        dry_run=True,
    )
    result = controller.apply(op, ["eth0"])
    assert result.ok
    assert runner.commands == []  # dry-run: no commands sent to runner


def test_subprocess_runner_uses_list_not_shell() -> None:
    runner = SubprocessRunner()
    # Just verify construction and that TC attribute is set; actual execution
    # requires root / tc, so we only check the class attribute here.
    assert runner.TC == "tc"


def test_tc_result_fields() -> None:
    result = TcResult(
        ok=True,
        commands=[["tc", "qdisc", "show"]],
        stdout="",
        stderr="",
        returncode=0,
        readback={"kind": "cake"},
    )
    assert result.ok
    assert result.readback == {"kind": "cake"}
