# NAME

revup edit - Interactively edit commits in a topic.

# SYNOPSIS

`revup [--verbose] [--keep-temp]`
: `edit [--help] [--commit] [--abort] <topic>`

# DESCRIPTION

Allows easy editing of files at a given topic commit, even if those
files have changed later in the stack. This is useful when you need
to fix something in an earlier commit but the file has since been
modified by subsequent commits.

This differs from `revup amend`, which works on the current state of
files at HEAD. With `revup edit`, git checks out the
exact state at the topic commit so you can edit files as they were.

Under the hood, this command uses `git rebase -i`, automatically
marking the topic's commits with the `edit` action while leaving
other commits as `pick`. When the rebase stops at a topic commit,
make your desired changes to the working directory. Then run
`revup edit --commit` to amend the current commit and continue.

Unlike `revup amend`, this command does modify the working directory
and cache, as it uses git's native rebase machinery.

# OPTIONS

**`<topic>`**
: The name of the topic to edit. All commits belonging to this topic
will be marked for editing. Required unless `--commit` is specified.

**--help, -h**
: Show this help page.

**--commit, -c**
: Amend the current commit and continue the rebase. Use this after
making changes during an edit session. This runs `git commit --amend`
followed by `git rebase --continue`.

**--abort, -a**
: Cancel the current edit session and restore the original state.
This runs `git rebase --abort`.

# WORKFLOW

1. Start an edit session:

: $ `revup edit mytopic`

2. Git stops at the first topic commit. Make your changes to files.

3. Stage your changes and amend:

: $ `revup edit --commit`

4. If the topic has multiple commits, git stops at the next one.
   Repeat steps 2-3 until the rebase completes.

# OTHER COMMANDS DURING EDIT

While stopped at an edit point, you can also use standard git commands:

**git rebase --continue**
: Continue without amending (skip this edit point).

**git commit --amend**
: Amend manually without continuing.

# EXAMPLES

Edit all commits in the topic `bugfix`:

: $ `revup edit bugfix`

After making changes, amend and continue:

: $ `revup edit --commit`

Or use the short form:

: $ `revup edit -c`

Cancel the edit session and restore original state:

: $ `revup edit --abort`

