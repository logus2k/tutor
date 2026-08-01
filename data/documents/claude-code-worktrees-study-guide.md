# Claude Code: Worktrees and Parallelism Reference

A study guide to running Claude Code sessions in parallel with git worktrees: creation and cleanup, subagent isolation, `.worktreeinclude`, what worktrees share with the main checkout, and replacing git with hooks for other VCS.

Verified against code.claude.com/docs/en/worktrees.

---

## 1. Where worktrees sit among the parallelism options

A git worktree is **a separate working directory with its own files and branch, sharing the same repository history and remote** as your main checkout.

The division of labor across Claude Code's parallelism mechanisms:

| Mechanism | What it provides |
|---|---|
| **Worktrees** | **File isolation.** Edits in one session never touch another's |
| **Subagents** | Coordinate the work itself, in separate context windows |
| **Agent teams** | Coordinate multiple Claude sessions |
| **Background sessions** | Detached execution, monitored via agent view |

Worktrees are the substrate the others build on when file conflicts matter. They are orthogonal to context isolation: **a subagent gets a fresh context window whether or not it gets a worktree.**

**Worktrees require git.** For other systems, `WorktreeCreate` and `WorktreeRemove` hooks replace the git logic. **In the desktop app, every new session gets its own worktree automatically.**

---

## 2. Creating

```bash
claude --worktree feature-auth      # or -w
```

Defaults: **`.claude/worktrees/<name>/` at your repository root, on a new branch named `worktree-<name>`.** Omitting the name generates one such as `bright-running-fox`.

Run it again with a different name in another terminal for a second isolated session.

**Interactive runs require workspace trust.** If you have not run Claude in the directory before, run `claude` once there to accept the trust dialog, **or `--worktree` exits with an error prompting you to**. **`-p` runs skip the trust check**, so `claude -p --worktree` proceeds.

**Add `.claude/worktrees/` to your `.gitignore`** so worktree contents do not appear as untracked files in your main checkout.

### Setting up the environment

**A worktree is a fresh checkout**, so initialize your dev environment there: ask Claude to install dependencies, or run setup yourself in the directory. For gitignored files, use `.worktreeinclude` (section 5).

### Asking Claude to create one

Ask Claude to "work in a worktree" and it uses the **`EnterWorktree`** tool. From inside a worktree, Claude can switch directly to another one under `.claude/worktrees/` by passing the target path; **the previous worktree stays on disk untouched.**

**Entering a path outside `.claude/worktrees/` requires your approval**, because it moves the session's working directory, write access, and project configuration such as CLAUDE.md and settings to that location.

**An `EnterWorktree` permission rule or "don't ask again" does not suppress this prompt. Only `bypassPermissions` mode skips it.** Before v2.1.206, Claude could enter any existing worktree path without asking.

---

## 3. Cleanup

On exiting an **interactive** worktree session, Claude checks for work removal would delete: **changed or untracked files, and new commits.**

| State | Behavior |
|---|---|
| **Clean, unnamed session** | Worktree and branch removed **automatically** |
| **Clean, named session** | **Prompts you first** so you can keep it |
| **Has work in it** | Prompts to keep or remove. **Removing deletes the directory and branch along with all the work** |

**`-p` runs have no exit prompt, so their worktrees are never cleaned up.** Remove them with `git worktree remove`.

### Windows link handling

Removing a worktree **does not delete files outside it**. If a folder inside the worktree is really a link elsewhere — an NTFS junction or directory symlink — **Claude Code deletes only the link and keeps the target.** Before v2.1.205, removing a worktree with a link nested in a subdirectory **could delete the folder it pointed to**.

---

## 4. Resuming a worktree session

**Resuming returns the session to its worktree.** This holds for interactive resumes, for `--continue` and `--resume` under `-p`, and for the Agent SDK. Claude can still leave with `ExitWorktree`.

Two exceptions:

- **`--fork-session` starts in the directory you launched from** and leaves the original's worktree untouched
- **If the worktree directory no longer exists**, the session resumes in the launch directory

### Transcript follows the working directory

As of v2.1.198, entering or exiting a **git-created** worktree relocates the session's transcript to the new working directory, **the same way `/cd` does**, so `/desktop` and `--resume` find it there. Exiting moves it back.

**A worktree created by a `WorktreeCreate` hook keeps its transcript at the launch directory.**

Before v2.1.212, a non-interactive resume stayed in the starting directory and `ExitWorktree` reported no active worktree session.

---

## 5. Customizing creation

### Base branch

**New worktrees branch from the repository's default branch.** Change with `worktree.baseRef`:

| Value | Meaning |
|---|---|
| `"fresh"` (default) | The **remote's** default branch, usually `main`, so the worktree starts from a clean tree matching the remote |
| `"head"` | Your current local `HEAD`, carrying unpushed commits and feature-branch state. **Inside a worktree, `"head"` resolves to that worktree's `HEAD`**, not the main checkout's |

**You cannot set `baseRef` to a branch name.** For a specific existing branch, create the worktree with git directly.

