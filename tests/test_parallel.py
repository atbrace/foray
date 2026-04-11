"""Tests for parallel experiment execution within rounds."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from foray.models import (
    Confidence,
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
from foray.orchestrator import Orchestrator
from foray.state import (
    init_directory,
    read_findings,
    read_paths,
    read_run_state,
    write_paths,
)


def _make_config(vision_path: str = "vision.md", **overrides) -> RunConfig:
    return RunConfig(vision_path=vision_path, **overrides)


def _make_state(config: RunConfig | None = None, **overrides) -> RunState:
    c = config or _make_config()
    return RunState(start_time=datetime.now(timezone.utc), config=c, **overrides)


def _make_path(id: str, status: PathStatus = PathStatus.OPEN) -> PathInfo:
    return PathInfo(
        id=id, description=f"Test path {id}",
        priority=Priority.HIGH, hypothesis=f"Test hypothesis for {id}",
        status=status,
    )


def _make_finding(exp_id: str, path_id: str, status: ExperimentStatus = ExperimentStatus.SUCCESS) -> Finding:
    return Finding(
        experiment_id=exp_id, path_id=path_id,
        status=status, summary="ok", one_liner="ok",
    )


def _make_evaluation(exp_id: str, path_id: str) -> Evaluation:
    return Evaluation(
        experiment_id=exp_id, path_id=path_id, outcome="conclusive",
        path_status=PathStatus.OPEN, confidence=Confidence.HIGH, summary="done",
    )


def _setup_foray_dir(tmp_path: Path, config: RunConfig | None = None, paths: list[PathInfo] | None = None) -> Path:
    """Initialize a foray directory with given config and paths."""
    c = config or _make_config()
    state = _make_state(config=c)
    foray_dir = init_directory(tmp_path, state)
    if paths:
        write_paths(foray_dir, paths)
    return foray_dir


# --- ExperimentResult model ---


def test_experiment_result_model():
    finding = _make_finding("001", "a")
    result = ExperimentResult(
        experiment_id="001",
        path_id="a",
        exp_status=ExperimentStatus.SUCCESS,
        finding=finding,
    )
    assert result.experiment_id == "001"
    assert result.assessment is None
    assert result.finding.status == ExperimentStatus.SUCCESS


def test_experiment_result_with_assessment():
    finding = _make_finding("001", "a")
    assessment = _make_evaluation("001", "a")
    result = ExperimentResult(
        experiment_id="001",
        path_id="a",
        exp_status=ExperimentStatus.SUCCESS,
        finding=finding,
        assessment=assessment,
    )
    assert result.assessment is not None
    assert result.assessment.path_status == PathStatus.OPEN


# --- _run_experiment returns ExperimentResult, catches exceptions ---


@patch("foray.orchestrator.dispatch")
@patch("foray.orchestrator.dispatch_executor")
@patch("foray.orchestrator.create_worktree")
@patch("foray.orchestrator.cleanup_worktree")
@patch("foray.orchestrator.copy_artifacts")
@patch("foray.orchestrator.enforce_worktree_limit")
@patch("foray.orchestrator.read_evaluation")
def test_run_experiment_returns_result(
    mock_eval, mock_enforce, mock_copy, mock_cleanup, mock_create_wt,
    mock_dispatch_exec, mock_dispatch, tmp_path,
):
    config = _make_config()
    foray_dir = _setup_foray_dir(tmp_path, config)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir
    orch._run_start = 0.0
    orch._prompt_cache = {"planner": "p", "executor": "e", "evaluator": "ev"}

    path = _make_path("a")
    state = _make_state(config=config)

    # Planner writes plan file
    plan_path = foray_dir / "experiments" / "001_plan.md"

    def write_plan(*args, **kwargs):
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text("# Plan\nTest plan")
        return MagicMock(exit_code=0, stdout="", stderr="", elapsed_seconds=1.0)

    mock_dispatch.side_effect = write_plan

    # Executor writes results
    results_path = foray_dir / "experiments" / "001_results.md"

    def write_results(*args, **kwargs):
        results_path.write_text("## Status\nSUCCESS\n\n## Findings\nTest")
        return MagicMock(exit_code=0, stdout="", stderr="", elapsed_seconds=5.0)

    mock_dispatch_exec.side_effect = write_results
    mock_create_wt.return_value = tmp_path / "worktree"
    (tmp_path / "worktree").mkdir()

    # Evaluator writes assessment
    assessment = _make_evaluation("001", "a")
    mock_eval.return_value = assessment

    result = orch._run_experiment(path, "001", [], state)

    assert isinstance(result, ExperimentResult)
    assert result.experiment_id == "001"
    assert result.path_id == "a"
    assert result.exp_status == ExperimentStatus.SUCCESS
    assert result.finding.status == ExperimentStatus.SUCCESS
    assert result.assessment is not None


@patch("foray.orchestrator.dispatch")
def test_run_experiment_crash_on_exception(mock_dispatch, tmp_path):
    config = _make_config()
    foray_dir = _setup_foray_dir(tmp_path, config)
    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir
    orch._run_start = 0.0
    orch._prompt_cache = {"planner": "p", "executor": "e", "evaluator": "ev"}

    mock_dispatch.side_effect = RuntimeError("dispatch exploded")

    path = _make_path("a")
    state = _make_state(config=config)
    result = orch._run_experiment(path, "001", [], state)

    assert isinstance(result, ExperimentResult)
    assert result.exp_status == ExperimentStatus.CRASH
    assert "dispatch exploded" in result.finding.summary


# --- _apply_experiment_result updates state ---


def test_apply_experiment_result_updates_state(tmp_path):
    config = _make_config()
    paths = [_make_path("a"), _make_path("b")]
    foray_dir = _setup_foray_dir(tmp_path, config, paths=paths)

    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir

    assessment = _make_evaluation("001", "a")
    finding = _make_finding("001", "a")
    result = ExperimentResult(
        experiment_id="001",
        path_id="a",
        exp_status=ExperimentStatus.SUCCESS,
        finding=finding,
        assessment=assessment,
    )

    orch._apply_experiment_result(result)

    # Finding was added
    findings = read_findings(foray_dir)
    assert len(findings) == 1
    assert findings[0].experiment_id == "001"

    # Experiment count incremented
    state = read_run_state(foray_dir)
    assert state.experiment_count == 1
    assert state.last_completed_experiment == "001"

    # Path experiment_count updated
    updated_paths = read_paths(foray_dir)
    path_a = next(p for p in updated_paths if p.id == "a")
    assert path_a.experiment_count == 1


# --- Budget trimming ---


def test_budget_trimming(tmp_path):
    """With max_experiments=7 and experiment_count=5, only 2 of 4 paths should run."""
    config = _make_config(max_experiments=7, max_concurrent=2)
    paths = [_make_path("a"), _make_path("b"), _make_path("c"), _make_path("d")]
    foray_dir = _setup_foray_dir(tmp_path, config, paths=paths)

    # Update experiment count to 5
    state = read_run_state(foray_dir)
    state.experiment_count = 5
    from foray.state import write_run_state
    write_run_state(foray_dir, state)

    orch = Orchestrator(tmp_path, config)
    orch.foray_dir = foray_dir
    orch._run_start = 0.0
    orch._prompt_cache = {"planner": "p", "executor": "e", "evaluator": "ev"}

    # Track how many experiments are dispatched
    dispatched_ids: list[str] = []
    original_run = orch._run_experiment

    def tracking_run(path, exp_id, findings, state):
        dispatched_ids.append(exp_id)
        return ExperimentResult(
            experiment_id=exp_id,
            path_id=path.id,
            exp_status=ExperimentStatus.SUCCESS,
            finding=_make_finding(exp_id, path.id),
            assessment=_make_evaluation(exp_id, path.id),
        )

    with patch.object(orch, "_run_experiment", side_effect=tracking_run):
        with patch.object(orch, "_run_synthesis"):
            orch.run()

    # Should have launched exactly 2 experiments (budget: 7 - 5 = 2)
    assert len(dispatched_ids) == 2


# --- Concurrent worktree creation ---


def test_concurrent_worktree_creation(git_repo):
    """ThreadPoolExecutor creates 3 worktrees simultaneously on same git repo."""
    from concurrent.futures import ThreadPoolExecutor

    from foray.worktree import cleanup_worktree, create_worktree

    foray_dir = git_repo / ".foray"
    foray_dir.mkdir()
    (foray_dir / "worktrees").mkdir()

    ids = ["001", "002", "003"]
    results: list[Path] = []

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(create_worktree, git_repo, exp_id, foray_dir)
            for exp_id in ids
        ]
        for f in futures:
            results.append(f.result())

    # All 3 worktrees exist
    assert len(results) == 3
    for wt_path in results:
        assert wt_path.exists()
        assert (wt_path / "README.md").exists()

    # Cleanup
    for wt_path in results:
        cleanup_worktree(git_repo, wt_path)
