from datetime import datetime, timedelta, timezone

from foray.models import (
    ExperimentStatus,
    Finding,
    PathInfo,
    PathStatus,
    Priority,
    RunConfig,
    RunState,
)
from foray.scheduler import (
    check_consecutive_failures,
    check_path_failure_threshold,
    get_round_paths,
    needs_concentration_justification,
    next_experiment_id,
    should_continue,
)


def _path(id: str, priority: Priority = Priority.MEDIUM, status: PathStatus = PathStatus.OPEN) -> PathInfo:
    return PathInfo(id=id, description=f"Path {id}", priority=priority, hypothesis="test", status=status)


def _finding(exp_id: str, path_id: str, status: ExperimentStatus = ExperimentStatus.SUCCESS) -> Finding:
    return Finding(experiment_id=exp_id, path_id=path_id, status=status, summary="test", one_liner="test")


def _state(hours: float = 8.0, max_exp: int = 50, elapsed_hours: float = 0) -> RunState:
    return RunState(
        start_time=datetime.now(timezone.utc) - timedelta(hours=elapsed_hours),
        config=RunConfig(vision_path="V.md", hours=hours, max_experiments=max_exp),
    )


# --- get_round_paths ---


def test_round_paths_priority_order():
    paths = [_path("low", Priority.LOW), _path("high", Priority.HIGH), _path("med", Priority.MEDIUM)]
    assert get_round_paths(paths) == ["high", "med", "low"]


def test_round_paths_excludes_non_open():
    paths = [
        _path("a", Priority.HIGH),
        _path("b", Priority.HIGH, PathStatus.RESOLVED),
        _path("c", Priority.MEDIUM),
        _path("d", Priority.LOW, PathStatus.BLOCKED),
    ]
    assert get_round_paths(paths) == ["a", "c"]


def test_round_paths_empty_when_all_resolved():
    paths = [_path("a", status=PathStatus.RESOLVED), _path("b", status=PathStatus.INCONCLUSIVE)]
    assert get_round_paths(paths) == []


# --- should_continue ---


def test_should_continue_normal():
    assert should_continue(_state(hours=8, elapsed_hours=1), [_path("a")]) is True


def test_should_continue_time_expired():
    assert should_continue(_state(hours=8, elapsed_hours=9), [_path("a")]) is False


def test_should_continue_experiments_exhausted():
    state = _state(max_exp=10)
    state.experiment_count = 10
    assert should_continue(state, [_path("a")]) is False


def test_should_continue_no_open_paths():
    assert should_continue(_state(), [_path("a", status=PathStatus.RESOLVED)]) is False


def test_should_continue_stop_file():
    assert should_continue(_state(), [_path("a")], stop_file_exists=True) is False


# --- concentration detection ---


def test_concentration_under_threshold():
    findings = [_finding("001", "a"), _finding("002", "a")]
    assert needs_concentration_justification("a", findings) is False


def test_concentration_at_threshold():
    findings = [_finding("001", "a"), _finding("002", "a"), _finding("003", "a")]
    assert needs_concentration_justification("a", findings) is True


def test_concentration_only_counts_target_path():
    findings = [_finding("001", "a"), _finding("002", "b"), _finding("003", "a")]
    assert needs_concentration_justification("a", findings) is False


# --- path failure threshold ---


def test_path_failure_threshold_not_enough():
    findings = [_finding("001", "a", ExperimentStatus.FAILED) for _ in range(3)]
    assert check_path_failure_threshold("a", findings) is False


def test_path_failure_threshold_triggered():
    findings = [
        _finding("001", "a", ExperimentStatus.FAILED),
        _finding("002", "a", ExperimentStatus.SUCCESS),
        _finding("003", "a", ExperimentStatus.FAILED),
        _finding("004", "a", ExperimentStatus.FAILED),
        _finding("005", "a", ExperimentStatus.FAILED),
    ]
    assert check_path_failure_threshold("a", findings) is True


def test_path_failure_threshold_not_triggered():
    findings = [
        _finding("001", "a", ExperimentStatus.FAILED),
        _finding("002", "a", ExperimentStatus.SUCCESS),
        _finding("003", "a", ExperimentStatus.FAILED),
        _finding("004", "a", ExperimentStatus.SUCCESS),
    ]
    assert check_path_failure_threshold("a", findings) is False


# --- consecutive failures ---


def test_consecutive_failures_triggered():
    findings = [
        _finding("001", "a", ExperimentStatus.SUCCESS),
        _finding("002", "b", ExperimentStatus.FAILED),
        _finding("003", "a", ExperimentStatus.FAILED),
        _finding("004", "c", ExperimentStatus.INFEASIBLE),
    ]
    assert check_consecutive_failures(findings) is True


def test_consecutive_failures_not_triggered():
    findings = [
        _finding("001", "a", ExperimentStatus.FAILED),
        _finding("002", "b", ExperimentStatus.SUCCESS),
        _finding("003", "a", ExperimentStatus.FAILED),
    ]
    assert check_consecutive_failures(findings) is False


def test_consecutive_failures_not_enough():
    assert check_consecutive_failures([_finding("001", "a", ExperimentStatus.FAILED)]) is False


# --- experiment ID ---


def test_next_experiment_id():
    assert next_experiment_id(0) == "001"
    assert next_experiment_id(9) == "010"
    assert next_experiment_id(99) == "100"
