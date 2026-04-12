import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from foray.models import (
    Confidence,
    DispatchResult,
    Evaluation,
    ExperimentResult,
    ExperimentStatus,
    Finding,
    PathInfo,
    PathStatus,
    Priority,
    RunConfig,
    RunState,
)
from foray.orchestrator import Orchestrator, apply_guardrails
from foray.state import init_directory


def _path(id: str = "a") -> PathInfo:
    return PathInfo(id=id, description="test", priority=Priority.HIGH, hypothesis="test")


def _assessment(
    path_id: str = "a",
    path_status: PathStatus = PathStatus.RESOLVED,
    confidence: Confidence = Confidence.HIGH,
    blocker: str = "",
) -> Evaluation:
    return Evaluation(
        experiment_id="001", path_id=path_id, outcome="conclusive",
        path_status=path_status, confidence=confidence, summary="done",
        blocker_description=blocker,
    )


def _finding(
    exp_id: str, path_id: str, status: ExperimentStatus = ExperimentStatus.SUCCESS,
) -> Finding:
    return Finding(experiment_id=exp_id, path_id=path_id, status=status, summary="ok", one_liner="ok")


# --- Resolved requires depth ---


def test_resolved_rejected_with_one_experiment():
    findings = [_finding("001", "a")]
    assert apply_guardrails(_assessment(), _path(), findings) == PathStatus.OPEN


def test_resolved_accepted_with_two_experiments():
    findings = [_finding("001", "a"), _finding("002", "a")]
    assert apply_guardrails(_assessment(), _path(), findings) == PathStatus.RESOLVED


def test_resolved_only_counts_non_failures():
    findings = [
        _finding("001", "a"),
        _finding("002", "a", ExperimentStatus.FAILED),
    ]
    assert apply_guardrails(_assessment(), _path(), findings) == PathStatus.OPEN


# --- Low confidence blocks resolution ---


def test_low_confidence_blocks_resolution():
    findings = [_finding("001", "a"), _finding("002", "a")]
    assert apply_guardrails(_assessment(confidence=Confidence.LOW), _path(), findings) == PathStatus.OPEN


def test_medium_confidence_allows_resolution():
    findings = [_finding("001", "a"), _finding("002", "a")]
    assert apply_guardrails(_assessment(confidence=Confidence.MEDIUM), _path(), findings) == PathStatus.RESOLVED


# --- Blocked requires evidence ---


def test_blocked_rejected_without_description():
    assert apply_guardrails(_assessment(path_status=PathStatus.BLOCKED, blocker=""), _path(), []) == PathStatus.OPEN


def test_blocked_accepted_with_description():
    a = _assessment(path_status=PathStatus.BLOCKED, blocker="Cannot install dependency")
    assert apply_guardrails(a, _path(), []) == PathStatus.BLOCKED


# --- Pass-through ---


def test_open_passes_through():
    assert apply_guardrails(_assessment(path_status=PathStatus.OPEN), _path(), []) == PathStatus.OPEN


def test_inconclusive_passes_through():
    assert apply_guardrails(_assessment(path_status=PathStatus.INCONCLUSIVE), _path(), []) == PathStatus.INCONCLUSIVE


# --- Exhaustion scenarios ---


def test_exhausted_path_can_be_resolved():
    """After exhaustion, if evaluator says resolved and path has 2+ successes, it resolves."""
    findings = [
        _finding("001", "a"),
        _finding("002", "a"),
        _finding("003", "a", ExperimentStatus.EXHAUSTED),
    ]
    assert apply_guardrails(_assessment(path_status=PathStatus.RESOLVED), _path(), findings) == PathStatus.RESOLVED


def test_exhausted_path_can_be_inconclusive():
    """After exhaustion, evaluator can mark path inconclusive."""
    findings = [
        _finding("001", "a"),
        _finding("002", "a", ExperimentStatus.EXHAUSTED),
    ]
    assert apply_guardrails(
        _assessment(path_status=PathStatus.INCONCLUSIVE), _path(), findings
    ) == PathStatus.INCONCLUSIVE


# --- Evaluator failure diagnostics (GH-18) ---


