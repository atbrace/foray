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
from foray.models import TimingRecord
from foray.state import (
    _atomic_write,
    add_finding,
    append_timing,
    init_directory,
    read_evaluation,
    read_findings,
    read_paths,
    read_rounds,
    read_run_state,
    read_timing,
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


def test_append_and_read_timing(tmp_path):
    """Timing records can be appended and read back."""
    state = RunState(start_time=datetime.now(timezone.utc), config=RunConfig(vision_path="v.md"))
    foray_dir = init_directory(tmp_path, state)

    record = TimingRecord(
        experiment_id="r1-001", agent_type="planner",
        elapsed_seconds=3.2, input_tokens=5000, output_tokens=1200, cost_usd=0.05,
    )
    append_timing(foray_dir, record)

    records = read_timing(foray_dir)
    assert len(records) == 1
    assert records[0].experiment_id == "r1-001"
    assert records[0].input_tokens == 5000


def test_append_timing_multiple(tmp_path):
    """Multiple timing records accumulate."""
    state = RunState(start_time=datetime.now(timezone.utc), config=RunConfig(vision_path="v.md"))
    foray_dir = init_directory(tmp_path, state)

    for i in range(3):
        append_timing(foray_dir, TimingRecord(
            experiment_id=f"r1-{i:03d}", agent_type="executor",
            elapsed_seconds=float(i), input_tokens=i * 100, output_tokens=i * 50,
        ))

    records = read_timing(foray_dir)
    assert len(records) == 3


def test_read_timing_empty(tmp_path):
    """Returns empty list when no timing file exists."""
    state = RunState(start_time=datetime.now(timezone.utc), config=RunConfig(vision_path="v.md"))
    foray_dir = init_directory(tmp_path, state)
    assert read_timing(foray_dir) == []


def test_append_writes_jsonl_line(tmp_path):
    """append_timing writes a single JSONL line, not a JSON array."""
    state = RunState(start_time=datetime.now(timezone.utc), config=RunConfig(vision_path="v.md"))
    foray_dir = init_directory(tmp_path, state)

    record = TimingRecord(
        experiment_id="r1-001", agent_type="planner",
        elapsed_seconds=3.2, input_tokens=5000, output_tokens=1200, cost_usd=0.05,
    )
    append_timing(foray_dir, record)

    jsonl_path = foray_dir / "state" / "timing.jsonl"
    assert jsonl_path.exists()
    raw = jsonl_path.read_text()
    lines = [l for l in raw.strip().split("\n") if l]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["experiment_id"] == "r1-001"


def test_multiple_appends_create_multiple_lines(tmp_path):
    """Each append_timing call adds exactly one line to the JSONL file."""
    state = RunState(start_time=datetime.now(timezone.utc), config=RunConfig(vision_path="v.md"))
    foray_dir = init_directory(tmp_path, state)

    for i in range(5):
        append_timing(foray_dir, TimingRecord(
            experiment_id=f"r1-{i:03d}", agent_type="executor",
            elapsed_seconds=float(i), input_tokens=i * 100, output_tokens=i * 50,
        ))

    jsonl_path = foray_dir / "state" / "timing.jsonl"
    lines = [l for l in jsonl_path.read_text().strip().split("\n") if l]
    assert len(lines) == 5


def test_read_timing_reads_jsonl(tmp_path):
    """read_timing parses JSONL format correctly."""
    state = RunState(start_time=datetime.now(timezone.utc), config=RunConfig(vision_path="v.md"))
    foray_dir = init_directory(tmp_path, state)

    # Write JSONL manually
    jsonl_path = foray_dir / "state" / "timing.jsonl"
    lines = [
        json.dumps({"experiment_id": "r1-001", "agent_type": "planner", "elapsed_seconds": 3.0, "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.01}),
        json.dumps({"experiment_id": "r1-002", "agent_type": "executor", "elapsed_seconds": 5.0, "input_tokens": 200, "output_tokens": 100, "cost_usd": 0.02}),
    ]
    jsonl_path.write_text("\n".join(lines) + "\n")

    records = read_timing(foray_dir)
    assert len(records) == 2
    assert records[0].experiment_id == "r1-001"
    assert records[1].agent_type == "executor"


def test_timing_json_migrated_to_jsonl(tmp_path):
    """Old timing.json gets migrated to timing.jsonl on first read_timing call."""
    state = RunState(start_time=datetime.now(timezone.utc), config=RunConfig(vision_path="v.md"))
    foray_dir = init_directory(tmp_path, state)

    # Write old-format timing.json
    old_path = foray_dir / "state" / "timing.json"
    old_data = [
        {"experiment_id": "r1-001", "agent_type": "planner", "elapsed_seconds": 3.0, "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.01},
        {"experiment_id": "r1-002", "agent_type": "executor", "elapsed_seconds": 5.0, "input_tokens": 200, "output_tokens": 100, "cost_usd": 0.02},
    ]
    old_path.write_text(json.dumps(old_data, indent=2))

    records = read_timing(foray_dir)
    assert len(records) == 2
    assert records[0].experiment_id == "r1-001"
    assert records[1].experiment_id == "r1-002"

    # Verify migration happened: jsonl exists, json removed
    assert (foray_dir / "state" / "timing.jsonl").exists()
    assert not old_path.exists()


# --- Strategy persistence ---


def test_write_and_read_strategy(tmp_path):
    from foray.models import StrategyOutput, StrategyDecision
    from foray.state import write_strategy, read_strategy
    foray_dir = tmp_path
    (foray_dir / "state").mkdir()

    strategy = StrategyOutput(
        vision_assessment="Good progress",
        decisions=[StrategyDecision(action="close", path_id="a", reason="done")],
        rationale="Staying focused",
    )
    write_strategy(foray_dir, strategy)
    loaded = read_strategy(foray_dir)
    assert loaded is not None
    assert loaded.vision_assessment == "Good progress"
    assert len(loaded.decisions) == 1
    assert loaded.decisions[0].path_id == "a"


def test_read_strategy_missing(tmp_path):
    from foray.state import read_strategy
    foray_dir = tmp_path
    (foray_dir / "state").mkdir()
    assert read_strategy(foray_dir) is None


def test_read_strategy_malformed(tmp_path):
    from foray.state import read_strategy
    foray_dir = tmp_path
    (foray_dir / "state").mkdir()
    (foray_dir / "state" / "strategy.json").write_text("not json{{{")
    assert read_strategy(foray_dir) is None


def test_write_strategy_overwrites(tmp_path):
    from foray.models import StrategyOutput
    from foray.state import write_strategy, read_strategy
    foray_dir = tmp_path
    (foray_dir / "state").mkdir()

    write_strategy(foray_dir, StrategyOutput(vision_assessment="first"))
    write_strategy(foray_dir, StrategyOutput(vision_assessment="second"))
    loaded = read_strategy(foray_dir)
    assert loaded.vision_assessment == "second"
