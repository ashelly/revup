"""Display topic dependency tree."""
import argparse
from dataclasses import dataclass
from typing import Optional

from revup import git, topic_stack


@dataclass
class TopicLayout:
    """Layout information for a single topic."""
    name: str
    commit_hash: str
    column: int
    parent: Optional[str]
    merge_cols: list[int]  # Columns merging into this topic (for fork points)
    has_children: bool  # True if topic has children (is a chain node)


def build_children_map(
    topics: dict[str, topic_stack.Topic]
) -> dict[str, list[str]]:
    """Build reverse map: for each topic, list topics that depend on it."""
    children: dict[str, list[str]] = {name: [] for name in topics}
    for name, topic in topics.items():
        rel_names = topic.tags.get(topic_stack.TAG_RELATIVE, set())
        if rel_names:
            rel_name = next(iter(rel_names))
            if rel_name in children:
                children[rel_name].append(name)
    return children


def find_connected_components(
    topics: dict[str, topic_stack.Topic], children_map: dict[str, list[str]]
) -> list[list[str]]:
    """Group topics into connected components (independent trees)."""
    visited: set[str] = set()
    components: list[list[str]] = []

    def dfs(name: str, component: list[str]) -> None:
        if name in visited or name not in topics:
            return
        visited.add(name)
        component.append(name)
        for child in children_map.get(name, []):
            dfs(child, component)
        rel_names = topics[name].tags.get(topic_stack.TAG_RELATIVE, set())
        if rel_names:
            dfs(next(iter(rel_names)), component)

    for name in topics:
        if name not in visited:
            component: list[str] = []
            dfs(name, component)
            if component:
                components.append(component)
    return components


def get_parent(topic: topic_stack.Topic, topics: dict[str, topic_stack.Topic]) -> Optional[str]:
    """Get the parent topic name from Relative: tag."""
    rel_names = topic.tags.get(topic_stack.TAG_RELATIVE, set())
    if rel_names:
        rel_name = next(iter(rel_names))
        if rel_name in topics:
            return rel_name
    return None


# =============================================================================
# Pass 1: Compute layout (assign display order and columns)
# =============================================================================

def compute_layout(
    topics: dict[str, topic_stack.Topic],
    children_map: dict[str, list[str]],
    component_names: list[str],
) -> list[TopicLayout]:
    """Pass 1: Assign display order and columns via DFS."""
    component_set = set(component_names)
    layout: list[TopicLayout] = []
    col_for_topic: dict[str, int] = {}
    used_cols: set[int] = set()

    def next_col() -> int:
        c = 0
        while c in used_cols:
            c += 1
        return c

    def visit(name: str, col: int) -> None:
        if name not in component_set or name in col_for_topic:
            return

        children = [c for c in children_map.get(name, []) if c in component_set]
        col_for_topic[name] = col
        used_cols.add(col)

        # Record this topic FIRST (pre-order: parent before children)
        parent = get_parent(topics[name], topics)
        parent_in_set = parent if parent and parent in component_set else None
        commit_hash = topics[name].original_commits[0].commit_id if topics[name].original_commits else ""
        
        # fork_cols will track columns that fork FROM this topic (children not in first column)
        fork_cols: list[int] = []

        if children:
            # Assign columns to all children
            # First child gets col 0 (parent's col), others get new columns
            child_cols: list[tuple[str, int]] = []
            for i, child in enumerate(children):
                if child in col_for_topic:
                    continue
                if i == 0:
                    child_col = col  # First child gets parent's column
                else:
                    child_col = next_col()
                    fork_cols.append(child_col)
                child_cols.append((child, child_col))
                used_cols.add(child_col)

            layout.append(TopicLayout(name, commit_hash, col, parent_in_set, fork_cols, True))

            # Visit children in reverse order so first child appears first after layout reversal
            for child, child_col in reversed(child_cols):
                visit(child, child_col)

            # Free up forked columns after children are done
            for fc in fork_cols:
                used_cols.discard(fc)
        else:
            # Leaf - no fork columns, no children
            layout.append(TopicLayout(name, commit_hash, col, parent_in_set, [], False))

    # Find roots and visit
    roots = [n for n in component_names
             if not get_parent(topics[n], topics) or get_parent(topics[n], topics) not in component_set]

    for root in roots:
        col = next_col()
        visit(root, col)

    return layout


# =============================================================================
# Pass 2: Render output from layout
# =============================================================================

