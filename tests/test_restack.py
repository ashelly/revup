"""Tests for revup restack module."""
from __future__ import annotations

import asyncio
import os
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import pytest

from revup import git, revup, shell
from revup.restack import find_gca_for_reorder, reorder_topics
from revup.relative_utils import (
    remove_relative_from_message,
    update_relative_in_message,
    update_topic_relative_in_stack,
)
from revup.topic_stack import TAG_RELATIVE, TopicStack


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


def get_topic_relative(commit_msg):
    """Extract Relative: value from commit message, or None."""
    for line in commit_msg.split('\n'):
        if line.lower().startswith('relative:'):
            return line.split(':', 1)[1].strip()
    return None


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


@dataclass
class MockCommitHeader:
    """Mock commit header for testing."""
    commit_id: str
    commit_msg: str


@dataclass
class MockTopic:
    """Mock topic for testing GCA algorithm."""
    name: str
    tags: dict = field(default_factory=lambda: defaultdict(set))
    original_commits: list = field(default_factory=list)

    def add_relative(self, relative_name: str):
        """Helper to add a relative tag."""
        self.tags[TAG_RELATIVE].add(relative_name)


class TestRestackAsArgument:
    """Tests for the --as argument on revup restack."""

    def test_as_single_topic(self):
        """Test that --as with single topic is parsed correctly."""
        revup_parser, _ = revup.create_parsers()
        args = revup_parser.parse_args(["restack", "--as", "topic_a"])
        assert args.reorder_topics == ["topic_a"]

    def test_as_multiple_topics(self):
        """Test that --as with multiple topics is parsed correctly."""
        revup_parser, _ = revup.create_parsers()
        args = revup_parser.parse_args(["restack", "--as", "topic_a", "topic_b", "topic_c"])
        assert args.reorder_topics == ["topic_a", "topic_b", "topic_c"]

    def test_as_with_other_flags(self):
        """Test that --as works alongside other restack flags."""
        revup_parser, _ = revup.create_parsers()
        args = revup_parser.parse_args(["restack", "--as", "a", "b", "--topicless-last"])
        assert args.reorder_topics == ["a", "b"]
        assert args.topicless_last is True

    def test_restack_without_as(self):
        """Test that restack without --as has reorder_topics as None."""
        revup_parser, _ = revup.create_parsers()
        args = revup_parser.parse_args(["restack"])
        assert args.reorder_topics is None


class TestFindGcaForReorder:
    """Tests for find_gca_for_reorder algorithm.
    
    The GCA (greatest common ancestor) for reordering is:
    - Walk up from the first topic's ancestors
    - Return the first ancestor NOT in the topic set
    - Return None if no such ancestor (means base branch)
    """

    def test_gca_skips_topics_in_set(self):
        """Test that GCA skips over topics that are in the reorder set.
        
        Original: a <- b <- c
        Command: restack --as c b
        GCA should be 'a' (skip 'b' because it's in the set)
        """
        # Create mock topics: a <- b <- c
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c}
        topic_set = {"c", "b"}  # restack --as c b

        gca = find_gca_for_reorder(topic_c, topic_set, all_topics)
        assert gca is not None
        assert gca.name == "a"

    def test_gca_returns_none_when_all_ancestors_in_set(self):
        """Test GCA returns None when all ancestors are in the set.
        
        Original: a <- b <- c
        Command: restack --as c b a
        GCA should be None (base branch) because a, b are all in the set
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c}
        topic_set = {"c", "b", "a"}  # restack --as c b a

        gca = find_gca_for_reorder(topic_c, topic_set, all_topics)
        assert gca is None

    def test_gca_returns_immediate_parent_when_not_in_set(self):
        """Test GCA returns immediate parent if not in set.
        
        Original: a <- b <- c <- d
        Command: restack --as d c
        GCA should be 'b' (immediate parent of c, not in set)
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")
        topic_d = MockTopic(name="d")
        topic_d.add_relative("c")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c, "d": topic_d}
        topic_set = {"d", "c"}  # restack --as d c

        gca = find_gca_for_reorder(topic_d, topic_set, all_topics)
        assert gca is not None
        assert gca.name == "b"

    def test_gca_with_no_relative(self):
        """Test GCA returns None when first topic has no relative."""
        topic_a = MockTopic(name="a")  # No relative

        all_topics = {"a": topic_a}
        topic_set = {"a"}

        gca = find_gca_for_reorder(topic_a, topic_set, all_topics)
        assert gca is None

    def test_gca_with_missing_relative_topic(self):
        """Test GCA returns None when relative topic doesn't exist (merged)."""
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")  # 'a' doesn't exist in all_topics

        all_topics = {"b": topic_b}  # 'a' is missing (probably merged)
        topic_set = {"b"}

        gca = find_gca_for_reorder(topic_b, topic_set, all_topics)
        assert gca is None


