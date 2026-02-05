"""Utilities for modifying topic relative relationships.

This module provides functions for updating Relative: tags in commit messages
and orchestrating the git operations to rewrite commits with updated messages.
"""
from __future__ import annotations

import logging
from typing import Optional

from revup import git
from revup.topic_stack import RE_TAGS, TAG_RELATIVE, Topic


def update_relative_in_message(commit_msg: str, new_relative: str, prompt: bool = True) -> Optional[str]:
    """
    Update or add a Relative: tag in a commit message.

    Args:
        commit_msg: The existing commit message
        new_relative: The new relative topic to set
        prompt: If True, prompt user to confirm when replacing existing value

    Returns:
        Modified commit message, or None if user cancels the replacement
    """
    # Find existing Relative: tag
    existing_relative = None
    relative_line_idx = None
    lines = commit_msg.split("\n")

    for i, line in enumerate(lines):
        match = RE_TAGS.match(line)
        if match and match.group("tagname").lower() == TAG_RELATIVE:
            existing_relative = match.group("tagvalue").strip()
            relative_line_idx = i
            break

    # If same value, no change needed
    if existing_relative == new_relative:
        logging.info(f"Relative: {new_relative} already set, no change needed.")
        return commit_msg

    # If different value exists, prompt for confirmation
    if existing_relative is not None:
        if prompt:
            logging.warning(f"Commit already has 'Relative: {existing_relative}'")
            confirm = input(f"Replace with 'Relative: {new_relative}'? [y/N]: ").strip().lower()
            if confirm != "y":
                logging.info("Aborted. Relative tag not changed.")
                return None
        # Replace existing line
        lines[relative_line_idx] = f"Relative: {new_relative}"
        return "\n".join(lines)

    # No existing tag - add after Topic: line if present, otherwise at end
    topic_line_idx = None
    for i, line in enumerate(lines):
        match = RE_TAGS.match(line)
        if match and match.group("tagname").lower() == "topic":
            topic_line_idx = i
            break

    new_line = f"Relative: {new_relative}"
    if topic_line_idx is not None:
        # Insert after Topic: line
        lines.insert(topic_line_idx + 1, new_line)
    else:
        # Append to end (but before any trailing empty lines)
        # Find last non-empty line
        last_content_idx = len(lines) - 1
        while last_content_idx >= 0 and not lines[last_content_idx].strip():
            last_content_idx -= 1
        lines.insert(last_content_idx + 1, new_line)

    return "\n".join(lines)


def remove_relative_from_message(commit_msg: str) -> str:
    """Remove Relative: tag from commit message if present.

    Args:
        commit_msg: The existing commit message

    Returns:
        Modified commit message with Relative: tag removed
    """
    lines = commit_msg.split("\n")
    result = []
    for line in lines:
        match = RE_TAGS.match(line)
        if match and match.group("tagname").lower() == TAG_RELATIVE:
            continue  # Skip this line
        result.append(line)
    return "\n".join(result)


def update_topic_relative_in_stack(
    topic: Topic,
    new_relative: Optional[Topic],
    stack: list[git.CommitHeader],
    prompt: bool = True,
) -> bool:
    """Update a topic's Relative: tag in the commit stack.

    This modifies the stack in place to update the commit message.
    The caller is responsible for rewriting the commits afterward.

    Args:
        topic: The topic to update
        new_relative: New relative topic (None to remove Relative: tag)
        stack: Full commit stack from HEAD to base (modified in place)
        prompt: If True, prompt user to confirm when replacing existing value

    Returns:
        True if a change was made, False otherwise
    """
    commit = topic.original_commits[0]

    if new_relative:
        updated_msg = update_relative_in_message(commit.commit_msg, new_relative.name, prompt)
    else:
        updated_msg = remove_relative_from_message(commit.commit_msg)

    if updated_msg is None:
        return False  # User cancelled

    if updated_msg == commit.commit_msg:
        return False  # No change needed

    # Update commit message in stack
    for stack_entry in stack:
        if stack_entry.commit_id == commit.commit_id:
            stack_entry.commit_msg = updated_msg
            return True

    return False
