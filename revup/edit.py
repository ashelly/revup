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
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set, Tuple

from revup import git, topic_stack
from revup.topic_stack import RE_TAGS, TAG_TOPIC
from revup.types import RevupUsageException


@dataclass
class EditState:
    """State detected during commit_and_continue."""
    should_amend: bool
    has_staged: bool
    topic_name: Optional[str]
    topic_commits: Set[str]
    off_topic_reason: str


def get_git_dir(repo_path: Path) -> Path:
    """Get the actual .git directory, handling worktrees."""
    git_dir = repo_path / ".git"
    if git_dir.is_file():
        # Handle worktrees where .git is a file pointing to the real git dir
        content = git_dir.read_text().strip()
        if content.startswith("gitdir: "):
            git_dir = Path(content[8:])
    return git_dir


def is_in_rebase(repo_path: Path) -> bool:
    """Check if we're currently in a rebase state."""
    git_dir = get_git_dir(repo_path)
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def get_edit_state_path(repo_root: str) -> Path:
    """State file inside rebase-merge - auto-cleaned by git when rebase ends."""
    git_dir = get_git_dir(Path(repo_root))
    return git_dir / "rebase-merge" / "revup-edit-topic"


def save_edit_state(repo_root: str, topic_name: str, topic_commits: Set[str]) -> None:
    """Save topic being edited. Only valid while rebase is active."""
    path = get_edit_state_path(repo_root)
    path.write_text(f"{topic_name}\n" + "\n".join(topic_commits))


def load_edit_state(repo_root: str) -> Optional[Tuple[str, Set[str]]]:
    """Load edit state. Returns None if not in revup-initiated rebase."""
    path = get_edit_state_path(repo_root)
    if not path.exists():
        return None
    lines = path.read_text().strip().split("\n")
    if len(lines) < 2:
        return None
    return (lines[0], set(lines[1:]))


def get_stopped_commit(repo_root: str) -> Optional[str]:
    """Get commit we're stopped at during rebase."""
    git_dir = get_git_dir(Path(repo_root))
    path = git_dir / "rebase-merge" / "stopped-sha"
    return path.read_text().strip() if path.exists() else None


async def get_commit_topic(git_ctx: git.Git, commit: str) -> Optional[str]:
    """Extract the Topic: tag from a commit message using shared regex."""
    message = await git_ctx.git_stdout("log", "-1", "--format=%B", commit)
    for line in message.split("\n"):
        m = RE_TAGS.match(line)
        if m and m.group("tagname").lower() == TAG_TOPIC:
            return m.group("tagvalue").strip()
    return None


def matches_any_topic_commit(commit_hash: str, topic_commits: Set[str]) -> bool:
    """Check if commit_hash matches any topic commit (handles short/long hashes)."""
    return any(
        commit_hash.startswith(tc[:len(commit_hash)]) or tc.startswith(commit_hash)
        for tc in topic_commits
    )


async def count_commits_after(git_ctx: git.Git, topic_commits: Set[str]) -> int:
    """Count commits processed after the last topic commit in the rebase."""
    done_path = get_git_dir(Path(git_ctx.repo_root)) / "rebase-merge" / "done"
    if not done_path.exists():
        return 0

    lines = done_path.read_text().strip().split("\n")
    hashes = [parts[1] for line in lines if len(parts := line.split()) >= 2]

    # Find last topic commit by iterating backwards, return count after it
    for i in range(len(hashes) - 1, -1, -1):
        if matches_any_topic_commit(hashes[i], topic_commits):
            return len(hashes) - i - 1
    return 0


async def has_unmerged_files(git_ctx: git.Git) -> bool:
    """Check for unresolved conflicts."""
    output = await git_ctx.git_stdout("ls-files", "-u")
    return bool(output.strip())


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
        # Save state so commit_and_continue knows which commits are topic commits
        save_edit_state(git_ctx.repo_root, topic_name, topic_commits)

        logging.info("")
        logging.info(f"Rebase stopped for editing topic '{topic_name}'. Make your changes, then run:")
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


