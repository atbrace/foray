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
    assert config.model == "claude-sonnet-4-6"
    assert config.max_turns == 50


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


# --- Agent null-coercion tests ---
# LLM agents often emit null for fields they consider absent.  These tests
# reproduce the exact JSON shapes that caused validation failures in prod.


def test_evaluation_null_blocker_from_json():
    """Evaluator emits blocker_description: null when path isn't blocked."""
    raw_json = json.dumps({
        "experiment_id": "001",
        "path_id": "contour-extraction",
        "outcome": "conclusive",
        "path_status": "open",
        "confidence": "medium",
        "topic_tags": ["opencv"],
        "summary": "Partial progress on contour extraction",
        "new_questions": [],
        "evidence_for": {},
        "evidence_against": {},
        "blocker_description": None,
    })
    ev = Evaluation.model_validate_json(raw_json)
    assert ev.blocker_description == ""


def test_evaluation_null_lists_from_json():
    """Evaluator emits null for optional list/dict fields."""
    raw_json = json.dumps({
        "experiment_id": "002",
        "path_id": "multi-angle",
        "outcome": "inconclusive",
        "path_status": "open",
        "confidence": "low",
        "topic_tags": None,
        "summary": "Needs more data",
        "new_questions": None,
        "evidence_for": None,
        "evidence_against": None,
        "blocker_description": None,
    })
    ev = Evaluation.model_validate_json(raw_json)
    assert ev.topic_tags == []
    assert ev.new_questions == []
    assert ev.evidence_for == {}
    assert ev.evidence_against == {}
    assert ev.blocker_description == ""


def test_evaluation_missing_optional_fields():
    """Evaluator omits optional fields entirely."""
    raw_json = json.dumps({
        "experiment_id": "003",
        "path_id": "contour-extraction",
        "outcome": "conclusive",
        "path_status": "resolved",
        "confidence": "high",
        "summary": "It works",
    })
    ev = Evaluation.model_validate_json(raw_json)
    assert ev.topic_tags == []
    assert ev.blocker_description == ""


def test_path_info_null_blocker_from_json():
    """Initializer emits blocker_description: null in paths.json."""
    raw_json = json.dumps({
        "id": "test-path",
        "description": "Test",
        "priority": "high",
        "hypothesis": "It works",
        "status": "open",
        "experiment_count": 0,
        "topic_tags": None,
        "blocker_description": None,
    })
    path = PathInfo.model_validate_json(raw_json)
    assert path.topic_tags == []
    assert path.blocker_description == ""


# --- Evaluation methodology field tests ---


def test_evaluation_methodology_default():
    """methodology defaults to empty string when not provided."""
    ev = Evaluation(
        experiment_id="001",
        path_id="test",
        outcome="conclusive",
        path_status=PathStatus.RESOLVED,
        confidence=Confidence.HIGH,
        summary="Test",
    )
    assert ev.methodology == ""


def test_exhausted_status_exists():
    assert ExperimentStatus.EXHAUSTED == "EXHAUSTED"


def test_evaluation_methodology_null_coercion():
    """methodology: null in JSON coerces to empty string."""
    raw_json = json.dumps({
        "experiment_id": "001",
        "path_id": "test",
        "outcome": "conclusive",
        "path_status": "resolved",
        "confidence": "high",
        "summary": "Test",
        "methodology": None,
    })
    ev = Evaluation.model_validate_json(raw_json)
    assert ev.methodology == ""


def test_evaluation_methodology_roundtrip():
    """methodology: 'self-evaluated' round-trips correctly."""
    ev = Evaluation(
        experiment_id="001",
        path_id="test",
        outcome="conclusive",
        path_status=PathStatus.RESOLVED,
        confidence=Confidence.MEDIUM,
        summary="Test",
        methodology="self-evaluated",
    )
    data = json.loads(ev.model_dump_json())
    assert data["methodology"] == "self-evaluated"
    restored = Evaluation.model_validate_json(ev.model_dump_json())
    assert restored.methodology == "self-evaluated"


def test_evaluation_methodology_missing_from_json():
    """methodology key entirely absent from JSON defaults to empty string."""
    raw_json = json.dumps({
        "experiment_id": "001",
        "path_id": "test",
        "outcome": "conclusive",
        "path_status": "resolved",
        "confidence": "high",
        "summary": "Test",
    })
    ev = Evaluation.model_validate_json(raw_json)
    assert ev.methodology == ""


