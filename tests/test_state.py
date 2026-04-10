import json
from datetime import datetime, timezone
from pathlib import Path

from foray.models import (
    Confidence,
    Evaluation,
    ExperimentStatus,
    Finding,
    PathInfo,
    PathStatus,
    Priority,
    RunConfig,
    RunState,
)
from foray.state import (
    _atomic_write,
    add_finding,
    init_directory,
    read_evaluation,
    read_findings,
    read_paths,
    read_rounds,
    read_run_state,
    write_evaluation,
    write_findings,
    write_paths,
    write_run_state,
)


def test_atomic_write(tmp_path):
    target = tmp_path / "test.json"
    _atomic_write(target, '{"key": "value"}')
    assert json.loads(target.read_text()) == {"key": "value"}


def test_atomic_write_overwrites(tmp_path):
    target = tmp_path / "test.json"
    _atomic_write(target, '{"v": 1}')
    _atomic_write(target, '{"v": 2}')
    assert json.loads(target.read_text()) == {"v": 2}


def test_init_directory(tmp_path):
    state = RunState(
        start_time=datetime.now(timezone.utc),
        config=RunConfig(vision_path="VISION.md"),
    )
    foray_dir = init_directory(tmp_path, state)
    assert (foray_dir / "state" / "paths.json").exists()
    assert (foray_dir / "state" / "rounds.json").exists()
    assert (foray_dir / "state" / "findings.json").exists()
    assert (foray_dir / "foray.json").exists()
    assert (foray_dir / "experiments").is_dir()
    assert (foray_dir / "worktrees").is_dir()
    assert (foray_dir / "agents").is_dir()


def test_paths_roundtrip(tmp_path):
    (tmp_path / "state").mkdir()
    paths = [
        PathInfo(
            id="path-a",
            description="Test path A",
            priority=Priority.HIGH,
            hypothesis="It will work",
        ),
        PathInfo(
            id="path-b",
            description="Test path B",
            priority=Priority.MEDIUM,
            hypothesis="Maybe",
            status=PathStatus.RESOLVED,
            experiment_count=3,
        ),
    ]
    write_paths(tmp_path, paths)
    restored = read_paths(tmp_path)
    assert len(restored) == 2
    assert restored[0].id == "path-a"
    assert restored[1].status == PathStatus.RESOLVED


def test_findings_roundtrip(tmp_path):
    (tmp_path / "state").mkdir()
    findings = [
        Finding(
            experiment_id="001",
            path_id="path-a",
            status=ExperimentStatus.SUCCESS,
            summary="Worked well",
            one_liner="Success with approach A",
        ),
    ]
    write_findings(tmp_path, findings)
    restored = read_findings(tmp_path)
    assert len(restored) == 1
    assert restored[0].experiment_id == "001"


def test_add_finding(tmp_path):
    (tmp_path / "state").mkdir()
    write_findings(tmp_path, [])
    add_finding(
        tmp_path,
        Finding(
            experiment_id="001", path_id="path-a",
            status=ExperimentStatus.SUCCESS, summary="First", one_liner="First",
        ),
    )
    add_finding(
        tmp_path,
        Finding(
            experiment_id="002", path_id="path-b",
            status=ExperimentStatus.FAILED, summary="Second", one_liner="Second",
        ),
    )
    assert len(read_findings(tmp_path)) == 2


def test_run_state_roundtrip(tmp_path):
    state = RunState(
        start_time=datetime(2026, 4, 10, 22, 0, 0, tzinfo=timezone.utc),
        config=RunConfig(vision_path="VISION.md"),
        experiment_count=5,
    )
    write_run_state(tmp_path, state)
    restored = read_run_state(tmp_path)
    assert restored.experiment_count == 5


def test_evaluation_roundtrip(tmp_path):
    (tmp_path / "experiments").mkdir()
    assessment = Evaluation(
        experiment_id="001", path_id="path-a",
        outcome="conclusive", path_status=PathStatus.RESOLVED,
        confidence=Confidence.HIGH, summary="Worked",
    )
    write_evaluation(tmp_path, assessment)
    restored = read_evaluation(tmp_path, "001")
    assert restored is not None
    assert restored.confidence == Confidence.HIGH


def test_read_evaluation_missing(tmp_path):
    (tmp_path / "experiments").mkdir()
    assert read_evaluation(tmp_path, "999") is None


def test_read_evaluation_malformed(tmp_path):
    (tmp_path / "experiments").mkdir()
    (tmp_path / "experiments" / "bad_eval.json").write_text("not valid json {{{")
    assert read_evaluation(tmp_path, "bad") is None