async def detect_edit_state(git_ctx: git.Git) -> EditState:
    """Analyze current rebase state to determine what action to take."""
    # Check if there are staged changes
    has_staged = await git_ctx.git_return_code("diff", "--cached", "--quiet") != 0

    # Determine if we should amend based on edit state
    state = load_edit_state(git_ctx.repo_root)
    stopped = get_stopped_commit(git_ctx.repo_root)

    should_amend = False
    topic_name: Optional[str] = None
    topic_commits: Set[str] = set()
    if state and stopped:
        topic_name, topic_commits = state
        # Check if stopped commit matches any topic commit
        should_amend = matches_any_topic_commit(stopped, topic_commits)

    # Build informative message for non-topic commits
    off_topic_reason = "No revup edit state found"
    if state and stopped and not should_amend:
        current_topic = await get_commit_topic(git_ctx, "HEAD")
        commits_after = await count_commits_after(git_ctx, topic_commits)
        topic_desc = f"' for {current_topic}'" if current_topic else ""
        off_topic_reason = f"Stopped at commit{topic_desc}, {commits_after} commit(s) after '{topic_name}'."

    return EditState(
        should_amend=should_amend,
        has_staged=has_staged,
        topic_name=topic_name,
        topic_commits=topic_commits,
        off_topic_reason=off_topic_reason,
    )


def check_rebase_continues(git_ctx: git.Git, result_code: int) -> int:
    """Check if rebase stopped again, log appropriate message, return exit code."""
    if is_in_rebase(Path(git_ctx.repo_root)):
        logging.info("")
        logging.info("Rebase stopped at next point. Make your changes, then run:")
        logging.info("  revup edit --commit")
        return 0

    if result_code == 0:
        logging.info("Edit session complete.")
    return result_code


def handle_drop_commit(git_ctx: git.Git, state: EditState) -> int:
    """Handle case where conflict resolution resulted in no changes - drop the commit."""
    logging.info(state.off_topic_reason)
    logging.info("No changes from previous commit after resolution.")
    confirm = input("Drop this commit? [Y/n]: ").strip().lower()
    if confirm and confirm != "y":
        logging.info("Aborted. Run 'revup edit --commit' again or 'git rebase --abort'.")
        return 1

    logging.info("Dropping commit...")
    skip_result = subprocess.run(
        [git_ctx.git_path, "rebase", "--skip"],
        cwd=git_ctx.repo_root,
    )
    return check_rebase_continues(git_ctx, skip_result.returncode)


def handle_amend_and_continue(git_ctx: git.Git, state: EditState) -> int:
    """Handle amending a topic commit and continuing the rebase."""
    # Warn if no staged changes
    if not state.has_staged:
        logging.warning(f"No staged changes to amend on topic '{state.topic_name}'.")
        logging.warning("Did you forget to 'git add' your changes?")
        confirm = input("Continue anyway (edit commit message only)? [y/N]: ").strip().lower()
        if confirm != "y":
            logging.info("Aborted. Stage your changes and run 'revup edit --commit' again.")
            return 1
    else:
        # Confirm action
        logging.info(f"Stopped on topic '{state.topic_name}' commit")
        confirm = input("Proceed to AMEND this commit and continue? [Y/n]: ").strip().lower()
        if confirm and confirm != "y":
            logging.info("Aborted. Run 'revup edit --commit' again or 'git rebase --abort'.")
            return 1

    logging.info("Amending topic commit...")
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
    return check_rebase_continues(git_ctx, continue_result.returncode)


def handle_continue_only(git_ctx: git.Git, state: EditState) -> int:
    """Handle continuing rebase without amending (conflict resolution with changes)."""
    logging.info(state.off_topic_reason)
    confirm = input("Proceed to CONTINUE rebase after resolution? [Y/n]: ").strip().lower()
    if confirm and confirm != "y":
        logging.info("Aborted. Run 'revup edit --commit' again or 'git rebase --abort'.")
        return 1

    logging.info("Continuing rebase ...")
    continue_result = subprocess.run(
        [git_ctx.git_path, "rebase", "--continue"],
        cwd=git_ctx.repo_root,
    )
    return check_rebase_continues(git_ctx, continue_result.returncode)


async def commit_and_continue(args: argparse.Namespace, git_ctx: git.Git) -> int:
    """Orchestrator: validate state, detect action needed, dispatch to handler."""
    # Validation: must be in a rebase
    if not is_in_rebase(Path(git_ctx.repo_root)):
        raise RevupUsageException(
            "Not in a rebase. Start an edit session with: revup edit <topic>"
        )

    # Validation: no unresolved conflicts
    if await has_unmerged_files(git_ctx):
        raise RevupUsageException(
            "Unresolved conflicts. Fix them first:\n"
            "  1. Edit conflicting files\n"
            "  2. git add <files>\n"
            "  3. revup edit --commit"
        )

    # Detect state and dispatch to appropriate handler
    state = await detect_edit_state(git_ctx)

    if not state.should_amend and not state.has_staged:
        return handle_drop_commit(git_ctx, state)
    elif state.should_amend:
        return handle_amend_and_continue(git_ctx, state)
    else:
        return handle_continue_only(git_ctx, state)


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

