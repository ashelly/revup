from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from revup import edit, git, revup, shell


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repo with some commits and topics."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    os.chdir(repo_dir)

    # Initialize git repo
    os.system("git init -b main")
    os.system("git config user.email 'test@test.com'")
    os.system("git config user.name 'Test User'")

    # Create initial commit
    (repo_dir / "file.txt").write_text("initial content")
    os.system("git add file.txt")
    os.system("git commit -m 'initial commit'")

    # Add a fake remote so revup can find origin/main
    os.system("git remote add origin .")
    os.system("git fetch origin")

    # Create a topic commit
    (repo_dir / "file.txt").write_text("topic content")
    os.system("git add file.txt")
    os.system("git commit -m 'topic change\n\nTopic: mytopic'")

    return repo_dir


@pytest.fixture
def git_ctx(git_repo):
    """Create a Git context for the test repo."""
    loop = asyncio.get_event_loop()
    sh = shell.Shell()
    git_ctx = loop.run_until_complete(git.make_git(
        sh,
        remote_name="origin",
        main_branch="main",
    ))
    return git_ctx


class TestEditCommand:
    """Tests for the revup edit command."""

    def test_edit_parser_registered(self):
        """Test that the edit command parser is registered."""
        loop = asyncio.get_event_loop()

        async def check_parser():
            revup_parser = revup.make_toplevel_parser()
            subparsers = revup_parser.add_subparsers(dest="cmd", required=True)
            # The edit parser should be addable
            edit_parser = subparsers.add_parser("edit", add_help=False)
            edit_parser.add_argument("topic", nargs="?")
            edit_parser.add_argument("--commit", "-c", action="store_true")
            args = revup_parser.parse_args(["edit", "mytopic"])
            assert args.cmd == "edit"
            assert args.topic == "mytopic"
            assert not args.commit

        loop.run_until_complete(check_parser())

    def test_edit_commit_flag(self):
        """Test that --commit flag is parsed correctly."""
        loop = asyncio.get_event_loop()

        async def check_parser():
            revup_parser = revup.make_toplevel_parser()
            subparsers = revup_parser.add_subparsers(dest="cmd", required=True)
            edit_parser = subparsers.add_parser("edit", add_help=False)
            edit_parser.add_argument("topic", nargs="?")
            edit_parser.add_argument("--commit", "-c", action="store_true")
            args = revup_parser.parse_args(["edit", "--commit"])
            assert args.cmd == "edit"
            assert args.commit
            assert args.topic is None

        loop.run_until_complete(check_parser())


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
        # Start a rebase
        os.system("GIT_SEQUENCE_EDITOR='sed -i s/pick/edit/' git rebase -i HEAD~1")
        assert edit.is_in_rebase(Path(git_repo))
        # Abort the rebase to clean up
        os.system("git rebase --abort")


class TestEditStateHelpers:
    """Tests for edit state file helpers."""

    def test_get_edit_state_path(self, git_repo):
        """Test that state path is inside rebase-merge directory."""
        path = edit.get_edit_state_path(str(git_repo))
        assert "rebase-merge" in str(path)
        assert path.name == "revup-edit-topic"

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


class TestAmendVsContinueLogic:
    """Tests for the amend vs continue decision logic."""

    def test_should_amend_when_on_topic_commit(self, git_repo):
        """Test that amend is chosen when stopped on a topic commit."""
        rebase_dir = git_repo / ".git" / "rebase-merge"
        rebase_dir.mkdir(parents=True, exist_ok=True)

        # Save state with topic commit
        topic_commits = {"abc123def456789"}
        edit.save_edit_state(str(git_repo), "my-topic", topic_commits)

        # Simulate stopped at a topic commit (short hash match)
        (rebase_dir / "stopped-sha").write_text("abc123def456789\n")

        # Load and check
        state = edit.load_edit_state(str(git_repo))
        stopped = edit.get_stopped_commit(str(git_repo))

        assert state is not None
        topic_name, commits = state
        should_amend = any(
            stopped.startswith(tc[:len(stopped)]) or tc.startswith(stopped[:len(tc)])
            for tc in commits
        )
        assert should_amend is True

    def test_should_not_amend_when_not_on_topic_commit(self, git_repo):
        """Test that amend is skipped when stopped on non-topic commit."""
        rebase_dir = git_repo / ".git" / "rebase-merge"
        rebase_dir.mkdir(parents=True, exist_ok=True)

        # Save state with topic commits
        topic_commits = {"abc123def456789"}
        edit.save_edit_state(str(git_repo), "my-topic", topic_commits)

        # Simulate stopped at a different commit
        (rebase_dir / "stopped-sha").write_text("zzz999otherhash\n")

        # Load and check
        state = edit.load_edit_state(str(git_repo))
        stopped = edit.get_stopped_commit(str(git_repo))

        assert state is not None
        topic_name, commits = state
        should_amend = any(
            stopped.startswith(tc[:len(stopped)]) or tc.startswith(stopped[:len(tc)])
            for tc in commits
        )
        assert should_amend is False

    def test_short_hash_matching(self, git_repo):
        """Test that short and long hash comparisons work correctly."""
        rebase_dir = git_repo / ".git" / "rebase-merge"
        rebase_dir.mkdir(parents=True, exist_ok=True)

        # Full hash in state
        topic_commits = {"abc123def456789abcdef0123456789abcdef01"}
        edit.save_edit_state(str(git_repo), "my-topic", topic_commits)

        # Short hash from stopped-sha (git often uses short hashes)
        (rebase_dir / "stopped-sha").write_text("abc123d\n")

        state = edit.load_edit_state(str(git_repo))
        stopped = edit.get_stopped_commit(str(git_repo))

        topic_name, commits = state
        should_amend = any(
            stopped.startswith(tc[:len(stopped)]) or tc.startswith(stopped[:len(tc)])
            for tc in commits
        )
        assert should_amend is True

    def test_no_state_falls_back_to_continue(self, git_repo):
        """Test that missing state falls back to continue without amend."""
        rebase_dir = git_repo / ".git" / "rebase-merge"
        rebase_dir.mkdir(parents=True, exist_ok=True)

        # No state file saved
        (rebase_dir / "stopped-sha").write_text("abc123\n")

        state = edit.load_edit_state(str(git_repo))
        assert state is None
        # With no state, should_amend defaults to False


class TestGitDirHandling:
    """Tests for git directory handling including worktrees."""

    def test_get_git_dir_normal_repo(self, git_repo):
        """Test getting git dir in a normal repo."""
        git_dir = edit.get_git_dir(Path(git_repo))
        assert git_dir == git_repo / ".git"
        assert git_dir.is_dir()

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

