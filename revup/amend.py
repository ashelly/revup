import argparse
import asyncio
import logging
import os
import re
import shlex
import subprocess

from typing import Optional

from revup import git, topic_stack
from revup.relative_utils import update_relative_in_message, update_topic_relative_in_stack
from revup.topic_stack import RE_TAGS, TAG_RELATIVE, is_ancestor
from revup.types import (
    CommitHeader,
    GitConflictException,
    GitTreeHash,
    RevupConflictException,
    RevupUsageException,
)

RE_TOPIC_WITH_MODIFIERS = re.compile(r"(?P<topic>[a-zA-Z\-_0-9]+)(?P<modifiers>[\^~]+[0-9]*)?")

CLEANUP_SCISSOR_LINE = r"------------------------ >8 ------------------------"
CLEANUP_SCISSOR_COMMENT = """Do not modify or remove the line above.
Everything below it will be ignored."""
CLEANUP_STRIP_COMMENT = """Please enter the commit message for your changes. Lines starting
with '{}' will be ignored, and an empty message aborts the amend."""


async def get_staged_files(git_ctx: git.Git) -> list[str]:
    """Get list of staged file paths."""
    output = await git_ctx.git_stdout("diff", "--cached", "--name-only")
    return [f for f in output.strip().split("\n") if f]