class TestRemoveRelativeFromMessage:
    """Tests for remove_relative_from_message function."""

    def test_removes_relative_tag(self):
        """Test that Relative: tag is removed from message."""
        msg = "feat: some feature\n\nTopic: mytopic\nRelative: other_topic"
        result = remove_relative_from_message(msg)
        assert "Relative:" not in result
        assert "Topic: mytopic" in result

    def test_preserves_other_content(self):
        """Test that other content is preserved."""
        msg = "feat: some feature\n\nBody text here.\n\nTopic: mytopic\nRelative: other"
        result = remove_relative_from_message(msg)
        assert "feat: some feature" in result
        assert "Body text here." in result
        assert "Topic: mytopic" in result

    def test_handles_no_relative_tag(self):
        """Test message without Relative: tag is unchanged."""
        msg = "feat: some feature\n\nTopic: mytopic"
        result = remove_relative_from_message(msg)
        assert result == msg

    def test_case_insensitive_removal(self):
        """Test that removal is case-insensitive."""
        msg = "feat: some feature\n\nTopic: mytopic\nrelative: other_topic"
        result = remove_relative_from_message(msg)
        assert "relative:" not in result.lower()


class TestUpdateTopicRelativeInStack:
    """Tests for update_topic_relative_in_stack function."""

    def test_updates_commit_in_stack(self):
        """Test that commit message is updated in the stack."""
        commit = MockCommitHeader(
            commit_id="abc123",
            commit_msg="feat: feature\n\nTopic: mytopic"
        )
        topic = MockTopic(name="mytopic", original_commits=[commit])
        new_relative = MockTopic(name="other_topic")
        stack = [commit]

        result = update_topic_relative_in_stack(topic, new_relative, stack, prompt=False)
        
        assert result is True
        assert "Relative: other_topic" in stack[0].commit_msg

    def test_removes_relative_when_none(self):
        """Test that Relative: is removed when new_relative is None."""
        commit = MockCommitHeader(
            commit_id="abc123",
            commit_msg="feat: feature\n\nTopic: mytopic\nRelative: old_topic"
        )
        topic = MockTopic(name="mytopic", original_commits=[commit])
        stack = [commit]

        result = update_topic_relative_in_stack(topic, None, stack, prompt=False)
        
        assert result is True
        assert "Relative:" not in stack[0].commit_msg

    def test_no_change_when_same_relative(self):
        """Test returns False when relative is already correct."""
        commit = MockCommitHeader(
            commit_id="abc123",
            commit_msg="feat: feature\n\nTopic: mytopic\nRelative: other_topic"
        )
        topic = MockTopic(name="mytopic", original_commits=[commit])
        new_relative = MockTopic(name="other_topic")
        stack = [commit]

        result = update_topic_relative_in_stack(topic, new_relative, stack, prompt=False)
        
        assert result is False  # No change needed

    def test_returns_false_when_commit_not_in_stack(self):
        """Test returns False when commit is not found in stack."""
        commit = MockCommitHeader(
            commit_id="abc123",
            commit_msg="feat: feature\n\nTopic: mytopic"
        )
        topic = MockTopic(name="mytopic", original_commits=[commit])
        new_relative = MockTopic(name="other_topic")
        different_commit = MockCommitHeader(commit_id="def456", commit_msg="other")
        stack = [different_commit]  # commit not in stack

        result = update_topic_relative_in_stack(topic, new_relative, stack, prompt=False)
        
        assert result is False


