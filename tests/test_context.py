from datetime import datetime, timezone
from pathlib import Path

from foray.models import (
    ExperimentStatus,
    Finding,
    PathInfo,
    Priority,
    RunConfig,
    RunState,
)
from foray.context import (
    build_evaluator_context,
    build_executor_context,
    build_planner_context,
    build_synthesizer_context,
    estimate_tokens,
)


def _finding(id: str, path_id: str, status: ExperimentStatus = ExperimentStatus.SUCCESS) -> Finding:
    return Finding(
        experiment_id=id, path_id=path_id, status=status,
        summary=f"Summary for {id}", one_liner=f"One-liner for {id}",
    )


def _state() -> RunState:
    return RunState(
        start_time=datetime.now(timezone.utc),
        config=RunConfig(vision_path="V.md"),
        current_round=2, experiment_count=5,
    )


def _path(id: str = "path-a") -> PathInfo:
    return PathInfo(id=id, description="Test path", priority=Priority.HIGH, hypothesis="Test", experiment_count=5)


def test_estimate_tokens():
    assert estimate_tokens("one two three four five") == int(5 * 1.3)


def test_planner_progressive_summarization(tmp_path: Path):
    """Last 3 experiments get full detail, older get one-liners."""
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()

    findings = [_finding(f"{i:03d}", "path-a") for i in range(1, 6)]

    for eid in ["003", "004", "005"]:
        (tmp_path / "experiments" / f"{eid}_results.md").write_text(
            f"## Status\nSUCCESS\n\nDetailed results for {eid}"
        )

    ctx = build_planner_context(tmp_path, _path(), findings, _state(), needs_justification=False)

    # Older experiments: one-liners only
    assert "One-liner for 001" in ctx
    assert "One-liner for 002" in ctx
    # Recent experiments: full detail
    assert "Detailed results for 003" in ctx
    assert "Detailed results for 004" in ctx
    assert "Detailed results for 005" in ctx


def test_planner_justification_requirement(tmp_path: Path):
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()

    ctx = build_planner_context(tmp_path, _path(), [], _state(), needs_justification=True)
    assert "Justification for Continued Investment" in ctx


def test_planner_failure_summary(tmp_path: Path):
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()

    findings = [
        _finding("001", "path-a", ExperimentStatus.FAILED),
        _finding("002", "path-a", ExperimentStatus.SUCCESS),
    ]
    ctx = build_planner_context(tmp_path, _path(), findings, _state(), needs_justification=False)
    assert "FAILED" in ctx
    assert "One-liner for 001" in ctx


def test_executor_context(tmp_path: Path):
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "codebase_map.md").write_text("# Project Map")
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("# Plan\nDo the thing")

    ctx = build_executor_context(tmp_path, plan_path)
    assert "Do the thing" in ctx
    assert "Project Map" in ctx
    assert "Test vision" in ctx


def test_evaluator_context(tmp_path: Path):
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()
    (tmp_path / "experiments" / "001_results.md").write_text("## Status\nSUCCESS\n\nResults here")

    ctx = build_evaluator_context(tmp_path, "001", _path(), [])
    assert "Results here" in ctx
    assert "Test vision" in ctx


def test_synthesizer_context(tmp_path: Path):
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "findings.json").write_text("[]")
    (tmp_path / "state" / "paths.json").write_text("[]")

    ctx = build_synthesizer_context(tmp_path)
    assert "Test vision" in ctx
    assert "All Findings" in ctx
