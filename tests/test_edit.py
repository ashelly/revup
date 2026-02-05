from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from revup import edit, git, revup, shell
from revup.types import RevupUsageException


class TestEditFunctions:
    """Tests for edit module functions."""

    def test_generate_rebase_sequence_single_commit(self):
        """Test generating rebase sequence for a single topic commit."""
        commits = ["abc123 first commit"]
        topic_commits = {"abc123"}
        result = edit.generate_rebase_sequence(commits, topic_commits)
        assert "edit abc123 first commit" in result

    def test_generate_rebase_sequence_multiple_commits(self):
        """Test generating rebase sequence with multiple commits, some in topic."""
        commits = [
            "abc123 first commit",
            "def456 topic commit",
            "ghi789 third commit",
        ]
        topic_commits = {"def456"}
        result = edit.generate_rebase_sequence(commits, topic_commits)
        lines = result.strip().split("\n")
        assert lines[0] == "pick abc123 first commit"
        assert lines[1] == "edit def456 topic commit"
        assert lines[2] == "pick ghi789 third commit"

    def test_generate_rebase_sequence_all_topic(self):
        """Test generating rebase sequence when all commits are in topic."""
        commits = [
            "abc123 topic commit 1",
            "def456 topic commit 2",
        ]
        topic_commits = {"abc123", "def456"}
        result = edit.generate_rebase_sequence(commits, topic_commits)
        lines = result.strip().split("\n")
        assert lines[0] == "edit abc123 topic commit 1"
        assert lines[1] == "edit def456 topic commit 2"


class TestRebaseState:
    """Tests for rebase state detection."""

    def test_not_in_rebase(self, git_repo):
        """Test detecting when not in a rebase."""
        assert not edit.is_in_rebase(Path(git_repo))

    def test_in_rebase(self, git_repo):
        """Test detecting when in a rebase."""
        from conftest import create_topic_commit

        # Need at least 2 commits for HEAD~1
        create_topic_commit(git_repo, "test_topic")

        # Start a rebase
        os.system("GIT_SEQUENCE_EDITOR='sed -i s/pick/edit/' git rebase -i HEAD~1")
        assert edit.is_in_rebase(Path(git_repo))
        # Abort the rebase to clean up
        os.system("git rebase --abort")


class TestEditStateHelpers:
    """Tests for edit state file helpers."""

    def test_save_and_load_edit_state(self, git_repo):
        """Test saving and loading edit state."""
        # Create the rebase-merge directory (normally created by git rebase)
        rebase_dir = git_repo / ".git" / "rebase-merge"
        rebase_dir.mkdir(parents=True, exist_ok=True)

        topic_name = "my-feature"
        topic_commits = {"abc123def456", "789xyz000111"}

        edit.save_edit_state(str(git_repo), topic_name, topic_commits)

        result = edit.load_edit_state(str(git_repo))
        assert result is not None
        loaded_name, loaded_commits = result
        assert loaded_name == topic_name
        assert loaded_commits == topic_commits

    def test_load_edit_state_missing_file(self, git_repo):
        """Test loading state when file doesn't exist."""
        result = edit.load_edit_state(str(git_repo))
        assert result is None

    def test_load_edit_state_empty_file(self, git_repo):
        """Test loading state when file is empty or malformed."""
        rebase_dir = git_repo / ".git" / "rebase-merge"
        rebase_dir.mkdir(parents=True, exist_ok=True)

        state_path = edit.get_edit_state_path(str(git_repo))
        state_path.write_text("only-topic-name\n")  # Missing commits

        result = edit.load_edit_state(str(git_repo))
        assert result is None

    def test_state_file_auto_cleanup(self, git_repo):
        """Test that state file is in a location that git cleans up."""
        from conftest import create_topic_commit

        # Need at least 2 commits for HEAD~1
        create_topic_commit(git_repo, "test_topic")

        # Start a rebase
        os.system("GIT_SEQUENCE_EDITOR='sed -i s/pick/edit/' git rebase -i HEAD~1")

        # Save state
        edit.save_edit_state(str(git_repo), "test-topic", {"abc123"})
        state_path = edit.get_edit_state_path(str(git_repo))
        assert state_path.exists()

        # Abort rebase - git should clean up rebase-merge directory
        os.system("git rebase --abort")

        # State file should be gone (along with rebase-merge dir)
        assert not state_path.exists()


