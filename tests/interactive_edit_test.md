# Interactive Test Plan for `revup edit` State Tracking

Manual tests to verify the edit state tracking functionality works correctly.

## Setup: Create a test repo with multiple topics

```bash
# Create test repo
rm -rf /tmp/test-revup
mkdir /tmp/test-revup && cd /tmp/test-revup
git init -b main
git config user.email "test@test.com"
git config user.name "Test"

# Initial commit (this becomes the base on main branch)
echo "base" > file.txt
git add file.txt
git commit -m "initial"

# Create a fake "origin" so revup can find origin/main
git remote add origin .
git fetch origin

# Topic A commit
echo "topic-a" > file.txt
git add file.txt
git commit -m "Topic A change

Topic: topic-a"

# Topic B commit (will conflict with A if rebased)
echo "topic-b" > file.txt
git add file.txt
git commit -m "Topic B change

Topic: topic-b"
```

**Note:** If your git version doesn't support `-b main`, use:
```bash
git init
git checkout -b main  # or: git branch -m master main
```

---

## Test 1: Basic Edit Flow (Happy Path)

```bash
# Start editing topic-a
revup edit topic-a

# Expected: Stops at topic-a commit, shows confirmation message
# Make a change
echo "edited-a" > file.txt
git add file.txt

# Continue
revup edit -c

# Expected prompt: "Stopped on topic 'topic-a' commit"
# Expected action: "Proceed to AMEND this commit and continue? [Y/n]"
# Press Y - should amend and continue to topic-b edit point
```

**Expected Result:** Amends the topic-a commit and continues. If topic-b also has edit markers, stops there next.

---

## Test 2: Conflict During Continue

```bash
# Start fresh - reset to initial state
git reset --hard HEAD~2

# Recreate commits with conflict potential
echo "topic-a-v2" > file.txt && git add . && git commit -m "A

Topic: topic-a"

echo "topic-b-v2" > file.txt && git add . && git commit -m "B

Topic: topic-b"

# Start editing topic-a
revup edit topic-a

# Make change that will conflict with topic-b
echo "changed-a-conflicts" > file.txt
git add file.txt

# Continue
revup edit -c
# Press Y to amend

# Expected: Rebase continues, hits CONFLICT when applying topic-b
# Fix conflict:
echo "resolved" > file.txt
git add file.txt

# Continue again
revup edit -c

# Expected prompt: "Not on a topic commit (likely conflict resolution)"
# Expected action: "Proceed to CONTINUE without amending? [Y/n]"
# Press Y - should continue WITHOUT amending
```

**Expected Result:** Detects that we're on a non-topic commit (the conflicting commit being cherry-picked) and continues without amending.

---

## Test 3: Abort Detection (State Auto-Cleanup)

```bash
revup edit topic-a
# Make changes
echo "change" > file.txt
git add file.txt

# Abort using git directly (not revup)
git rebase --abort

# Verify state is cleaned up
ls .git/rebase-merge/  # Should not exist or error "No such file"
```

**Expected Result:** The `.git/rebase-merge/` directory (and our state file within it) is deleted by git when the rebase is aborted.

---

## Test 4: User Declines Confirmation

```bash
revup edit topic-a
echo "change" > file.txt
git add file.txt

revup edit -c
# At prompt, press 'n'
```

**Expected Result:** 
- Message: "Aborted. Run 'revup edit --commit' again or 'git rebase --abort'."
- Returns without making any changes
- Still in rebase state (can retry or abort)

---

## Test 5: Unresolved Conflicts Error

```bash
# After hitting a conflict during rebase, DON'T resolve it
# (don't run git add on the conflicting files)

revup edit -c
```

**Expected Result:**
```
Unresolved conflicts. Fix them first:
  1. Edit conflicting files
  2. git add <files>
  3. revup edit --commit
```

---

## Test 6: No State File (Manual Rebase)

```bash
# Start a manual rebase (not through revup edit)
GIT_SEQUENCE_EDITOR='sed -i s/pick/edit/' git rebase -i HEAD~1

# Try using revup edit -c
revup edit -c
```

**Expected Result:**
- Prompt: "No revup edit state found"
- Action: "Proceed to CONTINUE without amending? [Y/n]"
- Falls back to safe behavior (just continue)

---

## Test 7: Empty Conflict Resolution (Skip)

```bash
# Setup: Create commits where conflict resolution results in no changes
git reset --hard HEAD~2

echo "same" > file.txt && git add . && git commit -m "A

Topic: topic-a"

echo "same" > file.txt && git add . && git commit -m "B

Topic: topic-b"

# Now create a conflict that resolves to same content
git checkout -b conflict-branch HEAD~1
echo "same" > file.txt && git add . && git commit -m "Conflict commit"
git checkout -

# Start editing topic-a
revup edit topic-a
# Make a change that keeps the file the same
echo "same" > file.txt
git add file.txt
revup edit -c  # Amend (no actual change)

# If rebase hits a conflict that resolves to no changes:
# Resolve by keeping same content
echo "same" > file.txt
git add file.txt
revup edit -c

# Expected prompt: "No changes after conflict resolution."
# Expected action: "Skip this commit? [Y/n]"
# Press Y - should use `git rebase --skip`
```

**Expected Result:** Detects no staged changes after conflict resolution and offers to skip instead of failing with "No changes - did you forget to use 'git add'?"

---

## Test 8: Multiple Topic Commits

```bash
# Setup: Create topic with 2 commits
git reset --hard HEAD~2

echo "a1" > file.txt && git add . && git commit -m "First A commit

Topic: topic-a"

echo "a2" > file.txt && git add . && git commit -m "Second A commit

Topic: topic-a"

# Edit topic-a (should stop at both commits)
revup edit topic-a

# First stop - make change and continue
echo "edited-a1" > file.txt
git add file.txt
revup edit -c  # Should amend first commit

# Second stop - make change and continue  
echo "edited-a2" > file.txt
git add file.txt
revup edit -c  # Should amend second commit
```

**Expected Result:** Both commits in the topic are marked for edit, and both prompt for amend.

---

## Verification Checklist

| Scenario | Expected Behavior | Pass? |
|----------|-------------------|-------|
| Stopped on topic commit | Prompts "AMEND this commit" | |
| Stopped on non-topic (conflict) | Prompts "CONTINUE without amending" | |
| No state file | Prompts "CONTINUE without amending" | |
| User declines (n) | Aborts, no changes made | |
| Unresolved conflicts | Error message with instructions | |
| `git rebase --abort` | State file auto-cleaned | |
| Multiple topic commits | Each prompts for amend | |
| Empty conflict resolution | Prompts "Skip this commit?" and uses `--skip` | |
| Topic commit + no changes | Warns "No staged changes" but still amends | |

---

## Notes

- The user confirmation prompt is temporary (for testing). It will be removed once the state tracking is verified to work correctly.
- State is stored in `.git/rebase-merge/revup-edit-topic` which is automatically cleaned up by git when the rebase ends.