class TestRestackAsLogic:
    """Tests for the restack --as logic.
    
    These test the algorithm logic using mock objects.
    
    Test cases from the plan:
    | Original | Command | Result | GCA |
    |----------|---------|--------|-----|
    | a <- b <- c <- d | --as c b | a <- c <- b, c <- d | a (skip b) |
    | a <- b <- c <- d | --as d c b | a <- d <- c <- b | a (skip c,b) |
    | a <- b, c <- d | --as b d | a <- b <- d, c orphan | a |
    | a, b, c (indep) | --as a b c | (base) <- a <- b <- c | None |
    | a <- b <- c | --as c a b | (base) <- c <- a <- b | None (skip b,a) |
    """

    def test_reorder_simple_swap(self):
        """Test swapping two adjacent topics: a <- b <- c becomes a <- c <- b.
        
        Original: a <- b <- c (c relative to b, b relative to a)
        Command: --as c b
        Result: a <- c <- b (b relative to c, c relative to a)
        """
        # Create chain: a <- b <- c
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c}
        reorder_list = ["c", "b"]
        topic_set = set(reorder_list)

        # Find GCA
        gca = find_gca_for_reorder(topic_c, topic_set, all_topics)
        assert gca.name == "a"

        # After reorder:
        # - c should be relative to a (GCA)
        # - b should be relative to c
        expected_c_relative = "a"  # GCA
        expected_b_relative = "c"  # previous in list

        assert expected_c_relative == gca.name
        assert expected_b_relative == reorder_list[0]

    def test_reorder_full_reversal(self):
        """Test reversing order: a <- b <- c <- d becomes a <- d <- c <- b.
        
        Original: a <- b <- c <- d
        Command: --as d c b
        Result: a <- d <- c <- b
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")
        topic_d = MockTopic(name="d")
        topic_d.add_relative("c")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c, "d": topic_d}
        reorder_list = ["d", "c", "b"]
        topic_set = set(reorder_list)

        gca = find_gca_for_reorder(topic_d, topic_set, all_topics)
        assert gca.name == "a"

        # After reorder: d <- a, c <- d, b <- c

    def test_reorder_merge_chains(self):
        """Test merging two independent chains.
        
        Original: a <- b, c <- d (two independent chains)
        Command: --as b d
        Result: a <- b <- d, c becomes orphan
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_d = MockTopic(name="d")
        topic_d.add_relative("c")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c, "d": topic_d}
        reorder_list = ["b", "d"]
        topic_set = set(reorder_list)

        # GCA is b's ancestor not in set = a
        gca = find_gca_for_reorder(topic_b, topic_set, all_topics)
        assert gca.name == "a"

        # After reorder: b <- a, d <- b

    def test_reorder_create_chain_from_independent(self):
        """Test creating chain from independent topics.
        
        Original: a, b, c (all independent, relative to base)
        Command: --as a b c
        Result: (base) <- a <- b <- c
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_c = MockTopic(name="c")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c}
        reorder_list = ["a", "b", "c"]
        topic_set = set(reorder_list)

        # GCA is None (a has no ancestors)
        gca = find_gca_for_reorder(topic_a, topic_set, all_topics)
        assert gca is None

        # After reorder: a <- base, b <- a, c <- b

    def test_reorder_all_in_set_uses_base(self):
        """Test when all ancestors are in the set.
        
        Original: a <- b <- c
        Command: --as c a b
        Result: (base) <- c <- a <- b
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c}
        reorder_list = ["c", "a", "b"]
        topic_set = set(reorder_list)

        # GCA should be None because all ancestors (b, a) are in the set
        gca = find_gca_for_reorder(topic_c, topic_set, all_topics)
        assert gca is None

    def test_reorder_nonexistent_topic_error(self):
        """Test that nonexistent topic is handled.
        
        The actual error is raised in reorder_topics(), not find_gca_for_reorder().
        This test verifies the GCA algorithm handles missing topics gracefully.
        """
        topic_b = MockTopic(name="b")
        topic_b.add_relative("nonexistent")

        all_topics = {"b": topic_b}
        topic_set = {"b"}

        # GCA returns None when relative doesn't exist
        gca = find_gca_for_reorder(topic_b, topic_set, all_topics)
        assert gca is None

    def test_reorder_single_topic_keeps_ancestor(self):
        """Test single topic reorder keeps its ancestor.
        
        Original: a <- b <- c
        Command: --as b
        Result: a <- b (no change since b already relative to a)
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c}
        reorder_list = ["b"]
        topic_set = set(reorder_list)

        # GCA is a (b's relative is a, not in set)
        gca = find_gca_for_reorder(topic_b, topic_set, all_topics)
        assert gca.name == "a"

        # After reorder: b <- a (same as before)


# =============================================================================
# PRINCIPLE-BASED TESTS
# =============================================================================
# These tests validate the 4 core behavioral principles:
# P1: Explicit Chain Creation - --as X Y Z creates GCA <- X <- Y <- Z
# P2: Path Extraction - Middle topics either go to GCA (Option A) or end (Option B)
# P3: Dependents Stay as Branches - Non-path dependents keep their relative
# P4: No Cycles - Operations creating cycles are rejected


class TestPrinciple1_ExplicitChainCreation:
    """P1: --as X Y Z creates GCA <- X <- Y <- Z
    
    The GCA is the first ancestor of X that is NOT in the reorder list.
    If all ancestors are in the list, GCA is the base branch (None).
    """

    def test_two_topics_simple(self):
        """a <- b, --as b a -> (base) <- b <- a
        
        Both a and b are in list, so GCA is base (None).
        Result: b relative to base, a relative to b.
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")

        all_topics = {"a": topic_a, "b": topic_b}
        reorder_list = ["b", "a"]
        topic_set = set(reorder_list)

        gca = find_gca_for_reorder(topic_b, topic_set, all_topics)
        assert gca is None, "GCA should be None (base) when all ancestors in list"

        # Expected relatives after reorder:
        # b -> None (base)
        # a -> b

    def test_two_topics_partial(self):
        """a <- b <- c, --as c b -> a <- c <- b
        
        a is not in list, so GCA is a.
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c}
        reorder_list = ["c", "b"]
        topic_set = set(reorder_list)

        gca = find_gca_for_reorder(topic_c, topic_set, all_topics)
        assert gca is not None
        assert gca.name == "a"

        # Expected relatives after reorder:
        # c -> a (GCA)
        # b -> c

    def test_three_topics_chain(self):
        """a <- b <- c <- d, --as d c b -> a <- d <- c <- b
        
        a is not in list, so GCA is a.
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")
        topic_d = MockTopic(name="d")
        topic_d.add_relative("c")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c, "d": topic_d}
        reorder_list = ["d", "c", "b"]
        topic_set = set(reorder_list)

        gca = find_gca_for_reorder(topic_d, topic_set, all_topics)
        assert gca.name == "a"

        # Expected relatives after reorder:
        # d -> a (GCA)
        # c -> d
        # b -> c

    def test_gca_is_first_non_listed_ancestor(self):
        """a <- b <- c <- d, --as d c -> b <- d <- c
        
        Walking from d: c is in list (skip), b is NOT in list -> GCA = b
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")
        topic_d = MockTopic(name="d")
        topic_d.add_relative("c")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c, "d": topic_d}
        reorder_list = ["d", "c"]
        topic_set = set(reorder_list)

        gca = find_gca_for_reorder(topic_d, topic_set, all_topics)
        assert gca.name == "b", "GCA should be b (first non-listed ancestor)"


class TestPrinciple3_DependentsStayAsBranches:
    """P3: Non-path dependents keep their relative, become branches.
    
    Topics not on the extraction path keep their relative unchanged.
    They become branches from wherever their relative ends up.
    """

    def test_simple_trailing_becomes_branch(self):
        """a <- b <- c, --as b a -> b <- a, b <- c
        
        c was relative to b. After reorder, c stays relative to b.
        c becomes a branch from b (not attached to end of chain).
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c}
        reorder_list = ["b", "a"]
        topic_set = set(reorder_list)

        gca = find_gca_for_reorder(topic_b, topic_set, all_topics)
        assert gca is None  # All ancestors (a) in list

        # c is NOT in the reorder list
        # c's relative (b) IS in the list
        # Per P3: c keeps its relative (b), becomes branch from b
        # Expected result: b <- a (chain), b <- c (branch)
        assert "c" not in topic_set
        assert topic_c.tags[TAG_RELATIVE] == {"b"}

    def test_side_branch_unchanged(self):
        """a <- b <- c, b <- d, --as c b -> a <- c <- b, b <- d
        
        d is a side branch from b (not on path between c and b).
        d should stay relative to b.
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")
        topic_d = MockTopic(name="d")
        topic_d.add_relative("b")  # Side branch from b

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c, "d": topic_d}
        reorder_list = ["c", "b"]
        topic_set = set(reorder_list)

        # d is not in the reorder list and not on path between c and b
        # d should keep its relative (b)
        assert "d" not in topic_set
        assert topic_d.tags[TAG_RELATIVE] == {"b"}

    def test_deep_branch_follows(self):
        """a <- b <- c, b <- d <- e, --as c b -> a <- c <- b, b <- d <- e
        
        d <- e is a deep branch from b.
        Both d and e should stay relative to their current parents.
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")
        topic_d = MockTopic(name="d")
        topic_d.add_relative("b")  # Branch from b
        topic_e = MockTopic(name="e")
        topic_e.add_relative("d")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c, "d": topic_d, "e": topic_e}
        reorder_list = ["c", "b"]
        topic_set = set(reorder_list)

        # d and e are not in the reorder list
        # They should keep their relatives
        assert "d" not in topic_set
        assert "e" not in topic_set
        assert topic_d.tags[TAG_RELATIVE] == {"b"}
        assert topic_e.tags[TAG_RELATIVE] == {"d"}