def test_evaluation_self_eval_caps_confidence():
    """Self-evaluated methodology caps confidence at MEDIUM."""
    raw_json = json.dumps({
        "experiment_id": "001",
        "path_id": "test",
        "outcome": "conclusive",
        "path_status": "open",
        "confidence": "high",
        "summary": "Test",
        "methodology": "self-evaluated",
    })
    ev = Evaluation.model_validate_json(raw_json)
    assert ev.confidence == Confidence.MEDIUM
    assert ev.methodology == "self-evaluated"


def test_finding_one_liner_auto_derived():
    """Finding with no explicit one_liner should derive it from summary[:100]."""
    f = Finding(
        experiment_id="001",
        path_id="path-a",
        status=ExperimentStatus.SUCCESS,
        summary="A very detailed summary that goes on for quite a while and has lots of information",
    )
    assert f.one_liner == f.summary[:100]


def test_finding_one_liner_preserves_explicit():
    """Finding with an explicit distinct one_liner should keep it."""
    f = Finding(
        experiment_id="001",
        path_id="path-a",
        status=ExperimentStatus.SUCCESS,
        summary="Full summary here",
        one_liner="Custom one-liner",
    )
    assert f.one_liner == "Custom one-liner"


def test_finding_one_liner_auto_derived_from_json():
    """Finding deserialized from JSON without one_liner should auto-derive."""
    raw = json.dumps({
        "experiment_id": "001",
        "path_id": "path-a",
        "status": "SUCCESS",
        "summary": "A summary that should be truncated to one hundred characters for the one liner field automatically",
    })
    f = Finding.model_validate_json(raw)
    assert f.one_liner == f.summary[:100]


def test_evaluation_independent_keeps_high_confidence():
    """Independent methodology does not cap confidence."""
    raw_json = json.dumps({
        "experiment_id": "001",
        "path_id": "test",
        "outcome": "conclusive",
        "path_status": "open",
        "confidence": "high",
        "summary": "Test",
        "methodology": "independent",
    })
    ev = Evaluation.model_validate_json(raw_json)
    assert ev.confidence == Confidence.HIGH


# --- Per-experiment timing (GH-14) ---


def test_round_outcome_timing_fields():
    """RoundOutcome includes optional timing fields."""
    now = datetime.now(timezone.utc)
    outcome = RoundOutcome(
        path_id="a",
        experiment_id="001",
        status=ExperimentStatus.SUCCESS,
        path_status_after=PathStatus.OPEN,
        started_at=now,
        completed_at=now,
        elapsed_seconds=42.5,
    )
    data = json.loads(outcome.model_dump_json())
    assert data["started_at"] is not None
    assert data["completed_at"] is not None
    assert data["elapsed_seconds"] == 42.5


def test_round_outcome_timing_defaults_to_none():
    """RoundOutcome timing fields default to None for backwards compat."""
    outcome = RoundOutcome(
        path_id="a",
        experiment_id="001",
        status=ExperimentStatus.SUCCESS,
        path_status_after=PathStatus.OPEN,
    )
    assert outcome.started_at is None
    assert outcome.completed_at is None
    assert outcome.elapsed_seconds is None


def test_round_outcome_timing_roundtrip():
    """RoundOutcome with timing survives JSON serialization roundtrip."""
    now = datetime.now(timezone.utc)
    outcome = RoundOutcome(
        path_id="a",
        experiment_id="001",
        status=ExperimentStatus.SUCCESS,
        path_status_after=PathStatus.OPEN,
        started_at=now,
        completed_at=now,
        elapsed_seconds=42.5,
    )
    restored = RoundOutcome.model_validate_json(outcome.model_dump_json())
    assert restored.elapsed_seconds == 42.5
    assert restored.started_at is not None


def test_experiment_result_timing_fields():
    """ExperimentResult carries timing for thread-to-merge flow."""
    now = datetime.now(timezone.utc)
    from foray.models import ExperimentResult
    result = ExperimentResult(
        experiment_id="001",
        path_id="a",
        exp_status=ExperimentStatus.SUCCESS,
        finding=Finding(
            experiment_id="001", path_id="a",
            status=ExperimentStatus.SUCCESS, summary="ok",
        ),
        started_at=now,
        completed_at=now,
        elapsed_seconds=42.5,
    )
    assert result.started_at == now
    assert result.elapsed_seconds == 42.5


