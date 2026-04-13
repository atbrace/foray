import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from foray.dispatcher import (
    dispatch,
    is_exhaustion_plan,
    parse_experiment_status,
    parse_stream_json_diagnostics,
    parse_stream_json_tokens,
    write_crash_stub,
    write_planner_crash_stub,
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


def test_parse_status_with_annotation(tmp_path):
    p = tmp_path / "results.md"
    p.write_text("## Status\nFAILED (hypothesis disproven)\n\nDetails")
    assert parse_experiment_status(p) == ExperimentStatus.FAILED


def test_parse_status_with_long_annotation(tmp_path):
    p = tmp_path / "results.md"
    p.write_text(
        "## Status\nPARTIAL (some metrics met, others not — see details below)\n\nDetails"
    )
    assert parse_experiment_status(p) == ExperimentStatus.PARTIAL


def test_parse_status_infeasible_with_reason(tmp_path):
    p = tmp_path / "results.md"
    p.write_text("## Status\nINFEASIBLE — dependencies unavailable\n\nDetails")
    assert parse_experiment_status(p) == ExperimentStatus.INFEASIBLE


def test_parse_status_with_empty_lines(tmp_path):
    """Empty/whitespace lines in results file should not crash the parser."""
    p = tmp_path / "results.md"
    p.write_text("## Status\n\n  \nSUCCESS\n\nDetails here")
    assert parse_experiment_status(p) == ExperimentStatus.SUCCESS


def test_parse_status_empty_file(tmp_path):
    p = tmp_path / "results.md"
    p.write_text("")
    assert parse_experiment_status(p) == ExperimentStatus.CRASH


def test_parse_status_body_line_not_misparsed(tmp_path):
    """A body line starting with a status word should NOT override the real status."""
    p = tmp_path / "results.md"
    p.write_text(
        "## Status\nSUCCESS\n\n## Findings\nFAILED experiments were re-run with better params"
    )
    assert parse_experiment_status(p) == ExperimentStatus.SUCCESS


def test_parse_status_only_reads_after_header(tmp_path):
    """Status must come from the line after '## Status', not from body content."""
    p = tmp_path / "results.md"
    p.write_text(
        "## Overview\nSUCCESS is not guaranteed\n\n## Status\nFAILED\n\nDetails"
    )
    assert parse_experiment_status(p) == ExperimentStatus.FAILED


def test_parse_status_no_status_header_with_status_word_in_body(tmp_path):
    """File without ## Status header returns CRASH even if body contains status words."""
    p = tmp_path / "results.md"
    p.write_text("## Results\nSUCCESS was achieved in all metrics\n\nGreat work")
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


def test_crash_stub_hard_timeout_with_partial_output(tmp_path):
    """Hard timeout with stdout should show both Hard timeout and Partial output."""
    (tmp_path / "experiments").mkdir()
    plan_path = tmp_path / "experiments" / "001_plan.md"
    plan_path.write_text("# Plan\nTimeout test")

    dr = DispatchResult(
        exit_code=-1, stdout="partial work", stderr="", elapsed_seconds=600.2,
    )
    write_crash_stub(tmp_path, "001", plan_path, dr, timeout_minutes=10.0)

    stub = (tmp_path / "experiments" / "001_results.md").read_text()
    assert "Hard timeout" in stub
    assert "Partial output" in stub


def test_crash_stub_unknown_failure(tmp_path):
    """exit_code=0, no stdout, has stderr → unknown failure."""
    (tmp_path / "experiments").mkdir()
    plan_path = tmp_path / "experiments" / "001_plan.md"
    plan_path.write_text("# Plan\nUnknown test")

    dr = DispatchResult(
        exit_code=0, stdout="", stderr="deprecation warning", elapsed_seconds=30.0,
    )
    write_crash_stub(tmp_path, "001", plan_path, dr, timeout_minutes=10.0)

    stub = (tmp_path / "experiments" / "001_results.md").read_text()
    assert "Unknown failure" in stub
    assert "exited with code 0" in stub


def test_exhaustion_plan_detected(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("## Status: EXHAUSTED\n\n## Rationale\nPath fully explored.\n")
    assert is_exhaustion_plan(plan) is True


def test_normal_plan_not_detected_as_exhaustion(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("# Experiment 029: Test something\n\n## Hypothesis\nStuff works.\n")
    assert is_exhaustion_plan(plan) is False


def test_missing_plan_not_detected_as_exhaustion(tmp_path):
    plan = tmp_path / "nonexistent.md"
    assert is_exhaustion_plan(plan) is False


def test_write_planner_crash_stub(tmp_path):
    """write_planner_crash_stub writes a crash file with details from both attempts."""
    (tmp_path / "experiments").mkdir()

    attempts = [
        DispatchResult(exit_code=1, stdout="attempt 1 output", stderr="err1", elapsed_seconds=5.0),
        DispatchResult(exit_code=0, stdout="attempt 2 output longer", stderr="err2", elapsed_seconds=8.3),
    ]
    write_planner_crash_stub(tmp_path, "exp_007", "path_alpha", attempts)

    stub_path = tmp_path / "experiments" / "exp_007_plan_crash.md"
    assert stub_path.exists()
    stub = stub_path.read_text()

    # Contains status and explanation
    assert "CRASH" in stub
    assert "2 attempt(s)" in stub
    assert "path_alpha" in stub

    # Attempt 1 details
    assert "Attempt 1" in stub
    assert "Exit code: 1" in stub
    assert "5.0s" in stub
    assert "err1" in stub
    assert "attempt 1 output" in stub

    # Attempt 2 details
    assert "Attempt 2" in stub
    assert "Exit code: 0" in stub
    assert "8.3s" in stub
    assert "err2" in stub
    assert "attempt 2 output longer" in stub


def test_write_planner_crash_stub_single_attempt(tmp_path):
    """write_planner_crash_stub works with a single attempt."""
    (tmp_path / "experiments").mkdir()

    attempts = [
        DispatchResult(exit_code=-1, stdout="", stderr="timeout", elapsed_seconds=600.0),
    ]
    write_planner_crash_stub(tmp_path, "exp_001", "path_beta", attempts)

    stub_path = tmp_path / "experiments" / "exp_001_plan_crash.md"
    stub = stub_path.read_text()

    assert "1 attempt(s)" in stub
    assert "path_beta" in stub
    assert "Exit code: -1" in stub
    assert "(empty)" in stub  # empty stdout
    assert "timeout" in stub


# --- parse_stream_json_diagnostics tests ---


def test_parse_stream_json_diagnostics_normal():
    """JSONL with tool_use and text events returns correct diagnostics."""
    jsonl = "\n".join([
        '{"type": "assistant", "message": {"content": [{"type": "text", "text": "Let me read the file."}]}}',
        '{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"path": "foo.py"}}]}}',
        '{"type": "assistant", "message": {"content": [{"type": "text", "text": "Now I will edit it."}]}}',
        '{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Write", "input": {"path": "bar.py"}}]}}',
        '{"type": "result", "result": "done"}',
    ])
    diag = parse_stream_json_diagnostics(jsonl)
    assert diag["tool_count"] == 2
    assert diag["last_tool"] == "Write"
    assert "Now I will edit it." in diag["partial_text"]


def test_parse_stream_json_diagnostics_empty():
    """Empty string returns empty diagnostics."""
    diag = parse_stream_json_diagnostics("")
    assert diag["tool_count"] == 0
    assert diag["last_tool"] == ""
    assert diag["partial_text"] == ""


def test_parse_stream_json_diagnostics_no_tool_use():
    """JSONL with only text events returns 0 tools and the text."""
    jsonl = "\n".join([
        '{"type": "assistant", "message": {"content": [{"type": "text", "text": "Thinking about the problem..."}]}}',
        '{"type": "assistant", "message": {"content": [{"type": "text", "text": "I need more context."}]}}',
    ])
    diag = parse_stream_json_diagnostics(jsonl)
    assert diag["tool_count"] == 0
    assert diag["last_tool"] == ""
    assert "Thinking about the problem..." in diag["partial_text"]
    assert "I need more context." in diag["partial_text"]


def test_parse_stream_json_diagnostics_invalid_lines():
    """Non-JSON lines are skipped gracefully."""
    jsonl = "\n".join([
        "not json at all",
        '{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}}',
        "another bad line",
    ])
    diag = parse_stream_json_diagnostics(jsonl)
    assert diag["tool_count"] == 1
    assert diag["last_tool"] == "Bash"


def test_parse_stream_json_diagnostics_truncates_text():
    """Partial text is truncated to last 500 chars."""
    long_text = "x" * 1000
    jsonl = f'{{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "{long_text}"}}]}}}}'
    diag = parse_stream_json_diagnostics(jsonl)
    assert len(diag["partial_text"]) == 500


def test_crash_stub_includes_stream_diagnostics(tmp_path):
    """Crash stub includes agent progress from stream-json output."""
    (tmp_path / "experiments").mkdir()
    plan_path = tmp_path / "experiments" / "001_plan.md"
    plan_path.write_text("# Plan\nStream test")

    jsonl_stdout = "\n".join([
        '{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]}}',
        '{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}}',
        '{"type": "assistant", "message": {"content": [{"type": "text", "text": "Installing deps..."}]}}',
    ])
    dr = DispatchResult(
        exit_code=-1, stdout=jsonl_stdout, stderr="", elapsed_seconds=600.0,
    )
    write_crash_stub(tmp_path, "001", plan_path, dr, timeout_minutes=10.0)

    stub = (tmp_path / "experiments" / "001_results.md").read_text()
    assert "Agent Progress" in stub
    assert "Tool calls completed: 2" in stub
    assert "Last tool used: Bash" in stub
    assert "Installing deps..." in stub


# --- Stream-json token extraction (foray-7d8) ---


def test_parse_stream_json_tokens_extracts_usage():
    """Extracts input_tokens, output_tokens, cost from result event."""
    stream = json.dumps({
        "type": "result",
        "subtype": "success",
        "usage": {
            "input_tokens": 5000,
            "output_tokens": 1200,
            "cache_creation_input_tokens": 3000,
            "cache_read_input_tokens": 2000,
        },
        "total_cost_usd": 0.058,
    })
    result = parse_stream_json_tokens(stream)
    assert result["input_tokens"] == 5000
    assert result["output_tokens"] == 1200
    assert result["cost_usd"] == 0.058


def test_parse_stream_json_tokens_no_result_event():
    """Returns zeros when no result event in output."""
    stream = json.dumps({"type": "assistant", "message": {"content": []}})
    result = parse_stream_json_tokens(stream)
    assert result["input_tokens"] == 0
    assert result["output_tokens"] == 0
    assert result["cost_usd"] == 0.0


def test_parse_stream_json_tokens_multiline():
    """Finds result event among multiple stream-json lines."""
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "assistant", "message": {"content": []}}),
        json.dumps({
            "type": "result", "subtype": "success",
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "total_cost_usd": 0.01,
        }),
    ]
    result = parse_stream_json_tokens("\n".join(lines))
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 50


def test_parse_stream_json_tokens_empty():
    """Returns zeros for empty string."""
    result = parse_stream_json_tokens("")
    assert result["input_tokens"] == 0
    assert result["output_tokens"] == 0
    assert result["cost_usd"] == 0.0
