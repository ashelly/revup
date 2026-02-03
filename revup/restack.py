import argparse
from typing import Optional

from revup import git, topic_stack
from revup.relative_utils import update_topic_relative_in_stack
from revup.types import RevupUsageException


def find_gca_for_reorder(
    first_topic: topic_stack.Topic,
    topic_set: set[str],
    all_topics: dict[str, topic_stack.Topic],
) -> Optional[topic_stack.Topic]:
    """Find GCA - the ancestor of the lowest topic in topic_set.

    Walk the ancestor chain and find the last (closest to base) topic
    that's in topic_set. Return that topic's parent as the GCA.

    This correctly handles "middle topics" - topics on the path between
    reordered topics but not in the reorder list themselves.

    Note: This uses topic.tags[TAG_RELATIVE] instead of topic.relative_topic because
    relative_topic is not populated until populate_reviews() is called.

    Args:
        first_topic: The first topic in the reorder list
        topic_set: Set of topic names being reordered
        all_topics: Dict of all topics by name

    Returns:
        The ancestor topic of the lowest topic in the set, or None if the
        lowest topic has no ancestor (meaning the chain should start from base branch)
    """
    # Walk entire chain, tracking the lowest topic in the set
    lowest_in_set = None
    current = first_topic

    while True:
        if current.name in topic_set:
            lowest_in_set = current

        relative_names = current.tags.get(topic_stack.TAG_RELATIVE, set())
        if not relative_names:
            break  # Reached base

        relative_name = next(iter(relative_names))
        if relative_name not in all_topics:
            break  # Relative not found

        current = all_topics[relative_name]

    # GCA is the parent of the lowest topic in the set
    if lowest_in_set is None:
        return None

    relative_names = lowest_in_set.tags.get(topic_stack.TAG_RELATIVE, set())
    if not relative_names:
        return None

    relative_name = next(iter(relative_names))
    return all_topics.get(relative_name)


async def reorder_topics(
    git_ctx: git.Git,
    topics: topic_stack.TopicStack,
    reorder_topic_names: list[str],
) -> None:
    """Reorder specified topics into a chain, updating Relative: tags.

    Args:
        git_ctx: Git context
        topics: The TopicStack with populated topics
        reorder_topic_names: List of topic names in desired order
    """
    # Validate all topics exist
    for name in reorder_topic_names:
        if name not in topics.topics:
            available = ", ".join(sorted(topics.topics.keys())) if topics.topics else "(none)"
            raise RevupUsageException(
                f"Topic '{name}' not found.\nAvailable topics: {available}"
            )

    topic_set = set(reorder_topic_names)

    # Find GCA - first ancestor of first topic NOT in the set
    first_topic = topics.topics[reorder_topic_names[0]]
    gca = find_gca_for_reorder(first_topic, topic_set, topics.topics)

    # Update each topic's relative in the commit stack
    changed = False
    for i, name in enumerate(reorder_topic_names):
        topic = topics.topics[name]
        if i == 0:
            new_relative = gca
        else:
            new_relative = topics.topics[reorder_topic_names[i - 1]]

        # Get current relative from tags (not relative_topic which isn't populated yet)
        current_relative_names = topic.tags.get(topic_stack.TAG_RELATIVE, set())
        current_relative_name = next(iter(current_relative_names), None) if current_relative_names else None
        new_relative_name = new_relative.name if new_relative else None

        # Skip if already pointing to correct relative
        if current_relative_name == new_relative_name:
            continue

        if update_topic_relative_in_stack(topic, new_relative, topics.commits, prompt=False):
            changed = True

    if changed:
        # Rewrite commits with updated messages
        new_parent = topics.commits[0].parents[0]
        for commit in topics.commits:
            new_parent = await git_ctx.synthetic_cherry_pick_from_commit(commit, new_parent)

        git_env = {
            "GIT_REFLOG_ACTION": "reset --soft (revup restack --as)",
        }
        await git_ctx.soft_reset(new_parent, git_env)


async def main(args: argparse.Namespace, git_ctx: git.Git) -> int:
    """
    Handles the "restack" command.
    """
    topics = topic_stack.TopicStack(
        git_ctx,
        args.base_branch,
        args.relative_branch,
        None,
        None,
    )

    await topics.populate_topics()

    # Handle --as option to reorder topics
    if args.reorder_topics:
        await reorder_topics(git_ctx, topics, args.reorder_topics)

        # Re-create topic stack with rewritten commits
        topics = topic_stack.TopicStack(
            git_ctx,
            args.base_branch,
            args.relative_branch,
            None,
            None,
        )
        await topics.populate_topics()

    await topics.populate_reviews()
    await topics.restack(args.topicless_last)
    return 0