# --- Evaluation new fields (foray-bdl, foray-ejn, foray-dj1) ---


def test_evaluation_failure_type_default():
    """failure_type defaults to empty string when not provided."""
    ev = Evaluation(
        experiment_id="001", path_id="test", outcome="conclusive",
        path_status=PathStatus.OPEN, confidence=Confidence.HIGH, summary="Test",
    )
    assert ev.failure_type == ""


def test_evaluation_failure_type_null_coercion():
    """failure_type: null coerces to empty string."""
    raw_json = json.dumps({
        "experiment_id": "001", "path_id": "test", "outcome": "conclusive",
        "path_status": "open", "confidence": "high", "summary": "Test",
        "failure_type": None,
    })
    ev = Evaluation.model_validate_json(raw_json)
    assert ev.failure_type == ""


def test_evaluation_failure_type_roundtrip():
    """failure_type: 'environment' round-trips correctly."""
    ev = Evaluation(
        experiment_id="001", path_id="test", outcome="conclusive",
        path_status=PathStatus.BLOCKED, confidence=Confidence.LOW,
        summary="Missing creds", failure_type="environment",
    )
    restored = Evaluation.model_validate_json(ev.model_dump_json())
    assert restored.failure_type == "environment"


def test_evaluation_independent_verification_default():
    """independent_verification defaults to empty string."""
    ev = Evaluation(
        experiment_id="001", path_id="test", outcome="conclusive",
        path_status=PathStatus.RESOLVED, confidence=Confidence.HIGH, summary="Test",
    )
    assert ev.independent_verification == ""


def test_evaluation_independent_verification_null_coercion():
    """independent_verification: null coerces to empty string."""
    raw_json = json.dumps({
        "experiment_id": "001", "path_id": "test", "outcome": "conclusive",
        "path_status": "resolved", "confidence": "high", "summary": "Test",
        "independent_verification": None,
    })
    ev = Evaluation.model_validate_json(raw_json)
    assert ev.independent_verification == ""


def test_evaluation_hypothesis_alignment_default():
    """hypothesis_alignment defaults to empty string."""
    ev = Evaluation(
        experiment_id="001", path_id="test", outcome="conclusive",
        path_status=PathStatus.OPEN, confidence=Confidence.HIGH, summary="Test",
    )
    assert ev.hypothesis_alignment == ""


def test_evaluation_hypothesis_alignment_null_coercion():
    """hypothesis_alignment: null coerces to empty string."""
    raw_json = json.dumps({
        "experiment_id": "001", "path_id": "test", "outcome": "conclusive",
        "path_status": "open", "confidence": "high", "summary": "Test",
        "hypothesis_alignment": None,
    })
    ev = Evaluation.model_validate_json(raw_json)
    assert ev.hypothesis_alignment == ""


def test_evaluation_divergence_note_default():
    """divergence_note defaults to empty string."""
    ev = Evaluation(
        experiment_id="001", path_id="test", outcome="conclusive",
        path_status=PathStatus.OPEN, confidence=Confidence.HIGH, summary="Test",
    )
    assert ev.divergence_note == ""


def test_evaluation_divergence_note_null_coercion():
    """divergence_note: null coerces to empty string."""
    raw_json = json.dumps({
        "experiment_id": "001", "path_id": "test", "outcome": "conclusive",
        "path_status": "open", "confidence": "high", "summary": "Test",
        "divergence_note": None,
    })
    ev = Evaluation.model_validate_json(raw_json)
    assert ev.divergence_note == ""


# --- Evaluation data_type field (foray-57i) ---


def test_evaluation_data_type_default():
    """data_type defaults to empty string."""
    ev = Evaluation(
        experiment_id="001", path_id="test", outcome="conclusive",
        path_status=PathStatus.OPEN, confidence=Confidence.HIGH, summary="Test",
    )
    assert ev.data_type == ""


