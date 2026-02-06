# New Features Guide

This guide covers the new tools and improvements recently added to revup.

## Table of Contents

1. [Shell Completion](#shell-completion-revup-completion)
2. [Topic Visualization](#topic-visualization-revup-tree)
3. [Pre-populated Commit Messages](#pre-populated-commit-messages-revup-commit---topic)
4. [Interactive Topic Editing](#interactive-topic-editing-revup-edit)
5. [Topic Reordering](#topic-reordering)
6. [Improved Error Messages](#improved-error-messages)

---

## Shell Completion (`revup completion`)

Revup now provides bash shell completion for commands, options, and topic names.

### Quick Install

```bash
revup completion bash --install
```

This interactive installer will:
1. Generate the completion script
2. Cache it to `~/.config/revup/completion.bash` (or your chosen path)
3. Add a source line to your `~/.bashrc` (or your chosen rc file)

After installing, start a new shell or run:

```bash
source ~/.bashrc
```

### Manual Setup

If you prefer manual setup:

```bash
# Add to your ~/.bashrc:
eval "$(revup completion bash)"
```

Or cache the script yourself:

```bash
revup completion bash > ~/.config/revup/completion.bash
echo 'source ~/.config/revup/completion.bash' >> ~/.bashrc
```

### What Gets Completed

- **Subcommands**: `revup <TAB>` shows all available commands
- **Global options**: `revup --<TAB>` shows global flags
- **Command options**: `revup upload --<TAB>` shows upload-specific flags
- **Topic names**: `revup edit <TAB>` completes topic names from your current branch
- **Nested commands**: `revup toolkit <TAB>` shows toolkit subcommands

### Examples

```bash
$ revup up<TAB>
upload

$ revup amend <TAB>
auth  database  api  feature-x

$ revup upload --<TAB>
--base-branch  --dry-run  --rebase  --skip-confirm  ...

$ revup toolkit <TAB>
cherry-pick  closest-branch  detect-branch  diff-target  fork-point  list-topics
```

### Updating Completions

After upgrading revup, regenerate completions:

```bash
revup completion bash --install
```

The installer detects existing completion setup and updates it in place.

---

## Topic Visualization (`revup tree`)

The `revup tree` command displays your topic dependency structure as an ASCII tree.

### Basic Usage

```bash
revup tree
```

### Output Format

Topics are displayed with their relationships:

```
* (a1b2c3d4) feature-c
+ (e5f6g7h8) feature-b
+ (i9j0k1l2) feature-a
```

For topics with multiple branches (fork points):

```
* (abc12345) leaf-topic-2
| * (def67890) leaf-topic-1
|/
+ (789abcde) shared-base
```

### Understanding the Output

- `*` marks the newest (top) topic in a chain
- `+` marks other topics in the chain
- `|` shows the dependency chain (child above, parent below)
- `/` shows where branches merge (multiple topics depend on one parent)
- Topics without `Relative:` tags appear as separate trees
- The commit hash prefix is shown in parentheses

---

## Pre-populated Commit Messages (`revup commit --topic`)

Create a new commit with a pre-populated message containing topic metadata tags.

The `revup commit` command inserts a new commit (like `revup amend --insert`) with a template message that includes `Topic:`, `Relative:`, and other tags based on the options you provide.

### New Options

| Option | Short | Description |
|--------|-------|-------------|
| `--topic NAME` | `-t NAME` | Pre-fill commit with `Topic: NAME` tag |
| `--relative [TOPIC]` | `-r [TOPIC]` | Add `Relative:` tag (auto-detect from insertion point, or specify explicitly) |
| `--draft` | | Add `Label: draft` tag, which forces the GitHub PR into draft state |
| `[REF_OR_TOPIC]` | | Insert after this commit/topic (defaults to HEAD) |

### Usage Examples

**Create a new commit with a topic:**

```bash
revup commit --topic my-feature
```

Opens your editor with a pre-filled template:

```
Feature: <feature description>

Topic: my-feature
```

**Create a commit with a relative dependency:**

```bash
revup commit --topic my-feature --relative [other-topic]
```

The `other-topic` argument is optional:
- If omitted, auto-detects from the insertion point (the topic of the commit you're inserting after)
- If specified, uses that topic explicitly regardless of insertion point

Template:

```
Feature: <feature description>

Topic: my-feature
Relative: other-topic  (or auto-detected topic)
```

**Create a draft commit:**

```bash
revup commit --topic my-feature --draft
```

Template:

```
Feature: <feature description>

Topic: my-feature
Label: draft
```

**Combine all options:**

```bash
revup commit --topic api-cleanup --relative base-api --draft
```

**Insert after a specific topic (not at HEAD):**

```bash
revup commit --topic new-feature --relative base-feature other-topic
```

This creates a commit with `Topic: new-feature` and `Relative: base-feature`, inserted AFTER the most recent commit of `other-topic`. This is useful when you want to insert a commit in the middle of your stack rather than at HEAD.

### Notes

- For `revup commit`: `--relative` and `--draft` require `--topic` to be specified
- `--relative` without an argument auto-detects from the commit being inserted after
- `--relative TOPIC` explicitly sets the relative topic

---

## Interactive Topic Editing (`revup edit`)

The `revup edit` command allows you to interactively edit commits within a topic, even when those files have been modified by subsequent commits in your stack.

### Why Use It?

When working with stacked commits, you may need to fix something in an earlier commit but the files have since been modified by later commits. Unlike `revup amend` (which works on the current state at HEAD), `revup edit` checks out the exact state at the topic commit so you can edit files as they were at that point in history.

### Basic Workflow

**1. Start an edit session:**

```bash
revup edit mytopic
```

Git stops at the first commit belonging to `mytopic`. You now see the files exactly as they existed at that commit.

**2. Make your changes:**

Edit the files as needed, then stage them with `git add`.

**3. Amend and continue:**

```bash
revup edit --commit
# or shorter:
revup edit -c
```

This amends the current commit with your changes and continues the rebase. If the topic has multiple commits, git stops at the next one.

**4. Repeat until complete.**

### Available Options

| Option | Short | Description |
|--------|-------|-------------|
| `<topic>` | | The topic name to edit (required to start) |
| `--commit` | `-c` | Amend current commit and continue rebase |
| `--abort` | `-a` | Cancel the edit session and restore original state |
| `--help` | `-h` | Show help |

### Example Session

```bash
# You have a stack with topics: auth, database, api
# You need to fix a typo in the 'auth' topic

$ revup edit auth
# Git stops at the first 'auth' commit

# Make your changes
$ vim src/auth/login.py
$ git add src/auth/login.py

# Amend and continue
$ revup edit --commit
# If 'auth' has more commits, git stops at the next one
# Otherwise, the rebase completes

# If something goes wrong, abort:
$ revup edit --abort
```

### Safety Features

- **Conflict detection**: If there are unresolved conflicts, `revup edit --commit` will prompt you to resolve them first.
- **Staged changes check**: Warns if you try to amend without staging any changes.
- **Off-topic commit handling**: When stopped at a commit outside your target topic (e.g., during conflict resolution), you'll be prompted to continue or drop.
- **State persistence**: The edit state is stored inside git's rebase directory, so it's automatically cleaned up when the rebase ends.

### Handling Merge Conflicts

When your changes conflict with later commits in the stack:

1. **Resolve the conflicts** in the affected files
2. **Stage the resolution**: `git add <files>`
3. **Continue**: `revup edit --commit`

`revup edit -c` detects which commit you're on and takes the appropriate action:

| Situation | What `edit -c` Does |
|-----------|---------------------|
| **Topic commit** | `git commit --amend` + `git rebase --continue` |
| **Off-topic commit** | `git rebase --continue` (or use git directly) |
| **No changes after resolution** | `git rebase --skip` (drops empty commit) |

Each case prompts for confirmation before proceeding. For off-topic commits, you can also use `git rebase --continue` directly.

If the state becomes untractable, you can always reset to the start with  `revup edit --abort`. 

### Tips

- You can still use standard git commands during an edit session:
  - `git rebase --continue` - Continue without amending
  - `git rebase --abort` - Cancel (same as `revup edit --abort`)
  - `git commit --amend` - Amend manually without continuing

---

## Topic Reordering

The `Relative:` tag in commit messages controls topic dependency ordering. There are two ways to modify these relationships.

### `revup amend --relative`

Add or update a single topic's `Relative:` tag:

```bash
revup amend --relative base_topic my_topic
```

This modifies `my_topic`'s commit message to add `Relative: base_topic`.

**Key behaviors:**

- Requires an explicit topic name
- Validates that the relative topic exists in the stack
- If the commit already has a different `Relative:` tag, prompts for confirmation

**Example workflow:**

```bash
# You have topics: auth, database, api
# You want api to depend on database instead of base

$ revup amend --relative database api
# Updates api's commit: adds "Relative: database"

$ revup restack
# Reorders commits so api is on top of database
```

### `revup restack --as`

For reordering multiple topics at once, use `restack --as` to update all their `Relative:` tags and restack in a single operation.

**Basic Usage:**

```bash
revup restack --as topic1 topic2 topic3
```

This creates a chain where:
- `topic1` depends on its existing ancestor (GCA) if one exists outside the reorder list, otherwise on base branch
- `topic2` depends on `topic1`
- `topic3` depends on `topic2`

**How It Works:**

1. **Validates** all specified topics exist
2. **Finds the GCA** (greatest common ancestor) - the point where the reordered chain should attach
3. **Updates `Relative:` tags** in each topic's commit message
4. **Rewrites commits** with updated messages
5. **Performs normal restack** to reorder the actual commits

**Example:**

```bash
# Current state (no dependencies):
# * api
# * database  
# * auth

$ revup restack --as auth database api

# After restack:
# * api          (Relative: database)
# |
# * database     (Relative: auth)
# |
# * auth         (no Relative - depends on base)
```

**Attachment Point (GCA):**

The first topic in the list attaches to its "greatest common ancestor" (GCA) - the closest ancestor topic that is NOT in the reorder list. If no such ancestor exists, it attaches to the base branch.

```bash
# Current: api -> middleware -> base
# Reorder: restack --as api  (just api, not middleware)
# Result: api -> middleware (GCA preserved, not base)
```

### Notes

- All topics must exist in the current stack
- Topics not mentioned in `--as` keep their existing relationships
- The `restack --as` operation is performed before the normal restack, so everything is consistent

---

## Improved Error Messages

### Remote Not Found

If your repository doesn't have the expected remote configured, revup now provides a clear, actionable error message:

```
Remote 'origin' not found.
Revup requires a remote to determine the base branch.
```

This replaces the previous cryptic git errors that could occur when:
- Working in a fresh clone without remotes
- Having a differently-named remote (use `--remote-name` to specify)
- Working in a detached repository

### Resolution

```bash
# Check your remotes
git remote -v

# Add a remote if needed
git remote add origin git@github.com:user/repo.git

# Or specify a different remote name
revup --remote-name upstream upload
```

---

## Summary

| Feature | Command | Purpose |
|---------|---------|---------|
| Shell Completion | `revup completion bash --install` | Tab completion for bash |
| Topic Visualization | `revup tree` | Display topic dependency structure |
| Topic Templates | `revup commit --topic NAME` | Pre-fill commit messages |
| Relative Tags (auto) | `revup commit --topic NAME --relative` | Auto-detect dependency from insertion point |
| Relative Tags (explicit) | `revup commit --topic NAME --relative OTHER` | Explicitly set dependency topic |
| Interactive Edit | `revup edit <topic>` | Edit commits at their original state |
| Topic Reordering | `revup restack --as T1 T2 T3` | Bulk reorder topics into a chain |
| Amend Relative | `revup amend --relative OTHER TOPIC` | Add/update Relative: tag in existing commit |
| Draft Labels | `revup commit --topic NAME --draft` | Mark PRs as draft |
| Better Errors | (automatic) | Clearer remote configuration errors |

For detailed documentation on each command, use `revup <command> --help` or see the individual documentation files in the `docs/` directory.
