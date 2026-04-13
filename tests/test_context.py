import json
from datetime import datetime, timezone
from pathlib import Path

from foray.context import (
    BUDGETS,
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


def test_planner_failure_shown_in_recent(tmp_path: Path):
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()

    findings = [
        _finding("001", "path-a", ExperimentStatus.FAILED),
        _finding("002", "path-a", ExperimentStatus.SUCCESS),
    ]
    ctx = build_planner_context(tmp_path, _path(), findings, _state(), needs_justification=False)
    assert "FAILED" in ctx
    # Failures appear in Recent Experiments with their status
    assert "Exp 001: [FAILED]" in ctx
    # No separate failure summary section
    assert "Failed Experiments" not in ctx


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


def test_evaluation_missing_path_status_defaults_to_open():
    """Evaluator may omit path_status — should default to OPEN, not crash."""
    eval_json = json.dumps({
        "experiment_id": "011",
        "path_id": "path-a",
        "outcome": "conclusive",
        "confidence": "medium",
        "summary": "Found evidence",
        "data_type": "synthetic",
    })
    evaluation = Evaluation.model_validate_json(eval_json)
    assert evaluation.path_status == "open"


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


def test_planner_includes_evidence_against(tmp_path: Path):
    """Recent experiments should show evidence_against from eval.json."""
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()

    findings = [_finding("001", "path-a")]

    # Write a corresponding eval.json with evidence_against
    eval_obj = Evaluation(
        experiment_id="001",
        path_id="path-a",
        outcome="conclusive",
        path_status="open",
        confidence="high",
        summary="Found strong evidence",
        evidence_against={"real-photo-outer-contour": "strong", "background-noise": "moderate"},
    )
    (tmp_path / "experiments" / "001_eval.json").write_text(eval_obj.model_dump_json())

    ctx = build_planner_context(tmp_path, _path(), findings, _state(), needs_justification=False)
    assert "Evidence against:" in ctx
    assert "real-photo-outer-contour (strong)" in ctx
    assert "background-noise (moderate)" in ctx


def test_planner_no_evidence_against_when_empty(tmp_path: Path):
    """No 'Evidence against:' line when evidence_against dict is empty."""
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()

    findings = [_finding("001", "path-a")]

    # Write eval.json with empty evidence_against
    eval_obj = Evaluation(
        experiment_id="001",
        path_id="path-a",
        outcome="conclusive",
        path_status="open",
        confidence="high",
        summary="Found strong evidence",
        evidence_against={},
    )
    (tmp_path / "experiments" / "001_eval.json").write_text(eval_obj.model_dump_json())

    ctx = build_planner_context(tmp_path, _path(), findings, _state(), needs_justification=False)
    assert "Evidence against:" not in ctx


def test_planner_no_evidence_against_when_no_eval_file(tmp_path: Path):
    """No 'Evidence against:' line when eval.json doesn't exist."""
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()

    findings = [_finding("001", "path-a")]

    ctx = build_planner_context(tmp_path, _path(), findings, _state(), needs_justification=False)
    assert "Evidence against:" not in ctx


def test_synthesizer_context(tmp_path: Path):
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "findings.json").write_text("[]")
    (tmp_path / "state" / "paths.json").write_text("[]")

    ctx = build_synthesizer_context(tmp_path)
    assert "Test vision" in ctx
    assert "Findings by Path" in ctx


# --- #3: Progressive summarization for synthesizer context ---


def test_synthesizer_progressive_summarization(tmp_path: Path):
    """Synthesizer groups findings by path, recent get full summaries, older get one-liners."""
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "state").mkdir()

    findings = []
    for i in range(1, 8):
        findings.append(Finding(
            experiment_id=f"{i:03d}", path_id="path-a",
            status=ExperimentStatus.SUCCESS,
            summary=f"Full summary for experiment {i:03d}",
            one_liner=f"One-liner for {i:03d}",
        ))
    findings.append(Finding(
        experiment_id="008", path_id="path-b",
        status=ExperimentStatus.SUCCESS,
        summary="Full summary for experiment 008 on path-b",
        one_liner="One-liner for 008",
    ))

    from foray.state import write_findings, write_paths
    write_findings(tmp_path, findings)

    paths = [_path("path-a"), _path("path-b")]
    write_paths(tmp_path, paths)

    ctx = build_synthesizer_context(tmp_path)

    # path-a has 7 findings: latest 3 (005, 006, 007) get full summaries
    assert "Full summary for experiment 005" in ctx
    assert "Full summary for experiment 006" in ctx
    assert "Full summary for experiment 007" in ctx

    # Older path-a findings (001-004) get one-liners only
    assert "One-liner for 001" in ctx
    assert "One-liner for 002" in ctx
    assert "Full summary for experiment 001" not in ctx

    # path-b has 1 finding: it's "recent" so gets full summary
    assert "Full summary for experiment 008 on path-b" in ctx


