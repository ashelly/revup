# revup edit Requirements

## Overview

`revup edit <topic>` allows editing files at a specific topic commit, even when those files have been modified by later commits in the stack. This document outlines all typical user paths and expected behaviors.

## Stack Structure Example

```
HEAD
  ↑
commit D (Topic: feature-d)  -- modifies file.txt
  ↑
commit C (Topic: feature-c)  -- modifies file.txt
  ↑
commit B (Topic: feature-b)  -- TARGET: user wants to edit this
  ↑
commit A (Topic: feature-a)
  ↑
origin/main
```

---

## User Paths

### Path 1: Happy Path - Edit with File Changes

**Scenario**: User edits topic-b, modifies files, no conflicts with later commits.

1. `revup edit feature-b`
2. Git rebases, stops at commit B with "edit" action
3. User modifies files
4. User runs `git add <files>`
5. User runs `revup edit -c`
6. Git amends commit B and continues rebase
7. Commits C and D replay cleanly
8. Done

**Expected behavior at step 5**:
- Detect: on topic commit, has staged changes
- Action: amend and continue
- Outcome: success

---

### Path 2: Happy Path - Edit Commit Message Only

**Scenario**: User wants to fix a typo in commit B's message, no file changes.

1. `revup edit feature-b`
2. Git stops at commit B
3. User runs `revup edit -c` (no file changes, nothing staged)
4. Git opens editor for commit message
5. User edits message, saves
6. Rebase continues

**Expected behavior at step 3**:
- Detect: on topic commit, NO staged changes
- Action: warn "No staged changes to amend. Did you forget to git add?"
- Prompt: "Continue anyway? [y/N]"
- If Y: proceed to amend (opens editor for message)
- If N: abort, let user stage files

---

### Path 3: Conflict on Later Commit - Successful Resolution

**Scenario**: User edits topic-b, changes conflict with commit C.

1. `revup edit feature-b`
2. Git stops at commit B
3. User modifies `file.txt`
4. User runs `git add file.txt`
5. User runs `revup edit -c`
6. Git amends B, continues, **stops at C with conflict**
7. User edits `file.txt` to resolve conflict markers
8. User runs `git add file.txt`
9. User runs `revup edit -c`
10. Git continues, commit D replays
11. Done

**Expected behavior at step 9**:
- Detect: NOT on original topic commit (conflict resolution), has staged changes
- Message: "Stopped at commit for 'feature-c', 1 commit(s) after 'feature-b'."
- Action: prompt "Proceed to CONTINUE without amending? [Y/n]"
- Outcome: staged resolution becomes part of commit C

---

### Path 4a: Conflict Resolution - Keep "Theirs" (Original Commit)

**Scenario**: Conflict resolved by keeping the incoming commit's version.

1. ... (steps 1-6 same as Path 3)
7. User resolves by keeping commit C's original version: `git checkout --theirs file.txt`
8. User runs `git add file.txt`
9. User runs `revup edit -c`

**Expected behavior at step 9**:
- Detect: NOT on topic, HAS staged changes (`git diff --cached` shows diff vs HEAD)
- Message: "Stopped at commit for 'feature-c', 1 commit(s) after 'feature-b'."
- Action: prompt "Proceed to CONTINUE without amending? [Y/n]"
- Outcome: commit C preserved with its original content

**Status**: ✅ Works correctly

---

### Path 4b: Conflict Resolution - Keep "Ours" (HEAD's Version)

**Scenario**: Conflict resolved by discarding the incoming commit's changes entirely.

1. ... (steps 1-6 same as Path 3)
7. User resolves by keeping HEAD's version: `git checkout --ours file.txt`
8. User runs `git add file.txt`
9. User runs `revup edit -c`

**Expected behavior at step 9**:
- Detect: NOT on topic, NO staged changes (`git diff --cached` is empty)
- Message: "Stopped at commit for 'feature-c', 1 commit(s) after 'feature-b'."
- Message: "No changes from previous commit after resolution."
- Action: prompt "Drop this commit? [Y/n]"
- If Y: commit C is dropped from history
- If N: user can reconsider

**Status**: ✅ Works correctly (both `--skip` and `--continue` drop the commit)

---

### Path 5: Conflict Resolution - Drop the Commit

**Scenario**: User decides the conflicting commit should be dropped entirely.

