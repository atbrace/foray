from __future__ import annotations

import logging
from pathlib import Path

from foray.models import (
    ExperimentStatus,
    Finding,
    PathInfo,
    RunState,
)

logger = logging.getLogger(__name__)

BUDGETS = {
    "planner": 30_000,
    "executor": 15_000,
    "evaluator": 20_000,
    "synthesizer": 60_000,
}


def estimate_tokens(text: str) -> int:
    """Estimate token count (word count * 1.3)."""
    return int(len(text.split()) * 1.3)


def _read_file(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def _failure_summary(findings: list[Finding]) -> str:
    failures = [f for f in findings if f.status != ExperimentStatus.SUCCESS]
    if not failures:
        return ""
    lines = ["## Failed Experiments on This Path"]
    for f in failures:
        lines.append(f"- Exp {f.experiment_id}: {f.status} -- {f.one_liner}")
    return "\n".join(lines)


def build_planner_context(
    foray_dir: Path,
    path: PathInfo,
    path_findings: list[Finding],
    run_state: RunState,
    needs_justification: bool,
) -> str:
    """Scoped planner context with progressive summarization.

    Last 3 experiments: full results. Older: one-line summaries.
    """
    vision = _read_file(foray_dir / "vision.md")
    recent = path_findings[-3:]
    older = path_findings[:-3]

    sections = [
        f"# Vision\n\n{vision}",
        (
            f"\n# Path: {path.id}\n\n"
            f"**Description:** {path.description}\n"
            f"**Priority:** {path.priority}\n"
            f"**Hypothesis:** {path.hypothesis}\n"
            f"**Status:** {path.status}\n"
            f"**Experiments so far:** {path.experiment_count}"
        ),
    ]

    if older:
        sections.append("\n## Previous Experiments (summaries)")
        for f in older:
            sections.append(f"- Exp {f.experiment_id}: [{f.status}] {f.one_liner}")

    if recent:
        sections.append("\n## Recent Experiments (full detail)")
        for f in recent:
            results = _read_file(foray_dir / "experiments" / f"{f.experiment_id}_results.md")
            if results:
                sections.append(f"\n### Experiment {f.experiment_id}\n\n{results}")
            else:
                sections.append(f"\n### Experiment {f.experiment_id}\n\n{f.summary}")

    fail_text = _failure_summary(path_findings)
    if fail_text:
        sections.append(f"\n{fail_text}")

    sections.append(
        f"\n## Run Status\n- Round: {run_state.current_round}"
        f"\n- Experiments completed: {run_state.experiment_count}"
        f"\n- Budget: {run_state.config.max_experiments} experiments, "
        f"{run_state.config.hours} hours"
    )

    if needs_justification:
        sections.append(
            "\n## IMPORTANT: Concentration Justification Required\n"
            "This path has had 3+ experiments without resolution. "
            "Your plan MUST include a '## Justification for Continued Investment' "
            "section explaining why more work is higher-value than declaring "
            "this path inconclusive."
        )

    context = "\n".join(sections)
    tokens = estimate_tokens(context)
    if tokens > BUDGETS["planner"]:
        logger.warning(f"Planner context for '{path.id}' exceeds budget: ~{tokens} tokens")
    return context


def build_executor_context(foray_dir: Path, plan_path: Path) -> str:
    """Executor context: plan + codebase map + vision."""
    plan = _read_file(plan_path)
    codebase_map = _read_file(foray_dir / "codebase_map.md")
    vision = _read_file(foray_dir / "vision.md")

    context = (
        f"# Experiment Plan\n\n{plan}\n\n"
        f"# Codebase Map\n\n{codebase_map}\n\n"
        f"# Vision\n\n{vision}"
    )
    tokens = estimate_tokens(context)
    if tokens > BUDGETS["executor"]:
        logger.warning(f"Executor context exceeds budget: ~{tokens} tokens")
    return context


def build_evaluator_context(
    foray_dir: Path,
    experiment_id: str,
    path: PathInfo,
    path_findings: list[Finding],
) -> str:
    """Evaluator context: results + vision + path state + recent assessments."""
    results = _read_file(foray_dir / "experiments" / f"{experiment_id}_results.md")
    vision = _read_file(foray_dir / "vision.md")

    recent_assessments = []
    for f in path_findings[-3:]:
        assessment_path = foray_dir / "experiments" / f"{f.experiment_id}_eval.json"
        if assessment_path.exists():
            recent_assessments.append(assessment_path.read_text())

    sections = [
        f"# Experiment {experiment_id} Results\n\n{results}",
        f"\n# Vision\n\n{vision}",
        (
            f"\n# Path State\n\n**ID:** {path.id}\n"
            f"**Description:** {path.description}\n"
            f"**Status:** {path.status}\n"
            f"**Experiments:** {path.experiment_count}"
        ),
    ]
    if recent_assessments:
        sections.append("\n# Recent Assessments on This Path")
        for a in recent_assessments:
            sections.append(f"\n```json\n{a}\n```")

    context = "\n".join(sections)
    tokens = estimate_tokens(context)
    if tokens > BUDGETS["evaluator"]:
        logger.warning(f"Evaluator context exceeds budget: ~{tokens} tokens")
    return context


def build_synthesizer_context(foray_dir: Path) -> str:
    """Synthesizer context: full findings + paths + vision."""
    vision = _read_file(foray_dir / "vision.md")
    findings = _read_file(foray_dir / "state" / "findings.json")
    paths = _read_file(foray_dir / "state" / "paths.json")

    context = (
        f"# Vision\n\n{vision}\n\n"
        f"# All Findings\n\n```json\n{findings}\n```\n\n"
        f"# All Paths\n\n```json\n{paths}\n```"
    )
    tokens = estimate_tokens(context)
    if tokens > BUDGETS["synthesizer"]:
        logger.warning(f"Synthesizer context exceeds budget: ~{tokens} tokens")
    return context