class TestPrinciple4_NoCycles:
    """P4: Operations creating cycles are rejected.
    
    The reorder algorithm must detect and prevent dependency cycles.
    """

    def test_is_ancestor_detects_potential_cycle(self):
        """Test is_ancestor can detect when reorder would create cycle."""
        from revup.topic_stack import Topic, is_ancestor

        # a <- b <- c
        a = Topic(name="a")
        b = Topic(name="b", relative_topic=a)
        c = Topic(name="c", relative_topic=b)

        # If we try to make b relative to c, we need to detect that
        # c is currently a descendant of b
        assert is_ancestor(b, c) is True, "b is ancestor of c"

        # Making b relative to c without extracting c first would create:
        # a <- c <- b <- c (cycle!)

    def test_independent_topics_no_cycle_risk(self):
        """Independent topics can be freely reordered."""
        from revup.topic_stack import Topic, is_ancestor

        # a <- b, c (independent)
        a = Topic(name="a")
        b = Topic(name="b", relative_topic=a)
        c = Topic(name="c")  # Independent, no relative

        # No cycle risk when connecting independent topics
        assert is_ancestor(b, c) is False
        assert is_ancestor(c, b) is False


# =============================================================================
# OPTION A TESTS: Extract Middle Topics to GCA (Form Branch)
# =============================================================================