# --- #4: Budget enforcement with truncation ---


def test_planner_context_truncation(tmp_path: Path):
    """Planner context truncates oldest findings when over budget."""
    (tmp_path / "vision.md").write_text("V")
    (tmp_path / "experiments").mkdir()

    # Create many findings with long summaries to exceed budget
    findings = [
        Finding(
            experiment_id=f"{i:03d}", path_id="path-a",
            status=ExperimentStatus.SUCCESS,
            summary="x " * 5000,  # ~6500 tokens each
            one_liner=f"Short {i:03d}",
            planner_brief="x " * 5000,
        )
        for i in range(1, 20)
    ]

    ctx = build_planner_context(tmp_path, _path(), findings, _state(), needs_justification=False)
    tokens = estimate_tokens(ctx)
    assert tokens <= BUDGETS["planner"], f"Planner context ({tokens}) exceeds budget ({BUDGETS['planner']})"


def test_planner_truncation_preserves_discarded_hypotheses(tmp_path: Path):
    """Discarded hypotheses survive planner context truncation."""
    (tmp_path / "vision.md").write_text("V")
    (tmp_path / "experiments").mkdir()

    path = _path()
    path = path.model_copy(update={"discarded_hypotheses": ["Grep approach failed due to binary files"]})

    findings = [
        Finding(
            experiment_id=f"{i:03d}", path_id="path-a",
            status=ExperimentStatus.SUCCESS,
            summary="x " * 5000,
            one_liner=f"Short {i:03d}",
            planner_brief="x " * 5000,
        )
        for i in range(1, 20)
    ]

    ctx = build_planner_context(tmp_path, path, findings, _state(), needs_justification=False)
    assert "Discarded Approaches" in ctx
    assert "Grep approach failed due to binary files" in ctx


def test_executor_context_truncation(tmp_path: Path):
    """Executor context truncates codebase map when over budget."""
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("# Plan\nDo the thing")
    (tmp_path / "codebase_map.md").write_text("x " * 50000)

    ctx = build_executor_context(tmp_path, plan_path)
    tokens = estimate_tokens(ctx)
    assert tokens <= BUDGETS["executor"], f"Executor context ({tokens}) exceeds budget ({BUDGETS['executor']})"
    # Plan is preserved, map is truncated
    assert "Do the thing" in ctx


def test_evaluator_context_truncation(tmp_path: Path):
    """Evaluator context truncates oldest assessments when over budget."""
    (tmp_path / "experiments").mkdir()
    (tmp_path / "experiments" / "010_results.md").write_text("## Status\nSUCCESS\n\nResults")

    # Create many assessment files with large content
    findings = []
    for i in range(1, 10):
        findings.append(_finding(f"{i:03d}", "path-a"))
        (tmp_path / "experiments" / f"{i:03d}_eval.json").write_text("x " * 10000)

    ctx = build_evaluator_context(tmp_path, "010", _path(), findings)
    tokens = estimate_tokens(ctx)
    assert tokens <= BUDGETS["evaluator"], f"Evaluator context ({tokens}) exceeds budget ({BUDGETS['evaluator']})"
    # Results for the current experiment are preserved
    assert "Results" in ctx


