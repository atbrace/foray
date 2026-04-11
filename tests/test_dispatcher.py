import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from foray.dispatcher import (
    dispatch,
    parse_experiment_status,
    write_crash_stub,
)
from foray.models import DispatchResult, ExperimentStatus


@patch("foray.dispatcher.subprocess.Popen")
def test_dispatch_success(mock_popen, tmp_path):
    proc = MagicMock()
    proc.returncode = 0
    proc.wait.return_value = None
    mock_popen.return_value = proc

    results_file = tmp_path / "results.md"
    results_file.write_text("## Status\nSUCCESS\n")

    result = dispatch(
        prompt="test prompt",
        workdir=tmp_path,
        model="test-model",
        max_turns=10,
        tools=["Read"],
        results_file=results_file,
    )
    assert result.exit_code == 0
    assert result.results_file_path is not None


@patch("foray.dispatcher.subprocess.Popen")
def test_dispatch_failure(mock_popen, tmp_path):
    proc = MagicMock()
    proc.returncode = 1
    proc.wait.return_value = None
    mock_popen.return_value = proc

    result = dispatch(
        prompt="test", workdir=tmp_path, model="m", max_turns=5, tools=[],
    )
    assert result.exit_code == 1
    assert result.results_file_path is None


@patch("foray.dispatcher.subprocess.Popen")
def test_dispatch_timeout_graceful(mock_popen, tmp_path):
    """SIGTERM succeeds — process exits within grace period, no SIGKILL needed."""
    proc = MagicMock()
    # First wait() raises timeout (main timeout), second wait() returns (SIGTERM worked)
    proc.wait.side_effect = [subprocess.TimeoutExpired(cmd=[], timeout=60), None]
    proc.terminate.return_value = None
    proc.returncode = 143  # SIGTERM exit code
    mock_popen.return_value = proc

    result = dispatch(
        prompt="test", workdir=tmp_path, model="m",
        max_turns=5, tools=[], timeout_minutes=1,
    )
    assert result.exit_code == -1
    proc.terminate.assert_called_once()
    proc.kill.assert_not_called()


@patch("foray.dispatcher.subprocess.Popen")
def test_dispatch_timeout_escalates_to_sigkill(mock_popen, tmp_path):
    """SIGTERM ignored — process doesn't exit within grace period, SIGKILL sent."""
    proc = MagicMock()
    # First wait() raises timeout (main), second wait() also raises (SIGTERM ignored),
    # third wait() returns (after SIGKILL)
    proc.wait.side_effect = [
        subprocess.TimeoutExpired(cmd=[], timeout=60),
        subprocess.TimeoutExpired(cmd=[], timeout=5),
        None,
    ]
    proc.terminate.return_value = None
    proc.kill.return_value = None
    proc.returncode = -9
    mock_popen.return_value = proc

    result = dispatch(
        prompt="test", workdir=tmp_path, model="m",
        max_turns=5, tools=[], timeout_minutes=1,
    )
    assert result.exit_code == -1
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()


def test_parse_status_success(tmp_path):
    p = tmp_path / "results.md"
    p.write_text("## Status\nSUCCESS\n\nDetails here")
    assert parse_experiment_status(p) == ExperimentStatus.SUCCESS


def test_parse_status_partial(tmp_path):
    p = tmp_path / "results.md"
    p.write_text("## Status\nPARTIAL\n\nGot stuck")
    assert parse_experiment_status(p) == ExperimentStatus.PARTIAL


def test_parse_status_missing_file(tmp_path):
    assert parse_experiment_status(tmp_path / "nope.md") == ExperimentStatus.CRASH


def test_parse_status_no_header(tmp_path):
    p = tmp_path / "results.md"
    p.write_text("Some text without a status header")
    assert parse_experiment_status(p) == ExperimentStatus.CRASH


