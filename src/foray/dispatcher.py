from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from foray.models import DispatchResult, ExperimentStatus
from foray.worktree import (
    cleanup_git_wrapper,
    create_git_wrapper,
    snapshot_git_state,
    verify_git_integrity,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MINUTES = 10


def dispatch(
    prompt: str,
    workdir: Path,
    model: str,
    max_turns: int,
    tools: list[str],
    timeout_minutes: float = DEFAULT_TIMEOUT_MINUTES,
    results_file: Path | None = None,
    env: dict[str, str] | None = None,
) -> DispatchResult:
    """Dispatch a Claude Code CLI agent and capture results."""
    cmd = [
        "claude", "-p", prompt,
        "--model", model,
        "--max-turns", str(max_turns),
        "--permission-mode", "acceptEdits",
        "--output-format", "text",
    ]
    if tools:
        cmd.extend(["--allowedTools", ",".join(tools)])

    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)

    start = time.monotonic()

    # Use Popen + temp files instead of subprocess.run(capture_output=True)
    # so we capture partial stdout/stderr even when the process is killed
    # on timeout. subprocess.run loses all output on TimeoutExpired.
    stdout_fd, stdout_path = tempfile.mkstemp(suffix=".stdout")
    stderr_fd, stderr_path = tempfile.mkstemp(suffix=".stderr")
    try:
        with os.fdopen(stdout_fd, "w") as stdout_f, os.fdopen(stderr_fd, "w") as stderr_f:
            proc = subprocess.Popen(
                cmd, cwd=workdir, stdout=stdout_f, stderr=stderr_f,
                text=True, env=proc_env,
            )
            try:
                proc.wait(timeout=timeout_minutes * 60)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

        elapsed = time.monotonic() - start
        stdout_text = Path(stdout_path).read_text()
        stderr_text = Path(stderr_path).read_text()
        timed_out = proc.returncode == -9 or proc.returncode is None

        if timed_out:
            logger.warning(f"Agent timed out after {elapsed:.0f}s")

        return DispatchResult(
            exit_code=-1 if timed_out else proc.returncode,
            stdout=stdout_text,
            stderr=stderr_text,
            elapsed_seconds=elapsed,
            results_file_path=(
                str(results_file)
                if results_file and results_file.exists()
                else None
            ),
        )
    finally:
        for p in (stdout_path, stderr_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def dispatch_executor(
    prompt: str,
    worktree_path: Path,
    project_root: Path,
    experiment_id: str,
    model: str,
    max_turns: int,
    tools: list[str],
    foray_dir: Path,
    timeout_minutes: float = DEFAULT_TIMEOUT_MINUTES,
) -> DispatchResult:
    """Dispatch executor with git safety: wrapper in PATH + integrity check."""
    real_git = shutil.which("git")
    wrapper_dir = create_git_wrapper(real_git)
    snapshot = snapshot_git_state(project_root)

    custom_env = {"PATH": f"{wrapper_dir}:{os.environ.get('PATH', '')}"}
    results_file = foray_dir / "experiments" / f"{experiment_id}_results.md"

    try:
        result = dispatch(
            prompt=prompt,
            workdir=worktree_path,
            model=model,
            max_turns=max_turns,
            tools=tools,
            timeout_minutes=timeout_minutes,
            results_file=results_file,
            env=custom_env,
        )
    finally:
        cleanup_git_wrapper(wrapper_dir)

    ok, msg = verify_git_integrity(project_root, snapshot)
    if not ok:
        logger.error(f"Git integrity check failed: {msg}")
        raise RuntimeError(f"Git integrity violation: {msg}")

    return result


def write_crash_stub(
    foray_dir: Path,
    experiment_id: str,
    plan_path: Path,
    dispatch_result: DispatchResult,
) -> None:
    """Write CRASH stub when executor dies without producing results."""
    plan_content = plan_path.read_text() if plan_path.exists() else "(plan not found)"
    stdout_tail = dispatch_result.stdout[-3000:] if dispatch_result.stdout else "(empty)"
    stub = (
        f"## Status\nCRASH\n\n"
        f"## What Happened\n"
        f"The executor process died without writing results.\n\n"
        f"- Exit code: {dispatch_result.exit_code}\n"
        f"- Elapsed: {dispatch_result.elapsed_seconds:.1f}s\n\n"
        f"## Stderr\n```\n{dispatch_result.stderr[:2000]}\n```\n\n"
        f"## Agent Output (last 3000 chars)\n```\n{stdout_tail}\n```\n\n"
        f"## Original Plan\n{plan_content}\n"
    )
    from foray.state import _atomic_write

    results_path = foray_dir / "experiments" / f"{experiment_id}_results.md"
    _atomic_write(results_path, stub)


def parse_experiment_status(results_path: Path) -> ExperimentStatus:
    """Parse Status header from experiment results file."""
    if not results_path.exists():
        return ExperimentStatus.CRASH

    for line in results_path.read_text().splitlines():
        stripped = line.strip()
        if stripped in ("SUCCESS", "PARTIAL", "FAILED", "INFEASIBLE", "CRASH"):
            return ExperimentStatus(stripped)

    return ExperimentStatus.CRASH
