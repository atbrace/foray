import shutil
import subprocess
from pathlib import Path

from foray.models import ExperimentStatus
from foray.worktree import (
    cleanup_git_wrapper,
    cleanup_worktree,
    create_git_wrapper,
    create_worktree,
    prune_worktrees,
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


def test_create_worktree_replaces_stale(git_repo: Path):
    """Simulates a killed run leaving a stale worktree behind."""
    foray_dir = git_repo / ".foray"
    foray_dir.mkdir()
    (foray_dir / "worktrees").mkdir()

    wt = create_worktree(git_repo, "001", foray_dir)
    assert wt.exists()
    marker = wt / "stale_marker.txt"
    marker.write_text("from old run")

    # Creating the same worktree again should succeed, replacing the old one.
    wt2 = create_worktree(git_repo, "001", foray_dir)
    assert wt2 == wt
    assert wt2.exists()
    assert not marker.exists()


def test_create_worktree_after_dir_deleted(git_repo: Path):
    """Simulates rm -rf .foray leaving stale git worktree references."""
    foray_dir = git_repo / ".foray"
    foray_dir.mkdir()
    (foray_dir / "worktrees").mkdir()

    wt = create_worktree(git_repo, "001", foray_dir)
    assert wt.exists()

    # Delete the directory but leave the git reference (like rm -rf .foray).
    shutil.rmtree(wt)
    assert not wt.exists()

    # Verify git still knows about it.
    result = subprocess.run(
        ["git", "worktree", "list"], cwd=git_repo,
        capture_output=True, text=True,
    )
    assert "exp-001" in result.stdout

    # Re-creating should succeed despite the orphaned reference.
    foray_dir.mkdir(exist_ok=True)
    (foray_dir / "worktrees").mkdir(exist_ok=True)
    wt2 = create_worktree(git_repo, "001", foray_dir)
    assert wt2.exists()


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


def test_prune_worktrees(git_repo: Path):
    """prune_worktrees cleans up stale worktree references."""
    foray_dir = git_repo / ".foray"
    foray_dir.mkdir()
    (foray_dir / "worktrees").mkdir()

    wt = create_worktree(git_repo, "stale", foray_dir)
    assert wt.exists()

    # Delete the directory but leave the git reference (simulates stale state).
    shutil.rmtree(wt)
    assert not wt.exists()

    # Git still knows about it.
    result = subprocess.run(
        ["git", "worktree", "list"], cwd=git_repo,
        capture_output=True, text=True,
    )
    assert "exp-stale" in result.stdout

    # Prune should clean the stale reference.
    prune_worktrees(git_repo)

    result = subprocess.run(
        ["git", "worktree", "list"], cwd=git_repo,
        capture_output=True, text=True,
    )
    assert "exp-stale" not in result.stdout


def test_create_worktree_does_not_prune(git_repo: Path):
    """create_worktree no longer calls git worktree prune internally."""
    foray_dir = git_repo / ".foray"
    foray_dir.mkdir()
    (foray_dir / "worktrees").mkdir()

    # Create a worktree, delete its dir to leave a stale reference for a DIFFERENT experiment.
    wt_stale = create_worktree(git_repo, "old", foray_dir)
    shutil.rmtree(wt_stale)

    # Now create a different worktree — it should NOT prune the stale one.
    create_worktree(git_repo, "new", foray_dir)

    result = subprocess.run(
        ["git", "worktree", "list"], cwd=git_repo,
        capture_output=True, text=True,
    )
    # The stale "old" reference should still be present (not pruned).
    assert "exp-old" in result.stdout


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
