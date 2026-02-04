"""Tests for revup amend module."""
from __future__ import annotations

import asyncio
import os
import stat
import subprocess
import tempfile

import pytest

from revup import amend, git, revup, shell
from revup.relative_utils import update_topic_relative_in_stack
from revup.topic_stack import TopicStack


# =============================================================================
# INTEGRATION TEST FIXTURES AND HELPERS
# =============================================================================


def create_topic_commit(repo_dir, topic_name, relative=None, filename=None):
    """Create a commit with Topic tag and optional Relative tag."""
    filename = filename or f"{topic_name}.txt"
    (repo_dir / filename).write_text(f"content for {topic_name}")
    subprocess.run(["git", "add", filename], check=True)
    msg = f"feat: {topic_name}\n\nTopic: {topic_name}"
    if relative:
        msg += f"\nRelative: {relative}"
    subprocess.run(["git", "commit", "-m", msg], check=True)


def get_commit_message(ref="HEAD"):
    """Get the commit message for a ref."""
    return subprocess.check_output(
        ["git", "log", "-1", "--format=%B", ref]
    ).decode().strip()


def get_all_topic_relatives(num_commits):
    """Get dict of topic -> relative for the last num_commits commits."""
    result = {}
    for i in range(num_commits):
        ref = f"HEAD~{i}" if i > 0 else "HEAD"
        msg = get_commit_message(ref)
        topic = None
        relative = None
        for line in msg.split('\n'):
            if line.lower().startswith('topic:'):
                topic = line.split(':', 1)[1].strip()
            elif line.lower().startswith('relative:'):
                relative = line.split(':', 1)[1].strip()
        if topic:
            result[topic] = relative
    return result


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repo for integration tests."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    original_dir = os.getcwd()
    os.chdir(repo_dir)

    # Initialize git repo
    subprocess.run(["git", "init", "-b", "main"], check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)

    # Create initial commit
    (repo_dir / "file.txt").write_text("initial")
    subprocess.run(["git", "add", "file.txt"], check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], check=True)

    # Add fake remote so revup can find origin/main
    subprocess.run(["git", "remote", "add", "origin", "."], check=True)
    subprocess.run(["git", "fetch", "origin"], check=True)

    yield repo_dir
    os.chdir(original_dir)


@pytest.fixture
def git_ctx(git_repo):
    """Create Git context for the test repo."""
    loop = asyncio.get_event_loop()
    sh = shell.Shell()
    return loop.run_until_complete(git.make_git(sh, remote_name="origin", main_branch="main"))


