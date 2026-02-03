"""Tests for revup stack module."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import pytest

from revup import stack
from revup.topic_stack import TAG_RELATIVE


@dataclass
class MockCommit:
    """Mock commit for testing."""
    commit_id: str = "abc12345"


@dataclass
class MockTopic:
    """Mock Topic for testing stack visualization."""
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
        children = stack.build_children_map(topics)
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
        children = stack.build_children_map(topics)
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
        children_map = stack.build_children_map(topics)
        layout = stack.compute_layout(topics, children_map, ["A", "B", "C"])
        
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
        children_map = stack.build_children_map(topics)
        layout = stack.compute_layout(topics, children_map, ["A", "B", "C"])
        
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
        children_map = stack.build_children_map(topics)
        lines = stack.render_tree(topics, children_map, ["A", "B", "C"])
        
        # Each line with * should have the same prefix
        topic_lines = [line for line in lines if "*" in line]
        for line in topic_lines:
            # Should not have multiple consecutive spaces before *
            assert "  *" not in line, f"Extra spaces in: {line}"

    def test_simple_fork_spacing(self):
        """Fork should have correct spacing."""
        topics = make_topics([
            ("A", None),
            ("B", "A"),
            ("C", "A"),
        ])
        children_map = stack.build_children_map(topics)
        lines = stack.render_tree(topics, children_map, ["A", "B", "C"])
        
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
        children_map = stack.build_children_map(topics)
        lines = stack.render_tree(topics, children_map, list(topics.keys()))
        
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
        children_map = stack.build_children_map(topics)
        lines = stack.render_tree(topics, children_map, list(topics.keys()))
        
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
        children_map = stack.build_children_map(topics)
        layout = stack.compute_layout(topics, children_map, list(topics.keys()))
        
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
        children_map = stack.build_children_map(topics)
        lines = stack.render_tree(topics, children_map, ["only_topic"])
        
        assert len(lines) == 1
        assert "*" in lines[0]
        assert "only_topic" in lines[0]

    def test_output_format(self):
        """Test basic output format is correct."""
        topics = make_topics([
            ("parent", None),
            ("child", "parent"),
        ])
        children_map = stack.build_children_map(topics)
        lines = stack.render_tree(topics, children_map, ["parent", "child"])
        
        # Should have topic lines with * and topic name
        topic_lines = [line for line in lines if "*" in line]
        assert len(topic_lines) == 2
        assert any("parent" in line for line in topic_lines)
        assert any("child" in line for line in topic_lines)
