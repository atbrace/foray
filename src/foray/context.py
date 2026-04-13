from __future__ import annotations

import json
import logging
from pathlib import Path

from foray.models import (
    Finding,
    PathInfo,
    RunState,
)
from foray.scheduler import detect_methodology_repetition
from foray.state import read_evaluation, read_findings, read_paths

logger = logging.getLogger(__name__)

BUDGETS = {
    "planner": 30_000,
    "executor": 15_000,
    "evaluator": 20_000,
    "synthesizer": 60_000,
}

_STATUS_ICONS = {"open": "○", "resolved": "●", "blocked": "✗", "inconclusive": "?"}


def estimate_tokens(text: str) -> int:
    """Estimate token count (word count * 1.3)."""
    return int(len(text.split()) * 1.3)


def _read_file(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def _truncate_text(text: str, max_tokens: int) -> str:
    """Truncate text to approximately max_tokens."""
    words = text.split()
    target_words = int(max_tokens / 1.3)
    if len(words) <= target_words:
        return text
    return " ".join(words[:target_words]) + "\n\n[truncated]"


def build_planner_context(
    foray_dir: Path,
    path: PathInfo,
    path_findings: list[Finding],
    run_state: RunState,
    needs_justification: bool,
) -> str:
    """Scoped planner context with progressive summarization.

    Last 3 experiments: planner briefs (falling back to summary).
    Older: one-line summaries.
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

    recent_tags: list[list[str]] = []
    if recent:
        sections.append("\n## Recent Experiments")
        for f in recent:
            brief = f.planner_brief if f.planner_brief else f.summary
            sections.append(f"- Exp {f.experiment_id}: [{f.status}] {brief}")
            evaluation = read_evaluation(foray_dir, f.experiment_id)
            if evaluation:
                recent_tags.append(evaluation.topic_tags)
                if evaluation.evidence_against:
                    pairs = ", ".join(
                        f"{k} ({v})" for k, v in evaluation.evidence_against.items()
                    )
                    sections.append(f"  Evidence against: {pairs}")

    # Trailing metadata sections (preserved during truncation)
    trailing: list[str] = []

    trailing.append(
        f"\n## Run Status\n- Round: {run_state.current_round}"
        f"\n- Experiments completed: {run_state.experiment_count}"
        f"\n- Budget: {run_state.config.max_experiments} experiments, "
        f"{run_state.config.hours} hours"
    )

    env_md = _read_file(foray_dir / "environment.md")
    if env_md:
        trailing.append(f"\n{env_md}")

    if needs_justification:
        trailing.append(
            "\n## IMPORTANT: Concentration Justification Required\n"
            "This path has had 3+ experiments without resolution. "
            "Your plan MUST include a '## Justification for Continued Investment' "
            "section explaining why more work is higher-value than declaring "
            "this path inconclusive."
        )

    if detect_methodology_repetition(recent_tags):
        trailing.append(
            "\n## Methodology Repetition Detected\n"
            "The last 3 experiments on this path share >70% of their methodology "
            "tags, suggesting similar approaches are being repeated. Consider a "
            "substantially different methodology or declare the path inconclusive."
        )

    sections.extend(trailing)
    context = "\n".join(sections)
    tokens = estimate_tokens(context)
    if tokens > BUDGETS["planner"]:
        # Rebuild with truncated recent briefs (biggest variable-size items)
        sections_truncated = sections[:2]  # vision + path info
        if older:
            sections_truncated.append("\n## Previous Experiments (summaries)")
            for f in older:
                sections_truncated.append(f"- Exp {f.experiment_id}: [{f.status}] {f.one_liner}")
        if recent:
            sections_truncated.append("\n## Recent Experiments")
            for f in recent:
                sections_truncated.append(f"- Exp {f.experiment_id}: [{f.status}] {f.one_liner}")
        sections_truncated.extend(trailing)
        context = "\n".join(sections_truncated)
        tokens = estimate_tokens(context)
        logger.info(f"Planner context truncated to ~{tokens} tokens (budget: {BUDGETS['planner']})")
    return context


def build_executor_context(foray_dir: Path, plan_path: Path) -> str:
    """Executor context: plan + codebase map.

    Vision is omitted — the plan already incorporates vision context
    from the planner, and the executor has the tightest token budget.
    """
    plan = _read_file(plan_path)
    codebase_map = _read_file(foray_dir / "codebase_map.md")

    plan_tokens = estimate_tokens(plan)
    remaining = BUDGETS["executor"] - plan_tokens - 100  # 100 tokens for headers
    if remaining > 0:
        codebase_map = _truncate_text(codebase_map, remaining)
    else:
        codebase_map = "[codebase map omitted — plan consumed full budget]"

    context = (
        f"# Experiment Plan\n\n{plan}\n\n"
        f"# Codebase Map\n\n{codebase_map}"
    )
    tokens = estimate_tokens(context)
    if tokens > BUDGETS["executor"]:
        logger.warning(
            f"Executor context still over budget after truncation: ~{tokens} tokens "
            f"(budget: {BUDGETS['executor']}). Plan may be too large."
        )
    return context


def build_evaluator_context(
    foray_dir: Path,
    experiment_id: str,
    path: PathInfo,
    path_findings: list[Finding],
) -> str:
    """Evaluator context: results + path state + recent assessments.

    Vision is omitted — the path description and hypothesis already encode
    the relevant slice, and the evaluator runs on the most expensive model.
    """
    results = _read_file(foray_dir / "experiments" / f"{experiment_id}_results.md")

    # Core content: results + path state (always included)
    core_sections = [
        f"# Experiment {experiment_id} Results\n\n{results}",
        (
            f"\n# Path State\n\n**ID:** {path.id}\n"
            f"**Description:** {path.description}\n"
            f"**Hypothesis:** {path.hypothesis}\n"
            f"**Status:** {path.status}\n"
            f"**Experiments:** {path.experiment_count}"
        ),
    ]
    core_tokens = estimate_tokens("\n".join(core_sections))
    remaining = BUDGETS["evaluator"] - core_tokens - 50

    # Add recent assessments newest-first, dropping when budget exceeded
    recent_findings = path_findings[-3:]
    assessment_sections: list[str] = []
    for f in reversed(recent_findings):
        assessment_path = foray_dir / "experiments" / f"{f.experiment_id}_eval.json"
        if assessment_path.exists():
            raw = assessment_path.read_text()
            try:
                full = json.loads(raw)
                if isinstance(full, dict):
                    projected = {
                        k: full[k] for k in ("outcome", "confidence", "summary", "planner_brief")
                        if k in full
                    }
                    content = json.dumps(projected)
                else:
                    content = raw
            except json.JSONDecodeError:
                content = raw
            content_tokens = estimate_tokens(content)
            if content_tokens <= remaining:
                assessment_sections.insert(0, f"\n```json\n{content}\n```")
                remaining -= content_tokens
            else:
                logger.info(f"Evaluator context: dropping assessment {f.experiment_id} (budget)")
                break

    sections = core_sections
    if assessment_sections:
        sections.append("\n# Recent Assessments on This Path")
        sections.extend(assessment_sections)

    context = "\n".join(sections)
    return context


def build_exhaustion_evaluator_context(
    foray_dir: Path,
    path: PathInfo,
    path_findings: list[Finding],
    rationale: str,
) -> str:
    """Context for evaluator when planner signals exhaustion.

    Includes the exhaustion rationale and full path history so the evaluator
    can make a final resolution/inconclusive call.
    """
    sections = [
        "# Planner EXHAUSTED Signal\n\n"
        "The planner has indicated that no further viable experiments exist for this path. "
        "Your job is to assess whether the path should be marked **resolved**, **inconclusive**, "
        "or remain **open** (if you disagree with the planner's assessment).\n\n"
        f"## Planner's Rationale\n\n{rationale}",
        (
            f"\n# Path State\n\n**ID:** {path.id}\n"
            f"**Description:** {path.description}\n"
            f"**Hypothesis:** {path.hypothesis}\n"
            f"**Status:** {path.status}\n"
            f"**Experiments:** {path.experiment_count}"
        ),
    ]

    if path_findings:
        sections.append("\n# All Experiments on This Path")
        for f in path_findings:
            sections.append(f"- {f.experiment_id}: [{f.status}] {f.summary}")

    # Include recent assessments for richer context
    recent = path_findings[-3:]
    assessment_sections: list[str] = []
    remaining = BUDGETS["evaluator"] - estimate_tokens("\n".join(sections)) - 50
    for f in reversed(recent):
        assessment_path = foray_dir / "experiments" / f"{f.experiment_id}_eval.json"
        if assessment_path.exists():
            content = assessment_path.read_text()
            content_tokens = estimate_tokens(content)
            if content_tokens <= remaining:
                assessment_sections.insert(0, f"\n```json\n{content}\n```")
                remaining -= content_tokens

    if assessment_sections:
        sections.append("\n# Recent Assessments")
        sections.extend(assessment_sections)

    return "\n".join(sections)


def build_synthesizer_context(foray_dir: Path) -> str:
    """Synthesizer context with progressive summarization.

    Findings grouped by path. Latest 3 per path get full summaries,
    older get one-liners. The synthesizer can still Read individual
    results files via tools for full details.
    """
    vision = _read_file(foray_dir / "vision.md")
    findings = read_findings(foray_dir)
    paths = read_paths(foray_dir)

    # Group findings by path
    by_path: dict[str, list[Finding]] = {}
    for f in findings:
        by_path.setdefault(f.path_id, []).append(f)

    sections = [f"# Vision\n\n{vision}"]

    # Path summaries
    sections.append("\n# Paths")
    for p in paths:
        icon = _STATUS_ICONS.get(p.status.value, "·")
        sections.append(
            f"\n## {icon} {p.id} ({p.status.value})\n"
            f"**Description:** {p.description}\n"
            f"**Hypothesis:** {p.hypothesis}\n"
            f"**Priority:** {p.priority} | **Experiments:** {p.experiment_count}"
        )
        if p.blocker_description:
            sections.append(f"**Blocker:** {p.blocker_description}")

    # Findings with progressive summarization
    sections.append("\n# Findings by Path")
    for p in paths:
        path_findings = by_path.get(p.id, [])
        if not path_findings:
            continue
        sections.append(f"\n## {p.id}")

        recent = path_findings[-3:]
        older = path_findings[:-3]

        if older:
            sections.append("### Earlier experiments")
            for f in older:
                sections.append(f"- {f.experiment_id}: [{f.status}] {f.one_liner}")

        if recent:
            sections.append("### Recent experiments")
            for f in recent:
                sections.append(f"- {f.experiment_id}: [{f.status}] {f.summary}")

    context = "\n".join(sections)
    tokens = estimate_tokens(context)
    if tokens > BUDGETS["synthesizer"]:
        # Further truncate: reduce all findings to one-liners
        sections_truncated = sections[:2]  # vision + paths header
        # Re-add paths (compact)
        for p in paths:
            sections_truncated.append(f"- {p.id}: {p.status.value} ({p.experiment_count} experiments)")
        sections_truncated.append("\n# Findings (summarized)")
        for p in paths:
            path_findings = by_path.get(p.id, [])
            if path_findings:
                sections_truncated.append(f"\n## {p.id}")
                for f in path_findings:
                    sections_truncated.append(f"- {f.experiment_id}: [{f.status}] {f.one_liner}")
        context = "\n".join(sections_truncated)
        tokens = estimate_tokens(context)
        logger.info(f"Synthesizer context truncated to ~{tokens} tokens (budget: {BUDGETS['synthesizer']})")
    return context