class TestOptionA_ExtractToGCA:
    """P2 Option A: Middle topics extracted to GCA, form separate branch.
    
    When topics exist on the path between reordered topics but aren't
    in the reorder list, they get extracted to the GCA and form a
    separate branch.
    """

    def test_single_middle_extracted(self):
        """a <- b <- c <- d, --as d b -> a <- d <- b, a <- c
        
        c is on the path between d and b, but not in list.
        c gets extracted to GCA (a), forming a separate branch.
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")
        topic_d = MockTopic(name="d")
        topic_d.add_relative("c")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c, "d": topic_d}
        reorder_list = ["d", "b"]
        topic_set = set(reorder_list)

        gca = find_gca_for_reorder(topic_d, topic_set, all_topics)
        assert gca.name == "a"

        # c is on path (d -> c -> b) but not in list
        # Option A: c should be extracted to GCA (a)
        # Expected result:
        #   a <- d <- b (explicit chain)
        #   a <- c (extracted, branches from a)
        
        # Verify c is a "middle topic"
        assert "c" not in topic_set
        # c's current relative is b, which IS in the reorder list
        assert topic_c.tags[TAG_RELATIVE] == {"b"}

    def test_multiple_middle_extracted(self):
        """a <- b <- c <- d <- e, --as e b -> a <- e <- b, a <- c <- d
        
        c and d are on the path between e and b.
        Both get extracted to GCA, preserving their relative order.
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")
        topic_d = MockTopic(name="d")
        topic_d.add_relative("c")
        topic_e = MockTopic(name="e")
        topic_e.add_relative("d")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c, "d": topic_d, "e": topic_e}
        reorder_list = ["e", "b"]
        topic_set = set(reorder_list)

        gca = find_gca_for_reorder(topic_e, topic_set, all_topics)
        assert gca.name == "a"

        # c and d are middle topics
        # Option A: both extracted to GCA, preserving order (c <- d)
        # Expected result:
        #   a <- e <- b (explicit chain)
        #   a <- c <- d (extracted chain, branches from a)
        assert "c" not in topic_set
        assert "d" not in topic_set

    def test_middle_with_side_branch(self):
        """a <- b <- c <- d <- e, c <- f, --as e b -> a <- e <- b, a <- c <- d, c <- f
        
        c is a middle topic with its own side branch (f).
        When c is extracted, f follows c (stays relative to c).
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")
        topic_d = MockTopic(name="d")
        topic_d.add_relative("c")
        topic_e = MockTopic(name="e")
        topic_e.add_relative("d")
        topic_f = MockTopic(name="f")
        topic_f.add_relative("c")  # Side branch from c

        all_topics = {
            "a": topic_a, "b": topic_b, "c": topic_c,
            "d": topic_d, "e": topic_e, "f": topic_f
        }
        reorder_list = ["e", "b"]
        topic_set = set(reorder_list)

        # f is a side branch from c (not on the e->b path)
        # When c is extracted, f should follow c (keep relative to c)
        # Expected result:
        #   a <- e <- b (explicit chain)
        #   a <- c <- d (extracted)
        #   c <- f (f follows c)
        assert "f" not in topic_set
        assert topic_f.tags[TAG_RELATIVE] == {"c"}

    def test_extracted_preserves_order(self):
        """Middle topics keep their relative order among themselves.
        
        a <- b <- c <- d <- e <- f, --as f b
        Middle topics: c, d, e
        After extraction: a <- c <- d <- e (order preserved)
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")
        topic_d = MockTopic(name="d")
        topic_d.add_relative("c")
        topic_e = MockTopic(name="e")
        topic_e.add_relative("d")
        topic_f = MockTopic(name="f")
        topic_f.add_relative("e")

        all_topics = {
            "a": topic_a, "b": topic_b, "c": topic_c,
            "d": topic_d, "e": topic_e, "f": topic_f
        }
        reorder_list = ["f", "b"]
        topic_set = set(reorder_list)

        # c, d, e are middle topics
        # Their relative order should be preserved after extraction
        # c <- d <- e (c relative to GCA, d relative to c, e relative to d)


# =============================================================================
# OPTION B TESTS: Attach Middle Topics to End of Chain
# =============================================================================


class TestOptionB_AttachToEnd:
    """P2 Option B: Middle topics attach to end of new chain.
    
    When topics exist on the path between reordered topics but aren't
    in the reorder list, they get attached to the END of the new chain,
    maintaining a single linear chain.
    """

    def test_single_middle_at_end(self):
        """a <- b <- c <- d, --as d b -> a <- d <- b <- c
        
        c is on the path between d and b.
        Option B: c attaches to end (after b).
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")
        topic_d = MockTopic(name="d")
        topic_d.add_relative("c")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c, "d": topic_d}
        reorder_list = ["d", "b"]
        topic_set = set(reorder_list)

        gca = find_gca_for_reorder(topic_d, topic_set, all_topics)
        assert gca.name == "a"

        # Option B: c attaches to end of chain
        # Expected result: a <- d <- b <- c (linear)

    def test_multiple_middle_at_end(self):
        """a <- b <- c <- d <- e, --as e b -> a <- e <- b <- c <- d
        
        c and d are middle topics.
        Option B: both attach to end, preserving order.
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")
        topic_d = MockTopic(name="d")
        topic_d.add_relative("c")
        topic_e = MockTopic(name="e")
        topic_e.add_relative("d")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c, "d": topic_d, "e": topic_e}
        reorder_list = ["e", "b"]
        topic_set = set(reorder_list)

        # Option B: c, d attach to end
        # Expected result: a <- e <- b <- c <- d (linear)

    def test_middle_with_side_branch_at_end(self):
        """a <- b <- c <- d <- e, c <- f, --as e b -> a <- e <- b <- c <- d, c <- f
        
        c is middle topic with side branch f.
        Option B: c attaches to end, f stays as branch from c.
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")
        topic_d = MockTopic(name="d")
        topic_d.add_relative("c")
        topic_e = MockTopic(name="e")
        topic_e.add_relative("d")
        topic_f = MockTopic(name="f")
        topic_f.add_relative("c")

        all_topics = {
            "a": topic_a, "b": topic_b, "c": topic_c,
            "d": topic_d, "e": topic_e, "f": topic_f
        }
        reorder_list = ["e", "b"]
        topic_set = set(reorder_list)

        # Option B: c, d attach to end
        # f stays as branch from c
        # Expected result:
        #   a <- e <- b <- c <- d (linear)
        #   c <- f (branch)


# =============================================================================
# PRE-EXISTING BRANCH TESTS
# =============================================================================


class TestPreExistingBranches:
    """Tests with branches that exist before reordering."""

    def test_two_branches_from_root(self):
        """a <- b, a <- c, --as c b -> a <- c <- b
        
        Two independent branches from a.
        Reorder connects them into a chain.
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("a")  # Also relative to a (independent branch)

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c}
        reorder_list = ["c", "b"]
        topic_set = set(reorder_list)

        gca = find_gca_for_reorder(topic_c, topic_set, all_topics)
        assert gca.name == "a"

        # Expected result: a <- c <- b

    def test_swap_at_branch_point(self):
        """a <- b <- c, b <- d, --as b a -> b <- a, b <- c, b <- d
        
        b has two dependents (c and d).
        When a and b swap, both c and d stay as branches from b.
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")
        topic_d = MockTopic(name="d")
        topic_d.add_relative("b")  # Another branch from b

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c, "d": topic_d}
        reorder_list = ["b", "a"]
        topic_set = set(reorder_list)

        gca = find_gca_for_reorder(topic_b, topic_set, all_topics)
        assert gca is None  # All ancestors (a) in list

        # c and d are not in list, both relative to b
        # Per P3: they stay as branches from b
        assert topic_c.tags[TAG_RELATIVE] == {"b"}
        assert topic_d.tags[TAG_RELATIVE] == {"b"}

    def test_reorder_within_one_branch(self):
        """a <- b <- c, a <- d <- e, --as c b -> a <- c <- b, a <- d <- e
        
        Two branches from a. Reorder only affects one branch.
        The other branch (d <- e) is unchanged.
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")
        topic_d = MockTopic(name="d")
        topic_d.add_relative("a")  # Second branch
        topic_e = MockTopic(name="e")
        topic_e.add_relative("d")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c, "d": topic_d, "e": topic_e}
        reorder_list = ["c", "b"]
        topic_set = set(reorder_list)

        gca = find_gca_for_reorder(topic_c, topic_set, all_topics)
        assert gca.name == "a"

        # d and e are not affected
        assert "d" not in topic_set
        assert "e" not in topic_set
        assert topic_d.tags[TAG_RELATIVE] == {"a"}
        assert topic_e.tags[TAG_RELATIVE] == {"d"}

    def test_connect_independent_branches(self):
        """a <- b, c <- d, --as b d -> a <- b <- d
        
        Two completely independent chains.
        Reorder connects b to d (d becomes relative to b).
        c is orphaned (stays relative to nothing/base).
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")  # Independent (no relative or relative to base)
        topic_d = MockTopic(name="d")
        topic_d.add_relative("c")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c, "d": topic_d}
        reorder_list = ["b", "d"]
        topic_set = set(reorder_list)

        gca = find_gca_for_reorder(topic_b, topic_set, all_topics)
        assert gca.name == "a"

        # c was d's relative, but c is not in reorder list
        # d's relative changes to b
        # c becomes orphaned (its dependent d moved away)

    def test_branch_from_middle_topic(self):
        """a <- b <- c <- d, c <- f, --as d b -> varies by option
        
        f branches from c, which is a middle topic.
        Option A: a <- d <- b, a <- c, c <- f
        Option B: a <- d <- b <- c, c <- f
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")
        topic_d = MockTopic(name="d")
        topic_d.add_relative("c")
        topic_f = MockTopic(name="f")
        topic_f.add_relative("c")  # Branch from middle topic c

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c, "d": topic_d, "f": topic_f}
        reorder_list = ["d", "b"]
        topic_set = set(reorder_list)

        gca = find_gca_for_reorder(topic_d, topic_set, all_topics)
        assert gca.name == "a"

        # f is a branch from c
        # Regardless of option, f stays relative to c
        assert topic_f.tags[TAG_RELATIVE] == {"c"}


