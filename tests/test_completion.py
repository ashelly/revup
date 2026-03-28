"""Tests for shell completion functionality."""
from __future__ import annotations

import argparse
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from revup import completion, revup


class TestCompletionScriptGeneration:
    """Tests for bash completion script generation."""

    def test_restack_completion_checks_for_as_option(self):
        """Test that restack completion script checks for --as multi-value option."""
        main_parser, _ = revup.create_parsers()
        script = completion.get_bash_completion_script(main_parser)

        # Should have a case for restack that checks --as
        assert "restack)" in script
        assert "--as)" in script
        # Should complete topics when --as is found
        assert "revup toolkit list-topics" in script

    def test_amend_completion_checks_for_relative_option(self):
        """Test that amend completion script checks for --relative option."""
        main_parser, _ = revup.create_parsers()
        script = completion.get_bash_completion_script(main_parser)

        # Should have a case for amend that checks --relative
        assert "amend)" in script
        assert "--relative" in script

    def test_completion_script_has_topic_completion_for_upload(self):
        """Test that upload command completes topic names."""
        main_parser, _ = revup.create_parsers()
        script = completion.get_bash_completion_script(main_parser)

        assert "upload)" in script
        # Upload should complete topics for positional argument
        assert "revup toolkit list-topics" in script

    def test_restack_multi_value_looks_back_through_words(self):
        """Test that restack --as completion looks back through all words."""
        main_parser, _ = revup.create_parsers()
        script = completion.get_bash_completion_script(main_parser)

        # The multi-value check should iterate through words (not just check $prev)
        # Look for the for loop that iterates through words
        assert "for ((j=i+1; j < cword; j++))" in script

    def test_amend_single_value_only_checks_prev(self):
        """Test that amend --relative completion only checks $prev."""
        main_parser, _ = revup.create_parsers()
        script = completion.get_bash_completion_script(main_parser)

        # The amend case should use case "$prev" for single-value options
        # Find the amend section and verify it checks $prev
        assert 'case "$prev"' in script


# =============================================================================
# INTEGRATION TESTS FOR COMPLETION COMMAND
# =============================================================================


class TestCompletionMain:
    """Integration tests for completion.main() function."""

    def test_main_prints_completion_script(self):
        """completion main() prints bash completion script to stdout."""
        main_parser, _ = revup.create_parsers()
        args = argparse.Namespace(install=False)

        output = StringIO()
        with patch("sys.stdout", output):
            result = completion.main(args, main_parser)

        assert result == 0
        script = output.getvalue()
        assert "_revup_completions" in script
        assert "complete -F _revup_completions revup" in script

    def test_main_script_contains_all_subcommands(self):
        """Generated script contains case entries for all subcommands."""
        main_parser, _ = revup.create_parsers()
        args = argparse.Namespace(install=False)

        output = StringIO()
        with patch("sys.stdout", output):
            completion.main(args, main_parser)

        script = output.getvalue()
        expected_commands = ["amend", "commit", "upload", "restack", "edit", "stack", "completion", "toolkit"]
        for cmd in expected_commands:
            assert f"{cmd})" in script, f"Missing case for {cmd}"


class TestInstallBashCompletion:
    """Integration tests for install_bash_completion() function."""

    def test_install_creates_cache_file_and_updates_rc(self):
        """install_bash_completion creates cache file and adds source to rc."""
        main_parser, _ = revup.create_parsers()

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "completion.bash"
            rc_file = Path(tmpdir) / ".bashrc"
            rc_file.write_text("# existing content\n")

            # Mock user input to provide paths
            inputs = iter([str(cache_file), str(rc_file)])
            with patch("builtins.input", lambda _: next(inputs)):
                result = completion.install_bash_completion(main_parser)

            assert result == 0
            assert cache_file.exists()
            assert "_revup_completions" in cache_file.read_text()

            rc_content = rc_file.read_text()
            assert completion.COMPLETION_MARKER in rc_content
            assert f'source "{cache_file}"' in rc_content

    def test_install_updates_existing_source_line(self):
        """install_bash_completion updates existing source line if marker present."""
        main_parser, _ = revup.create_parsers()

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "new_completion.bash"
            rc_file = Path(tmpdir) / ".bashrc"
            # Pre-existing completion with old path
            old_content = f"# stuff\n{completion.COMPLETION_MARKER}\nsource \"/old/path\"\n# more stuff\n"
            rc_file.write_text(old_content)

            inputs = iter([str(cache_file), str(rc_file)])
            with patch("builtins.input", lambda _: next(inputs)):
                result = completion.install_bash_completion(main_parser)

            assert result == 0
            rc_content = rc_file.read_text()
            # Old path should be replaced
            assert "/old/path" not in rc_content
            assert f'source "{cache_file}"' in rc_content

    def test_install_prompts_to_create_missing_rc(self):
        """install_bash_completion prompts to create rc file if missing."""
        main_parser, _ = revup.create_parsers()

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "completion.bash"
            rc_file = Path(tmpdir) / "subdir" / ".bashrc"  # Doesn't exist

            # Say yes to creating the file
            inputs = iter([str(cache_file), str(rc_file), "y"])
            with patch("builtins.input", lambda _: next(inputs)):
                result = completion.install_bash_completion(main_parser)

            assert result == 0
            assert rc_file.exists()

    def test_install_aborts_if_user_declines_create_rc(self):
        """install_bash_completion aborts if user declines to create rc file."""
        main_parser, _ = revup.create_parsers()

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "completion.bash"
            rc_file = Path(tmpdir) / "nonexistent" / ".bashrc"

            # Say no to creating the file
            inputs = iter([str(cache_file), str(rc_file), "n"])
            with patch("builtins.input", lambda _: next(inputs)):
                result = completion.install_bash_completion(main_parser)

            assert result == 1
            assert not rc_file.exists()
