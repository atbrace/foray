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
