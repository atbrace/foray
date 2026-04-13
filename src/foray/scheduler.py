from __future__ import annotations

from datetime import datetime, timezone

from foray.models import (
    ExperimentStatus,
    Finding,
    PathInfo,
    PathStatus,
    Priority,
    RunState,
)

PRIORITY_ORDER = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}

NON_FAILURE_STATUSES = (ExperimentStatus.SUCCESS, ExperimentStatus.PARTIAL, ExperimentStatus.EXHAUSTED)


def get_round_paths(paths: list[PathInfo]) -> list[str]:
    """Get open path IDs ordered by priority for the next round."""
    open_paths = [p for p in paths if p.status == PathStatus.OPEN]
    open_paths.sort(key=lambda p: PRIORITY_ORDER[p.priority])
    return [p.id for p in open_paths]


def should_continue(
    state: RunState,
    paths: list[PathInfo],
    stop_file_exists: bool = False,
) -> bool:
    """Check if exploration should continue."""
    if stop_file_exists:
        return False
    elapsed_hours = (datetime.now(timezone.utc) - state.start_time).total_seconds() / 3600
    if elapsed_hours >= state.config.hours:
        return False
    if state.experiment_count >= state.config.max_experiments:
        return False
    if not any(p.status == PathStatus.OPEN for p in paths):
        return False
    return True


def needs_concentration_justification(path_id: str, findings: list[Finding]) -> bool:
    """True if path has 3+ experiments without resolution."""
    return sum(1 for f in findings if f.path_id == path_id) >= 3


def check_path_failure_threshold(path_id: str, findings: list[Finding]) -> bool:
    """True if 3 of last 4 experiments on this path failed."""
    path_findings = [f for f in findings if f.path_id == path_id]
    if len(path_findings) < 4:
        return False
    last_4 = path_findings[-4:]
    failures = sum(
        1 for f in last_4
        if f.status not in NON_FAILURE_STATUSES
    )
    return failures >= 3


def check_consecutive_failures(findings: list[Finding]) -> bool:
    """True if last 3 experiments (any path) all failed."""
    if len(findings) < 3:
        return False
    return all(
        f.status not in NON_FAILURE_STATUSES
        for f in findings[-3:]
    )


def detect_methodology_repetition(tag_lists: list[list[str]]) -> bool:
    """True if last 3+ experiments share >70% of their topic tags.

    Uses Jaccard similarity (intersection/union) across the last 3 tag lists.
    Informational signal — does not block experiments.
    """
    if len(tag_lists) < 3:
        return False
    last_3 = [set(tags) for tags in tag_lists[-3:]]
    if any(len(s) == 0 for s in last_3):
        return False
    intersection = last_3[0] & last_3[1] & last_3[2]
    union = last_3[0] | last_3[1] | last_3[2]
    return len(intersection) / len(union) > 0.7


def next_experiment_id(experiment_count: int) -> str:
    """Generate zero-padded experiment ID."""
    return f"{experiment_count + 1:03d}"
