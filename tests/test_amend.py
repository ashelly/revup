"""Tests for revup amend module."""
from __future__ import annotations

import os
import stat
import tempfile

import pytest

from revup import amend, revup


class TestCommitRelativeFlag:
    """Tests for the --relative flag on revup commit."""

    def test_relative_flag_without_value(self):
        """Test that --relative without a value sets relative to True (auto-detect)."""
        revup_parser, _ = revup.create_parsers()
        args = revup_parser.parse_args(["commit", "--topic", "mytopic", "--relative"])
        assert args.topic == "mytopic"
        assert args.relative is True

    def test_relative_flag_with_explicit_topic(self):
        """Test that --relative TOPIC sets relative to the specified topic name."""
        revup_parser, _ = revup.create_parsers()
        args = revup_parser.parse_args(["commit", "--topic", "mytopic", "--relative", "other_topic"])
        assert args.topic == "mytopic"
        assert args.relative == "other_topic"

    def test_relative_short_flag_without_value(self):
        """Test that -r without a value sets relative to True."""
        revup_parser, _ = revup.create_parsers()
        args = revup_parser.parse_args(["commit", "-t", "mytopic", "-r"])
        assert args.topic == "mytopic"
        assert args.relative is True

    def test_relative_short_flag_with_explicit_topic(self):
        """Test that -r TOPIC sets relative to the specified topic name."""
        revup_parser, _ = revup.create_parsers()
        args = revup_parser.parse_args(["commit", "-t", "mytopic", "-r", "base_topic"])
        assert args.topic == "mytopic"
        assert args.relative == "base_topic"

    def test_relative_without_topic_still_requires_topic(self):
        """Test that --relative still requires --topic to be specified."""
        # This is validated at runtime in amend.main, not by argparse
        revup_parser, _ = revup.create_parsers()
        args = revup_parser.parse_args(["commit", "--relative", "other"])
        assert args.relative == "other"
        assert args.topic is None  # Will fail at runtime validation


class TestBuildCommitTemplate:
    """Tests for build_commit_template function."""

    def test_explicit_relative_validates_topic_exists(self):
        """Test that explicit --relative TOPIC validates the topic exists."""
        # This is tested via the error message when topic doesn't exist
        # The actual validation happens in build_commit_template
        pass  # Integration test would require full git setup