def run_commit_message_script(
    script_path: str,
    topic: str,
    relative: bool | str,
    draft: bool,
    commit_type: Optional[str],
    scope: Optional[str],
    staged_files: list[str],
    repo_root: str,
) -> Optional[str]:
    """
    Run custom commit message script, return message or None on failure.

    Args:
        script_path: Path to script (absolute or relative to repo_root)
        topic: Topic name for the commit
        relative: False, True (auto-detect), or explicit topic name
        draft: Whether to mark as draft
        commit_type: Conventional commit type (feat, fix, etc.)
        scope: Conventional commit scope
        staged_files: list of staged file paths
        repo_root: Repository root path

    Returns:
        Commit message string on success, None on failure (to fall back to template)
    """
    # Resolve script path
    if not os.path.isabs(script_path):
        script_path = os.path.join(repo_root, script_path)

    if not os.path.isfile(script_path):
        logging.debug(f"Commit message script not found: {script_path}")
        return None

    # Build command arguments
    cmd = [script_path, "--topic", topic]

    if relative:
        if isinstance(relative, str):
            cmd.extend(["--relative", relative])
        else:
            cmd.append("--relative")

    if draft:
        cmd.append("--draft")

    if commit_type:
        cmd.extend(["--type", commit_type])

    if scope:
        cmd.extend(["--scope", scope])

    # Add file list after --
    if staged_files:
        cmd.append("--")
        cmd.extend(staged_files)

    try:
        result = subprocess.run(
            cmd,
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            message = result.stdout.strip()
            if message:
                return message
            logging.warning("Commit message script returned empty output, using template")
            return None
        else:
            logging.debug(f"Commit message script failed with code {result.returncode}")
            if result.stderr:
                logging.debug(f"Script stderr: {result.stderr}")
            return None
    except Exception as e:
        logging.debug(f"Failed to run commit message script: {e}")
        return None




async def invoke_editor_for_commit_msg(
    git_ctx: git.Git, editor: str, topic_summary: str, commit_msg: str, cache_stat: str, stat: str
) -> str:
    """
    Allow the user to modify the given commit msg by opening an editor.
    Stats for the commit are shown in comment lines in the editor.
    Return the final message with comment lines stripped out.
    """
    full_stat = []
    if cache_stat:
        full_stat.append(f"Changes to be committed:\n{cache_stat}")
    if stat:
        full_stat.append(f"Original commit:\n{stat}")
    stat_text = "\n\n".join(full_stat)

    # Respect the configured option for commit msg cleanup.
    cleanup_ret, cleanup_type = await git_ctx.git("config", "commit.cleanup", raiseonerror=False)
    if cleanup_ret != 0:
        # git's default if the message is being edited (which if we've reached this it is)
        cleanup_type = "strip"

    # Respect the configured comment character
    comment_ret, comment_char = await git_ctx.git("config", "core.commentChar", raiseonerror=False)
    if comment_ret != 0:
        comment_char = "#"

    comments = f"{topic_summary}\n{stat_text}"
    if cleanup_type == "scissors":
        comments = f"\n{CLEANUP_SCISSOR_LINE}\n{CLEANUP_SCISSOR_COMMENT}\n{comments}"
    elif cleanup_type == "strip":
        comments = f"\n{CLEANUP_STRIP_COMMENT.format(comment_char)}\n{comments}"

    comments = "\n{} ".format(comment_char).join(comments.splitlines())

    with open(git_ctx.get_scratch_dir() + "/COMMIT_EDITMSG", mode="w") as temp_file:
        temp_file.write(f"{commit_msg}\n{comments}")

    subprocess.check_call((*shlex.split(editor), temp_file.name))
    with open(temp_file.name, "r") as editor_file:
        msg = editor_file.read()

    if cleanup_type == "strip":
        # Strip out comment lines
        msg = re.sub(r"^{}.*$\n?".format(comment_char), "", msg, flags=re.M)
    elif cleanup_type == "scissors":
        msg = msg.split(f"{comment_char} {CLEANUP_SCISSOR_LINE}")[0]

    if cleanup_type != "verbatim":
        # Match behavior of git, which will trim all trailing whitespace
        msg = re.sub(r"[ \t]+$", "", msg, flags=re.M)
        # collapse consecutive empty lines
        msg = re.sub(r"[\n]{3,}", "\n\n", msg)
        # and remove all leading and trailing whitespace and newlines
        msg = msg.strip()

    return msg


async def get_topic_summary(topics: topic_stack.TopicStack) -> str:
    await topics.populate_topics()

    if len(topics.topics) == 0:
        return ""

    topic_lines = "".join([f"  {topic}\n" for topic in reversed(topics.topics.keys())])
    return f"\nTopics found between HEAD and {topics.relative_branch}:\n{topic_lines}"


async def parse_ref_or_topic(
    ref_or_topic: str,
    args: argparse.Namespace,
    git_ctx: git.Git,
    topics: topic_stack.TopicStack,
) -> str:
    """
    Parse and return the hash of the commit that is referred to by the given topic or commit-ish.
    """
    if args.parse_refs:
        if await git_ctx.is_branch_or_commit(ref_or_topic):
            return ref_or_topic

    if args.parse_topics:
        match = RE_TOPIC_WITH_MODIFIERS.match(ref_or_topic)
        if match:
            topic = match.group("topic")
            modifiers = match.group("modifiers") or ""

            await topics.populate_topics()

            if topic in topics.topics:
                ref = topics.topics[topic].original_commits[-1].commit_id + modifiers
                if await git_ctx.is_branch_or_commit(ref):
                    return ref

    if args.parse_refs and args.parse_topics:
        raise RevupUsageException(f"{ref_or_topic} is not a valid topic, commit, or branch name!")
    elif args.parse_refs:
        raise RevupUsageException(f"{ref_or_topic} is not a valid commit or branch name!")
    elif args.parse_topics:
        raise RevupUsageException(f"{ref_or_topic} is not a valid topic!")
    else:
        # It might make more sense to check this above, but if we do mypy thinks we've forgotten a
        # return.
        raise RevupUsageException("Can't have both --no-parse-refs and --no-parse-topics!")


async def build_commit_template(
    topic_name: str,
    relative: bool | str,
    commit: str,
    git_ctx: git.Git,
    topics: topic_stack.TopicStack,
) -> str:
    """Build commit message template with Topic: and optionally Relative:/Label: tags.

    Args:
        topic_name: The topic name for the new commit
        relative: False to skip, True to auto-detect, or a string topic name to use explicitly
        commit: The commit being inserted after (used for auto-detection)
        git_ctx: Git context
        topics: Topic stack for looking up topics
    """
    template_lines = ["Feature: <feature description>", "", f"Topic: {topic_name}"]

    if relative:
        await topics.populate_topics()
        if isinstance(relative, str):
            # Explicit topic name provided - validate it exists
            if relative not in topics.topics:
                available = ", ".join(topics.topics.keys()) if topics.topics else "(none found)"
                raise RevupUsageException(
                    f"Relative topic '{relative}' not found.\nAvailable topics: {available}"
                )
            template_lines.append(f"Relative: {relative}")
        else:
            # Auto-detect from the commit being inserted after
            commit_id = await git_ctx.git_stdout("rev-parse", commit)

            for topic in topics.topics.values():
                for c in topic.original_commits:
                    if c.commit_id == commit_id:
                        template_lines.append(f"Relative: {topic.name}")
                        break
                else:
                    continue
                break

    return "\n".join(template_lines)


def validate_amend_arguments(args: argparse.Namespace) -> None:
    """Validate argument combinations for amend/commit commands."""
    if args.drop and args.insert:
        raise RevupUsageException("Doesn't make sense to drop and insert")
    if args.topic and args.cmd != "commit":
        raise RevupUsageException("--topic is only valid for 'revup commit'")
    if args.cmd == "commit" and args.relative and not args.topic:
        raise RevupUsageException("--relative requires --topic for 'revup commit'")


async def resolve_target_commit(
    args: argparse.Namespace, git_ctx: git.Git, topics: topic_stack.TopicStack
) -> str:
    """Resolve ref_or_topic to commit hash, or return HEAD if not specified."""
    if not args.ref_or_topic:
        return "HEAD"
    commit = await parse_ref_or_topic(args.ref_or_topic, args, git_ctx, topics)
    if not await git_ctx.is_ancestor(f"{commit}~", "HEAD"):
        raise RevupUsageException(
            "Specified commit is not a first parent ancestor of HEAD"
            if commit == args.ref_or_topic
            else (
                f"Commit ({commit}, from topic {args.ref_or_topic}) is not a first parent"
                " ancestor of HEAD"
            )
        )
    return commit


async def get_commit_stack(git_ctx: git.Git, commit: str) -> list[CommitHeader]:
    """Get the stack of commits from HEAD to the target commit."""
    stack = git.parse_rev_list(
        await git_ctx.rev_list(
            "HEAD", f"{commit}~", header=True, first_parent=True, exclude_first_parent=True
        )
    )
    if len(stack) == 0:
        raise RevupUsageException(f"Couldn't find any commits between HEAD and {commit}~")
    return stack


async def prepare_insert_commit(
    args: argparse.Namespace, git_ctx: git.Git, topics: topic_stack.TopicStack,
    stack: list[CommitHeader], commit: str
) -> None:
    """Prepare stack[0] for inserting a new commit."""
    stack[0].parents = [stack[0].commit_id]
    stack[0].author_name = stack[0].author_email = stack[0].author_date = ""
    stack[0].committer_name = stack[0].committer_email = stack[0].committer_date = ""

    if args.topic:
        commit_msg = None
        if args.commit_message_script:
            staged_files = await get_staged_files(git_ctx)
            commit_msg = run_commit_message_script(
                script_path=args.commit_message_script, topic=args.topic,
                relative=args.relative,
                commit_type=getattr(args, "type", None), scope=getattr(args, "scope", None),
                staged_files=staged_files, repo_root=git_ctx.repo_root,
            )
        if commit_msg is None:
            commit_msg = await build_commit_template(
                args.topic, args.relative, args.draft, commit, git_ctx, topics
            )
        stack[0].commit_msg = commit_msg
    else:
        stack[0].commit_msg = ""


def validate_relative_topic_name(args: argparse.Namespace) -> str:
    """Extract and validate relative topic name from args."""
    relative_topic = args.relative if isinstance(args.relative, str) else None
    if relative_topic is None:
        raise RevupUsageException(
            "--relative requires an explicit topic name for 'revup amend'\n"
            "Usage: revup amend --relative OTHER_TOPIC TOPIC_TO_AMEND"
        )
    return relative_topic


async def validate_relative_topic_exists(
    relative_topic: str, topics: topic_stack.TopicStack
) -> None:
    """Ensure the relative topic exists in the topic stack."""
    await topics.populate_topics()
    if relative_topic not in topics.topics:
        available = ", ".join(topics.topics.keys()) if topics.topics else "(none found)"
        raise RevupUsageException(
            f"Relative topic '{relative_topic}' not found.\nAvailable topics: {available}"
        )


def find_topic_for_commit(
    commit: str, topics: topic_stack.TopicStack
) -> Optional[topic_stack.Topic]:
    """Find which topic contains the given commit."""
    for t in topics.topics.values():
        if any(c.commit_id == commit for c in t.original_commits):
            return t
    return None


async def extract_descendant_from_chain(
    target_topic: topic_stack.Topic, new_relative_topic: topic_stack.Topic,
    git_ctx: git.Git, stack: list[CommitHeader]
) -> None:
    """Extract a descendant topic from the chain to prevent cycles.
    
    When setting A's relative to B, but B is currently below A in the chain,
    we first need to "extract" B by setting B's relative to A's current relative.
    """
    gca = target_topic.relative_topic
    logging.info(
        f"Extracting '{new_relative_topic.name}' from below '{target_topic.name}' "
        f"(setting its relative to '{gca.name if gca else 'base branch'}')"
    )
    if not update_topic_relative_in_stack(new_relative_topic, gca, stack, prompt=False):
        return
    # Rewrite commits with updated messages
    new_commit = stack[0].parents[0]
    for i, commit_obj in enumerate(stack):
        if i == len(stack) - 1:
            break
        tree = commit_obj.tree if commit_obj.tree else GitTreeHash(
            await git_ctx.git_stdout("rev-parse", f"{commit_obj.commit_id}^{{tree}}")
        )
        new_commit = await git_ctx.synthetic_amend(tree, commit_obj.commit_msg, new_commit)
    await git_ctx.git(
        "reset", "--soft", new_commit,
        env={"GIT_REFLOG_ACTION": "reset --soft (revup amend --relative extraction)"},
    )
    # Re-read the stack with updated commit messages
    new_stack = git.parse_rev_list(
        await git_ctx.rev_list(
            "HEAD", f"{new_commit}~{len(stack)}", header=True,
            first_parent=True, exclude_first_parent=True
        )
    )
    stack[:] = new_stack


async def handle_relative_update(
    args: argparse.Namespace, git_ctx: git.Git, topics: topic_stack.TopicStack,
    stack: list[CommitHeader], commit: str
) -> bool:
    """Update Relative: tag in target commit. Returns True if message changed."""
    relative_topic = validate_relative_topic_name(args)
    await validate_relative_topic_exists(relative_topic, topics)

    target_topic = find_topic_for_commit(commit, topics)
    new_relative_topic = topics.topics[relative_topic]

    # Prevent cycles: if new relative is below target, extract it first
    if target_topic and is_ancestor(target_topic, new_relative_topic):
        await extract_descendant_from_chain(target_topic, new_relative_topic, git_ctx, stack)

    original_msg = stack[0].commit_msg
    updated_msg = update_relative_in_message(stack[0].commit_msg, relative_topic)
    if updated_msg is None:
        raise RevupUsageException("User cancelled relative tag update")
    stack[0].commit_msg = updated_msg
    return updated_msg != original_msg


async def edit_commit_message(
    args: argparse.Namespace, git_ctx: git.Git, topics: topic_stack.TopicStack,
    stack: list[CommitHeader], commit: str, has_diff: bool
) -> Optional[str]:
    """Open editor for commit message. Returns new message or None if empty."""
    new_msg = await invoke_editor_for_commit_msg(
        git_ctx, git_ctx.editor,
        await get_topic_summary(topics) if args.parse_topics else "",
        stack[0].commit_msg,
        (await git_ctx.git_stdout("--no-pager", "diff", "--cached", "--stat", "--no-color")
         if has_diff else ""),
        ("" if args.insert else await git_ctx.git_stdout(
            "--no-pager", "diff", commit + "~", commit, "--stat", "--no-color")),
    )
    return new_msg if new_msg.strip() else None


async def rewrite_commits_with_diff(
    args: argparse.Namespace, git_ctx: git.Git, stack: list[CommitHeader]
) -> str:
    """Rewrite commits when there are staged changes. Returns new HEAD hash."""
    new_commit = stack[0].parents[0]
    if not args.drop:
        stack[-1].tree = GitTreeHash(await git_ctx.git_stdout("write-tree"))

    for i, commit_obj in enumerate(stack):
        if i == 0 and args.drop:
            continue
        elif i == 0 and len(stack) > 1:
            # Amend the first commit with cached changes
            temp_commit = CommitHeader(stack[-1].tree, [git.HEAD_COMMIT])
            temp_commit.title = temp_commit.commit_msg = "cached changes"
            temp_commit.commit_id = await git_ctx.commit_tree(temp_commit)
            stack[-1].tree = temp_commit.tree
            try:
                new_commit = await git_ctx.synthetic_amend(commit_obj, temp_commit)
            except GitConflictException as exc:
                await git_ctx.dump_conflict(exc)
                raise RevupConflictException(
                    temp_commit, commit_obj.commit_id,
                    "You may need to `git rebase -i` to resolve these conflicts!",
                ) from exc
        else:
            if i == len(stack) - 1 and not args.drop:
                new_commit = await git_ctx.cherry_pick_from_tree(commit_obj, new_commit)
            else:
                try:
                    new_commit = await git_ctx.synthetic_cherry_pick_from_commit(
                        commit_obj, new_commit)
                except GitConflictException as exc:
                    await git_ctx.dump_conflict(exc)
                    raise RevupConflictException(
                        commit_obj, new_commit,
                        "You may need to `git rebase -i` to resolve these conflicts!",
                    ) from exc
    return new_commit


async def rewrite_commits_message_only(git_ctx: git.Git, stack: list[CommitHeader]) -> str:
    """Rewrite commits when only the message changed (faster, reuses trees)."""
    new_commit = stack[0].parents[0]
    for stack_entry in stack:
        new_commit = await git_ctx.cherry_pick_from_tree(stack_entry, new_commit)
    return new_commit


async def finalize_amend(
    git_ctx: git.Git, stack: list[CommitHeader], new_commit: str, args: argparse.Namespace
) -> None:
    """Perform final soft reset and update reflog."""
    reflog_action_str = 'revup amend {}{}: "{}"'.format(
        "--drop " if args.drop else "--insert " if args.insert else "",
        stack[0].commit_id[:8], stack[0].commit_msg.splitlines()[0][:40],
    )
    await git_ctx.soft_reset(new_commit, {"GIT_REFLOG_ACTION": reflog_action_str})


async def main(args: argparse.Namespace, git_ctx: git.Git) -> int:
    """
    Amend the given commit and recreate the history on top of that commit to make
    a new head commit with the same tree as the cache. Then, soft reset to that commit.
    The result is that the given commit will be changed, but the cache and working
    tree will not be touched.
    """

    async def get_has_unstaged() -> bool:
        return args.all and await git_ctx.git_return_code("diff", "--no-renames", "--quiet") != 0

    has_staged, has_unstaged = await asyncio.gather(
        git_ctx.git_return_code("diff", "--cached", "--no-renames", "--quiet"),
        get_has_unstaged(),
    )

    args.edit = args.edit or args.insert
    has_diff = has_staged or has_unstaged or args.drop
    # Don't early return if --relative is set, as we need to process the tag update
    if not has_diff and not args.edit and not args.relative:
        return 0

    if args.drop and args.insert:
        raise RevupUsageException("Doesn't make sense to drop and insert")

    # --topic only works with 'commit' command, not 'amend'
    if args.topic and args.cmd != "commit":
        raise RevupUsageException("--topic is only valid for 'revup commit'")

    # For 'commit', --relative requires --topic
    if args.cmd == "commit" and args.relative and not args.topic:
        raise RevupUsageException("--relative requires --topic for 'revup commit'")

    if has_unstaged:
        await git_ctx.git("add", "--update")

    topics = topic_stack.TopicStack(
        git_ctx,
        args.base_branch,
        args.relative_branch,
        None,
        None,
    )
    if args.ref_or_topic:
        commit = await parse_ref_or_topic(args.ref_or_topic, args, git_ctx, topics)

        if not await git_ctx.is_ancestor(f"{commit}~", "HEAD"):
            raise RevupUsageException(
                "Specified commit is not a first parent ancestor of HEAD"
                if commit == args.ref_or_topic
                else (
                    f"Commit ({commit}, from topic {args.ref_or_topic}) is not a first parent"
                    " ancestor of HEAD"
                )
            )
    else:
        commit = "HEAD"

    stack = git.parse_rev_list(
        await git_ctx.rev_list(
            "HEAD", f"{commit}~", header=True, first_parent=True, exclude_first_parent=True
        )
    )
    if len(stack) == 0:
        raise RevupUsageException(f"Couldn't find any commits between HEAD and {commit}~")

    if args.insert:
        # Create a new empty commit after the given commit
        stack[0].parents = [stack[0].commit_id]
        # Clear commit specific fields
        stack[0].author_name = ""
        stack[0].author_email = ""
        stack[0].author_date = ""
        stack[0].committer_name = ""
        stack[0].committer_email = ""
        stack[0].committer_date = ""

        # Build commit message template if --topic was provided
        if args.topic:
            stack[0].commit_msg = await build_commit_template(
                args.topic, args.relative, commit, git_ctx, topics
            )
        else:
            stack[0].commit_msg = ""

    # Handle --relative for amend command (not insert/commit)
    # Track if --relative changed the message to prevent incorrect early return
    relative_changed_msg = False
    if args.relative and not args.insert:
        try:
            relative_changed_msg = await handle_relative_update(
                args, git_ctx, topics, stack, commit
            )
        except RevupUsageException as e:
            if "User cancelled" in str(e):
                return 1
            raise

    if args.edit and not args.drop:
        new_msg = await invoke_editor_for_commit_msg(
            git_ctx,
            git_ctx.editor,
            await get_topic_summary(topics) if args.parse_topics else "",
            stack[0].commit_msg,
            (
                await git_ctx.git_stdout("--no-pager", "diff", "--cached", "--stat", "--no-color")
                if has_diff
                else ""
            ),
            (
                ""
                if args.insert
                else await git_ctx.git_stdout(
                    "--no-pager", "diff", commit + "~", commit, "--stat", "--no-color"
                )
            ),
        )
        if len(new_msg.strip()) == 0:
            logging.info("Exited due to empty commit message.")
            return 1

        # Don't early return if --relative changed the message, as the change
        # still needs to be persisted to the git commit
        if stack[0].commit_msg == new_msg and not has_diff and not relative_changed_msg:
            return 0

        stack[0].commit_msg = new_msg

    if has_diff:
        new_commit = stack[0].parents[0]
        if not args.drop:
            stack[-1].tree = GitTreeHash(await git_ctx.git_stdout("write-tree"))
        for i, commit_obj in enumerate(stack):
            if i == 0 and args.drop:
                # Drop the target commit
                continue
            elif i == 0 and len(stack) > 1:
                # Perform an amend for the first commit, unless there's only one
                # in which case we can use the tree shortcut.
                temp_commit = CommitHeader(stack[-1].tree, [git.HEAD_COMMIT])
                temp_commit.title = temp_commit.commit_msg = "cached changes"
                temp_commit.commit_id = await git_ctx.commit_tree(temp_commit)
                # drop must be false, so this will be the result of write-tree from above
                stack[-1].tree = temp_commit.tree
                try:
                    new_commit = await git_ctx.synthetic_amend(commit_obj, temp_commit)
                except GitConflictException as exc:
                    await git_ctx.dump_conflict(exc)
                    raise RevupConflictException(
                        temp_commit,
                        commit_obj.commit_id,
                        "You may need to `git rebase -i` to resolve these conflicts!",
                    ) from exc
            else:
                if i == len(stack) - 1 and not args.drop:
                    # For the final commit (if drop isn't used) we can assume that
                    # the state is the exact same as the original cache, so we
                    # don't actually have to apply a patch.
                    new_commit = await git_ctx.cherry_pick_from_tree(commit_obj, new_commit)
                else:
                    try:
                        new_commit = await git_ctx.synthetic_cherry_pick_from_commit(
                            commit_obj, new_commit
                        )
                    except GitConflictException as exc:
                        await git_ctx.dump_conflict(exc)
                        raise RevupConflictException(
                            commit_obj,
                            new_commit,
                            "You may need to `git rebase -i` to resolve these conflicts!",
                        ) from exc
    else:
        # If there's no diff (only text changed), its much faster to use the same trees
        new_commit = stack[0].parents[0]
        for stack_entry in stack:
            new_commit = await git_ctx.cherry_pick_from_tree(stack_entry, new_commit)

    reflog_action_str = 'revup amend {}{}: "{}"'.format(
        "--drop " if args.drop else "--insert " if args.insert else "",
        stack[0].commit_id[:8],
        stack[0].commit_msg.splitlines()[0][:40],
    )
    git_env = {
        "GIT_REFLOG_ACTION": reflog_action_str,
    }
    await git_ctx.soft_reset(new_commit, git_env)
    return 0
