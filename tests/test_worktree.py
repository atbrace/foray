import shutil
import subprocess
from pathlib import Path

from foray.models import ExperimentStatus
from foray.worktree import (
    cleanup_git_wrapper,
    cleanup_worktree,
    create_git_wrapper,
    create_worktree,
    should_preserve_worktree,
    snapshot_git_state,
    verify_git_integrity,
)


def test_create_worktree(git_repo: Path):
    foray_dir = git_repo / ".foray"
    foray_dir.mkdir()
    (foray_dir / "worktrees").mkdir()

    wt = create_worktree(git_repo, "001", foray_dir)
    assert wt.exists()
    assert (wt / "README.md").exists()

    # Verify detached HEAD
    result = subprocess.run(
        ["git", "symbolic-ref", "HEAD"],
        cwd=wt, capture_output=True, text=True,
    )
    assert result.returncode != 0


def test_cleanup_worktree(git_repo: Path):
    foray_dir = git_repo / ".foray"
    foray_dir.mkdir()
    (foray_dir / "worktrees").mkdir()

    wt = create_worktree(git_repo, "001", foray_dir)
    assert wt.exists()
    cleanup_worktree(git_repo, wt)
    assert not wt.exists()


def test_should_preserve_worktree():
    assert should_preserve_worktree(ExperimentStatus.SUCCESS) is False
    assert should_preserve_worktree(ExperimentStatus.PARTIAL) is True
    assert should_preserve_worktree(ExperimentStatus.FAILED) is True
    assert should_preserve_worktree(ExperimentStatus.INFEASIBLE) is True
    assert should_preserve_worktree(ExperimentStatus.CRASH) is True


def test_git_wrapper_blocks_push():
    real_git = shutil.which("git")
    wrapper_dir = create_git_wrapper(real_git)
    try:
        result = subprocess.run(
            [str(wrapper_dir / "git"), "push"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "blocked" in result.stderr.lower()
    finally:
        cleanup_git_wrapper(wrapper_dir)


def test_git_wrapper_blocks_branch_delete():
    real_git = shutil.which("git")
    wrapper_dir = create_git_wrapper(real_git)
    try:
        result = subprocess.run(
            [str(wrapper_dir / "git"), "branch", "-D", "some-branch"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "blocked" in result.stderr.lower()
    finally:
        cleanup_git_wrapper(wrapper_dir)


def test_git_wrapper_blocks_remote_remove():
    real_git = shutil.which("git")
    wrapper_dir = create_git_wrapper(real_git)
    try:
        result = subprocess.run(
            [str(wrapper_dir / "git"), "remote", "remove", "origin"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "blocked" in result.stderr.lower()
    finally:
        cleanup_git_wrapper(wrapper_dir)


def test_git_wrapper_allows_normal_commands(git_repo: Path):
    real_git = shutil.which("git")
    wrapper_dir = create_git_wrapper(real_git)
    try:
        result = subprocess.run(
            [str(wrapper_dir / "git"), "status"],
            cwd=git_repo, capture_output=True, text=True,
        )
        assert result.returncode == 0
    finally:
        cleanup_git_wrapper(wrapper_dir)


def test_git_integrity_unchanged(git_repo: Path):
    snapshot = snapshot_git_state(git_repo)
    ok, msg = verify_git_integrity(git_repo, snapshot)
    assert ok is True


def test_git_integrity_detects_head_change(git_repo: Path):
    snapshot = snapshot_git_state(git_repo)
    (git_repo / "new.txt").write_text("new")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "change"], cwd=git_repo, check=True, capture_output=True)
    ok, msg = verify_git_integrity(git_repo, snapshot)
    assert ok is False
    assert "HEAD changed" in msg