def test_synthesizer_context_truncation(tmp_path: Path):
    """Synthesizer context stays within budget even with many findings."""
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "state").mkdir()

    findings = [
        Finding(
            experiment_id=f"{i:03d}", path_id=f"path-{i % 3}",
            status=ExperimentStatus.SUCCESS,
            summary="x " * 2000,
            one_liner=f"Short {i:03d}",
        )
        for i in range(1, 60)
    ]

    from foray.state import write_findings, write_paths
    write_findings(tmp_path, findings)
    write_paths(tmp_path, [_path("path-0"), _path("path-1"), _path("path-2")])

    ctx = build_synthesizer_context(tmp_path)
    tokens = estimate_tokens(ctx)
    assert tokens <= BUDGETS["synthesizer"], f"Synthesizer context ({tokens}) exceeds budget ({BUDGETS['synthesizer']})"


def test_evaluator_context_projects_prior_evals(tmp_path: Path):
    """Evaluator context should only include outcome, confidence, summary, planner_brief from prior evals."""
    (tmp_path / "experiments").mkdir()
    (tmp_path / "experiments" / "010_results.md").write_text("## Status\nSUCCESS\n\nResults here")

    # Create a prior finding and its full eval JSON
    findings = [_finding("009", "path-a")]
    full_eval = {
        "experiment_id": "009",
        "path_id": "path-a",
        "outcome": "conclusive",
        "path_status": "open",
        "confidence": "high",
        "summary": "Found strong evidence for approach",
        "planner_brief": "Tested grep approach, found 12 matches",
        "new_questions": ["What about edge cases?"],
        "evidence_for": {"grep-approach": "strong"},
        "evidence_against": {"manual-search": "weak"},
        "blocker_description": "",
        "methodology": "independent",
        "topic_tags": ["search", "grep"],
    }
    (tmp_path / "experiments" / "009_eval.json").write_text(json.dumps(full_eval))

    ctx = build_evaluator_context(tmp_path, "010", _path(), findings)

    # Projected fields should be present
    assert "outcome" in ctx
    assert "confidence" in ctx
    assert "Found strong evidence for approach" in ctx

    # Fields that should NOT be in the projected output
    assert "new_questions" not in ctx
    assert "evidence_for" not in ctx
    assert "evidence_against" not in ctx
    assert "methodology" not in ctx
    assert "topic_tags" not in ctx
    assert "What about edge cases?" not in ctx
    assert "grep-approach" not in ctx


def test_planner_no_failure_double_counting(tmp_path: Path):
    """Failures should not appear in a separate 'Failed Experiments' section
    when they are already shown in Recent/Previous Experiments."""
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()

    findings = [
        _finding("001", "path-a", ExperimentStatus.FAILED),
        _finding("002", "path-a", ExperimentStatus.SUCCESS),
        _finding("003", "path-a", ExperimentStatus.FAILED),
    ]
    ctx = build_planner_context(tmp_path, _path(), findings, _state(), needs_justification=False)

    # The redundant "Failed Experiments" section should not exist
    assert "Failed Experiments" not in ctx
    # But the failures should still be listed in the Recent Experiments section
    assert "Exp 001: [FAILED]" in ctx
    assert "Exp 003: [FAILED]" in ctx


def test_planner_methodology_repetition_warning(tmp_path: Path):
    """Planner context warns when last 3 experiments share >70% tags."""
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()

    findings = [_finding(f"{i:03d}", "path-a") for i in range(1, 4)]

    # Write evals with identical topic_tags
    for i in range(1, 4):
        eval_obj = Evaluation(
            experiment_id=f"{i:03d}", path_id="path-a",
            outcome="conclusive", path_status="open", confidence="high",
            summary="Found evidence",
            topic_tags=["rembg", "carving", "contour"],
        )
        (tmp_path / "experiments" / f"{i:03d}_eval.json").write_text(eval_obj.model_dump_json())

    ctx = build_planner_context(tmp_path, _path(), findings, _state(), needs_justification=False)
    assert "Methodology Repetition Detected" in ctx


def test_planner_no_methodology_repetition_warning(tmp_path: Path):
    """No repetition warning when experiment tags differ substantially."""
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()

    findings = [_finding(f"{i:03d}", "path-a") for i in range(1, 4)]

    tags_per_exp = [
        ["rembg", "carving"],
        ["opencv", "edge-detection"],
        ["ml", "segmentation"],
    ]
    for i, tags in enumerate(tags_per_exp, start=1):
        eval_obj = Evaluation(
            experiment_id=f"{i:03d}", path_id="path-a",
            outcome="conclusive", path_status="open", confidence="high",
            summary="Found evidence", topic_tags=tags,
        )
        (tmp_path / "experiments" / f"{i:03d}_eval.json").write_text(eval_obj.model_dump_json())

    ctx = build_planner_context(tmp_path, _path(), findings, _state(), needs_justification=False)
    assert "Methodology Repetition Detected" not in ctx


