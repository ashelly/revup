"""Tests for revup tree module."""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from io import StringIO
from unittest.mock import patch

import pytest

from revup import tree
from revup.topic_stack import TAG_RELATIVE

from conftest import create_topic_commit, run_async


@dataclass
class MockCommit:
    """Mock commit for testing."""
    commit_id: str = "abc12345"


@dataclass
class MockTopic:
    """Mock Topic for testing tree visualization."""
    name: str
    tags: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    original_commits: list[MockCommit] = field(default_factory=lambda: [MockCommit()])


def make_topics(topic_specs: list[tuple[str, str | None]]) -> dict[str, MockTopic]:
    """Create mock topics from specifications.
    
    Args:
        topic_specs: List of (name, parent_name) tuples. parent_name can be None.
    
    Returns:
        Dictionary of topic name to MockTopic.
    """
    topics = {}
    for name, parent in topic_specs:
        topic = MockTopic(name=name)
        if parent:
            topic.tags[TAG_RELATIVE].add(parent)
        topics[name] = topic
    return topics


class TestBuildChildrenMap:
    """Tests for build_children_map function."""

    def test_linear_chain(self):
        """A -> B -> C should have correct children map."""
        topics = make_topics([
            ("A", None),
            ("B", "A"),
            ("C", "B"),
        ])
        children = tree.build_children_map(topics)
        assert children["A"] == ["B"]
        assert children["B"] == ["C"]
        assert children["C"] == []

    def test_fork(self):
        """A -> B, A -> C should show both as children of A."""
        topics = make_topics([
            ("A", None),
            ("B", "A"),
            ("C", "A"),
        ])
        children = tree.build_children_map(topics)
        assert set(children["A"]) == {"B", "C"}
        assert children["B"] == []
        assert children["C"] == []


class TestComputeLayout:
    """Tests for compute_layout function."""

    def test_linear_chain_single_column(self):
        """Linear chain should use single column."""
        topics = make_topics([
            ("A", None),
            ("B", "A"),
            ("C", "B"),
        ])
        children_map = tree.build_children_map(topics)
        layout = tree.compute_layout(topics, children_map, ["A", "B", "C"])
        
        # All should be in column 0
        cols = {item.name: item.column for item in layout}
        assert cols["A"] == cols["B"] == cols["C"]

    def test_simple_fork_two_columns(self):
        """Fork should use two columns."""
        topics = make_topics([
            ("A", None),
            ("B", "A"),
            ("C", "A"),
        ])
        children_map = tree.build_children_map(topics)
        layout = tree.compute_layout(topics, children_map, ["A", "B", "C"])
        
        # A should be in one column, B and C in columns (one same as A, one different)
        cols = {item.name: item.column for item in layout}
        # One of B or C should share column with A
        assert cols["A"] in (cols["B"], cols["C"])


class TestRenderFromLayout:
    """Tests for render_from_layout function."""

    def test_linear_chain_no_extra_spaces(self):
        """Linear chain should have consistent spacing."""
        topics = make_topics([
            ("A", None),
            ("B", "A"),
            ("C", "B"),
        ])
        children_map = tree.build_children_map(topics)
        lines = tree.render_tree(topics, children_map, ["A", "B", "C"])
        
        # Each line with * or + should have the same prefix
        topic_lines = [line for line in lines if "*" in line or "+" in line]
        for line in topic_lines:
            # Should not have multiple consecutive spaces before marker
            assert "  *" not in line and "  +" not in line, f"Extra spaces in: {line}"

    def test_simple_fork_spacing(self):
        """Fork should have correct spacing."""
        topics = make_topics([
            ("A", None),
            ("B", "A"),
            ("C", "A"),
        ])
        children_map = tree.build_children_map(topics)
        lines = tree.render_tree(topics, children_map, ["A", "B", "C"])
        
        for line in lines:
            # No line should have 3+ consecutive spaces
            assert "   " not in line, f"Too many spaces in: {line}"


