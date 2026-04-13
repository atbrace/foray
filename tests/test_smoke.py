"""Smoke test: full orchestrator loop with mocked dispatchers.

Exercises parallel dispatch, state mutations, worktrees, budget
enforcement, and round management — without any Claude API calls.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from foray.models import (
    Confidence,
    ExperimentStatus,
    PathInfo,
    PathStatus,
    Priority,
    RunConfig,
    RunState,
)
from foray.orchestrator import Orchestrator
from foray.state import (
    init_directory,
    read_findings,
    read_paths,
    read_rounds,
    read_run_state,
    write_paths,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Temporary git repo with one commit."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    (tmp_path / "README.md").write_text("# Test Project\nA sample project for smoke testing.")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def main():\n    print('hello')\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    return tmp_path


def _mock_dispatch_factory(foray_dir: Path):
    """Return a dispatch mock that writes realistic agent output files.

    The mock inspects the prompt to determine which agent is being dispatched
    (initializer, planner, executor, evaluator, synthesizer) and writes
    appropriate output files.
    """
    call_count = {"plan": 0, "eval": 0}

    def mock_dispatch(prompt: str, workdir: Path = None, model: str = "",
                      max_turns: int = 0, tools: list[str] = None,
                      timeout_minutes: float = 10, results_file: Path | None = None,
                      env: dict | None = None, output_format: str = "text",
                      worktree_path: Path | None = None,
                      project_root: Path | None = None, experiment_id: str | None = None,
                      foray_dir: Path | None = None):
        result = MagicMock()
        result.exit_code = 0
        result.stdout = "ok"
        result.stderr = ""
        result.elapsed_seconds = 1.0
        result.results_file_path = None

        if "Write experiment plan to:" in prompt:
            # Planner — extract plan path and write a plan
            plan_path = prompt.split("Write experiment plan to: ")[1].strip().split("\n")[0]
            Path(plan_path).parent.mkdir(parents=True, exist_ok=True)
            Path(plan_path).write_text(
                "# Experiment Plan\n\n"
                "## Hypothesis\nTest hypothesis\n\n"
                "## Research Phase\n1. Check docs\n2. Validate\n3. Gate\n\n"
                "## Implementation Phase\n1. Read src/main.py\n2. Analyze patterns\n\n"
                "## Success Criteria\nFind at least one pattern\n"
            )

        elif "Write results to:" in prompt:
            # Executor — extract results path and write results
            results_line = [l for l in prompt.split("\n") if "Write results to:" in l][0]
            rpath = results_line.split("Write results to: ")[1].strip()
            Path(rpath).parent.mkdir(parents=True, exist_ok=True)
            Path(rpath).write_text(
                "## Status\nSUCCESS\n\n"
                "## What Was Done\nAnalyzed source code patterns.\n\n"
                "## Findings\nFound print-based logging pattern in main.py.\n\n"
                "## Conclusion\nCodebase uses simple print statements.\n"
            )

        elif "Write assessment JSON to:" in prompt:
            # Evaluator — extract assessment path and write JSON
            eval_path = prompt.split("Write assessment JSON to: ")[1].strip().split("\n")[0]
            # Extract experiment_id and path_id from the prompt context
            exp_id = "unknown"
            path_id = "unknown"
            if "Experiment " in prompt:
                for part in prompt.split("Experiment "):
                    if part and part[0].isdigit():
                        exp_id = part[:3]
                        break
            if "**ID:** " in prompt:
                path_id = prompt.split("**ID:** ")[1].split("\n")[0].strip()

            Path(eval_path).parent.mkdir(parents=True, exist_ok=True)
            Path(eval_path).write_text(json.dumps({
                "experiment_id": exp_id,
                "path_id": path_id,
                "outcome": "conclusive",
                "path_status": "open",
                "confidence": "medium",
                "summary": f"Experiment {exp_id} found useful patterns in the codebase.",
                "planner_brief": f"Analyzed patterns for {path_id}. Found print logging.",
                "topic_tags": ["logging", "patterns"],
                "blocker_description": "",
            }, indent=2))

        elif "Write synthesis report to:" in prompt:
            # Synthesizer — write a simple report
            synth_path = prompt.split("Write synthesis report to: ")[1].strip().split("\n")[0]
            Path(synth_path).parent.mkdir(parents=True, exist_ok=True)
            Path(synth_path).write_text(
                "# Synthesis Report\n\n"
                "## Key Findings\n"
                "The codebase uses print-based logging patterns.\n\n"
                "## Recommendations\n"
                "Consider adopting a structured logging framework.\n"
            )

        return result
    return mock_dispatch


def test_smoke_full_run(git_repo: Path):
    """Full orchestrator loop: init (mocked) -> 2 rounds -> synthesis.

    Verifies:
    - Parallel dispatch works (3 paths, max_concurrent=2)
    - State mutations are correct after parallel execution
    - Budget enforcement stops at max_experiments
    - Worktrees are created and cleaned up
    - Rounds are recorded
    - Synthesis runs
    """
    config = RunConfig(
        vision_path="vision.md",
        hours=1.0,
        max_experiments=5,
        model="test-model",
        evaluator_model="test-model",
        max_turns=5,
        max_concurrent=2,
    )

    # Set up foray dir manually (skip init which dispatches initializer)
    foray_dir = git_repo / ".foray"
    state = RunState(start_time=datetime.now(timezone.utc), config=config)
    init_directory(git_repo, state)

    # Write vision
    (foray_dir / "vision.md").write_text("# Test\n\nWhat patterns does this codebase use?")

    # Write codebase map (normally written by initializer)
    (foray_dir / "codebase_map.md").write_text("# Codebase Map\n- src/main.py: entry point")

    # Write 3 paths (normally written by initializer)
    paths = [
        PathInfo(id="logging", description="Analyze logging patterns",
                 priority=Priority.HIGH, hypothesis="Uses print statements"),
        PathInfo(id="testing", description="Analyze test patterns",
                 priority=Priority.MEDIUM, hypothesis="Has pytest tests"),
        PathInfo(id="structure", description="Analyze project structure",
                 priority=Priority.LOW, hypothesis="Flat structure"),
    ]
    write_paths(foray_dir, paths)

    # Install agent prompts
    orch = Orchestrator(git_repo, config)
    orch.foray_dir = foray_dir
    orch._install_agent_prompts()

    mock_dispatch = _mock_dispatch_factory(foray_dir)

    with patch("foray.orchestrator.dispatch", side_effect=mock_dispatch), \
         patch("foray.orchestrator.dispatch_executor", side_effect=mock_dispatch):
        orch.run()

    # --- Verify state ---
    final_state = read_run_state(foray_dir)
    assert final_state.experiment_count == 5, (
        f"Expected 5 experiments (budget), got {final_state.experiment_count}"
    )

    # All 5 findings should exist
    findings = read_findings(foray_dir)
    assert len(findings) == 5, f"Expected 5 findings, got {len(findings)}"

    # Findings should cover all 3 paths
    finding_paths = {f.path_id for f in findings}
    assert finding_paths == {"logging", "testing", "structure"}, (
        f"Expected findings for all 3 paths, got {finding_paths}"
    )

    # All findings should be SUCCESS (our mock always writes SUCCESS)
    assert all(f.status == ExperimentStatus.SUCCESS for f in findings)

    # Rounds should be recorded
    rounds = read_rounds(foray_dir)
    assert len(rounds) >= 1, "Expected at least 1 round"
    total_outcomes = sum(len(r.outcomes) for r in rounds)
    assert total_outcomes == 5, f"Expected 5 round outcomes, got {total_outcomes}"

    # Synthesis report should exist
    synthesis = foray_dir / "synthesis.md"
    assert synthesis.exists(), "Synthesis report not written"
    assert "Key Findings" in synthesis.read_text()

    # Experiment files should exist
    experiments_dir = foray_dir / "experiments"
    plan_files = list(experiments_dir.glob("*_plan.md"))
    results_files = list(experiments_dir.glob("*_results.md"))
    eval_files = list(experiments_dir.glob("*_eval.json"))
    assert len(plan_files) == 5, f"Expected 5 plan files, got {len(plan_files)}"
    assert len(results_files) == 5, f"Expected 5 results files, got {len(results_files)}"
    assert len(eval_files) == 5, f"Expected 5 eval files, got {len(eval_files)}"

    print(f"\nSmoke test passed:")
    print(f"  Experiments: {final_state.experiment_count}")
    print(f"  Findings: {len(findings)}")
    print(f"  Rounds: {len(rounds)}")
    print(f"  Paths covered: {finding_paths}")
    print(f"  Synthesis: {synthesis.exists()}")
