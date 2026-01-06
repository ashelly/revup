"""
revup edit: Start an interactive editing session for a topic.

Usage:
    revup edit <topic>      - Start editing the topic's commits
    revup edit --commit     - Amend and continue after making changes
    revup edit --abort      - Cancel the edit session
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
from pathlib import Path
from typing import List, Set

from revup import git, topic_stack
from revup.types import RevupUsageException


def is_in_rebase(repo_path: Path) -> bool:
    """Check if we're currently in a rebase state."""
    git_dir = repo_path / ".git"
    if git_dir.is_file():
        # Handle worktrees where .git is a file pointing to the real git dir
        content = git_dir.read_text().strip()
        if content.startswith("gitdir: "):
            git_dir = Path(content[8:])

    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def generate_rebase_sequence(commits: List[str], topic_commits: Set[str]) -> str:
    """
    Generate a rebase sequence file with 'edit' for topic commits and 'pick' for others.

    Args:
        commits: List of "hash title" strings from git rebase todo
        topic_commits: Set of commit hashes that belong to the topic

    Returns:
        The rebase sequence content
    """
    lines = []
    for commit_line in commits:
        parts = commit_line.split(" ", 1)
        if len(parts) < 2:
            continue
        commit_hash = parts[0]
        title = parts[1]
        # Check if this commit's hash (or short hash) matches any topic commit
        action = "edit" if any(
            commit_hash.startswith(tc[:len(commit_hash)]) or tc.startswith(commit_hash)
            for tc in topic_commits
        ) else "pick"
        lines.append(f"{action} {commit_hash} {title}")
    return "\n".join(lines) + "\n"


async def start_edit(args: argparse.Namespace, git_ctx: git.Git) -> int:
    """Start an interactive rebase with 'edit' for the specified topic's commits."""
    topic_name = args.topic

    if not topic_name:
        raise RevupUsageException(
            "Topic name required. Usage: revup edit <topic>\n"
            "Or use --commit to finish editing: revup edit --commit"
        )

    # Check if already in a rebase
    if is_in_rebase(Path(git_ctx.repo_root)):
        raise RevupUsageException(
            "Already in a rebase. Either:\n"
            "  - Run 'revup edit --commit' to amend and continue\n"
            "  - Run 'git rebase --abort' to cancel\n"
            "  - Run 'git rebase --continue' to continue without amending"
        )

    # Parse topics to find the target topic
    topics = topic_stack.TopicStack(
        git_ctx,
        "",  # base_branch - auto-detect
        "",  # relative_branch - auto-detect
        None,
        None,
    )
    await topics.populate_topics()

    if topic_name not in topics.topics:
        available = ", ".join(topics.topics.keys()) if topics.topics else "(none found)"
        raise RevupUsageException(
            f"Topic '{topic_name}' not found.\nAvailable topics: {available}"
        )

    topic = topics.topics[topic_name]
    topic_commits = {c.commit_id for c in topic.original_commits}

    if not topic_commits:
        raise RevupUsageException(f"Topic '{topic_name}' has no commits.")

    # Find the base commit (parent of the oldest topic commit in the stack)
    # We need to rebase from the commit before any of our target commits
    oldest_topic_commit = topic.original_commits[0]
    base_commit = oldest_topic_commit.parents[0]

    # Get the list of commits from base to HEAD for the rebase
    commits_output = await git_ctx.git_stdout(
        "log", "--oneline", "--reverse", f"{base_commit}..HEAD"
    )
    commits = [line for line in commits_output.split("\n") if line.strip()]

    if not commits:
        raise RevupUsageException("No commits found before the topic to edit.")

    # Generate the rebase sequence
    sequence = generate_rebase_sequence(commits, topic_commits)

    # Write the sequence and editor script to the scratch directory
    scratch_dir = git_ctx.get_scratch_dir()
    sequence_file = f"{scratch_dir}/rebase_sequence"
    editor_script = f"{scratch_dir}/sequence_editor.sh"

    with open(sequence_file, "w") as f:
        f.write(sequence)

    with open(editor_script, "w") as f:
        f.write(f"#!/bin/sh\ncat '{sequence_file}' > \"$1\"\n")
    os.chmod(editor_script, 0o755)

    # Run the rebase with our custom sequence editor
    env = os.environ.copy()
    env["GIT_SEQUENCE_EDITOR"] = editor_script

    result = subprocess.run(
        [git_ctx.git_path, "rebase", "-i", base_commit],
        env=env,
        cwd=git_ctx.repo_root,
    )

    # Check the result: rebase can return non-zero when stopping at an edit point
    in_rebase = is_in_rebase(Path(git_ctx.repo_root))

    if in_rebase:
        # Stopped at an edit point - this is the expected flow
        logging.info("")
        logging.info("Rebase stopped for editing. Make your changes, then run:")
        logging.info("  revup edit --commit")
        logging.info("")
        logging.info("Other options:")
        logging.info("  git rebase --abort      - Cancel the edit session")
        return 0

    if result.returncode != 0:
        # Rebase failed and we're not in a rebase state - something went wrong
        logging.error("Rebase failed. Check git output above for details.")
        return result.returncode

    # Rebase completed successfully (no edit points hit, or nothing to do)
    logging.info("Edit session complete.")
    return 0


async def commit_and_continue(args: argparse.Namespace, git_ctx: git.Git) -> int:
    """Amend the current commit and continue the rebase."""
    if not is_in_rebase(Path(git_ctx.repo_root)):
        raise RevupUsageException(
            "Not in a rebase. Start an edit session with: revup edit <topic>"
        )

    # Run git commit --amend
    amend_result = subprocess.run(
        [git_ctx.git_path, "commit", "--amend"],
        cwd=git_ctx.repo_root,
    )

    if amend_result.returncode != 0:
        logging.error("Amend failed. Resolve any issues and try again.")
        return 1

    # Continue the rebase
    continue_result = subprocess.run(
        [git_ctx.git_path, "rebase", "--continue"],
        cwd=git_ctx.repo_root,
    )

    # Check if we're still in a rebase (more edits to go)
    if is_in_rebase(Path(git_ctx.repo_root)):
        logging.info("")
        logging.info("Rebase stopped at next edit point. Make your changes, then run:")
        logging.info("  revup edit --commit")
        return 0

    if continue_result.returncode == 0:
        logging.info("Edit session complete.")
    return continue_result.returncode


async def abort_edit(git_ctx: git.Git) -> int:
    """Abort the current rebase session."""
    if not is_in_rebase(Path(git_ctx.repo_root)):
        raise RevupUsageException(
            "Not in a rebase. Nothing to abort."
        )

    result = subprocess.run(
        [git_ctx.git_path, "rebase", "--abort"],
        cwd=git_ctx.repo_root,
    )

    if result.returncode == 0:
        logging.info("Edit session aborted.")
    return result.returncode


async def main(args: argparse.Namespace, git_ctx: git.Git) -> int:
    """Main entry point for the edit command."""
    if args.commit and args.abort:
        raise RevupUsageException("Cannot use --commit and --abort together.")

    if args.abort:
        return await abort_edit(git_ctx)
    elif args.commit:
        return await commit_and_continue(args, git_ctx)
    else:
        return await start_edit(args, git_ctx)