For `"fresh"`, Claude Code keeps `origin/HEAD` current: **when the repository has not been fetched in 24 hours it fetches the default branch, capped at five seconds**, falling back to the cached ref on failure. **With no remote configured, or no cached `origin/HEAD` that can be fetched, the worktree falls back to your local `HEAD`.**

### From a pull request

```bash
claude --worktree "#1234"
```

Fetches `pull/<number>/head` from `origin` and creates the worktree at `.claude/worktrees/pr-<number>`. **Quote the argument** so your shell does not treat `#` as a comment. A full GitHub PR URL also works.

### .worktreeinclude

A worktree is a fresh checkout, so untracked files like `.env` are absent. A `.worktreeinclude` file at your project root copies them in automatically.

```text
.env
.env.local
config/secrets.json
```

Uses `.gitignore` syntax. **Only files that match a pattern AND are also gitignored are copied**, so tracked files are never duplicated.

Applies to **every worktree Claude Code creates with git**: `--worktree`, subagent worktrees, and desktop parallel sessions. **With a `WorktreeCreate` hook it is not processed at all** — copy the files inside your hook script.

### Reusing a name

**Passing a name whose directory already exists opens that worktree instead of creating one.**

With the default `"fresh"` base, a reopened worktree **resets to the default branch** rather than continuing at its old tip when **all** of these hold:

- No uncommitted changes or untracked files
- Still on the branch Claude Code created for it
- **No commits of its own, or its PR was merged and its remote branch deleted**

Claude Code detects the merged case from git state alone: the remote branch it pushed to no longer exists, and every commit is already on the default branch.

**Everything else reopens at the old tip**: any failed condition, unverifiable state, and **any reuse when `baseRef` is `"head"` or the name is a PR number**. Before v2.1.208, a reused name always reopened at the old tip.

---

## 6. Subagent isolation

Ask Claude to "use worktrees for your agents", or make it permanent with `isolation: worktree`:

```markdown
---
name: refactorer
description: Applies mechanical refactors across many files
isolation: worktree
---

Apply the requested refactor across every affected file, then run the tests
and report the results.
```

**Each subagent gets a temporary worktree that Claude Code removes automatically when the subagent finishes without changes.** A worktree **with** changes stays on disk until the periodic sweep can remove it without losing work.

**Subagent worktrees use the same base branch as `--worktree`**, so they branch from the default branch unless `baseRef` is `"head"`.

### The periodic sweep

Removes worktrees Claude created **for subagents and background sessions** once they are older than `cleanupPeriodDays`.

Three rules that bound it:

- **It skips a worktree that still holds work**: changed or untracked files, or unpushed commits
- **It never removes worktrees you create with `--worktree`**
- **It never releases a lock you set yourself** with `git worktree lock`

### Locking

**While an agent runs, Claude runs `git worktree lock` on its worktree** so concurrent cleanup cannot remove it. The lock releases when the agent finishes.

As of v2.1.210, **the sweep also releases a lock set for a session whose process has exited**, so a killed background session does not leave its worktree permanently locked. Before that, you had to run `git worktree unlock` yourself.

To remove a worktree the sweep keeps: `git worktree remove`, adding `--force` for uncommitted changes or untracked files.

---

## 7. What worktrees share with the main checkout

A worktree gets its own files and branch, but **three things are shared**, and all three apply whether the worktree came from `--worktree`, `git worktree add`, or the desktop app.

### The `.git` directory

Git commands in a worktree **write to the main repository's shared `.git` directory**, and **sandboxing allows those writes**, so `git commit` works from inside a worktree with the sandbox enabled. (Writes to `hooks/` and `config` inside that directory remain denied.)

### Plugins (v2.1.200+)

**Plugins installed at project scope from the main checkout also load in worktrees of the same repository.** No reinstalling per worktree.

### Permission approvals (v2.1.211+)

**"Yes, don't ask again" for a Bash command in a worktree saves the rule to the main checkout's `.claude/settings.local.json`**, so it applies in the main checkout and every other worktree, **and survives the worktree's removal**.

Before v2.1.211, an approval granted in a worktree was saved inside that worktree, did not apply elsewhere, and **was lost when the worktree was removed** — a genuinely annoying failure mode if you are on an older version.

---

## 8. Manual worktrees

Use git directly when you need a specific existing branch or a location outside the repository.

```bash
git worktree add ../project-feature-a -b feature-a      # new branch
git worktree add ../project-bugfix fix-issue-456        # existing branch
cd ../project-feature-a && claude
git worktree list
git worktree remove ../project-feature-a
```

---

## 9. Non-git version control

Configure **`WorktreeCreate` and `WorktreeRemove` hooks** to provide custom creation and cleanup for SVN, Perforce, Mercurial, or anything else.

**Because the hook replaces the default git behavior, `.worktreeinclude` is not processed.** Copy local configuration files inside your hook script.

```json
{
  "hooks": {
    "WorktreeCreate": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash -c 'NAME=$(jq -r .name); DIR=\"$HOME/.claude/worktrees/$NAME\"; svn checkout https://svn.example.com/repo/trunk \"$DIR\" >&2 && echo \"$DIR\"'"
          }
        ]
      }
    ]
  }
}
```

