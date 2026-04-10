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