class TestColumnGaps:
    """Tests specifically for column gap issues."""

    def test_nested_fork_no_column_gaps(self):
        """Nested forks should not create column gaps.
        
        This reproduces the user's reported issue where:
        - Expected: "| * topic_name"
        - Actual:   "|   * topic_name"
        
        Structure being tested:
        root
        └── branch1
            ├── leaf1
            └── branch2
                └── leaf2
        """
        topics = make_topics([
            ("root", None),
            ("branch1", "root"),
            ("leaf1", "branch1"),
            ("branch2", "branch1"),
            ("leaf2", "branch2"),
        ])
        children_map = tree.build_children_map(topics)
        lines = tree.render_tree(topics, children_map, list(topics.keys()))
        
        # No line should have 3+ consecutive spaces (which indicates a column gap)
        for line in lines:
            assert "   " not in line, f"Column gap detected in: {line}"

    def test_complex_tree_no_column_gaps(self):
        """Complex tree with multiple forks and merges should not have gaps.
        
        Structure similar to user's example:
        A
        └── B
            └── C
                ├── D
                │   └── E
                └── F
        └── G
        """
        topics = make_topics([
            ("A", None),
            ("B", "A"),
            ("C", "B"),
            ("D", "C"),
            ("E", "D"),
            ("F", "C"),
            ("G", "A"),
        ])
        children_map = tree.build_children_map(topics)
        lines = tree.render_tree(topics, children_map, list(topics.keys()))
        
        # No line should have 3+ consecutive spaces
        for line in lines:
            assert "   " not in line, f"Column gap detected in: {line}"

    def test_columns_are_contiguous(self):
        """After layout computation, columns should be contiguous (0, 1, 2, ...)."""
        topics = make_topics([
            ("A", None),
            ("B", "A"),
            ("C", "B"),
            ("D", "B"),  # Fork
            ("E", "A"),  # Another branch from A
        ])
        children_map = tree.build_children_map(topics)
        layout = tree.compute_layout(topics, children_map, list(topics.keys()))
        
        # Collect all used columns
        all_cols = set()
        for item in layout:
            all_cols.add(item.column)
            all_cols.update(item.merge_cols)
        
        # Columns should be 0, 1, 2, ... with no gaps
        sorted_cols = sorted(all_cols)
        expected = list(range(len(sorted_cols)))
        assert sorted_cols == expected, f"Column gaps detected: {sorted_cols} != {expected}"


class TestRenderTree:
    """Integration tests for render_tree function."""

    def test_single_topic(self):
        """Single topic renders correctly."""
        topics = make_topics([("only_topic", None)])
        children_map = tree.build_children_map(topics)
        lines = tree.render_tree(topics, children_map, ["only_topic"])
        
        assert len(lines) == 1
        assert "*" in lines[0]  # Single topic is a leaf, uses *
        assert "only_topic" in lines[0]

    def test_output_format(self):
        """Test basic output format is correct."""
        topics = make_topics([
            ("parent", None),
            ("child", "parent"),
        ])
        children_map = tree.build_children_map(topics)
        lines = tree.render_tree(topics, children_map, ["parent", "child"])
        
        # Should have topic lines with * or + and topic name
        topic_lines = [line for line in lines if "*" in line or "+" in line]
        assert len(topic_lines) == 2
        assert any("parent" in line for line in topic_lines)
        assert any("child" in line for line in topic_lines)

    def test_chain_nodes_use_plus_leaves_use_asterisk(self):
        """Chain nodes (with children) use +, leaves (no children) use *."""
        # Structure: A -> B -> C (leaf)
        #                 \-> D (leaf)
        topics = make_topics([
            ("A", None),
            ("B", "A"),
            ("C", "B"),
            ("D", "B"),
        ])
        children_map = tree.build_children_map(topics)
        lines = tree.render_tree(topics, children_map, ["A", "B", "C", "D"])
        
        # Find topic lines
        for line in lines:
            if "A " in line:
                assert "+ A" in line, f"A has children, should use +: {line}"
            elif "B " in line:
                assert "+ B" in line, f"B has children, should use +: {line}"
            elif "C " in line:
                assert "* C" in line, f"C is leaf, should use *: {line}"
            elif "D " in line:
                assert "* D" in line, f"D is leaf, should use *: {line}"


# =============================================================================
# INTEGRATION TESTS WITH REAL GIT REPOSITORIES
# =============================================================================