**The hook must print the directory path on stdout** so Claude Code can use it as the working directory. Note the `>&2` on the checkout: everything except the path goes to stderr.

Recall from the hooks reference that **`WorktreeCreate` is the one event where any non-zero exit code aborts**, not just exit 2.

---

## 10. Troubleshooting

**Claude Code cannot enter the worktree at startup.** It prints an error naming the path and **exits with code 1**. Causes: a `WorktreeCreate` hook printing something other than the directory it created, or the directory being deleted after setup. Before v2.1.205 this **crashed the session**, and with `-p` it stalled ~30 seconds before exiting with code **0**.

**Creation fails on a symlinked path.** Claude Code **refuses** when `.claude`, `.claude/worktrees`, or the worktree directory itself is a symlink, naming the path. Remove the symlink and retry. Before v2.1.212, a committed symlink at one of those paths was followed and **could create files outside the repository**.

---

## 11. Choosing an approach

| Situation | Use |
|---|---|
| Two features in parallel, separate terminals | `--worktree` per session |
| One large change decomposed into independent units | `/batch`, which spawns one background subagent per unit in its own worktree |
| A subagent that always edits many files | `isolation: worktree` in its frontmatter |
| Research that should not pollute your context | A plain subagent; **no worktree needed** since nothing is written |
| A specific existing branch | `git worktree add` manually |
| Non-git VCS | `WorktreeCreate` / `WorktreeRemove` hooks |
| Desktop app | Automatic per session |

The rule of thumb: **worktrees are for write conflicts, subagents are for context.** Reach for a worktree when two things will edit the same files, not merely when two things run at once.

---

## 12. Gotchas

1. **Interactive `--worktree` requires workspace trust first**, and exits with an error otherwise. `-p` skips the check.
2. **Add `.claude/worktrees/` to `.gitignore`** or worktree contents show as untracked in your main checkout.
3. **A worktree is a fresh checkout.** Dependencies and env files are not there until you put them there.
4. **`.worktreeinclude` copies only files that are both matched and gitignored.**
5. **`.worktreeinclude` is ignored entirely when a `WorktreeCreate` hook is configured.**
6. **`-p` runs never clean up their worktrees.** Remove them manually.
7. **Removing a worktree with work in it deletes the branch and the work.**
8. **The periodic sweep never touches `--worktree` worktrees**, only subagent and background-session ones.
9. **The sweep skips worktrees holding work**, so "it did not clean up" often means "there were uncommitted changes."
10. **A killed background session used to leave a permanent lock.** Fixed in v2.1.210; earlier versions need `git worktree unlock`.
11. **`baseRef` cannot be a branch name.** Only `"fresh"` or `"head"`.
12. **`"fresh"` fetches `origin/HEAD` if stale**, capped at five seconds, and **falls back to your local `HEAD`** with no remote.
13. **Inside a worktree, `"head"` means that worktree's HEAD.**
14. **Quote `--worktree "#1234"`** or your shell eats it as a comment.
15. **Reusing a name reopens rather than creates**, and may reset to the default branch under a specific set of conditions.
16. **`EnterWorktree` outside `.claude/worktrees/` always prompts.** No permission rule suppresses it; only `bypassPermissions` does.
17. **Entering a worktree moves your project configuration too**, including CLAUDE.md and settings.
18. **The transcript follows a git worktree but not a hook-created one.**
19. **`--fork-session` on a worktree session starts in the launch directory instead.**
20. **Permission approvals now save to the main checkout** (v2.1.211+); on older versions they were lost with the worktree.
21. **Project-scope plugins load in worktrees** as of v2.1.200.
22. **Sandboxing explicitly permits writes to the shared `.git`**, so commits work from inside a sandboxed worktree.
23. **A symlink at `.claude`, `.claude/worktrees`, or the worktree path blocks creation** as of v2.1.212, and used to be followed.
24. **A `WorktreeCreate` hook must print the path and nothing else** on stdout, and **any non-zero exit aborts creation.**

---

## Reference links

- Worktrees: https://code.claude.com/docs/en/worktrees
- Run agents in parallel: https://code.claude.com/docs/en/agents
- Subagents: https://code.claude.com/docs/en/sub-agents
- Agent teams: https://code.claude.com/docs/en/agent-teams
- Agent view: https://code.claude.com/docs/en/agent-view
- Manage sessions: https://code.claude.com/docs/en/sessions
- WorktreeCreate hook: https://code.claude.com/docs/en/hooks
- Sandboxing, filesystem isolation: https://code.claude.com/docs/en/sandboxing
- Settings, worktree settings: https://code.claude.com/docs/en/settings
- Plugin installation scopes: https://code.claude.com/docs/en/plugins-reference
- Desktop parallel sessions: https://code.claude.com/docs/en/desktop
- Git worktree documentation: https://git-scm.com/docs/git-worktree
- Full docs index: https://code.claude.com/docs/llms.txt