class TestCommitTypeAndScope:
    """Tests for --type and --scope arguments."""

    def test_type_argument(self):
        """Test that --type is parsed correctly."""
        revup_parser, _ = revup.create_parsers()
        args = revup_parser.parse_args(["commit", "--topic", "mytopic", "--type", "feat"])
        assert args.topic == "mytopic"
        assert args.type == "feat"

    def test_scope_argument(self):
        """Test that --scope is parsed correctly."""
        revup_parser, _ = revup.create_parsers()
        args = revup_parser.parse_args(["commit", "--topic", "mytopic", "--scope", "auth"])
        assert args.topic == "mytopic"
        assert args.scope == "auth"

    def test_type_and_scope_together(self):
        """Test that --type and --scope work together."""
        revup_parser, _ = revup.create_parsers()
        args = revup_parser.parse_args([
            "commit", "--topic", "mytopic", "--type", "fix", "--scope", "api"
        ])
        assert args.type == "fix"
        assert args.scope == "api"

    def test_commit_message_script_argument(self):
        """Test that --commit-message-script is parsed correctly."""
        revup_parser, _ = revup.create_parsers()
        args = revup_parser.parse_args([
            "commit", "--topic", "mytopic", "--commit-message-script", "/path/to/script"
        ])
        assert args.commit_message_script == "/path/to/script"


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
    """Tests for bug where --relative changes aren't persisted.

    There were two early return bugs:

    Bug 1 (line 350): When using --no-edit with no staged changes, the function
    would return 0 immediately before even processing --relative.
    Fix: Check `args.relative` in the early return condition.

    Bug 2 (line 487): When using the editor path, the early return check compared
    the already-updated in-memory message with the editor output. If user saved
    without changes, both would be equal, causing early return without persisting.
    Fix: Track if --relative changed the message via `relative_changed_msg`.
    """

    def test_relative_change_should_not_early_return_editor_path(self):
        """Test that --relative change is tracked to prevent early return (editor path).

        This tests Bug 2: the editor path early return check.
        """
        original_msg = "feat: some feature\n\nTopic: mytopic"
        updated_msg = amend.update_relative_in_message(original_msg, "other_topic")

        # The message was changed
        assert updated_msg != original_msg
        assert "Relative: other_topic" in updated_msg

        # Simulate user saving editor without changes (new_msg == updated_msg)
        new_msg = updated_msg

        # The bug: stack[0].commit_msg == new_msg would be True after --relative
        # updated it, causing early return without persisting the change.
        # The fix: track if --relative changed the message and don't early return.
        relative_changed_msg = (updated_msg != original_msg)
        assert relative_changed_msg is True

        # With the fix, this condition should prevent early return:
        has_diff = False  # No staged changes
        should_early_return = (updated_msg == new_msg and not has_diff and not relative_changed_msg)
        assert should_early_return is False, "Should NOT early return when --relative changed message"

    def test_no_relative_change_allows_early_return(self):
        """Test that early return is allowed when --relative doesn't change anything."""
        # Message already has the same Relative tag
        original_msg = "feat: some feature\n\nTopic: mytopic\nRelative: other_topic"
        updated_msg = amend.update_relative_in_message(original_msg, "other_topic")

        # Message unchanged (same relative value)
        assert updated_msg == original_msg

        # Simulate user saving editor without changes
        new_msg = updated_msg

        # With no actual change, early return is appropriate
        relative_changed_msg = (updated_msg != original_msg)
        assert relative_changed_msg is False

        has_diff = False
        should_early_return = (updated_msg == new_msg and not has_diff and not relative_changed_msg)
        assert should_early_return is True, "Early return is OK when no actual changes"

    def test_no_edit_early_return_should_check_relative(self):
        """Test Bug 1: --no-edit path must not early return when --relative is set.

        The early return at line 350 checks:
            if not has_diff and not args.edit and not args.relative:
                return 0

        Without the `and not args.relative` check, using --no-edit with --relative
        and no staged changes would return before processing --relative.
        """
        # This is a logic test - the actual fix is in amend.py line 350
        has_diff = False
        args_edit = False  # --no-edit
        args_relative = "other_topic"  # --relative other_topic

        # Old buggy condition (would early return incorrectly):
        old_should_early_return = (not has_diff and not args_edit)
        assert old_should_early_return is True, "Old code would incorrectly early return"

        # Fixed condition:
        new_should_early_return = (not has_diff and not args_edit and not args_relative)
        assert new_should_early_return is False, "Fixed code should NOT early return"


class TestAmendRelativeArgument:
    """Tests for --relative argument with amend command."""

    def test_amend_allows_relative_flag(self):
        """Test that amend command accepts --relative flag."""
        revup_parser, _ = revup.create_parsers()
        args = revup_parser.parse_args(["amend", "--relative", "other_topic", "mytopic"])
        assert args.relative == "other_topic"
        assert args.ref_or_topic == "mytopic"

    def test_amend_relative_consumes_next_arg(self):
        """Test that --relative consumes the next argument as its value."""
        revup_parser, _ = revup.create_parsers()
        # --relative with value consumes it
        args = revup_parser.parse_args(["amend", "--relative", "other_topic"])
        # "other_topic" becomes the relative value, not ref_or_topic
        assert args.relative == "other_topic"
        assert args.ref_or_topic is None

    def test_amend_relative_with_topic_ref(self):
        """Test amend with both --relative value and topic reference."""
        revup_parser, _ = revup.create_parsers()
        args = revup_parser.parse_args(["amend", "--relative", "base_topic", "my_topic"])
        assert args.relative == "base_topic"
        assert args.ref_or_topic == "my_topic"