class TestTreeMainIntegration:
    """Integration tests for tree.main() with real git repos."""

    def test_no_topics_shows_message(self, git_repo, git_ctx):
        """When no topics exist, shows 'No topics found.' message."""
        args = argparse.Namespace(
            base_branch="origin/main",
            relative_branch="",
            debug=False,
        )
        output = StringIO()
        with patch("sys.stdout", output):
            result = run_async(tree.main(args, git_ctx))

        assert result == 0
        assert "No topics found" in output.getvalue()

    def test_single_topic_displays(self, git_repo, git_ctx):
        """Single topic is displayed correctly."""
        create_topic_commit(git_repo, "my_feature")

        args = argparse.Namespace(
            base_branch="origin/main",
            relative_branch="",
            debug=False,
        )
        output = StringIO()
        with patch("sys.stdout", output):
            result = run_async(tree.main(args, git_ctx))

        assert result == 0
        out = output.getvalue()
        assert "my_feature" in out
        assert "*" in out or "+" in out  # Topic marker (* for leaf, + for chain)

    def test_linear_chain_displays(self, git_repo, git_ctx):
        """Linear chain A -> B -> C displays all topics."""
        create_topic_commit(git_repo, "base_feature")
        create_topic_commit(git_repo, "middle_feature", relative="base_feature")
        create_topic_commit(git_repo, "top_feature", relative="middle_feature")

        args = argparse.Namespace(
            base_branch="origin/main",
            relative_branch="",
            debug=False,
        )
        output = StringIO()
        with patch("sys.stdout", output):
            result = run_async(tree.main(args, git_ctx))

        assert result == 0
        out = output.getvalue()
        assert "base_feature" in out
        assert "middle_feature" in out
        assert "top_feature" in out

    def test_forked_topics_display(self, git_repo, git_ctx):
        """Forked topics (A with children B, C) display correctly."""
        create_topic_commit(git_repo, "parent_topic")
        create_topic_commit(git_repo, "child_one", relative="parent_topic")
        create_topic_commit(git_repo, "child_two", relative="parent_topic")

        args = argparse.Namespace(
            base_branch="origin/main",
            relative_branch="",
            debug=False,
        )
        output = StringIO()
        with patch("sys.stdout", output):
            result = run_async(tree.main(args, git_ctx))

        assert result == 0
        out = output.getvalue()
        assert "parent_topic" in out
        assert "child_one" in out
        assert "child_two" in out

    def test_independent_topics_as_separate_components(self, git_repo, git_ctx):
        """Independent topics (no Relative) appear as separate components."""
        create_topic_commit(git_repo, "feature_a")
        create_topic_commit(git_repo, "feature_b")

        args = argparse.Namespace(
            base_branch="origin/main",
            relative_branch="",
            debug=False,
        )
        output = StringIO()
        with patch("sys.stdout", output):
            result = run_async(tree.main(args, git_ctx))

        assert result == 0
        out = output.getvalue()
        assert "feature_a" in out
        assert "feature_b" in out

    def test_debug_flag_shows_extra_info(self, git_repo, git_ctx):
        """Debug flag shows topic-relative mapping."""
        create_topic_commit(git_repo, "base")
        create_topic_commit(git_repo, "child", relative="base")

        args = argparse.Namespace(
            base_branch="origin/main",
            relative_branch="",
            debug=True,
        )
        output = StringIO()
        with patch("sys.stdout", output):
            result = run_async(tree.main(args, git_ctx))

        assert result == 0
        out = output.getvalue()
        assert "Topics and relatives:" in out
        assert "base" in out
        assert "child" in out

    def test_independent_trees_newest_first(self, git_repo, git_ctx):
        """Independent trees should be ordered newest first (like git log).
        
        Creates three independent topics in order: oldest, middle, newest.
        Output should show newest first, then middle, then oldest.
        """
        create_topic_commit(git_repo, "oldest_topic")
        create_topic_commit(git_repo, "middle_topic")
        create_topic_commit(git_repo, "newest_topic")

        args = argparse.Namespace(
            base_branch="origin/main",
            relative_branch="",
            debug=False,
        )
        output = StringIO()
        with patch("sys.stdout", output):
            result = run_async(tree.main(args, git_ctx))

        assert result == 0
        out = output.getvalue()
        
        # Find positions of each topic in output
        newest_pos = out.find("newest_topic")
        middle_pos = out.find("middle_topic")
        oldest_pos = out.find("oldest_topic")
        
        # All should be present
        assert newest_pos != -1, "newest_topic not found"
        assert middle_pos != -1, "middle_topic not found"
        assert oldest_pos != -1, "oldest_topic not found"
        
        # Newest should appear first, then middle, then oldest
        assert newest_pos < middle_pos, "newest should appear before middle"
        assert middle_pos < oldest_pos, "middle should appear before oldest"

