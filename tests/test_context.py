import json
from datetime import datetime, timezone
from pathlib import Path

from foray.context import (
    build_evaluator_context,
    build_executor_context,
    build_planner_context,
    build_synthesizer_context,
    estimate_tokens,
)
from foray.models import (
    Evaluation,
    ExperimentStatus,
    Finding,
    PathInfo,
    Priority,
    RunConfig,
    RunState,
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
    """Last 3 experiments get briefs, older get one-liners."""
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()

    findings = [_finding(f"{i:03d}", "path-a") for i in range(1, 6)]

    ctx = build_planner_context(tmp_path, _path(), findings, _state(), needs_justification=False)

    # Older experiments: one-liners only
    assert "One-liner for 001" in ctx
    assert "One-liner for 002" in ctx
    # Recent experiments: summaries (planner_brief falls back to summary when empty)
    assert "Summary for 003" in ctx
    assert "Summary for 004" in ctx
    assert "Summary for 005" in ctx


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


def test_executor_context_excludes_vision(tmp_path: Path):
    """Executor should NOT receive vision.md — the plan already incorporates it."""
    (tmp_path / "vision.md").write_text("Test vision content that should not appear")
    (tmp_path / "codebase_map.md").write_text("# Project Map")
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("# Plan\nDo the thing")

    ctx = build_executor_context(tmp_path, plan_path)
    assert "Do the thing" in ctx
    assert "Project Map" in ctx
    assert "Test vision" not in ctx


def test_evaluator_context_excludes_vision(tmp_path: Path):
    """Evaluator should NOT receive vision.md — path description/hypothesis suffice."""
    (tmp_path / "vision.md").write_text("Test vision content that should not appear")
    (tmp_path / "experiments").mkdir()
    (tmp_path / "experiments" / "001_results.md").write_text("## Status\nSUCCESS\n\nResults here")

    ctx = build_evaluator_context(tmp_path, "001", _path(), [])
    assert "Results here" in ctx
    assert "Test vision" not in ctx


def test_planner_uses_briefs_instead_of_results_files(tmp_path: Path):
    """Recent experiments should show planner_brief, not full results files."""
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()

    # Create a results file that should NOT be read
    (tmp_path / "experiments" / "001_results.md").write_text(
        "## Status\nSUCCESS\n\nVery long detailed results with code blocks"
    )

    findings = [Finding(
        experiment_id="001", path_id="path-a", status=ExperimentStatus.SUCCESS,
        summary="Summary for 001", one_liner="One-liner for 001",
        planner_brief="Tested X approach, found Y, no blockers.",
    )]

    ctx = build_planner_context(tmp_path, _path(), findings, _state(), needs_justification=False)

    # Should contain the brief, not the raw results file content
    assert "Tested X approach, found Y, no blockers." in ctx
    assert "Very long detailed results with code blocks" not in ctx


def test_planner_brief_on_evaluation_model():
    """Evaluation model should have planner_brief field."""
    eval_json = json.dumps({
        "experiment_id": "001",
        "path_id": "path-a",
        "outcome": "conclusive",
        "path_status": "open",
        "confidence": "high",
        "summary": "Found strong evidence",
        "planner_brief": "Tested grep-based search. Found 12 matches. No blockers.",
    })
    evaluation = Evaluation.model_validate_json(eval_json)
    assert evaluation.planner_brief == "Tested grep-based search. Found 12 matches. No blockers."


def test_planner_brief_null_coercion():
    """planner_brief should coerce null to empty string (agent output defense)."""
    eval_json = json.dumps({
        "experiment_id": "001",
        "path_id": "path-a",
        "outcome": "conclusive",
        "path_status": "open",
        "confidence": "high",
        "summary": "Found strong evidence",
        "planner_brief": None,
    })
    evaluation = Evaluation.model_validate_json(eval_json)
    assert evaluation.planner_brief == ""


def test_finding_has_planner_brief():
    """Finding model should carry planner_brief from evaluation."""
    finding = Finding(
        experiment_id="001", path_id="path-a", status=ExperimentStatus.SUCCESS,
        summary="Found it", one_liner="Found it",
        planner_brief="Used static analysis on src/. Found 3 patterns.",
    )
    assert finding.planner_brief == "Used static analysis on src/. Found 3 patterns."


def test_synthesizer_context(tmp_path: Path):
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "findings.json").write_text("[]")
    (tmp_path / "state" / "paths.json").write_text("[]")

    ctx = build_synthesizer_context(tmp_path)
    assert "Test vision" in ctx
    assert "All Findings" in ctx
