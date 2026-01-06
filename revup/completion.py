"""
Shell completion scripts for revup.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import List


COMPLETION_MARKER = "# revup bash completion"

# Commands that accept topic names as arguments
TOPIC_COMMANDS = {"edit", "amend", "upload"}


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


def get_bash_completion_script(main_parser: argparse.ArgumentParser) -> str:
    """Return a bash completion script for revup, with options derived from argparse."""
    subparsers = get_subparsers_dict(main_parser)

    # Get global options from main parser
    global_opts = " ".join(get_parser_options(main_parser))

    # Get subcommand names
    subcommands = " ".join(subparsers.keys())

    # Build case statements for each subcommand
    case_statements = []
    for cmd_name, parser in subparsers.items():
        opts = " ".join(get_parser_options(parser))

        if cmd_name in TOPIC_COMMANDS:
            # Commands that take topics: complete options OR topics
            case_statements.append(f'''        {cmd_name})
            if [[ "$cur" == -* ]]; then
                COMPREPLY=($(compgen -W "{opts}" -- "$cur"))
            else
                # Complete topic names
                local topics
                topics=$(revup toolkit list-topics 2>/dev/null)
                COMPREPLY=($(compgen -W "$topics" -- "$cur"))
            fi
            ;;''')
        elif cmd_name == "toolkit":
            # Toolkit has nested subcommands
            case_statements.append('''        toolkit)
            local toolkit_cmds="detect-branch cherry-pick diff-target fork-point closest-branch list-topics"
            COMPREPLY=($(compgen -W "$toolkit_cmds" -- "$cur"))
            ;;''')
        elif cmd_name == "completion":
            # Completion has shell choice and --install
            case_statements.append(f'''        completion)
            if [[ "$cur" == -* ]]; then
                COMPREPLY=($(compgen -W "{opts}" -- "$cur"))
            else
                COMPREPLY=($(compgen -W "bash" -- "$cur"))
            fi
            ;;''')
        else:
            # Other commands: just complete options
            case_statements.append(f'''        {cmd_name})
            if [[ "$cur" == -* ]]; then
                COMPREPLY=($(compgen -W "{opts}" -- "$cur"))
            fi
            ;;''')

    cases_block = "\n".join(case_statements)

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