def test_write_crash_stub(tmp_path):
    (tmp_path / "experiments").mkdir()
    plan_path = tmp_path / "experiments" / "001_plan.md"
    plan_path.write_text("# Plan\nDo the thing")

    dr = DispatchResult(exit_code=1, stdout="", stderr="Segfault", elapsed_seconds=5.0)
    write_crash_stub(tmp_path, "001", plan_path, dr)

    stub = (tmp_path / "experiments" / "001_results.md").read_text()
    assert "CRASH" in stub
    assert "Segfault" in stub
    assert "Do the thing" in stub
    # With default timeout_minutes=10, elapsed=5.0, exit_code=1 → early crash
    assert "Early crash" in stub
    assert "Failure Classification" in stub


def test_crash_stub_hard_timeout(tmp_path):
    """Hard timeout: elapsed >= timeout, exit_code == -1."""
    (tmp_path / "experiments").mkdir()
    plan_path = tmp_path / "experiments" / "001_plan.md"
    plan_path.write_text("# Plan\nTimeout test")

    dr = DispatchResult(
        exit_code=-1, stdout="partial work", stderr="", elapsed_seconds=600.2,
    )
    write_crash_stub(tmp_path, "001", plan_path, dr, timeout_minutes=10.0)

    stub = (tmp_path / "experiments" / "001_results.md").read_text()
    assert "Hard timeout" in stub
    assert "still working when killed" in stub
    assert "600.2s" in stub


def test_crash_stub_early_crash(tmp_path):
    """Early crash: elapsed < timeout, exit_code nonzero and != -1."""
    (tmp_path / "experiments").mkdir()
    plan_path = tmp_path / "experiments" / "001_plan.md"
    plan_path.write_text("# Plan\nCrash test")

    dr = DispatchResult(
        exit_code=1, stdout="some output", stderr="error msg", elapsed_seconds=12.3,
    )
    write_crash_stub(tmp_path, "001", plan_path, dr, timeout_minutes=10.0)

    stub = (tmp_path / "experiments" / "001_results.md").read_text()
    assert "Early crash" in stub
    assert "crashed after" in stub
    assert "12.3s" in stub
    assert "exit code 1" in stub


def test_crash_stub_no_output(tmp_path):
    """No output: empty stdout and empty stderr."""
    (tmp_path / "experiments").mkdir()
    plan_path = tmp_path / "experiments" / "001_plan.md"
    plan_path.write_text("# Plan\nNo output test")

    dr = DispatchResult(
        exit_code=1, stdout="", stderr="", elapsed_seconds=3.0,
    )
    write_crash_stub(tmp_path, "001", plan_path, dr, timeout_minutes=10.0)

    stub = (tmp_path / "experiments" / "001_results.md").read_text()
    assert "No output" in stub
    assert "stalled on startup" in stub


def test_crash_stub_partial_output(tmp_path):
    """Partial output: has stdout but no results file was written."""
    (tmp_path / "experiments").mkdir()
    plan_path = tmp_path / "experiments" / "001_plan.md"
    plan_path.write_text("# Plan\nPartial output test")

    dr = DispatchResult(
        exit_code=1, stdout="I started working on the task...",
        stderr="something", elapsed_seconds=45.0,
    )
    write_crash_stub(tmp_path, "001", plan_path, dr, timeout_minutes=10.0)

    stub = (tmp_path / "experiments" / "001_results.md").read_text()
    assert "Partial output" in stub
    assert "chars of output" in stub


def test_crash_stub_combined(tmp_path):
    """Combined: early crash + no output."""
    (tmp_path / "experiments").mkdir()
    plan_path = tmp_path / "experiments" / "001_plan.md"
    plan_path.write_text("# Plan\nCombined test")

    dr = DispatchResult(
        exit_code=2, stdout="", stderr="", elapsed_seconds=1.5,
    )
    write_crash_stub(tmp_path, "001", plan_path, dr, timeout_minutes=10.0)

    stub = (tmp_path / "experiments" / "001_results.md").read_text()
    assert "Early crash" in stub
    assert "No output" in stub
    assert "Failure Classification" in stub
