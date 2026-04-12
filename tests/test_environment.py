import importlib.util
import shutil
from datetime import datetime, timezone
from pathlib import Path

from foray.context import build_planner_context
from foray.environment import run_preflight
from foray.models import PathInfo, Priority, RunConfig, RunState


def _path() -> PathInfo:
    return PathInfo(id="path-a", description="Test", priority=Priority.HIGH, hypothesis="Test", experiment_count=0)


def _state() -> RunState:
    return RunState(start_time=datetime.now(timezone.utc), config=RunConfig(vision_path="V.md"))


def test_preflight_creates_environment_md(tmp_path: Path):
    run_preflight(tmp_path)
    env_file = tmp_path / "environment.md"
    assert env_file.exists()
    content = env_file.read_text()
    assert content.startswith("# Environment")


def test_preflight_reports_tools(tmp_path: Path):
    run_preflight(tmp_path)
    content = (tmp_path / "environment.md").read_text()
    assert "## CLI Tools" in content
    assert "- claude:" in content
    assert "- uv:" in content
    assert "- git:" in content


def test_preflight_reports_missing_tool(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    run_preflight(tmp_path)
    content = (tmp_path / "environment.md").read_text()
    assert "- claude: not found" in content
    assert "- uv: not found" in content
    assert "- git: not found" in content


def test_preflight_reports_packages(tmp_path: Path):
    run_preflight(tmp_path)
    content = (tmp_path / "environment.md").read_text()
    assert "## Python Packages" in content
    # Each line should match "- name: ..." format
    lines = [l for l in content.splitlines() if l.startswith("- ") and ":" in l]
    assert len(lines) > 0


def test_preflight_reports_unavailable_package(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    run_preflight(tmp_path)
    content = (tmp_path / "environment.md").read_text()
    assert "not available" in content


def test_planner_context_includes_environment(tmp_path: Path):
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()
    (tmp_path / "environment.md").write_text("# Environment\n\n## CLI Tools\n- git: /usr/bin/git\n")

    ctx = build_planner_context(tmp_path, _path(), [], _state(), needs_justification=False)
    assert "## CLI Tools" in ctx
    assert "- git: /usr/bin/git" in ctx


def test_planner_context_without_environment_file(tmp_path: Path):
    (tmp_path / "vision.md").write_text("Test vision")
    (tmp_path / "experiments").mkdir()

    ctx = build_planner_context(tmp_path, _path(), [], _state(), needs_justification=False)
    assert "Environment" not in ctx
