from foray.models import (
    Confidence,
    Evaluation,
    ExperimentStatus,
    Finding,
    PathInfo,
    PathStatus,
    Priority,
)
from foray.orchestrator import apply_guardrails


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