class TestStoppedCommit:
    """Tests for get_stopped_commit function."""

    def test_get_stopped_commit_not_in_rebase(self, git_repo):
        """Test getting stopped commit when not in rebase."""
        result = edit.get_stopped_commit(str(git_repo))
        assert result is None

    def test_get_stopped_commit_in_rebase(self, git_repo):
        """Test getting stopped commit during rebase."""
        from conftest import create_topic_commit

        # Need at least 2 commits for HEAD~1
        create_topic_commit(git_repo, "test_topic")

        # Get the current HEAD commit
        head_commit = os.popen("git rev-parse HEAD").read().strip()

        # Start a rebase with edit on the last commit
        os.system("GIT_SEQUENCE_EDITOR='sed -i s/pick/edit/' git rebase -i HEAD~1")

        # Should be stopped at the commit we're editing
        stopped = edit.get_stopped_commit(str(git_repo))
        assert stopped is not None
        assert stopped == head_commit

        # Clean up
        os.system("git rebase --abort")


class TestUnmergedFiles:
    """Tests for has_unmerged_files function."""

    def test_no_unmerged_files(self, git_ctx):
        """Test detection when no conflicts exist."""
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(edit.has_unmerged_files(git_ctx))
        assert result is False

    def test_with_unmerged_files(self, git_repo, git_ctx):
        """Test detection when conflicts exist."""
        from conftest import create_topic_commit

        # Need at least 2 commits for HEAD~1
        create_topic_commit(git_repo, "test_topic")

        # Create a conflict scenario
        # First, create another branch with conflicting changes
        os.system("git checkout -b conflict-branch HEAD~1")
        (git_repo / "file.txt").write_text("conflict content")
        os.system("git add file.txt")
        os.system("git commit -m 'conflict commit'")

        # Try to rebase onto main (will conflict)
        os.system("git checkout -")  # back to original branch
        result = os.system("git rebase conflict-branch 2>/dev/null")

        if result != 0:  # Rebase had conflicts
            loop = asyncio.get_event_loop()
            has_conflicts = loop.run_until_complete(edit.has_unmerged_files(git_ctx))
            assert has_conflicts is True

            # Clean up
            os.system("git rebase --abort")


class TestGitDirHandling:
    """Tests for git directory handling including worktrees."""

    def test_get_git_dir_worktree(self, git_repo):
        """Test getting git dir in a worktree."""
        # Create a worktree
        worktree_path = git_repo.parent / "worktree"
        os.system(f"git worktree add {worktree_path} HEAD~1 2>/dev/null")

        if worktree_path.exists():
            git_dir = edit.get_git_dir(worktree_path)
            # In a worktree, .git is a file pointing to the real git dir
            assert git_dir.is_dir()
            assert "worktrees" in str(git_dir)

            # Clean up
            os.system(f"git worktree remove {worktree_path} 2>/dev/null")


# =============================================================================
# INTEGRATION TESTS FOR EDIT COMMAND MAIN ENTRY POINTS
# =============================================================================