def test_build_exhaustion_evaluator_context_includes_rationale(tmp_path: Path):
    """Exhaustion context includes the rationale and full path history."""
    from foray.context import build_exhaustion_evaluator_context

    (tmp_path / "experiments").mkdir()

    path = PathInfo(id="test-path", description="A test path", priority=Priority.HIGH, hypothesis="test hyp")
    findings = [
        Finding(experiment_id="001", path_id="test-path", status=ExperimentStatus.SUCCESS, summary="Found X", one_liner="Found X"),
        Finding(experiment_id="002", path_id="test-path", status=ExperimentStatus.SUCCESS, summary="Confirmed X", one_liner="Confirmed X"),
    ]
    rationale = "All key questions answered. Remaining gaps require real user data."

    ctx = build_exhaustion_evaluator_context(tmp_path, path, findings, rationale)
    assert "EXHAUSTED" in ctx
    assert "All key questions answered" in ctx
    assert "test-path" in ctx
    assert "Found X" in ctx
    assert "Confirmed X" in ctx


# --- Finding annotations in planner context (foray-hzv) ---


def test_planner_context_includes_observations(tmp_path: Path):
    """Planner context surfaces observations from recent findings."""
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()

    findings = [Finding(
        experiment_id="001", path_id="path-a", status=ExperimentStatus.SUCCESS,
        summary="Found patterns", one_liner="Found patterns",
        observations=["Uses monorepo layout", "No integration tests found"],
    )]

    ctx = build_planner_context(tmp_path, _path(), findings, _state(), needs_justification=False)
    assert "Observations:" in ctx
    assert "Uses monorepo layout" in ctx
    assert "No integration tests found" in ctx


def test_planner_context_includes_suggested_next(tmp_path: Path):
    """Planner context surfaces suggested_next from recent findings."""
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()

    findings = [Finding(
        experiment_id="001", path_id="path-a", status=ExperimentStatus.SUCCESS,
        summary="Found patterns", one_liner="Found patterns",
        suggested_next=["Test with empty input", "Try alternative parser"],
    )]

    ctx = build_planner_context(tmp_path, _path(), findings, _state(), needs_justification=False)
    assert "Suggested next:" in ctx
    assert "Test with empty input" in ctx
    assert "Try alternative parser" in ctx


def test_planner_context_omits_empty_annotations(tmp_path: Path):
    """Planner context does not show Observations/Suggested next when empty."""
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()

    findings = [Finding(
        experiment_id="001", path_id="path-a", status=ExperimentStatus.SUCCESS,
        summary="Found patterns", one_liner="Found patterns",
    )]

    ctx = build_planner_context(tmp_path, _path(), findings, _state(), needs_justification=False)
    assert "Observations:" not in ctx
    assert "Suggested next:" not in ctx


# --- Discarded hypotheses in planner context (foray-dhu) ---


def test_planner_context_includes_discarded_hypotheses(tmp_path: Path):
    """Planner context surfaces discarded hypotheses when present on path."""
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()

    path = PathInfo(
        id="path-a", description="Test path", priority=Priority.HIGH,
        hypothesis="Test", experiment_count=3,
        discarded_hypotheses=[
            "OpenCV contour extraction failed due to noise sensitivity",
            "ML segmentation approach diverged from path hypothesis",
        ],
    )

    ctx = build_planner_context(tmp_path, path, [], _state(), needs_justification=False)
    assert "Discarded Approaches (do NOT retry)" in ctx
    assert "OpenCV contour extraction failed due to noise sensitivity" in ctx
    assert "ML segmentation approach diverged from path hypothesis" in ctx


def test_planner_context_omits_discarded_when_empty(tmp_path: Path):
    """Planner context does not show discarded section when list is empty."""
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()

    path = PathInfo(
        id="path-a", description="Test path", priority=Priority.HIGH,
        hypothesis="Test", experiment_count=0,
    )

    ctx = build_planner_context(tmp_path, path, [], _state(), needs_justification=False)
    assert "Discarded Approaches" not in ctx