1. ... (steps 1-6 same as Path 3)
7. User decides commit C's changes are no longer needed
8. User runs `git reset HEAD` (or doesn't stage anything)
9. User runs `revup edit -c`

**Expected behavior at step 9**:
- Detect: NOT on topic, NO staged changes
- Message: "Stopped at commit for 'feature-c', N commit(s) after 'feature-b'."
- Message: "No changes from previous commit after resolution."
- Action: prompt "Drop this commit? [Y/n]"
- If Y: run `git rebase --skip`
- If N: abort, let user decide

---

### Path 6: Forgot to Resolve Conflict

**Scenario**: User runs `revup edit -c` without resolving conflicts.

1. ... (steps 1-6 same as Path 3)
7. User runs `revup edit -c` without touching conflicting files

**Expected behavior at step 7**:
- Detect: unmerged files exist (`git ls-files -u`)
- Action: error "Unresolved conflicts. Fix them first..."
- Outcome: user must resolve and stage

---

### Path 7: Resolved Conflict But Forgot to Stage

**Scenario**: User edits conflicting files but forgets `git add`.

1. ... (steps 1-6 same as Path 3)
7. User edits `file.txt` to remove conflict markers
8. User runs `revup edit -c` (forgot to `git add`)

**Expected behavior at step 8**:
- Detect: `git ls-files -u` still shows file as unmerged (index not updated)
- Action: error "Unresolved conflicts. Fix them first..."
- User must run `git add` to mark as resolved

**Status**: ✅ Works correctly - `has_unmerged_files()` catches this

---

### Path 8: Multiple Topic Commits

**Scenario**: Topic B has multiple commits, user edits all of them.

```
commit B2 (Topic: feature-b)  -- second commit in topic
commit B1 (Topic: feature-b)  -- first commit in topic
```

1. `revup edit feature-b`
2. Git stops at B1
3. User makes changes, stages, runs `revup edit -c`
4. Git amends B1, continues, **stops at B2**
5. User makes changes, stages, runs `revup edit -c`
6. Git amends B2, continues
7. Later commits replay
8. Done

**Expected behavior at step 5**:
- Detect: on topic commit (B2 is in topic_commits set), has staged changes
- Action: amend and continue

---

### Path 9: Abort Mid-Edit

**Scenario**: User starts edit, decides to cancel.

1. `revup edit feature-b`
2. Git stops at commit B
3. User runs `revup edit --abort` (or `git rebase --abort`)
4. Stack returns to original state

**Expected behavior**:
- Clean abort, no changes to history
- State file in `.git/rebase-merge/` auto-cleaned by git

---

### Path 10: Multiple Conflicts in Sequence

**Scenario**: Editing topic-b causes conflicts in both C and D.

1. `revup edit feature-b`
2. Stop at B, make changes, stage, `revup edit -c`
3. Conflict at C, resolve, stage, `revup edit -c`
4. Conflict at D, resolve, stage, `revup edit -c`
5. Done

**Expected behavior**: Each conflict stop correctly identified as "not on topic commit".

---

## State Detection Matrix

| Stopped at | has_staged | has_unmerged | Expected Action |
|------------|------------|--------------|-----------------|
| Topic commit | Yes | No | Amend and continue |
| Topic commit | No | No | Warn "forgot git add?", confirm, then amend (message only) |
| Topic commit | * | Yes | Error: resolve conflicts first |
| Other commit | Yes | No | Show "Stopped at {topic}, N commits after {original}", continue |
| Other commit | No | No | Show "Stopped at {topic}, N commits after {original}", prompt to drop |
| Other commit | * | Yes | Error: resolve conflicts first |

**Note**: "Other commit" with `has_staged=No` can happen when:
- User resolves conflict by keeping HEAD's version (`git checkout --ours`)
- User intentionally wants to drop the commit
- Both result in the commit being removed from history

---

## Open Questions

1. **Path 4**: What does `git diff --cached --quiet` return when staged content equals HEAD exactly?
   - **Answer**: Returns 0 (no changes). This happens with `git checkout --ours`.
   - Both `git rebase --continue` and `git rebase --skip` work - commit is dropped.
   - Current code asks "Skip this commit?" which is accurate.

2. **Path 7**: After editing a conflicting file but not staging, is it still marked as unmerged in the index?
   - **Answer**: YES. `git ls-files -u` still shows unmerged entries.
   - Current `has_unmerged_files()` check catches this correctly. ✅

3. **Working tree changes**: Should we warn if there are unstaged modifications in the working tree?
   - **Answer**: No

4. **Edge case**: What if user stages unrelated files during conflict resolution?
   - **Answer**: Allow without comment

---

## TODO

- [x] Test Path 4 behavior with `git checkout --theirs` → staged changes detected ✅
- [x] Test Path 4 behavior with `git checkout --ours` → no changes, prompts to skip ✅
- [x] Test Path 7 behavior - edit conflict markers without staging → unmerged detected ✅
- [x] Document expected vs actual behavior for each path