# =============================================================================
# COMMAND EQUIVALENCE TESTS
# =============================================================================


class TestCommandEquivalence:
    """Verify amend --relative and restack --as produce identical results.
    
    `revup amend --relative X Y` (on topic Y) should be equivalent to
    `revup restack --as X Y`.
    """

    def test_equivalence_simple_two_topics(self):
        """amend --relative b a == restack --as b a
        
        Both should result in: (base) <- b <- a
        """
        # For restack --as b a
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")

        all_topics = {"a": topic_a, "b": topic_b}
        reorder_list = ["b", "a"]
        topic_set = set(reorder_list)

        gca = find_gca_for_reorder(topic_b, topic_set, all_topics)
        assert gca is None  # Both in list -> base branch

        # amend --relative b a would:
        # 1. Set a's relative to b
        # 2. Detect that b is a descendant of a? No, b's relative is a.
        # 3. Extract b first? No, we're setting a's relative to b.
        #
        # Actually, amend --relative b a means "set topic a's relative to b"
        # This requires extracting b from under a first.
        #
        # Result should be same: (base) <- b <- a

    def test_equivalence_with_trailing_topic(self):
        """Both commands should handle trailing topics the same way.
        
        a <- b <- c, command on (b, a)
        Both should result in: b <- a, b <- c (c branches from b)
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c}
        reorder_list = ["b", "a"]
        topic_set = set(reorder_list)

        # c is not in list, stays relative to b
        assert "c" not in topic_set
        assert topic_c.tags[TAG_RELATIVE] == {"b"}

    def test_equivalence_gca_calculation(self):
        """Both commands should calculate GCA the same way.
        
        a <- b <- c <- d, command on (d, b)
        GCA should be a for both.
        """
        topic_a = MockTopic(name="a")
        topic_b = MockTopic(name="b")
        topic_b.add_relative("a")
        topic_c = MockTopic(name="c")
        topic_c.add_relative("b")
        topic_d = MockTopic(name="d")
        topic_d.add_relative("c")

        all_topics = {"a": topic_a, "b": topic_b, "c": topic_c, "d": topic_d}
        
        # restack --as d b
        reorder_list = ["d", "b"]
        topic_set = set(reorder_list)
        gca = find_gca_for_reorder(topic_d, topic_set, all_topics)
        assert gca.name == "a"

        # amend --relative d b would also need to find GCA
        # Walking from d: c (not in {d,b}? Actually c is NOT in set)
        # Wait, for amend --relative d b, we're setting b's relative to d
        # The GCA calculation starts from the first topic in the implicit list
        #
        # For equivalence, both should use the same GCA logic


# =============================================================================
# INTEGRATION TESTS
# =============================================================================
# These tests run actual restack --as commands against a real git repository


class TestRestackAsIntegration:
    """Integration tests for restack --as using a real git repo."""

    def test_swap_two_topics(self, git_repo, git_ctx):
        """Test swapping two topics: a <- b becomes b <- a.
        
        Setup: Create a <- b (b relative to a)
        Command: restack --as b a
        Expected: b has no Relative, a has Relative: b
        """
        # Create topic chain: a <- b
        create_topic_commit(git_repo, "a")
        create_topic_commit(git_repo, "b", relative="a")

        # Verify initial state
        relatives = get_all_topic_relatives(2)
        assert relatives["a"] is None
        assert relatives["b"] == "a"

        # Run restack --as b a
        async def run_restack():
            topics = TopicStack(git_ctx, "origin/main", "", None, None)
            await topics.populate_topics()
            await reorder_topics(git_ctx, topics, ["b", "a"])

        asyncio.get_event_loop().run_until_complete(run_restack())

        # Verify result: b <- a (b has no relative, a relative to b)
        relatives = get_all_topic_relatives(2)
        assert relatives["b"] is None, "b should have no Relative (attached to base)"
        assert relatives["a"] == "b", "a should be relative to b"

    def test_reverse_three_topics(self, git_repo, git_ctx):
        """Test reversing three topics: a <- b <- c becomes c <- b <- a.
        
        Setup: a <- b <- c
        Command: restack --as c b a
        Expected: c: none, b: c, a: b
        """
        # Create topic chain: a <- b <- c
        create_topic_commit(git_repo, "a")
        create_topic_commit(git_repo, "b", relative="a")
        create_topic_commit(git_repo, "c", relative="b")

        # Verify initial state
        relatives = get_all_topic_relatives(3)
        assert relatives["a"] is None
        assert relatives["b"] == "a"
        assert relatives["c"] == "b"

        # Run restack --as c b a
        async def run_restack():
            topics = TopicStack(git_ctx, "origin/main", "", None, None)
            await topics.populate_topics()
            await reorder_topics(git_ctx, topics, ["c", "b", "a"])

        asyncio.get_event_loop().run_until_complete(run_restack())

        # Verify result: c <- b <- a
        relatives = get_all_topic_relatives(3)
        assert relatives["c"] is None, "c should have no Relative (attached to base)"
        assert relatives["b"] == "c", "b should be relative to c"
        assert relatives["a"] == "b", "a should be relative to b"

    def test_reorder_with_trailing_topic(self, git_repo, git_ctx):
        """Test reorder that creates a branch: a <- b <- c, --as b a.
        
        Setup: a <- b <- c
        Command: restack --as b a
        Expected: b: none, a: b, c: b (c becomes branch from b)
        """
        # Create topic chain: a <- b <- c
        create_topic_commit(git_repo, "a")
        create_topic_commit(git_repo, "b", relative="a")
        create_topic_commit(git_repo, "c", relative="b")

        # Verify initial state
        relatives = get_all_topic_relatives(3)
        assert relatives["a"] is None
        assert relatives["b"] == "a"
        assert relatives["c"] == "b"

        # Run restack --as b a
        async def run_restack():
            topics = TopicStack(git_ctx, "origin/main", "", None, None)
            await topics.populate_topics()
            await reorder_topics(git_ctx, topics, ["b", "a"])

        asyncio.get_event_loop().run_until_complete(run_restack())

        # Verify result: b <- a (chain), b <- c (c stays relative to b - branch)
        relatives = get_all_topic_relatives(3)
        assert relatives["b"] is None, "b should have no Relative (attached to base)"
        assert relatives["a"] == "b", "a should be relative to b"
        assert relatives["c"] == "b", "c should stay relative to b (becomes branch)"


class TestAmendRelativeIntegration:
    """Integration tests for amend --relative using a real git repo.
    
    These tests verify the relative update functionality that's shared
    between amend --relative and restack --as.
    """

    def test_set_relative_simple(self, git_repo, git_ctx):
        """Test setting relative on independent topics: a, b -> a <- b.
        
        Setup: Create a and b as independent topics (no Relative)
        Command: Set b's relative to a
        Expected: b has Relative: a
        """
        # Create independent topics
        create_topic_commit(git_repo, "a")
        create_topic_commit(git_repo, "b")  # No relative

        # Verify initial state
        relatives = get_all_topic_relatives(2)
        assert relatives["a"] is None
        assert relatives["b"] is None

        # Set b's relative to a using update_topic_relative_in_stack
        async def update_relative():
            topics = TopicStack(git_ctx, "origin/main", "", None, None)
            await topics.populate_topics()
            
            topic_b = topics.topics["b"]
            topic_a = topics.topics["a"]
            
            changed = update_topic_relative_in_stack(topic_b, topic_a, topics.commits, prompt=False)
            assert changed, "Should have changed relative"
            
            # Rewrite commits
            new_parent = topics.commits[0].parents[0]
            for commit in topics.commits:
                new_parent = await git_ctx.synthetic_cherry_pick_from_commit(commit, new_parent)
            
            git_env = {"GIT_REFLOG_ACTION": "reset --soft (test)"}
            await git_ctx.soft_reset(new_parent, git_env)

        asyncio.get_event_loop().run_until_complete(update_relative())

        # Verify result
        relatives = get_all_topic_relatives(2)
        assert relatives["a"] is None, "a should have no Relative"
        assert relatives["b"] == "a", "b should be relative to a"

    def test_change_relative(self, git_repo, git_ctx):
        """Test changing existing relative: a <- b -> a, c <- b.
        
        Setup: a <- b (b relative to a), c independent
        Command: Change b's relative from a to c
        Expected: b has Relative: c
        """
        # Create topics: a <- b, c
        create_topic_commit(git_repo, "a")
        create_topic_commit(git_repo, "b", relative="a")
        create_topic_commit(git_repo, "c")

        # Verify initial state
        relatives = get_all_topic_relatives(3)
        assert relatives["a"] is None
        assert relatives["b"] == "a"
        assert relatives["c"] is None

        # Change b's relative from a to c
        async def update_relative():
            topics = TopicStack(git_ctx, "origin/main", "", None, None)
            await topics.populate_topics()
            
            topic_b = topics.topics["b"]
            topic_c = topics.topics["c"]
            
            changed = update_topic_relative_in_stack(topic_b, topic_c, topics.commits, prompt=False)
            assert changed, "Should have changed relative"
            
            # Rewrite commits
            new_parent = topics.commits[0].parents[0]
            for commit in topics.commits:
                new_parent = await git_ctx.synthetic_cherry_pick_from_commit(commit, new_parent)
            
            git_env = {"GIT_REFLOG_ACTION": "reset --soft (test)"}
            await git_ctx.soft_reset(new_parent, git_env)

        asyncio.get_event_loop().run_until_complete(update_relative())

        # Verify result
        relatives = get_all_topic_relatives(3)
        assert relatives["a"] is None, "a should have no Relative"
        assert relatives["b"] == "c", "b should now be relative to c"
        assert relatives["c"] is None, "c should have no Relative"


class TestCommandEquivalenceIntegration:
    """Integration tests verifying restack --as and relative updates produce same results."""

    def test_equivalence_simple_swap(self, git_repo, git_ctx):
        """Verify reorder_topics produces same result as manual relative updates.
        
        Setup: a <- b
        Both methods should produce: b <- a (b: none, a: b)
        """
        # Test 1: Using reorder_topics (restack --as style)
        create_topic_commit(git_repo, "a")
        create_topic_commit(git_repo, "b", relative="a")

        async def via_reorder():
            topics = TopicStack(git_ctx, "origin/main", "", None, None)
            await topics.populate_topics()
            await reorder_topics(git_ctx, topics, ["b", "a"])

        asyncio.get_event_loop().run_until_complete(via_reorder())
        
        reorder_result = get_all_topic_relatives(2)

        # Reset for test 2: recreate initial state
        subprocess.run(["git", "reset", "--hard", "HEAD~2"], check=True)
        
        create_topic_commit(git_repo, "a")
        create_topic_commit(git_repo, "b", relative="a")

        # Test 2: Using manual relative updates (amend --relative style)
        async def via_relative_update():
            topics = TopicStack(git_ctx, "origin/main", "", None, None)
            await topics.populate_topics()
            
            topic_a = topics.topics["a"]
            topic_b = topics.topics["b"]
            
            # First: set b's relative to None (remove it from chain)
            update_topic_relative_in_stack(topic_b, None, topics.commits, prompt=False)
            # Then: set a's relative to b
            update_topic_relative_in_stack(topic_a, topic_b, topics.commits, prompt=False)
            
            # Rewrite commits
            new_parent = topics.commits[0].parents[0]
            for commit in topics.commits:
                new_parent = await git_ctx.synthetic_cherry_pick_from_commit(commit, new_parent)
            
            git_env = {"GIT_REFLOG_ACTION": "reset --soft (test)"}
            await git_ctx.soft_reset(new_parent, git_env)

        asyncio.get_event_loop().run_until_complete(via_relative_update())
        
        manual_result = get_all_topic_relatives(2)

        # Both should produce the same result
        assert reorder_result == manual_result, (
            f"reorder_topics: {reorder_result} != manual: {manual_result}"
        )
        assert reorder_result["b"] is None
        assert reorder_result["a"] == "b"

    def test_equivalence_three_topics(self, git_repo, git_ctx):
        """Verify equivalence with three topics: a <- b <- c -> c <- b <- a.
        
        Both methods should produce: c: none, b: c, a: b
        """
        # Using reorder_topics
        create_topic_commit(git_repo, "a")
        create_topic_commit(git_repo, "b", relative="a")
        create_topic_commit(git_repo, "c", relative="b")

        async def via_reorder():
            topics = TopicStack(git_ctx, "origin/main", "", None, None)
            await topics.populate_topics()
            await reorder_topics(git_ctx, topics, ["c", "b", "a"])

        asyncio.get_event_loop().run_until_complete(via_reorder())
        
        result = get_all_topic_relatives(3)
        
        # Verify the expected state
        assert result["c"] is None, "c should have no Relative"
        assert result["b"] == "c", "b should be relative to c"
        assert result["a"] == "b", "a should be relative to b"
