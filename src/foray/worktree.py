from __future__ import annotations

import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from foray.models import ExperimentStatus


def create_worktree(project_root: Path, experiment_id: str, foray_dir: Path) -> Path:
    """Create a detached HEAD worktree for an experiment."""
    worktree_path = foray_dir / "worktrees" / f"exp-{experiment_id}"
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree_path)],
        cwd=project_root,
        check=True,
        capture_output=True,
    )
    return worktree_path


def cleanup_worktree(project_root: Path, worktree_path: Path) -> None:
    """Remove a worktree and its directory."""
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        cwd=project_root,
        check=True,
        capture_output=True,
    )


def should_preserve_worktree(status: ExperimentStatus) -> bool:
    """True unless SUCCESS — all failure modes preserve the worktree."""
    return status != ExperimentStatus.SUCCESS


def copy_artifacts(worktree_path: Path, artifacts_dir: Path) -> None:
    """Copy files changed in the worktree to permanent storage."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    for filename in result.stdout.strip().splitlines():
        if not filename:
            continue
        src = worktree_path / filename
        if src.exists():
            dst = artifacts_dir / filename
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def enforce_worktree_limit(foray_dir: Path, project_root: Path, max_kept: int = 10) -> None:
    """Clean oldest worktrees if limit exceeded."""
    worktrees_dir = foray_dir / "worktrees"
    if not worktrees_dir.exists():
        return
    kept = sorted(
        [d for d in worktrees_dir.iterdir() if d.is_dir()],
        key=lambda p: p.stat().st_mtime,
    )
    while len(kept) > max_kept:
        oldest = kept.pop(0)
        try:
            cleanup_worktree(project_root, oldest)
        except subprocess.CalledProcessError:
            shutil.rmtree(oldest, ignore_errors=True)


def create_git_wrapper(real_git_path: str) -> Path:
    """Create shell script intercepting destructive git commands.

    Returns directory containing the wrapper — prepend to PATH.
    """
    wrapper_dir = Path(tempfile.mkdtemp(prefix="foray-git-"))
    wrapper_path = wrapper_dir / "git"
    wrapper_path.write_text(
        f'#!/bin/bash\n'
        f'REAL_GIT="{real_git_path}"\n'
        f'\n'
        f'case "$1" in\n'
        f'  push)\n'
        f'    echo "ERROR: git push is blocked in Foray executor worktrees." >&2\n'
        f'    exit 1\n'
        f'    ;;\n'
        f'  remote)\n'
        f'    if [[ "$2" == "set-url" || "$2" == "remove" ]]; then\n'
        f'      echo "ERROR: git remote $2 is blocked in Foray executor worktrees." >&2\n'
        f'      exit 1\n'
        f'    fi\n'
        f'    ;;\n'
        f'esac\n'
        f'\n'
        f'if [[ "$1" == "branch" ]]; then\n'
        f'  for arg in "$@"; do\n'
        f'    if [[ "$arg" == "-D" || "$arg" == "-d" || "$arg" == "--delete" ]]; then\n'
        f'      echo "ERROR: git branch deletion is blocked in Foray executor worktrees." >&2\n'
        f'      exit 1\n'
        f'    fi\n'
        f'  done\n'
        f'fi\n'
        f'\n'
        f'exec "$REAL_GIT" "$@"\n'
    )
    wrapper_path.chmod(wrapper_path.stat().st_mode | stat.S_IEXEC)
    return wrapper_dir


def cleanup_git_wrapper(wrapper_dir: Path) -> None:
    """Remove the temporary git wrapper directory."""
    shutil.rmtree(wrapper_dir, ignore_errors=True)


def snapshot_git_state(project_root: Path) -> dict:
    """Capture HEAD ref and branch list for integrity checking."""
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=project_root, capture_output=True, text=True, check=True,
    ).stdout.strip()
    branches = subprocess.run(
        ["git", "branch", "--list"],
        cwd=project_root, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return {"head": head, "branches": branches}


def verify_git_integrity(project_root: Path, snapshot: dict) -> tuple[bool, str]:
    """Verify git state hasn't changed. Returns (ok, message)."""
    current = snapshot_git_state(project_root)
    if current["head"] != snapshot["head"]:
        return False, f"HEAD changed: {snapshot['head']} -> {current['head']}"
    if current["branches"] != snapshot["branches"]:
        return False, "Branches changed"
    return True, "OK"