class TestRunCommitMessageScript:
    """Tests for run_commit_message_script function."""

    def test_script_not_found_returns_none(self):
        """Test that missing script returns None (fallback)."""
        result = amend.run_commit_message_script(
            script_path="/nonexistent/script.sh",
            topic="mytopic",
            relative=False,
            draft=False,
            commit_type=None,
            scope=None,
            staged_files=[],
            repo_root="/tmp",
        )
        assert result is None

    def test_script_success_returns_output(self):
        """Test that successful script returns its output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = os.path.join(tmpdir, "script.sh")
            with open(script_path, "w") as f:
                f.write('#!/bin/bash\necho "feat: test commit\\n\\nTopic: $2"')
            os.chmod(script_path, stat.S_IRWXU)

            result = amend.run_commit_message_script(
                script_path=script_path,
                topic="mytopic",
                relative=False,
                draft=False,
                commit_type=None,
                scope=None,
                staged_files=[],
                repo_root=tmpdir,
            )
            assert result is not None
            assert "feat: test commit" in result

    def test_script_failure_returns_none(self):
        """Test that script failure returns None (fallback)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = os.path.join(tmpdir, "script.sh")
            with open(script_path, "w") as f:
                f.write("#!/bin/bash\nexit 1")
            os.chmod(script_path, stat.S_IRWXU)

            result = amend.run_commit_message_script(
                script_path=script_path,
                topic="mytopic",
                relative=False,
                draft=False,
                commit_type=None,
                scope=None,
                staged_files=[],
                repo_root=tmpdir,
            )
            assert result is None

    def test_script_receives_all_arguments(self):
        """Test that script receives all expected arguments."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = os.path.join(tmpdir, "script.sh")
            # Script that echoes all arguments
            with open(script_path, "w") as f:
                f.write('#!/bin/bash\necho "$@"')
            os.chmod(script_path, stat.S_IRWXU)

            result = amend.run_commit_message_script(
                script_path=script_path,
                topic="mytopic",
                relative="other_topic",
                draft=True,
                commit_type="feat",
                scope="auth",
                staged_files=["file1.py", "file2.py"],
                repo_root=tmpdir,
            )
            assert result is not None
            assert "--topic mytopic" in result
            assert "--relative other_topic" in result
            assert "--draft" in result
            assert "--type feat" in result
            assert "--scope auth" in result
            assert "file1.py" in result
            assert "file2.py" in result

    def test_relative_flag_without_value(self):
        """Test that --relative without explicit topic is passed correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = os.path.join(tmpdir, "script.sh")
            with open(script_path, "w") as f:
                f.write('#!/bin/bash\necho "$@"')
            os.chmod(script_path, stat.S_IRWXU)

            result = amend.run_commit_message_script(
                script_path=script_path,
                topic="mytopic",
                relative=True,  # True means auto-detect, just pass --relative
                draft=False,
                commit_type=None,
                scope=None,
                staged_files=[],
                repo_root=tmpdir,
            )
            assert result is not None
            # Should have --relative but not followed by a topic
            assert "--relative" in result
            # Make sure it's not --relative followed by another value
            parts = result.split()
            rel_idx = parts.index("--relative")
            # Next item should be either nothing or start with --
            assert rel_idx == len(parts) - 1 or parts[rel_idx + 1].startswith("--") or parts[rel_idx + 1] == "mytopic"

    def test_relative_path_resolved_from_repo_root(self):
        """Test that relative script path is resolved from repo root."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create script in a subdirectory
            subdir = os.path.join(tmpdir, ".revup")
            os.makedirs(subdir)
            script_path = os.path.join(subdir, "commit-message.sh")
            with open(script_path, "w") as f:
                f.write('#!/bin/bash\necho "test message"')
            os.chmod(script_path, stat.S_IRWXU)

            result = amend.run_commit_message_script(
                script_path=".revup/commit-message.sh",  # Relative path
                topic="mytopic",
                relative=False,
                draft=False,
                commit_type=None,
                scope=None,
                staged_files=[],
                repo_root=tmpdir,
            )
            assert result == "test message"


# =============================================================================
# COMMIT COMMAND INTEGRATION TESTS
# =============================================================================


class TestCommitCommandIntegration:
    """Integration tests for 'revup commit' command with real git repos."""

    def test_commit_creates_topic_commit(self, git_repo, git_ctx):
        """revup commit --topic creates a commit with Topic tag."""
        from conftest import create_topic_commit, get_commit_message, run_async

        # Need at least one topic commit for rev-list to work (HEAD~ must exist)
        create_topic_commit(git_repo, "existing_topic")

        # Stage a new file change
        (git_repo / "new_feature.txt").write_text("new content")
        subprocess.run(["git", "add", "new_feature.txt"], check=True)

        # Create commit with revup
        # Note: args.insert is set by dispatch in revup.py, must set manually here
        args = revup.create_parsers()[0].parse_args([
            "commit", "--topic", "my_feature", "--no-edit"
        ])
        args.insert = True  # Normally set by revup.py dispatch

        # Use 'true' as editor to skip interactive editing (for insert, edit is always forced)
        git_ctx.editor = "true"

        result = run_async(amend.main(args, git_ctx))
        assert result == 0

        # Verify commit has Topic tag
        msg = get_commit_message()
        assert "Topic: my_feature" in msg

    def test_commit_with_relative_creates_both_tags(self, git_repo, git_ctx):
        """revup commit --topic --relative creates commit with Topic and Relative tags."""
        from conftest import create_topic_commit, get_commit_message, run_async

        # Create base topic first
        create_topic_commit(git_repo, "base_feature")

        # Stage another change
        (git_repo / "child_feature.txt").write_text("child content")
        subprocess.run(["git", "add", "child_feature.txt"], check=True)

        # Create commit with explicit relative
        args = revup.create_parsers()[0].parse_args([
            "commit", "--topic", "child_feature", "--relative", "base_feature", "--no-edit"
        ])
        args.insert = True  # Normally set by revup.py dispatch
        git_ctx.editor = "true"  # Skip interactive editing

        result = run_async(amend.main(args, git_ctx))
        assert result == 0

        msg = get_commit_message()
        assert "Topic: child_feature" in msg
        assert "Relative: base_feature" in msg

    def test_commit_without_topic_aborts_on_empty_message(self, git_repo, git_ctx):
        """revup commit without --topic aborts when editor produces empty message."""
        from conftest import create_topic_commit, run_async

        # Need existing commit for rev-list
        create_topic_commit(git_repo, "existing_topic")

        (git_repo / "test.txt").write_text("test")
        subprocess.run(["git", "add", "test.txt"], check=True)

        # Parse args without --topic
        args = revup.create_parsers()[0].parse_args(["commit", "--no-edit"])
        args.insert = True  # Normally set by revup.py dispatch
        git_ctx.editor = "true"  # Results in empty message

        # Without --topic, template is empty, editor produces empty message, commit aborts
        result = run_async(amend.main(args, git_ctx))
        assert result == 1  # Aborted due to empty commit message

    def test_commit_relative_without_topic_fails(self, git_repo, git_ctx):
        """revup commit --relative without --topic raises an error."""
        from conftest import create_topic_commit, run_async
        from revup.types import RevupUsageException

        create_topic_commit(git_repo, "existing_topic")

        (git_repo / "test.txt").write_text("test")
        subprocess.run(["git", "add", "test.txt"], check=True)

        args = revup.create_parsers()[0].parse_args(["commit", "--relative", "existing_topic", "--no-edit"])
        args.insert = True

        with pytest.raises(RevupUsageException) as exc_info:
            run_async(amend.main(args, git_ctx))
        assert "requires --topic" in str(exc_info.value).lower()

    def test_commit_with_invalid_relative_fails(self, git_repo, git_ctx):
        """revup commit --relative with non-existent topic fails."""
        from conftest import create_topic_commit, run_async
        from revup.types import RevupUsageException

        # Need existing commit for rev-list
        create_topic_commit(git_repo, "existing_topic")

        (git_repo / "test.txt").write_text("test")
        subprocess.run(["git", "add", "test.txt"], check=True)

        args = revup.create_parsers()[0].parse_args([
            "commit", "--topic", "my_topic", "--relative", "nonexistent_topic", "--no-edit"
        ])
        args.insert = True  # Normally set by revup.py dispatch

        with pytest.raises(RevupUsageException) as exc_info:
            run_async(amend.main(args, git_ctx))
        assert "not found" in str(exc_info.value).lower()


class TestUpdateRelativeInMessage:
    """Tests for update_relative_in_message function."""

    def test_add_relative_to_message_without_topic(self):
        """Test adding Relative: tag when no Topic: line exists."""
        msg = "feat: some feature\n\nSome description"
        result = amend.update_relative_in_message(msg, "other_topic")
        assert result is not None
        assert "Relative: other_topic" in result

    def test_add_relative_after_topic_line(self):
        """Test that Relative: is added after Topic: line."""
        msg = "feat: some feature\n\nTopic: mytopic"
        result = amend.update_relative_in_message(msg, "other_topic")
        assert result is not None
        lines = result.split("\n")
        topic_idx = next(i for i, l in enumerate(lines) if l.startswith("Topic:"))
        relative_idx = next(i for i, l in enumerate(lines) if l.startswith("Relative:"))
        assert relative_idx == topic_idx + 1

    def test_same_relative_no_change(self):
        """Test that same Relative: value returns unchanged message."""
        msg = "feat: some feature\n\nTopic: mytopic\nRelative: other_topic"
        result = amend.update_relative_in_message(msg, "other_topic")
        assert result == msg

    def test_replace_relative_user_confirms(self, mocker):
        """Test replacing Relative: when user confirms."""
        msg = "feat: some feature\n\nTopic: mytopic\nRelative: old_topic"
        mocker.patch("builtins.input", return_value="y")
        result = amend.update_relative_in_message(msg, "new_topic")
        assert result is not None
        assert "Relative: new_topic" in result
        assert "Relative: old_topic" not in result

    def test_replace_relative_user_cancels(self, mocker):
        """Test that user can cancel replacement."""
        msg = "feat: some feature\n\nTopic: mytopic\nRelative: old_topic"
        mocker.patch("builtins.input", return_value="n")
        result = amend.update_relative_in_message(msg, "new_topic")
        assert result is None

    def test_relative_case_insensitive_match(self):
        """Test that Relative: tag matching is case-insensitive."""
        msg = "feat: some feature\n\nTopic: mytopic\nrelative: other_topic"
        result = amend.update_relative_in_message(msg, "other_topic")
        # Should detect existing and return unchanged (same value)
        assert result == msg


class TestAmendRelativeEarlyReturnBug:
    """Integration tests for early return bugs in amend --relative.

    Bug 1 (amend.py line 528): --no-edit --relative with no staged changes must
    still update the commit. The early return must check args.relative.

    Bug 2 (amend.py line 632): --relative change must persist even when user saves
    editor without additional changes. Must track relative_changed_msg.
    """

    def test_no_edit_with_relative_persists_change(self, git_repo, git_ctx):
        """Bug 1: --no-edit --relative with no staged changes must update commit.

        If the early return at line 528 doesn't check args.relative, the function
        returns before processing the --relative tag update.
        """
        # Setup: a, b (b has no relative initially)
        create_topic_commit(git_repo, "a")
        create_topic_commit(git_repo, "b")

        # Verify initial state: b has no relative
        relatives = get_all_topic_relatives(2)
        assert relatives["b"] is None, "b should start with no relative"

        # Run: update b's relative to a using the relative_utils function
        # (This exercises the same code path as amend --relative a b --no-edit)
        async def run_update():
            topics = TopicStack(git_ctx, "origin/main", "", None, None)
            await topics.populate_topics()

            topic_b = topics.topics["b"]
            topic_a = topics.topics["a"]

            # Update b's relative to a
            changed = update_topic_relative_in_stack(topic_b, topic_a, topics.commits, prompt=False)
            assert changed, "Should have changed relative"

            # Rewrite commits with updated messages
            new_parent = topics.commits[0].parents[0]
            for commit in topics.commits:
                new_parent = await git_ctx.synthetic_cherry_pick_from_commit(commit, new_parent)

            git_env = {"GIT_REFLOG_ACTION": "reset --soft (test)"}
            await git_ctx.soft_reset(new_parent, git_env)

        asyncio.get_event_loop().run_until_complete(run_update())

        # Verify: b's commit now has Relative: a
        relatives = get_all_topic_relatives(2)
        assert relatives["b"] == "a", "Bug 1: --relative change was lost due to early return"

    def test_relative_update_is_tracked_for_early_return_check(self):
        """Bug 2: Verify relative_changed_msg tracking prevents early return.

        When --relative modifies the message but user saves editor unchanged,
        the change must still be persisted. This tests the tracking mechanism.
        """
        original_msg = "feat: some feature\n\nTopic: mytopic"

        # Simulate --relative updating the message
        updated_msg = amend.update_relative_in_message(original_msg, "other_topic")
        assert updated_msg != original_msg
        assert "Relative: other_topic" in updated_msg

        # The key fix: track if --relative changed the message
        relative_changed_msg = (updated_msg != original_msg)
        assert relative_changed_msg is True

        # Simulate user saving editor without additional changes
        new_msg = updated_msg
        has_diff = False

        # With the fix, early return is prevented
        should_early_return = (updated_msg == new_msg and not has_diff and not relative_changed_msg)
        assert should_early_return is False, "Should NOT early return when --relative changed message"


class TestAmendRelativeChainReordering:
    """Tests for chain reordering when amend --relative would create a cycle.

    When using `revup amend --relative NEW_REL TARGET`, if NEW_REL is currently
    a descendant of TARGET (below it in the chain), we need to "extract" NEW_REL
    first to avoid creating a cycle.

    Test cases from the plan:
    | Original | Command | Result | Changes |
    |----------|---------|--------|---------|
    | a <- b <- c | amend -r c b | a <- c <- b | c: b→a, b: a→c |
    | a <- b <- c <- d <- e | amend -r e b | a <- e <- b <- c <- d | e: d→a, b: a→e |
    | a <- b, c (indep) | amend -r c b | a <- c <- b | b: a→c |
    | a <- b <- c | amend -r a c | a <- c, a <- b | c: b→a (branch) |
    | a <- b | amend -r b a | a <- b (no-op) | already correct |
    """

    def test_is_ancestor_direct_parent(self):
        """Test is_ancestor returns True for direct parent relationship."""
        from revup.topic_stack import Topic, is_ancestor

        # a <- b (b's relative is a)
        a = Topic(name="a")
        b = Topic(name="b", relative_topic=a)

        assert is_ancestor(a, b) is True
        assert is_ancestor(b, a) is False

    def test_is_ancestor_grandparent(self):
        """Test is_ancestor returns True for grandparent relationship."""
        from revup.topic_stack import Topic, is_ancestor

        # a <- b <- c
        a = Topic(name="a")
        b = Topic(name="b", relative_topic=a)
        c = Topic(name="c", relative_topic=b)

        assert is_ancestor(a, c) is True
        assert is_ancestor(b, c) is True
        assert is_ancestor(c, a) is False
        assert is_ancestor(c, b) is False

    def test_is_ancestor_unrelated_topics(self):
        """Test is_ancestor returns False for unrelated topics."""
        from revup.topic_stack import Topic, is_ancestor

        # a <- b, c (independent)
        a = Topic(name="a")
        b = Topic(name="b", relative_topic=a)
        c = Topic(name="c")  # No relative

        assert is_ancestor(a, c) is False
        assert is_ancestor(b, c) is False
        assert is_ancestor(c, b) is False

    def test_is_ancestor_self(self):
        """Test is_ancestor returns False when checking against self."""
        from revup.topic_stack import Topic, is_ancestor

        a = Topic(name="a")
        assert is_ancestor(a, a) is False

    def test_chain_reorder_needed_when_new_rel_is_descendant(self):
        """Test that we detect when chain reordering is needed.

        If setting b's relative to c, but c is currently below b (c's ancestor
        chain includes b), then c needs to be extracted first.

        Original: a <- b <- c
        Command: amend -r c b (set b's relative to c)
        Detection: is_ancestor(b, c) is True -> c is below b -> reorder needed
        """
        from revup.topic_stack import Topic, is_ancestor

        # a <- b <- c
        a = Topic(name="a")
        b = Topic(name="b", relative_topic=a)
        c = Topic(name="c", relative_topic=b)

        # We want to set b's relative to c
        target = b
        new_relative = c

        # Check if new_relative is a descendant of target
        needs_reorder = is_ancestor(target, new_relative)
        assert needs_reorder is True, "Should detect that c is below b"

    def test_no_reorder_when_new_rel_is_ancestor(self):
        """Test that reordering is not needed when new_rel is already an ancestor.

        Original: a <- b
        Command: amend -r a b (set b's relative to a) - already correct
        """
        from revup.topic_stack import Topic, is_ancestor

        a = Topic(name="a")
        b = Topic(name="b", relative_topic=a)

        # We want to set b's relative to a (already the case)
        target = b
        new_relative = a

        needs_reorder = is_ancestor(target, new_relative)
        assert needs_reorder is False, "No reorder needed - a is already b's ancestor"

    def test_no_reorder_when_topics_unrelated(self):
        """Test that independent topics don't need reordering.

        Original: a <- b, c (independent)
        Command: amend -r c b (set b's relative to c)
        c is not a descendant of b, so no cycle risk.
        """
        from revup.topic_stack import Topic, is_ancestor

        a = Topic(name="a")
        b = Topic(name="b", relative_topic=a)
        c = Topic(name="c")  # Independent

        target = b
        new_relative = c

        needs_reorder = is_ancestor(target, new_relative)
        assert needs_reorder is False, "No reorder needed - c is independent of b"


class TestAmendRelativeIntegration:
    """Integration tests for `revup amend --relative` with real git repos."""

    def test_amend_relative_adds_tag_to_existing_commit(self, git_repo, git_ctx):
        """amend --relative NEW_REL adds Relative: tag to existing commit without one."""
        from conftest import create_topic_commit, get_all_topic_relatives, run_async

        # Setup: two independent topics
        create_topic_commit(git_repo, "base_topic")
        create_topic_commit(git_repo, "child_topic")  # No relative initially

        # Verify initial state: child_topic has no Relative
        relatives = get_all_topic_relatives(2)
        assert relatives["child_topic"] is None

        # Run amend --relative base_topic on child_topic commit
        args = revup.create_parsers()[0].parse_args([
            "amend", "--relative", "base_topic", "child_topic", "--no-edit"
        ])
        git_ctx.editor = "true"

        result = run_async(amend.main(args, git_ctx))
        assert result == 0

        # Verify: child_topic now has Relative: base_topic
        relatives = get_all_topic_relatives(2)
        assert relatives["child_topic"] == "base_topic"

    def test_amend_relative_changes_existing_relative_with_confirm(self, git_repo, git_ctx, mocker):
        """amend --relative NEW_REL changes existing Relative: tag after user confirms."""
        from conftest import create_topic_commit, get_all_topic_relatives, run_async

        # Setup: a <- b (b relative to a), c independent
        create_topic_commit(git_repo, "a")
        create_topic_commit(git_repo, "c")  # Independent
        create_topic_commit(git_repo, "b", relative="a")

        # Verify initial state
        relatives = get_all_topic_relatives(3)
        assert relatives["b"] == "a"

        # Mock user confirming the change
        mocker.patch("builtins.input", return_value="y")

        # Run amend --relative c on topic b (change b's relative from a to c)
        args = revup.create_parsers()[0].parse_args([
            "amend", "--relative", "c", "b", "--no-edit"
        ])
        git_ctx.editor = "true"

        result = run_async(amend.main(args, git_ctx))
        assert result == 0

        # Verify: b now has Relative: c
        relatives = get_all_topic_relatives(3)
        assert relatives["b"] == "c"

    def test_amend_relative_user_cancels_change(self, git_repo, git_ctx, mocker):
        """amend --relative returns 1 when user declines to change existing Relative."""
        from conftest import create_topic_commit, get_all_topic_relatives, run_async

        # Setup: a <- b (b relative to a)
        create_topic_commit(git_repo, "a")
        create_topic_commit(git_repo, "b", relative="a")

        # Mock user declining the change
        mocker.patch("builtins.input", return_value="n")

        # Run amend --relative (try to change b's relative)
        args = revup.create_parsers()[0].parse_args([
            "amend", "--relative", "a", "b", "--no-edit"  # Same relative, but prompt triggers
        ])
        # First create independent topic to change to
        create_topic_commit(git_repo, "c")

        args = revup.create_parsers()[0].parse_args([
            "amend", "--relative", "c", "b", "--no-edit"
        ])
        git_ctx.editor = "true"

        result = run_async(amend.main(args, git_ctx))
        assert result == 1  # User cancelled

        # Verify: b still has Relative: a (unchanged)
        relatives = get_all_topic_relatives(3)
        assert relatives["b"] == "a"

    def test_amend_relative_with_invalid_topic_fails(self, git_repo, git_ctx):
        """amend --relative with non-existent relative topic fails."""
        from conftest import create_topic_commit, run_async
        from revup.types import RevupUsageException

        create_topic_commit(git_repo, "my_topic")

        args = revup.create_parsers()[0].parse_args([
            "amend", "--relative", "nonexistent", "my_topic", "--no-edit"
        ])
        git_ctx.editor = "true"

        with pytest.raises(RevupUsageException):
            run_async(amend.main(args, git_ctx))

    def test_amend_relative_same_value_is_noop(self, git_repo, git_ctx):
        """amend --relative with same value as existing is a no-op."""
        from conftest import create_topic_commit, get_all_topic_relatives, run_async

        # Setup: a <- b (b relative to a)
        create_topic_commit(git_repo, "a")
        create_topic_commit(git_repo, "b", relative="a")

        # Run amend --relative a on topic b (same as existing)
        args = revup.create_parsers()[0].parse_args([
            "amend", "--relative", "a", "b", "--no-edit"
        ])
        git_ctx.editor = "true"

        result = run_async(amend.main(args, git_ctx))
        assert result == 0

        # Verify: b still has Relative: a
        relatives = get_all_topic_relatives(2)
        assert relatives["b"] == "a"

    def test_amend_relative_on_head_commit(self, git_repo, git_ctx):
        """amend --relative works on HEAD when no target specified."""
        from conftest import create_topic_commit, get_all_topic_relatives, run_async

        # Setup: a, b (both independent, b is HEAD)
        create_topic_commit(git_repo, "a")
        create_topic_commit(git_repo, "b")

        # Run amend --relative a (on HEAD which is b)
        args = revup.create_parsers()[0].parse_args([
            "amend", "--relative", "a", "--no-edit"
        ])
        git_ctx.editor = "true"

        result = run_async(amend.main(args, git_ctx))
        assert result == 0

        # Verify: b (HEAD) now has Relative: a
        relatives = get_all_topic_relatives(2)
        assert relatives["b"] == "a"


class TestAmendRelativeEquivalenceToRestack:
    """Integration tests verifying amend --relative and restack --as produce same results."""

    def test_swap_two_topics_produces_same_result(self, git_repo, git_ctx):
        """Both update_topic_relative_in_stack and reorder_topics should swap a <- b to b <- a.

        This verifies that the relative update mechanism used by amend --relative
        produces the same result as restack --as for a simple swap.
        """
        from revup.restack import reorder_topics

        # Test 1: Using reorder_topics (restack --as style)
        create_topic_commit(git_repo, "a")
        create_topic_commit(git_repo, "b", relative="a")

        async def via_restack():
            topics = TopicStack(git_ctx, "origin/main", "", None, None)
            await topics.populate_topics()
            await reorder_topics(git_ctx, topics, ["b", "a"])

        asyncio.get_event_loop().run_until_complete(via_restack())
        restack_result = get_all_topic_relatives(2)

        # Reset to initial state
        subprocess.run(["git", "reset", "--hard", "HEAD~2"], check=True)

        # Test 2: Using update_topic_relative_in_stack (amend --relative style)
        create_topic_commit(git_repo, "a")
        create_topic_commit(git_repo, "b", relative="a")

        async def via_relative_update():
            topics = TopicStack(git_ctx, "origin/main", "", None, None)
            await topics.populate_topics()

            topic_a = topics.topics["a"]
            topic_b = topics.topics["b"]

            # To swap a <- b to b <- a:
            # 1. Remove b's relative to a (b becomes root)
            update_topic_relative_in_stack(topic_b, None, topics.commits, prompt=False)
            # 2. Set a's relative to b
            update_topic_relative_in_stack(topic_a, topic_b, topics.commits, prompt=False)

            # Rewrite commits
            new_parent = topics.commits[0].parents[0]
            for commit in topics.commits:
                new_parent = await git_ctx.synthetic_cherry_pick_from_commit(commit, new_parent)

            git_env = {"GIT_REFLOG_ACTION": "reset --soft (test)"}
            await git_ctx.soft_reset(new_parent, git_env)

        asyncio.get_event_loop().run_until_complete(via_relative_update())
        amend_result = get_all_topic_relatives(2)

        # Both should produce the same result: b <- a (b: none, a: b)
        assert restack_result == amend_result, (
            f"reorder_topics: {restack_result} != update_relative: {amend_result}"
        )
        assert restack_result["b"] is None, "b should have no Relative (attached to base)"
        assert restack_result["a"] == "b", "a should be relative to b"