@patch("foray.orchestrator.dispatch")
@patch("foray.orchestrator.dispatch_executor")
@patch("foray.orchestrator.create_worktree")
@patch("foray.orchestrator.cleanup_worktree")
@patch("foray.orchestrator.copy_artifacts")
@patch("foray.orchestrator.enforce_worktree_limit")
@patch("foray.orchestrator.read_evaluation")
def test_evaluator_failure_logs_diagnostics(
    mock_eval, mock_enforce, mock_copy, mock_cleanup, mock_create_wt,
    mock_dispatch_exec, mock_dispatch, tmp_path, caplog,
):
    """When evaluator produces no assessment file, warning is logged with exit code and stderr."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir
    orch._run_start = 0.0
    orch._prompt_cache = {"planner": "p", "executor": "e", "evaluator": "ev"}

    path = PathInfo(
        id="a", description="test", priority=Priority.HIGH, hypothesis="test",
    )

    # Planner and evaluator both use dispatch; differentiate by prompt content
    plan_path = foray_dir / "experiments" / "001_plan.md"

    def dispatch_side_effect(prompt: str, **kwargs):
        if "ev" in prompt and "_eval.json" in prompt:
            # Evaluator call — return failure diagnostics
            return MagicMock(exit_code=1, stdout="", stderr="model overloaded error", elapsed_seconds=2.0)
        # Planner call
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text("# Plan\nTest plan")
        return MagicMock(exit_code=0, stdout="", stderr="", elapsed_seconds=1.0)

    mock_dispatch.side_effect = dispatch_side_effect

    # Executor writes results (SUCCESS)
    results_path = foray_dir / "experiments" / "001_results.md"

    def write_results(*args, **kwargs):
        results_path.write_text("## Status\nSUCCESS\n\n## Findings\nTest")
        return MagicMock(exit_code=0, stdout="", stderr="", elapsed_seconds=5.0)

    mock_dispatch_exec.side_effect = write_results
    mock_create_wt.return_value = tmp_path / "worktree"
    (tmp_path / "worktree").mkdir()

    # Evaluator returns None (no assessment file written)
    mock_eval.return_value = None

    with caplog.at_level(logging.WARNING, logger="foray.orchestrator"):
        result = orch._run_experiment(path, "001", [], state)

    assert result.finding.summary == "(assessment failed)"
    assert result.assessment is None
    # Verify diagnostic warning was logged
    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("Evaluator produced no assessment for 001" in m for m in warning_msgs)
    assert any("exit=1" in m for m in warning_msgs)
    assert any("model overloaded error" in m for m in warning_msgs)


# --- Synthesis on early termination (GH-17) ---


@patch("foray.orchestrator.Orchestrator._run_synthesis")
@patch("foray.orchestrator.read_findings")
@patch("foray.orchestrator.read_paths")
@patch("foray.orchestrator.read_run_state")
@patch("foray.orchestrator.check_consecutive_failures")
def test_synthesis_runs_after_circuit_breaker(
    mock_circuit, mock_read_state, mock_read_paths, mock_read_findings, mock_synth, tmp_path,
):
    """Synthesis must run even when circuit breaker fires."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config, experiment_count=5)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir
    orch._run_start = 0.0

    mock_read_state.return_value = state
    mock_read_paths.return_value = [_path()]
    mock_read_findings.return_value = [_finding("001", "a")]
    mock_circuit.return_value = True  # circuit breaker fires

    orch.run()
    mock_synth.assert_called_once()


@patch("foray.orchestrator.Orchestrator._run_synthesis")
@patch("foray.orchestrator.Orchestrator._apply_experiment_result")
@patch("foray.orchestrator.Orchestrator._run_experiment")
@patch("foray.orchestrator.read_findings")
@patch("foray.orchestrator.read_paths")
@patch("foray.orchestrator.read_run_state")
@patch("foray.orchestrator.read_rounds")
@patch("foray.orchestrator.write_run_state")
@patch("foray.orchestrator.write_rounds")
@patch("foray.orchestrator.should_continue")
@patch("foray.orchestrator.check_consecutive_failures")
@patch("foray.orchestrator.get_round_paths")
def test_synthesis_runs_after_exception_in_merge(
    mock_get_round, mock_circuit, mock_should_continue,
    mock_write_rounds, mock_write_state, mock_read_rounds,
    mock_read_state, mock_read_paths, mock_read_findings,
    mock_run_exp, mock_apply, mock_synth, tmp_path,
):
    """Synthesis must run even when an exception occurs during merge phase."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config, experiment_count=0)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir
    orch._run_start = 0.0
    orch._prompt_cache = {"planner": "p", "executor": "e", "evaluator": "ev"}

    mock_read_state.return_value = state
    mock_read_paths.return_value = [_path()]
    mock_read_findings.return_value = []
    mock_should_continue.return_value = True
    mock_circuit.return_value = False
    mock_get_round.return_value = ["a"]
    mock_read_rounds.return_value = []

    result = ExperimentResult(
        experiment_id="001", path_id="a", exp_status=ExperimentStatus.SUCCESS,
        finding=_finding("001", "a"),
    )
    mock_run_exp.return_value = result
    mock_apply.side_effect = RuntimeError("State corruption")

    # The exception should propagate, but synthesis must still run
    try:
        orch.run()
    except RuntimeError:
        pass
    mock_synth.assert_called_once()


# --- Synthesis retry and failure ---


@patch("foray.orchestrator.dispatch")
@patch("foray.orchestrator.build_synthesizer_context", return_value="ctx")
def test_synthesis_retries_on_failure(mock_ctx, mock_dispatch, tmp_path):
    """Synthesis retries once when first dispatch fails to create synthesis.md."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir
    orch._run_start = 0.0
    orch._prompt_cache = {"synthesizer": "synth prompt"}

    synthesis_path = foray_dir / "synthesis.md"

    fail_result = DispatchResult(exit_code=1, stdout="", stderr="agent crashed", elapsed_seconds=5.0)
    ok_result = DispatchResult(exit_code=0, stdout="", stderr="", elapsed_seconds=10.0)

    call_count = 0

    def side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return fail_result
        # Second call: create the file and succeed
        synthesis_path.write_text("# Report")
        return ok_result

    mock_dispatch.side_effect = side_effect

    orch._run_synthesis()

    assert mock_dispatch.call_count == 2
    assert synthesis_path.exists()


@patch("foray.orchestrator.dispatch")
@patch("foray.orchestrator.build_synthesizer_context", return_value="ctx")
def test_synthesis_logs_warning_on_double_failure(mock_ctx, mock_dispatch, tmp_path, caplog):
    """Both synthesis attempts fail — warning is logged, no exception raised."""
    config = RunConfig(vision_path="vision.md")
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    foray_dir = init_directory(tmp_path, state)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir
    orch._run_start = 0.0
    orch._prompt_cache = {"synthesizer": "synth prompt"}

    fail_result = DispatchResult(exit_code=1, stdout="", stderr="agent crashed", elapsed_seconds=5.0)
    mock_dispatch.return_value = fail_result

    with caplog.at_level(logging.WARNING, logger="foray.orchestrator"):
        orch._run_synthesis()  # Should not raise

    assert mock_dispatch.call_count == 2
    assert not (foray_dir / "synthesis.md").exists()
    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("Synthesis failed after 2 attempts" in m for m in warning_msgs)
