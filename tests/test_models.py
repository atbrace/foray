import json
from datetime import datetime, timezone

from foray.models import (
    Confidence,
    Evaluation,
    ExperimentStatus,
    Finding,
    PathInfo,
    PathStatus,
    Priority,
    Round,
    RoundOutcome,
    RunConfig,
    RunState,
)


def test_path_info_serialization():
    path = PathInfo(
        id="contour-extraction",
        description="Which contour extraction method works best?",
        priority=Priority.HIGH,
        hypothesis="OpenCV classical methods will work",
    )
    data = json.loads(path.model_dump_json())
    assert data["id"] == "contour-extraction"
    assert data["priority"] == "high"
    assert data["status"] == "open"
    assert data["experiment_count"] == 0
    assert data["topic_tags"] == []


def test_path_info_deserialization():
    raw = {
        "id": "edge-detection",
        "description": "Best edge detection approach",
        "priority": "medium",
        "hypothesis": "Canny will work",
        "status": "resolved",
        "experiment_count": 3,
        "topic_tags": ["opencv", "edges"],
        "blocker_description": "",
    }
    path = PathInfo.model_validate(raw)
    assert path.priority == Priority.MEDIUM
    assert path.status == PathStatus.RESOLVED
    assert path.experiment_count == 3


def test_round_with_outcomes():
    now = datetime.now(timezone.utc)
    r = Round(
        round_number=1,
        paths=["path-a", "path-b"],
        outcomes=[
            RoundOutcome(
                path_id="path-a",
                experiment_id="001",
                status=ExperimentStatus.SUCCESS,
                path_status_after=PathStatus.OPEN,
            )
        ],
        started_at=now,
    )
    data = json.loads(r.model_dump_json())
    assert data["round_number"] == 1
    assert len(data["outcomes"]) == 1
    assert data["completed_at"] is None


def test_evaluation_deserialization():
    raw = {
        "experiment_id": "001",
        "path_id": "contour-extraction",
        "outcome": "conclusive",
        "path_status": "resolved",
        "confidence": "high",
        "topic_tags": ["opencv"],
        "summary": "OpenCV works well",
        "new_questions": [],
        "evidence_for": {"opencv": "strong"},
        "evidence_against": {"potrace": "strong"},
        "blocker_description": "",
    }
    ev = Evaluation.model_validate(raw)
    assert ev.confidence == Confidence.HIGH
    assert ev.path_status == PathStatus.RESOLVED


def test_finding_serialization():
    f = Finding(
        experiment_id="001",
        path_id="contour-extraction",
        status=ExperimentStatus.SUCCESS,
        summary="OpenCV Canny works well",
        one_liner="Canny + polygon approximation: 94% success",
    )
    data = json.loads(f.model_dump_json())
    assert data["status"] == "SUCCESS"


def test_run_config_defaults():
    config = RunConfig(vision_path="VISION.md")
    assert config.hours == 8.0
    assert config.max_experiments == 50
    assert config.model == "claude-sonnet-4-20250514"
    assert config.max_turns == 30


def test_run_state_roundtrip():
    state = RunState(
        start_time=datetime(2026, 4, 10, 22, 0, 0, tzinfo=timezone.utc),
        config=RunConfig(vision_path="VISION.md"),
        experiment_count=5,
        current_round=2,
        current_path_index=1,
        last_completed_experiment="005",
    )
    json_str = state.model_dump_json()
    restored = RunState.model_validate_json(json_str)
    assert restored.experiment_count == 5
    assert restored.last_completed_experiment == "005"
