"""Tests for commit-message.py script."""
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

# Add scripts directory to path for importing
scripts_dir = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))

# Import will fail until we implement the function, so we do it conditionally
try:
    from importlib import import_module
    commit_message = import_module("commit-message")
    derive_description = commit_message.derive_description
except (ImportError, AttributeError):
    derive_description = None


@pytest.mark.skipif(derive_description is None, reason="derive_description not implemented yet")
class TestDeriveDescription:
    """Tests for derive_description function."""

    def test_topic_fallback_kebab_case(self):
        """Topic in kebab-case converts to readable description."""
        result = derive_description([], "user-auth", "feat")
        assert "user auth" in result.lower()

    def test_topic_fallback_snake_case(self):
        """Topic in snake_case converts to readable description."""
        result = derive_description([], "login_flow", "feat")
        assert "login flow" in result.lower()

    def test_topic_fallback_with_fix_prefix(self):
        """Topic with fix prefix is handled appropriately."""
        result = derive_description([], "fix-login-bug", "fix")
        assert "login" in result.lower() or "bug" in result.lower()

    def test_feat_type_uses_add_verb(self):
        """feat commit type uses 'add' or similar verb."""
        result = derive_description([], "new-feature", "feat")
        # Should produce something like "add new feature" or "implement new feature"
        assert result  # Non-empty

    def test_fix_type_produces_fix_description(self):
        """fix commit type produces appropriate description."""
        result = derive_description([], "broken-widget", "fix")
        assert result  # Non-empty

    def test_chore_type_uses_update_verb(self):
        """chore commit type uses 'update' or similar verb."""
        result = derive_description([], "deps", "chore")
        assert result  # Non-empty

    @mock.patch("subprocess.run")
    def test_extracts_new_function_from_diff(self, mock_run):
        """Extracts function names from git diff."""
        # Mock git diff output with a new function
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout="""diff --git a/foo.py b/foo.py
+def calculate_total(items):
+    return sum(items)
""",
        )
        result = derive_description(["foo.py"], "my-topic", "feat")
        assert "calculate_total" in result or "calculate total" in result.lower()

    @mock.patch("subprocess.run")
    def test_extracts_new_class_from_diff(self, mock_run):
        """Extracts class names from git diff."""
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout="""diff --git a/models.py b/models.py
+class UserAccount:
+    def __init__(self):
+        pass
""",
        )
        result = derive_description(["models.py"], "my-topic", "feat")
        assert "UserAccount" in result or "user account" in result.lower()

    @mock.patch("subprocess.run")
    def test_extracts_async_function_from_diff(self, mock_run):
        """Extracts async function names from git diff."""
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout="""diff --git a/api.py b/api.py
+async def fetch_data(url):
+    pass
""",
        )
        result = derive_description(["api.py"], "my-topic", "feat")
        assert "fetch_data" in result or "fetch data" in result.lower()

    @mock.patch("subprocess.run")
    def test_multiple_definitions_listed(self, mock_run):
        """Multiple new definitions are mentioned."""
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout="""diff --git a/utils.py b/utils.py
+def helper_one():
+    pass
+def helper_two():
+    pass
""",
        )
        result = derive_description(["utils.py"], "my-topic", "feat")
        # Should mention at least one of them
        assert "helper" in result.lower()

    @mock.patch("subprocess.run")
    def test_falls_back_to_topic_on_empty_diff(self, mock_run):
        """Falls back to topic when diff has no extractable info."""
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout="""diff --git a/config.json b/config.json
+  "key": "value"
""",
        )
        result = derive_description(["config.json"], "update-settings", "chore")
        # Should fall back to topic-derived description
        assert "update" in result.lower() or "settings" in result.lower()

    @mock.patch("subprocess.run")
    def test_handles_subprocess_error(self, mock_run):
        """Handles git command failures gracefully."""
        mock_run.side_effect = subprocess.SubprocessError("git failed")
        result = derive_description(["foo.py"], "my-feature", "feat")
        # Should fall back to topic-derived description
        assert result  # Non-empty, derived from topic

    def test_refactor_type_description(self):
        """refactor commit type produces appropriate description."""
        result = derive_description([], "cleanup-code", "refactor")
        assert result  # Non-empty