def test_evaluation_data_type_null_coercion():
    """data_type: null coerces to empty string."""
    raw_json = json.dumps({
        "experiment_id": "001", "path_id": "test", "outcome": "conclusive",
        "path_status": "open", "confidence": "high", "summary": "Test",
        "data_type": None,
    })
    ev = Evaluation.model_validate_json(raw_json)
    assert ev.data_type == ""


def test_evaluation_data_type_roundtrip():
    """data_type: 'synthetic' round-trips correctly."""
    ev = Evaluation(
        experiment_id="001", path_id="test", outcome="conclusive",
        path_status=PathStatus.OPEN, confidence=Confidence.MEDIUM,
        summary="Test", data_type="synthetic",
    )
    restored = Evaluation.model_validate_json(ev.model_dump_json())
    assert restored.data_type == "synthetic"


# --- Finding observations and suggested_next (foray-hzv) ---


def test_finding_observations_default():
    """Finding observations defaults to empty list."""
    f = Finding(
        experiment_id="001", path_id="test",
        status=ExperimentStatus.SUCCESS, summary="Test",
    )
    assert f.observations == []


def test_finding_suggested_next_default():
    """Finding suggested_next defaults to empty list."""
    f = Finding(
        experiment_id="001", path_id="test",
        status=ExperimentStatus.SUCCESS, summary="Test",
    )
    assert f.suggested_next == []


def test_finding_observations_populated():
    """Finding carries observations from orchestrator."""
    f = Finding(
        experiment_id="001", path_id="test",
        status=ExperimentStatus.SUCCESS, summary="Test",
        observations=["Codebase uses monorepo pattern", "No existing test fixtures"],
    )
    assert len(f.observations) == 2
    assert "Codebase uses monorepo pattern" in f.observations


def test_finding_suggested_next_populated():
    """Finding carries suggested_next from orchestrator."""
    f = Finding(
        experiment_id="001", path_id="test",
        status=ExperimentStatus.SUCCESS, summary="Test",
        suggested_next=["Test edge case with empty input", "Verify on real-world data"],
    )
    assert len(f.suggested_next) == 2
    assert "Test edge case with empty input" in f.suggested_next


def test_finding_annotations_roundtrip():
    """Finding observations and suggested_next survive JSON roundtrip."""
    f = Finding(
        experiment_id="001", path_id="test",
        status=ExperimentStatus.SUCCESS, summary="Test",
        observations=["Found a pattern"],
        suggested_next=["Explore further"],
    )
    restored = Finding.model_validate_json(f.model_dump_json())
    assert restored.observations == ["Found a pattern"]
    assert restored.suggested_next == ["Explore further"]


# --- Evaluation observations (foray-hzv) ---


def test_evaluation_observations_default():
    """Evaluation observations defaults to empty list."""
    ev = Evaluation(
        experiment_id="001", path_id="test", outcome="conclusive",
        path_status=PathStatus.OPEN, confidence=Confidence.HIGH, summary="Test",
    )
    assert ev.observations == []


def test_evaluation_observations_null_coercion():
    """Evaluation observations: null coerces to empty list."""
    raw_json = json.dumps({
        "experiment_id": "001", "path_id": "test", "outcome": "conclusive",
        "path_status": "open", "confidence": "high", "summary": "Test",
        "observations": None,
    })
    ev = Evaluation.model_validate_json(raw_json)
    assert ev.observations == []


def test_evaluation_observations_roundtrip():
    """Evaluation observations round-trips correctly."""
    ev = Evaluation(
        experiment_id="001", path_id="test", outcome="conclusive",
        path_status=PathStatus.OPEN, confidence=Confidence.HIGH,
        summary="Test",
        observations=["Uses unusual dependency injection pattern"],
    )
    restored = Evaluation.model_validate_json(ev.model_dump_json())
    assert restored.observations == ["Uses unusual dependency injection pattern"]


# --- PathInfo discarded_hypotheses (foray-dhu) ---


def test_path_info_discarded_hypotheses_default():
    """discarded_hypotheses defaults to empty list."""
    path = PathInfo(
        id="test", description="Test", priority=Priority.HIGH, hypothesis="Test",
    )
    assert path.discarded_hypotheses == []