def render_from_layout(layout: list[TopicLayout]) -> list[str]:
    """Pass 2: Generate output lines with connectors and merge lines."""
    raw_lines: list[str] = []
    active_cols: set[int] = set()  # Columns with pending vertical lines
    prev_col: Optional[int] = None

    for item in layout:
        col = item.column
        merge_cols = item.merge_cols

        # Determine if we need a connector line
        need_connector = False
        if prev_col is not None and not merge_cols:
            other_active = active_cols - {col}
            if prev_col != col or other_active:
                need_connector = True

        # Print connector line if needed
        if need_connector and active_cols:
            parts = ["|"] * len(active_cols)
            raw_lines.append(" ".join(parts))

        # Print merge line BEFORE topic if children are merging in
        if merge_cols:
            relevant_cols = sorted((active_cols | {col}) - set(merge_cols))
            parts = ["|" for _ in relevant_cols]
            merge_str = "/" * len(merge_cols)
            raw_lines.append(" ".join(parts) + merge_str)
            active_cols -= set(merge_cols)

        # Print topic line
        relevant_cols = sorted(active_cols | {col})
        parts = []
        for c in relevant_cols:
            if c == col:
                parts.append("+" if item.has_children else "*")
            else:
                parts.append("|")
        hash_str = f"({item.commit_hash[:8]}) " if item.commit_hash else ""
        raw_lines.append(" ".join(parts) + " " + hash_str + item.name)

        # Update active columns based on parent relationship
        if item.parent:
            active_cols.add(col)
        else:
            active_cols.discard(col)

        prev_col = col

    # Compress: remove pipe-only lines between topics in the same column
    def is_connector_only(line: str) -> bool:
        return all(c in "| " for c in line)

    result: list[str] = []
    prev_topic_col: Optional[int] = None
    
    for line in raw_lines:
        if is_connector_only(line):
            # Find the next topic line to check if same column
            # For now, just add it - we'll filter in a second pass
            result.append(line)
        else:
            result.append(line)
    
    # Second pass: remove connector lines between same-column topics
    final: list[str] = []
    i = 0
    while i < len(result):
        line = result[i]
        if is_connector_only(line):
            # Look back for previous topic, forward for next topic
            prev_col = None
            next_col = None
            for j in range(i - 1, -1, -1):
                if not is_connector_only(result[j]) and '\\' not in result[j]:
                    prev_col = result[j].index('*') // 2 if '*' in result[j] else None
                    break
            for j in range(i + 1, len(result)):
                if not is_connector_only(result[j]) and '\\' not in result[j]:
                    next_col = result[j].index('*') // 2 if '*' in result[j] else None
                    break
            # Keep connector if columns differ
            if prev_col != next_col:
                final.append(line)
        else:
            final.append(line)
        i += 1
    
    return final


def render_tree(
    topics: dict[str, topic_stack.Topic],
    children_map: dict[str, list[str]],
    component_names: list[str],
) -> list[str]:
    """Render a tree with branching graph lines (two-pass algorithm)."""
    layout = compute_layout(topics, children_map, component_names)
    # Reverse layout for rendering (renderer expects children-before-parents)
    # This produces output with roots at bottom, leaves at top
    reversed_layout = list(reversed(layout))
    return render_from_layout(reversed_layout)


async def main(args: argparse.Namespace, git_ctx: git.Git) -> int:
    """Display the topic dependency tree."""
    topics_stack = topic_stack.TopicStack(
        git_ctx, args.base_branch, args.relative_branch, None, None
    )
    await topics_stack.populate_topics()

    if not topics_stack.topics:
        print("No topics found.")
        return 0

    children_map = build_children_map(topics_stack.topics)
    
    # Debug output
    if getattr(args, 'debug', False):
        print("Topics and relatives:")
        for name, topic in sorted(topics_stack.topics.items()):
            rel = topic.tags.get(topic_stack.TAG_RELATIVE, set())
            rel_str = next(iter(rel)) if rel else "(none)"
            kids = children_map.get(name, [])
            print(f"  {name} -> {rel_str}, children: {kids}")
        print()
    
    components = find_connected_components(topics_stack.topics, children_map)
    components.reverse()  # Show newest stacks first (like git log)

    first = True
    for component in components:
        if not first:
            print()  # Blank line between components
        first = False
        lines = render_tree(topics_stack.topics, children_map, component)
        for line in lines:
            print(line)

    return 0
