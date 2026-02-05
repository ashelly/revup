"""Shared test fixtures and helpers for revup tests.

This module provides common test infrastructure used across all test files:
- Git repository fixtures for integration tests
- Helper functions for creating commits and inspecting state
- CLI runner for end-to-end tests
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest

from revup import git, shell


# =============================================================================
# GIT REPOSITORY FIXTURES
# =============================================================================


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repo for integration tests.

    Creates a minimal git repository with:
    - Initial commit on main branch
    - Fake origin remote (pointing to self)
    - Proper git config for commits

    Yields the repo directory path and restores original cwd on cleanup.
    """
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    original_dir = os.getcwd()
    os.chdir(repo_dir)

    # Initialize git repo
    subprocess.run(["git", "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)

    # Create initial commit
    (repo_dir / "file.txt").write_text("initial")
    subprocess.run(["git", "add", "file.txt"], check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], check=True, capture_output=True)

    # Add fake remote so revup can find origin/main
    subprocess.run(["git", "remote", "add", "origin", "."], check=True)
    subprocess.run(["git", "fetch", "origin"], check=True, capture_output=True)

    yield repo_dir
    os.chdir(original_dir)


@pytest.fixture
def git_ctx(git_repo):
    """Create Git context for the test repo.

    Requires git_repo fixture. Returns a revup Git context configured
    for the test repository.
    """
    loop = asyncio.get_event_loop()
    sh = shell.Shell()
    return loop.run_until_complete(git.make_git(sh, remote_name="origin", main_branch="main"))


# =============================================================================
# COMMIT CREATION HELPERS
# =============================================================================


def create_topic_commit(
    repo_dir: Path,
    topic_name: str,
    relative: Optional[str] = None,
    filename: Optional[str] = None,
    draft: bool = False,
    commit_type: Optional[str] = None,
    scope: Optional[str] = None,
):
    """Create a commit with Topic tag and optional metadata.

    Args:
        repo_dir: Path to the git repository
        topic_name: Name for the Topic tag
        relative: Optional Relative tag value
        filename: Optional filename (defaults to {topic_name}.txt)
        draft: If True, adds Label: draft tag
        commit_type: Optional conventional commit type (feat, fix, etc.)
        scope: Optional conventional commit scope
    """
    filename = filename or f"{topic_name}.txt"
    (repo_dir / filename).write_text(f"content for {topic_name}")
    subprocess.run(["git", "add", filename], check=True)

    # Build commit message
    title = topic_name
    if commit_type:
        title = f"{commit_type}: {topic_name}" if not scope else f"{commit_type}({scope}): {topic_name}"
    else:
        title = f"feat: {topic_name}"

    msg = f"{title}\n\nTopic: {topic_name}"
    if relative:
        msg += f"\nRelative: {relative}"
    if draft:
        msg += "\nLabel: draft"

    subprocess.run(["git", "commit", "-m", msg], check=True, capture_output=True)


def create_non_topic_commit(repo_dir: Path, message: str, filename: Optional[str] = None):
    """Create a commit without a Topic tag.

    Args:
        repo_dir: Path to the git repository
        message: Commit message
        filename: Optional filename (defaults to message-based name)
    """
    filename = filename or f"{message.replace(' ', '_')[:20]}.txt"
    (repo_dir / filename).write_text(f"content: {message}")
    subprocess.run(["git", "add", filename], check=True)
    subprocess.run(["git", "commit", "-m", message], check=True, capture_output=True)


# =============================================================================
# COMMIT INSPECTION HELPERS
# =============================================================================


def get_commit_message(ref: str = "HEAD") -> str:
    """Get the commit message for a ref."""
    return subprocess.check_output(
        ["git", "log", "-1", "--format=%B", ref]
    ).decode().strip()


def get_commit_hash(ref: str = "HEAD") -> str:
    """Get the full commit hash for a ref."""
    return subprocess.check_output(
        ["git", "rev-parse", ref]
    ).decode().strip()


def get_topic_from_message(commit_msg: str) -> Optional[str]:
    """Extract Topic: value from commit message, or None."""
    for line in commit_msg.split('\n'):
        if line.lower().startswith('topic:'):
            return line.split(':', 1)[1].strip()
    return None


def get_relative_from_message(commit_msg: str) -> Optional[str]:
    """Extract Relative: value from commit message, or None."""
    for line in commit_msg.split('\n'):
        if line.lower().startswith('relative:'):
            return line.split(':', 1)[1].strip()
    return None


def get_label_from_message(commit_msg: str) -> Optional[str]:
    """Extract Label: value from commit message, or None."""
    for line in commit_msg.split('\n'):
        if line.lower().startswith('label:'):
            return line.split(':', 1)[1].strip()
    return None


def get_all_topic_relatives(num_commits: int) -> dict[str, Optional[str]]:
    """Get dict of topic -> relative for the last num_commits commits.

    Returns a dictionary mapping topic names to their Relative tag values
    (or None if no Relative tag).
    """
    result = {}
    for i in range(num_commits):
        ref = f"HEAD~{i}" if i > 0 else "HEAD"
        msg = get_commit_message(ref)
        topic = get_topic_from_message(msg)
        relative = get_relative_from_message(msg)
        if topic:
            result[topic] = relative
    return result


def get_commit_count() -> int:
    """Get the total number of commits in the repository."""
    output = subprocess.check_output(
        ["git", "rev-list", "--count", "HEAD"]
    ).decode().strip()
    return int(output)


# =============================================================================
# CLI RUNNER FOR E2E TESTS
# =============================================================================


def run_revup_cli(
    args: list[str],
    cwd: Optional[Path] = None,
    input_text: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    """Run revup CLI as a subprocess.

    Args:
        args: Command-line arguments (without 'revup' prefix)
        cwd: Working directory for the command
        input_text: Optional stdin input
        env: Optional environment variables to set

    Returns:
        CompletedProcess with stdout, stderr, and returncode
    """
    cmd = [sys.executable, "-m", "revup"] + args

    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    return subprocess.run(
        cmd,
        cwd=cwd,
        input=input_text,
        capture_output=True,
        text=True,
        env=run_env,
    )


def run_revup_cli_success(
    args: list[str],
    cwd: Optional[Path] = None,
    input_text: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
) -> str:
    """Run revup CLI and assert it succeeds.

    Args:
        args: Command-line arguments (without 'revup' prefix)
        cwd: Working directory for the command
        input_text: Optional stdin input
        env: Optional environment variables to set

    Returns:
        stdout output

    Raises:
        AssertionError: If command fails (non-zero exit code)
    """
    result = run_revup_cli(args, cwd=cwd, input_text=input_text, env=env)
    assert result.returncode == 0, f"revup {' '.join(args)} failed:\n{result.stderr}"
    return result.stdout


# =============================================================================
# ASYNC TEST HELPERS
# =============================================================================


def run_async(coro):
    """Run an async coroutine synchronously.

    Helper for running async functions in sync test code.
    """
    return asyncio.get_event_loop().run_until_complete(coro)