def test_path_info_discarded_hypotheses_null_coercion():
    """discarded_hypotheses: null in JSON coerces to empty list."""
    raw_json = json.dumps({
        "id": "test", "description": "Test", "priority": "high",
        "hypothesis": "Test", "discarded_hypotheses": None,
    })
    path = PathInfo.model_validate_json(raw_json)
    assert path.discarded_hypotheses == []


def test_path_info_discarded_hypotheses_roundtrip():
    """discarded_hypotheses round-trips correctly."""
    path = PathInfo(
        id="test", description="Test", priority=Priority.HIGH, hypothesis="Test",
        discarded_hypotheses=["Approach A failed due to missing deps", "Method B diverged"],
    )
    restored = PathInfo.model_validate_json(path.model_dump_json())
    assert restored.discarded_hypotheses == [
        "Approach A failed due to missing deps",
        "Method B diverged",
    ]


# --- Strategist models ---


def test_strategy_decision_close():
    from foray.models import StrategyDecision, PathStatus
    d = StrategyDecision(action="close", path_id="path-a", status=PathStatus.INCONCLUSIVE, reason="Not advancing vision")
    assert d.action == "close"
    assert d.path_id == "path-a"
    assert d.status == PathStatus.INCONCLUSIVE
    assert d.reason == "Not advancing vision"


def test_strategy_decision_open():
    from foray.models import StrategyDecision, PathInfo, Priority
    new_path = PathInfo(id="path-new", description="New exploration", priority=Priority.HIGH, hypothesis="New hyp")
    d = StrategyDecision(action="open", new_path=new_path)
    assert d.action == "open"
    assert d.new_path.id == "path-new"


def test_strategy_decision_reprioritize():
    from foray.models import StrategyDecision, Priority
    d = StrategyDecision(action="reprioritize", path_id="path-b", priority=Priority.HIGH)
    assert d.action == "reprioritize"
    assert d.priority == Priority.HIGH


def test_strategy_decision_defaults():
    from foray.models import StrategyDecision
    d = StrategyDecision(action="close")
    assert d.path_id == ""
    assert d.status is None
    assert d.reason == ""
    assert d.priority is None
    assert d.new_path is None


def test_strategy_output_basic():
    from foray.models import StrategyOutput
    s = StrategyOutput(vision_assessment="Good progress on path-a", rationale="Stay the course")
    assert s.vision_assessment == "Good progress on path-a"
    assert s.decisions == []
    assert s.rationale == "Stay the course"


def test_strategy_output_null_coercion():
    """StrategyOutput extends _AgentOutput — null fields coerce to defaults."""
    import json
    from foray.models import StrategyOutput
    raw = json.dumps({
        "vision_assessment": "test",
        "decisions": None,
        "rationale": None,
    })
    s = StrategyOutput.model_validate_json(raw)
    assert s.decisions == []
    assert s.rationale == ""


def test_strategy_output_with_decisions():
    import json
    from foray.models import StrategyOutput
    raw = json.dumps({
        "vision_assessment": "Path-a is stale",
        "decisions": [
            {"action": "close", "path_id": "path-a", "status": "inconclusive", "reason": "Not advancing"},
            {"action": "open", "new_path": {
                "id": "path-c", "description": "Fresh angle", "priority": "high", "hypothesis": "New hyp"
            }},
            {"action": "reprioritize", "path_id": "path-b", "priority": "high"},
        ],
        "rationale": "Pivoting to fresh approach",
    })
    s = StrategyOutput.model_validate_json(raw)
    assert len(s.decisions) == 3
    assert s.decisions[0].action == "close"
    assert s.decisions[0].status == "inconclusive"
    assert s.decisions[1].new_path.id == "path-c"
    assert s.decisions[2].priority == "high"


def test_strategy_output_roundtrip():
    import json
    from foray.models import StrategyOutput, StrategyDecision, PathStatus, Priority
    s = StrategyOutput(
        vision_assessment="test",
        decisions=[
            StrategyDecision(action="close", path_id="a", status=PathStatus.INCONCLUSIVE, reason="done"),
            StrategyDecision(action="reprioritize", path_id="b", priority=Priority.LOW),
        ],
        rationale="reason",
    )
    raw = s.model_dump_json()
    s2 = StrategyOutput.model_validate_json(raw)
    assert s2.vision_assessment == "test"
    assert len(s2.decisions) == 2
    assert s2.decisions[0].status == PathStatus.INCONCLUSIVE
