from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from pydantic import ValidationError

logger = logging.getLogger(__name__)

from foray.models import (
    Evaluation,
    Finding,
    PathInfo,
    Round,
    RunState,
    StrategyOutput,
    TimingRecord,
)


def _atomic_write(path: Path, content: str) -> None:
    """Write content atomically via temp file + rename."""
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.rename(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def init_directory(project_root: Path, state: RunState) -> Path:
    """Create .foray/ directory structure and write initial state files."""
    foray_dir = project_root / state.config.output_dir
    foray_dir.mkdir(exist_ok=True)
    (foray_dir / "state").mkdir(exist_ok=True)
    (foray_dir / "experiments").mkdir(exist_ok=True)
    (foray_dir / "worktrees").mkdir(exist_ok=True)
    (foray_dir / "agents").mkdir(exist_ok=True)

    write_run_state(foray_dir, state)
    write_paths(foray_dir, [])
    write_rounds(foray_dir, [])
    write_findings(foray_dir, [])

    return foray_dir


def _serialize_model_list(items: list) -> str:
    return json.dumps([item.model_dump(mode="json") for item in items], indent=2)


def write_paths(foray_dir: Path, paths: list[PathInfo]) -> None:
    _atomic_write(foray_dir / "state" / "paths.json", _serialize_model_list(paths))


def read_paths(foray_dir: Path) -> list[PathInfo]:
    raw = json.loads((foray_dir / "state" / "paths.json").read_text())
    return [PathInfo.model_validate(item) for item in raw]


def write_rounds(foray_dir: Path, rounds: list[Round]) -> None:
    _atomic_write(foray_dir / "state" / "rounds.json", _serialize_model_list(rounds))


def read_rounds(foray_dir: Path) -> list[Round]:
    raw = json.loads((foray_dir / "state" / "rounds.json").read_text())
    return [Round.model_validate(item) for item in raw]


def write_findings(foray_dir: Path, findings: list[Finding]) -> None:
    _atomic_write(foray_dir / "state" / "findings.json", _serialize_model_list(findings))


def read_findings(foray_dir: Path) -> list[Finding]:
    raw = json.loads((foray_dir / "state" / "findings.json").read_text())
    return [Finding.model_validate(item) for item in raw]


def add_finding(foray_dir: Path, finding: Finding) -> None:
    findings = read_findings(foray_dir)
    findings.append(finding)
    write_findings(foray_dir, findings)


def write_run_state(foray_dir: Path, state: RunState) -> None:
    _atomic_write(foray_dir / "foray.json", state.model_dump_json(indent=2))


def read_run_state(foray_dir: Path) -> RunState:
    return RunState.model_validate_json((foray_dir / "foray.json").read_text())


def write_evaluation(foray_dir: Path, assessment: Evaluation) -> None:
    path = foray_dir / "experiments" / f"{assessment.experiment_id}_eval.json"
    _atomic_write(path, assessment.model_dump_json(indent=2))


def read_evaluation(foray_dir: Path, experiment_id: str) -> Evaluation | None:
    path = foray_dir / "experiments" / f"{experiment_id}_eval.json"
    if not path.exists():
        return None
    try:
        return Evaluation.model_validate_json(path.read_text())
    except (ValidationError, ValueError) as e:
        logger.warning(f"Failed to parse evaluation {experiment_id}: {e}")
        return None


def append_timing(foray_dir: Path, record: TimingRecord) -> None:
    """Append a timing record to timing.jsonl (one JSON object per line).

    Callers must serialize access externally (e.g. orchestrator's _timing_lock).
    """
    path = foray_dir / "state" / "timing.jsonl"
    with open(path, "a") as f:
        f.write(record.model_dump_json() + "\n")


def _migrate_timing_json(foray_dir: Path) -> None:
    """Migrate old timing.json to timing.jsonl format."""
    old_path = foray_dir / "state" / "timing.json"
    new_path = foray_dir / "state" / "timing.jsonl"
    records = [TimingRecord.model_validate(r) for r in json.loads(old_path.read_text())]
    lines = "".join(r.model_dump_json() + "\n" for r in records)
    _atomic_write(new_path, lines)
    old_path.unlink()


def read_timing(foray_dir: Path) -> list[TimingRecord]:
    """Read all timing records from timing.jsonl."""
    path = foray_dir / "state" / "timing.jsonl"
    old_path = foray_dir / "state" / "timing.json"
    if not path.exists():
        if old_path.exists():
            _migrate_timing_json(foray_dir)
        else:
            return []
    return [
        TimingRecord.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def write_strategy(foray_dir: Path, strategy: StrategyOutput) -> None:
    _atomic_write(foray_dir / "state" / "strategy.json", strategy.model_dump_json(indent=2))


def read_strategy(foray_dir: Path) -> StrategyOutput | None:
    path = foray_dir / "state" / "strategy.json"
    if not path.exists():
        return None
    try:
        return StrategyOutput.model_validate_json(path.read_text())
    except (ValidationError, ValueError) as e:
        logger.warning(f"Failed to parse strategy: {e}")
        return None
