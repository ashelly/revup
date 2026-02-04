"""
Shell completion scripts for revup.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import List


COMPLETION_MARKER = "# revup bash completion"


def get_parser_options(parser: argparse.ArgumentParser) -> List[str]:
    """
    Extract long-form options from an argparse parser.

    Args:
        parser: An ArgumentParser instance

    Returns:
        List of long-form option strings (e.g., ['--help', '--commit'])
    """
    options = []
    for action in parser._actions:
        for opt in action.option_strings:
            if opt.startswith("--"):
                options.append(opt)
    return options


def get_subparsers_dict(main_parser: argparse.ArgumentParser) -> dict:
    """Extract subparsers dict from main parser."""
    for action in main_parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def has_topic_argument(parser: argparse.ArgumentParser) -> bool:
    """Check if parser has a positional argument with 'topic' in the name."""
    for action in parser._actions:
        if not action.option_strings and "topic" in action.dest.lower():
            return True
    return False


def get_positional_choices(parser: argparse.ArgumentParser) -> List[str]:
    """Get choices from the first positional argument that has them."""
    for action in parser._actions:
        if not action.option_strings and action.choices:
            return list(action.choices)
    return []


def get_topic_taking_options(parser: argparse.ArgumentParser) -> tuple[list[str], list[str]]:
    """
    Get options that take topic names as values.

    Identifies options where:
    - dest contains 'topic' (like --as with dest='reorder_topics')
    - dest is 'relative' (the --relative flag for amend)

    Returns a tuple of (single_value_options, multi_value_options).
    - single_value_options: options that take exactly one topic (nargs=None, "?", or 1)
    - multi_value_options: options that take multiple topics (nargs="+" or "*")
    """
    single_opts = []
    multi_opts = []
    for action in parser._actions:
        if not action.option_strings:
            continue
        # Check if this option takes topic values
        dest = action.dest.lower()
        if "topic" in dest or dest == "relative":
            # Categorize by how many values it accepts
            if action.nargs in ("+", "*"):
                multi_opts.extend(action.option_strings)
            elif action.nargs is None or action.nargs in ("?", 1):
                single_opts.extend(action.option_strings)
    return single_opts, multi_opts


# =============================================================================
# Bash Script Generation Helpers
# =============================================================================
# These functions return bash script fragments. The _bash_ prefix indicates
# they produce shell code, not Python logic.


def _bash_complete_topics() -> str:
    """Return bash snippet to complete topic names."""
    return '''local topics
            topics=$(revup toolkit list-topics 2>/dev/null)
            COMPREPLY=($(compgen -W "$topics" -- "$cur"))'''


def _bash_complete_words(words: str) -> str:
    """Return bash snippet to complete from a word list."""
    return f'COMPREPLY=($(compgen -W "{words}" -- "$cur"))'


def _bash_complete_options(opts: str) -> str:
    """Return bash snippet to complete options when cur starts with -."""
    return f'''if [[ "$cur" == -* ]]; then
                COMPREPLY=($(compgen -W "{opts}" -- "$cur"))
            fi'''


def _bash_complete_options_or_topics(opts: str) -> str:
    """Return bash snippet to complete options or topic names."""
    return f'''if [[ "$cur" == -* ]]; then
                COMPREPLY=($(compgen -W "{opts}" -- "$cur"))
            else
                {_bash_complete_topics()}
            fi'''


def _bash_complete_options_or_choices(opts: str, choices: str) -> str:
    """Return bash snippet to complete options or fixed choices."""
    return f'''if [[ "$cur" == -* ]]; then
                COMPREPLY=($(compgen -W "{opts}" -- "$cur"))
            else
                COMPREPLY=($(compgen -W "{choices}" -- "$cur"))
            fi'''


def _bash_check_single_topic_opts(pattern: str) -> str:
    """Return bash snippet checking $prev for single-value topic options."""
    return f'''# Check if previous word is a single-value topic option
            case "$prev" in
                {pattern})
                    {_bash_complete_topics()}
                    return
                    ;;
            esac'''


def _bash_check_multi_topic_opts(pattern: str) -> str:
    """Return bash snippet checking prior words for multi-value topic options."""
    # Note: uses {{}} for literal braces in f-string
    return f'''# Check if any prior word is a multi-value topic option (like --as)
            local j
            for ((j=i+1; j < cword; j++)); do
                case "${{words[j]}}" in
                    {pattern})
                        {_bash_complete_topics()}
                        return
                        ;;
                    -*)
                        break  # Hit another option, stop looking
                        ;;
                esac
            done'''


def _bash_case_entry(cmd: str, body: str) -> str:
    """Return a formatted case entry for a subcommand."""
    return f"        {cmd})\n{body}\n            ;;"


def _build_subcommand_case(
    cmd_name: str,
    opts: str,
    nested_subs: dict,
    pos_choices: list[str],
    single_topic_opts: list[str],
    multi_topic_opts: list[str],
    has_topic_positional: bool,
) -> str:
    """Build the case statement body for a single subcommand."""
    # Case 1: Has nested subcommands (like toolkit)
    if nested_subs:
        nested_cmds = " ".join(nested_subs.keys())
        body = f"            {_bash_complete_words(nested_cmds)}"
        return _bash_case_entry(cmd_name, body)

    # Build topic option checks if any exist
    checks = []
    if single_topic_opts:
        checks.append(_bash_check_single_topic_opts("|".join(single_topic_opts)))
    if multi_topic_opts:
        checks.append(_bash_check_multi_topic_opts("|".join(multi_topic_opts)))
    checks_block = "\n".join(f"            {check}" for check in checks)

    # Case 2: Has positional topic argument
    if has_topic_positional:
        completion = _bash_complete_options_or_topics(opts)
        body = f"{checks_block}\n            {completion}" if checks_block else f"            {completion}"
        return _bash_case_entry(cmd_name, body)

    # Case 3: Has topic-taking options but no positional topics (like restack --as)
    if single_topic_opts or multi_topic_opts:
        completion = _bash_complete_options(opts)
        body = f"{checks_block}\n            {completion}" if checks_block else f"            {completion}"
        return _bash_case_entry(cmd_name, body)

    # Case 4: Has positional with fixed choices (like completion -> bash)
    if pos_choices:
        choices_str = " ".join(pos_choices)
        body = f"            {_bash_complete_options_or_choices(opts, choices_str)}"
        return _bash_case_entry(cmd_name, body)

    # Case 5: Default - just complete options
    body = f"            {_bash_complete_options(opts)}"
    return _bash_case_entry(cmd_name, body)


def get_bash_completion_script(main_parser: argparse.ArgumentParser) -> str:
    """Return a bash completion script for revup, with options derived from argparse."""
    subparsers = get_subparsers_dict(main_parser)
    global_opts = " ".join(get_parser_options(main_parser))
    subcommands = " ".join(subparsers.keys())

    # Build case statements for each subcommand
    case_statements = []
    for cmd_name, parser in subparsers.items():
        single_topic_opts, multi_topic_opts = get_topic_taking_options(parser)
        case_statements.append(_build_subcommand_case(
            cmd_name=cmd_name,
            opts=" ".join(get_parser_options(parser)),
            nested_subs=get_subparsers_dict(parser),
            pos_choices=get_positional_choices(parser),
            single_topic_opts=single_topic_opts,
            multi_topic_opts=multi_topic_opts,
            has_topic_positional=has_topic_argument(parser),
        ))

    return _bash_completion_template(subcommands, global_opts, "\n".join(case_statements))


def _bash_completion_template(subcommands: str, global_opts: str, cases_block: str) -> str:
    """Return the full bash completion script with the given parameters."""
    return f'''
_revup_completions() {{
    local cur prev words cword
    _init_completion || return

    local subcommands="{subcommands}"
    local global_opts="{global_opts}"

    # Find the subcommand (skip options)
    local cmd=""
    local i
    for ((i=1; i < cword; i++)); do
        case "${{words[i]}}" in
            -*)
                ;;
            *)
                cmd="${{words[i]}}"
                break
                ;;
        esac
    done

    # If no subcommand yet, complete subcommands and global options
    if [[ -z "$cmd" ]]; then
        if [[ "$cur" == -* ]]; then
            COMPREPLY=($(compgen -W "$global_opts" -- "$cur"))
        else
            COMPREPLY=($(compgen -W "$subcommands" -- "$cur"))
        fi
        return
    fi

    # Subcommand-specific completions
    case "$cmd" in
{cases_block}
    esac
}}

complete -F _revup_completions revup
'''


def main(args: argparse.Namespace, main_parser: argparse.ArgumentParser) -> int:
    """Main entry point for the completion command."""
    if args.install:
        return install_bash_completion(main_parser)
    else:
        print(get_bash_completion_script(main_parser))
        return 0


def install_bash_completion(main_parser: argparse.ArgumentParser) -> int:
    """Install bash completion by caching script to a file and sourcing from rc."""
    default_cache = os.path.expanduser("~/.config/revup/completion.bash")
    default_rc = os.path.expanduser("~/.bashrc")

    # Prompt for cache file location
    cache_file = input(f"Enter path for completion script cache [{default_cache}]: ").strip()
    if not cache_file:
        cache_file = default_cache

    cache_path = Path(os.path.expanduser(cache_file))

    # Prompt for rc file
    rc_file = input(f"Enter path to bash rc file [{default_rc}]: ").strip()
    if not rc_file:
        rc_file = default_rc

    rc_path = Path(os.path.expanduser(rc_file))

    # Check if rc file exists
    if not rc_path.exists():
        create = input(f"{rc_path} does not exist. Create it? [y/N]: ").strip().lower()
        if create != "y":
            print("Aborted.")
            return 1
        rc_path.parent.mkdir(parents=True, exist_ok=True)
        rc_path.touch()

    # Generate and write completion script to cache file
    script = get_bash_completion_script(main_parser)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(script)
    print(f"Completion script written to {cache_path}")

    # Check if completion is already installed in rc
    rc_content = rc_path.read_text()
    source_line = f'source "{cache_path}"'

    if COMPLETION_MARKER in rc_content:
        # Already has revup completion - update the source line
        lines = rc_content.splitlines()
        new_lines = []
        i = 0
        while i < len(lines):
            if lines[i].strip() == COMPLETION_MARKER:
                # Found marker, replace the next line (the source/eval line)
                new_lines.append(lines[i])
                i += 1
                if i < len(lines):
                    # Skip old source/eval line, add new one
                    new_lines.append(source_line)
                    i += 1
            else:
                new_lines.append(lines[i])
                i += 1

        rc_path.write_text("\n".join(new_lines) + "\n")
        print(f"Updated completion source line in {rc_path}")
    else:
        # Add new completion setup
        completion_block = f'\n{COMPLETION_MARKER}\n{source_line}\n'
        with open(rc_path, "a") as f:
            f.write(completion_block)
        print(f"Revup completion installed to {rc_path}")

    print(f"Run 'source {rc_path}' or start a new shell to enable completions.")
    print("To update completions after reinstalling revup, run 'revup completion bash --install' again.")
    return 0