class TestEditMainIntegration:
    """Integration tests for edit.main() function."""

    def test_start_edit_with_valid_topic(self, git_repo, git_ctx):
        """revup edit <topic> starts rebase and stops at topic commit."""
        from conftest import create_topic_commit, run_async

        # Setup: create a topic commit
        create_topic_commit(git_repo, "edit_me")

        args = argparse.Namespace(
            topic="edit_me",
            commit=False,
            abort=False,
            base_branch="origin/main",
        )

        result = run_async(edit.main(args, git_ctx))
        assert result == 0

        # Should be in a rebase state, stopped at the topic commit
        assert edit.is_in_rebase(Path(git_repo))

        # State file should exist
        state = edit.load_edit_state(str(git_repo))
        assert state is not None
        topic_name, _ = state
        assert topic_name == "edit_me"

        # Clean up
        subprocess.run(["git", "rebase", "--abort"], check=True)

    def test_start_edit_with_invalid_topic_fails(self, git_repo, git_ctx):
        """revup edit <nonexistent_topic> raises error."""
        from conftest import run_async

        args = argparse.Namespace(
            topic="nonexistent_topic",
            commit=False,
            abort=False,
            base_branch="origin/main",
        )

        with pytest.raises(RevupUsageException) as exc_info:
            run_async(edit.main(args, git_ctx))

        assert "not found" in str(exc_info.value)

    def test_start_edit_without_topic_fails(self, git_repo, git_ctx):
        """revup edit without topic raises error."""
        from conftest import run_async

        args = argparse.Namespace(
            topic=None,
            commit=False,
            abort=False,
            base_branch="origin/main",
        )

        with pytest.raises(RevupUsageException) as exc_info:
            run_async(edit.main(args, git_ctx))

        assert "Topic name required" in str(exc_info.value)

    def test_start_edit_while_in_rebase_fails(self, git_repo, git_ctx):
        """revup edit <topic> while already in rebase raises error."""
        from conftest import create_topic_commit, run_async

        create_topic_commit(git_repo, "first_topic")

        # Start a manual rebase first
        subprocess.run(
            ["git", "rebase", "-i", "HEAD~1"],
            env={**os.environ, "GIT_SEQUENCE_EDITOR": "sed -i s/pick/edit/"},
            check=False
        )

        args = argparse.Namespace(
            topic="first_topic",
            commit=False,
            abort=False,
            base_branch="origin/main",
        )

        with pytest.raises(RevupUsageException) as exc_info:
            run_async(edit.main(args, git_ctx))

        assert "Already in a rebase" in str(exc_info.value)

        # Clean up
        subprocess.run(["git", "rebase", "--abort"], check=False)

    def test_abort_edit_cancels_rebase(self, git_repo, git_ctx):
        """revup edit --abort cancels the rebase session."""
        from conftest import create_topic_commit, run_async

        create_topic_commit(git_repo, "abort_me")

        # Start an edit session
        start_args = argparse.Namespace(
            topic="abort_me",
            commit=False,
            abort=False,
            base_branch="origin/main",
        )
        run_async(edit.main(start_args, git_ctx))
        assert edit.is_in_rebase(Path(git_repo))

        # Abort it
        abort_args = argparse.Namespace(
            topic=None,
            commit=False,
            abort=True,
            base_branch="origin/main",
        )
        result = run_async(edit.main(abort_args, git_ctx))
        assert result == 0
        assert not edit.is_in_rebase(Path(git_repo))

    def test_abort_when_not_in_rebase_fails(self, git_repo, git_ctx):
        """revup edit --abort when not in rebase raises error."""
        from conftest import run_async

        args = argparse.Namespace(
            topic=None,
            commit=False,
            abort=True,
            base_branch="origin/main",
        )

        with pytest.raises(RevupUsageException) as exc_info:
            run_async(edit.main(args, git_ctx))

        assert "Not in a rebase" in str(exc_info.value)

    def test_commit_and_abort_together_fails(self, git_repo, git_ctx):
        """revup edit --commit --abort raises error."""
        from conftest import run_async

        args = argparse.Namespace(
            topic=None,
            commit=True,
            abort=True,
            base_branch="origin/main",
        )

        with pytest.raises(RevupUsageException) as exc_info:
            run_async(edit.main(args, git_ctx))

        assert "Cannot use --commit and --abort together" in str(exc_info.value)

    def test_commit_when_not_in_rebase_fails(self, git_repo, git_ctx):
        """revup edit --commit when not in rebase raises error."""
        from conftest import run_async

        args = argparse.Namespace(
            topic=None,
            commit=True,
            abort=False,
            base_branch="origin/main",
        )

        with pytest.raises(RevupUsageException) as exc_info:
            run_async(edit.main(args, git_ctx))

        assert "Not in a rebase" in str(exc_info.value)


class TestEditCommitAndContinue:
    """Integration tests for the commit_and_continue flow."""

    def test_commit_amends_topic_and_continues(self, git_repo, git_ctx):
        """revup edit --commit amends topic commit and continues rebase."""
        from conftest import create_topic_commit, run_async

        create_topic_commit(git_repo, "amend_topic")

        # Start edit session
        start_args = argparse.Namespace(
            topic="amend_topic",
            commit=False,
            abort=False,
            base_branch="origin/main",
        )
        run_async(edit.main(start_args, git_ctx))

        # Make a change and stage it
        (git_repo / "new_file.txt").write_text("new content")
        subprocess.run(["git", "add", "new_file.txt"], check=True)

        # Mock user confirmation and run commit
        commit_args = argparse.Namespace(
            topic=None,
            commit=True,
            abort=False,
            base_branch="origin/main",
        )

        # Set GIT_EDITOR to avoid interactive editor during amend
        with patch.dict(os.environ, {"GIT_EDITOR": "true"}):
            with patch("builtins.input", return_value="y"):
                result = run_async(edit.main(commit_args, git_ctx))

        assert result == 0
        # Rebase should have completed (only one topic commit)
        assert not edit.is_in_rebase(Path(git_repo))

        # Verify the new file is in the commit
        files = subprocess.run(
            ["git", "show", "--name-only", "--format=", "HEAD"],
            capture_output=True, text=True
        ).stdout.strip().split("\n")
        assert "new_file.txt" in files

    def test_commit_without_staged_warns_user(self, git_repo, git_ctx):
        """revup edit --commit with no staged changes prompts user."""
        from conftest import create_topic_commit, run_async

        create_topic_commit(git_repo, "no_changes")

        # Start edit session
        start_args = argparse.Namespace(
            topic="no_changes",
            commit=False,
            abort=False,
            base_branch="origin/main",
        )
        run_async(edit.main(start_args, git_ctx))

        # Run commit without staging anything, user declines
        commit_args = argparse.Namespace(
            topic=None,
            commit=True,
            abort=False,
            base_branch="origin/main",
        )

        with patch("builtins.input", return_value="n"):
            result = run_async(edit.main(commit_args, git_ctx))

        assert result == 1  # User aborted
        assert edit.is_in_rebase(Path(git_repo))

        # Clean up
        subprocess.run(["git", "rebase", "--abort"], check=True)