# --- Strategist context ---


def test_strategist_context_includes_vision(tmp_path: Path):
    from foray.context import build_strategist_context
    from foray.models import StrategyOutput
    (tmp_path / "vision.md").write_text("Explore testing patterns")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "paths.json").write_text("[]")
    (tmp_path / "state" / "findings.json").write_text("[]")
    (tmp_path / "experiments").mkdir()

    ctx = build_strategist_context(tmp_path, _state(), previous_assessment=None)
    assert "Explore testing patterns" in ctx


def test_strategist_context_includes_all_paths(tmp_path: Path):
    from foray.context import build_strategist_context
    from foray.state import write_paths, write_findings
    (tmp_path / "vision.md").write_text("Vision")
    (tmp_path / "state").mkdir()
    (tmp_path / "experiments").mkdir()

    paths = [_path("path-a"), _path("path-b")]
    write_paths(tmp_path, paths)
    write_findings(tmp_path, [])

    ctx = build_strategist_context(tmp_path, _state(), previous_assessment=None)
    assert "path-a" in ctx
    assert "path-b" in ctx


def test_strategist_context_includes_findings_by_path(tmp_path: Path):
    from foray.context import build_strategist_context
    from foray.state import write_paths, write_findings
    (tmp_path / "vision.md").write_text("Vision")
    (tmp_path / "state").mkdir()
    (tmp_path / "experiments").mkdir()

    write_paths(tmp_path, [_path("path-a")])
    write_findings(tmp_path, [
        _finding("001", "path-a"),
        _finding("002", "path-a"),
    ])

    ctx = build_strategist_context(tmp_path, _state(), previous_assessment=None)
    assert "001" in ctx
    assert "002" in ctx


def test_strategist_context_includes_previous_assessment(tmp_path: Path):
    from foray.context import build_strategist_context
    (tmp_path / "vision.md").write_text("Vision")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "paths.json").write_text("[]")
    (tmp_path / "state" / "findings.json").write_text("[]")
    (tmp_path / "experiments").mkdir()

    ctx = build_strategist_context(
        tmp_path, _state(),
        previous_assessment="Path-a is going deep but not advancing the core question",
    )
    assert "Path-a is going deep but not advancing the core question" in ctx


def test_strategist_context_no_previous_assessment(tmp_path: Path):
    from foray.context import build_strategist_context
    (tmp_path / "vision.md").write_text("Vision")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "paths.json").write_text("[]")
    (tmp_path / "state" / "findings.json").write_text("[]")
    (tmp_path / "experiments").mkdir()

    ctx = build_strategist_context(tmp_path, _state(), previous_assessment=None)
    assert "Previous Vision Assessment" not in ctx


def test_strategist_context_includes_budget(tmp_path: Path):
    from foray.context import build_strategist_context
    (tmp_path / "vision.md").write_text("Vision")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "paths.json").write_text("[]")
    (tmp_path / "state" / "findings.json").write_text("[]")
    (tmp_path / "experiments").mkdir()

    ctx = build_strategist_context(tmp_path, _state(), previous_assessment=None)
    assert "50 experiments" in ctx  # from default RunConfig.max_experiments
    assert "8.0 hours" in ctx  # from default RunConfig.hours


# --- Planner vision assessment injection ---


def test_planner_includes_vision_assessment(tmp_path: Path):
    from foray.state import write_strategy
    from foray.models import StrategyOutput
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()
    (tmp_path / "state").mkdir()

    write_strategy(tmp_path, StrategyOutput(
        vision_assessment="Path-a is central to the vision, keep pushing",
    ))

    ctx = build_planner_context(tmp_path, _path(), [], _state(), needs_justification=False)
    assert "Path-a is central to the vision, keep pushing" in ctx


def test_planner_no_vision_assessment_when_no_strategy(tmp_path: Path):
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()
    (tmp_path / "state").mkdir()

    ctx = build_planner_context(tmp_path, _path(), [], _state(), needs_justification=False)
    assert "Vision Assessment" not in ctx
