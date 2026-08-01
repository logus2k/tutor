# Claude Code: Sessions and Checkpointing Reference

A study guide to session persistence and recovery: resuming, naming, branching, the session picker, transcript storage, and the checkpoint system behind `/rewind`.

Verified against code.claude.com/docs/en/sessions and the checkpointing documentation.

---

## 1. What a session is

**A saved conversation tied to a project directory.** Stored locally and **saved continuously as you work**, so you can return after exiting or running `/clear`.

**The desktop app, Claude Code on the web, and the VS Code extension each maintain their own session history.** This guide covers the CLI.

Two distinct recovery mechanisms, easy to conflate:

| | Sessions | Checkpointing |
|---|---|---|
| **Persists** | The conversation | File snapshots |
| **Scope** | Across restarts | Within a session |
| **Recovery** | `--resume`, `/branch` | `/rewind` |

**Sessions persist the conversation, not the filesystem.** Checkpointing is the filesystem side.

---

## 2. Resuming

| Command | Effect |
|---|---|
| `claude --continue` | Most recent session in the current directory |
| `claude --resume` | Opens the session picker |
| `claude --resume <name>` | Resumes the named session directly |
| `claude --from-pr <number>` | Picker filtered to sessions linked to that PR |
| `/resume` | Switch conversations from inside an active session |

**Sessions created with `claude -p` or the Agent SDK do not appear in the picker.** You can still resume by passing the session ID, **run from the directory the session started in**: ID lookup is scoped to the current project directory and its git worktrees, and a session created elsewhere reports `No conversation found with session ID: <session-id>`.

### What a resume restores

| State | Restored? |
|---|---|
| **Conversation history** | Yes, in full, including tool calls and results |
| **Model** | Yes, **unless** retired, excluded by `availableModels`, overridden by `--model` or an `ANTHROPIC_MODEL`-family variable, or on a provider using deployment IDs (Bedrock, Agent Platform, Foundry) |
| **Agent** (`--agent`) | Yes, with its system prompt, tool restrictions, and model |
| **Permission mode** | Yes, **except `plan` and `bypassPermissions`, which are never restored** |
| **Active goal** | Yes, but **turn count, timer, and token-spend baseline reset** |
| **Scheduled tasks** | Unexpired ones yes. **Background Bash and monitor tasks, no** |

Two details worth pinning down:

- **`auto` mode is restored only if your account still meets the auto mode requirements**
- **Agent lookup checks two places** (v2.1.216+): the session's original directory, *provided you trusted that workspace*, then the directory you resume from. If found in neither, the session resumes with default tools and a warning naming the agent

### Flags that are not restored

**Pass these again on resume**: `--mcp-config`, `--settings`, `--plugin-dir`, `--fallback-model`, and `--add-dir` directories. **Directories added mid-session with `/add-dir` are not restored either**, though the picker still uses them to locate the session.

**Standard settings files are re-read at launch**, so anything living in `settings.json` needs no re-passing.

### Resume from a summary

On Pro or Max, resuming a session **inactive for more than about an hour and over 100,000 tokens** opens a dialog first. **The prompt cache has expired by then, so the next request processes the full history once regardless of your choice.**

| Option | Effect |
|---|---|
| **Resume from summary** | Runs `/compact` immediately. One summarization request over the full history, then history is replaced with the summary, your most recent exchanges, and **up to five recently read files**. Later requests carry the summary |
| **Resume full session as-is** | Loads unchanged. After your first message, Claude Code reprocesses and re-caches the full history, then re-reads from cache while it stays warm |
| **Don't ask me again** | Full resume, dialog suppressed on all future resumes |

The tradeoff: as-is keeps every detail at a per-request cost scaling with conversation size; from-summary costs less per request but **whatever the summary omits is gone from context**.

---

## 3. The session picker

### Where it looks

By default:

- Sessions from the **current worktree**, including background sessions marked `bg`
- Sessions started elsewhere that **added the current directory with `/add-dir`**

Widen with `Ctrl+W` (all worktrees of the repository) or `Ctrl+A` (every project on this machine).

**Sessions whose first prompt was `/loop` do not appear** (v2.1.211+). Running `/loop` later in a conversation does not hide it. Before v2.1.211, an early `/loop` hid the session **permanently**.

`/cd` **relocates a session to the new directory's project storage** (v2.1.169+), so it appears in that directory's picker afterward.

### Cross-directory behavior

| Selecting a session from | Result |
|---|---|
| Another worktree of the same repository | **Resumes in place** |
| An unrelated project | **Copies a `cd` and resume command to your clipboard** |

### Name resolution

Both forms **resolve across the current repository and its worktrees** and resume an exact match directly even in a different worktree. They differ on ambiguity:

| Command | Ambiguous name |
|---|---|
| `claude --resume <name>` | **Opens the picker with the name pre-filled** as a search term |
| `/resume <name>` | **Reports an error.** Run `/resume` with no argument for the picker |

### Shortcuts

| Key | Action |
|---|---|
| `↑` / `↓` | Navigate |
| `→` / `←` | Expand or collapse grouped sessions |
| `Enter` | Resume |
| `Space` | **Preview** the content. `Ctrl+V` also works where terminals capture Space as paste |
| `Ctrl+R` | Rename in place |
| `/` or any printable character | Search. **Paste a GitHub, GHE, GitLab, or Bitbucket PR/MR URL to find the session that created it** |
| `Ctrl+A` | All projects on this machine |
| `Ctrl+W` | All worktrees of this repository. **Only shown in multi-worktree repositories** |
| `Ctrl+B` | Filter to the current git branch |
| `Esc` | Exit picker or search |

Each row shows the name (or generated title, summary, or first prompt), time since activity, git branch, and file size. `Ctrl+A` also reveals each session's project path.

**`/branch` and `--fork-session` sessions get their own IDs and appear as separate rows.** Multiple entries for the same session group under one row; `→` expands.

**Failure behavior differs by entry point**: from `claude --resume`, a load failure prints `Failed to resume the conversation` and **exits with code 1**. From `/resume` inside a session, it reports the failure and **your current conversation keeps running**.

---

## 4. Naming

| When | How |
|---|---|
| At startup | `claude -n auth-refactor` |
| During a session | `/rename auth-refactor`, also shown on the prompt bar |
| From the picker | `Ctrl+R` |
| **On plan accept** | **Accepting a plan names the session from the plan content** unless you already set one |

### Three kinds of name, only one of which resumes

This is the distinction that trips people up:

1. **A name you set** with `--name`, `/rename`, or `Ctrl+R`. **The only resume handle.**
2. **A default display name** (v2.1.196+) for unnamed interactive sessions: the working directory's name plus a two-character suffix, e.g. `my-app-3f`. It identifies the session in agent view and `claude agents --json`. **Not a resume handle.**
3. **A generated session title**: a short summary of your first prompt, written by a background request to a Haiku-class model. Appears in the picker and the statusline `session_name` field when no name is set. **Not a resume handle.**

**`claude --resume <name>`, `/resume <name>`, and the picker match only names you set.** Naming replaces both the default and the generated title.

---

## 5. Branching

**Creates a copy of the conversation so far and switches you into it, leaving the original intact.**

```text
/branch try-streaming-approach
```

Omitting the name uses the first prompt in the conversation. As of v2.1.198 this **also works after compaction**, looking past the summary to the original first prompt; earlier versions fell back to the literal name `Branched conversation`.

From the CLI: `claude --continue --fork-session`.

**The `/branch` confirmation prints two session IDs**, the new branch and the original. The original is unchanged on disk and stays in the picker.

### What a branch inherits

The mechanism explains the behavior: **`/branch` copies the transcript and switches the running process to write to it.**

| State | After `/branch` |
|---|---|
| Conversation history | Copied up to the branch point |
| **"Allow for this session" grants** | **Carried over**, because the branch runs in the same process. **With `--fork-session` you get a separate process, which starts without them and you re-approve** |
| **In-flight background subagents and background Bash** | **Keep running, and their output appears in the new branch**, not the original |

That last row is the surprising one: backgrounded work follows the process, not the transcript it started in.

### The two-terminal warning

**Resuming the same session in two terminals without forking interleaves messages from both into one transcript.** Fork if you want parallel work on one starting point.

---

## 6. Managing context in-session

| Command | Effect |
|---|---|
| `/clear` | Empty context. **The previous conversation is saved**; resume with `/resume`, or in the same process from **the rewind menu's previous-session entry** (v2.1.191+). **You keep a name set with `--name` or `/rename`, but not a generated title** |
| `/compact [instructions]` | Replace history with a summary, optionally focused |
| `/context` | Show what is consuming context |

---

## 7. Checkpointing

**Automatic file snapshots taken before each edit, with every prompt you send creating a checkpoint you can return to.** No configuration required; it is on by default.

Open the menu with **`/rewind`, or press `Esc` twice** when the prompt input is empty.

### The restore options

| Option | Effect |
|---|---|
| **Restore code and conversation** | Full rollback to that point |
| **Restore conversation only** | Rewind history, **keep code changes** |
| **Restore code only** | Revert files, **keep the conversation** |
| **Summarize from here** | Compress context from that point |

Selecting a message also **restores its original prompt to the input field**, so you can edit and re-send.

### What is tracked

**Only changes made through `Write`, `Edit`, and `NotebookEdit`.**

On rewind, Claude Code **deletes files it created and restores modified files to their content at that point.**

### What is NOT tracked

This is the most misunderstood part of the feature, and the docs are emphatic:

- **Files modified by Bash commands.** `rm file.txt`, `mv old new`, `cp src dest`, `rm -rf dist/`, `find . -name "*.log" -delete` — **none of these can be undone through rewind**
- **External changes.** Edits you make in your own editor while a session runs
- **Concurrent sessions' edits**, unless they happen to touch the same files
- **Edits a subagent applies**, with one exception: **a skill with `context: fork` running in the foreground**

The logical extension: any side effect that is not a file edit through those three tools — directory creation or deletion, database state, migrations, package installs, network calls — is not reverted either.

**Checkpoints are session-level undo. Git is your permanent safety net.** Commit before risky work.

### Link handling (v2.1.216+)

Claude Code **skips a tracked path that is a symlink, hard link, or other non-regular file.** It also skips a tracked file whose parent directory no longer resolves to its checkpoint-time location, or whose backup it cannot read safely. Skipped paths are counted and reported.

**Before v2.1.216, a rewind wrote and deleted through links at tracked paths**, which could affect files outside the intended target.

### Retention

**Checkpoints are cleaned up with their session on the `cleanupPeriodDays` schedule, 30 days by default.** As of v2.1.117 that single setting governs four on-disk caches uniformly, not just checkpoints.

### From the SDK

```bash
claude -p --resume <session-id> --rewind-files <checkpoint-uuid>
```

With `--replay-user-messages`, **each user message in the response stream carries a UUID that serves as a checkpoint**. Capturing the first user message UUID and rewinding to it restores tracked files to their original state. **Conversation history and context remain intact** after a file rewind.

---

## 8. Transcript storage

Default location: **`~/.claude/projects/<project>/<session-id>.jsonl`**, where `<project>` is your working directory path with **non-alphanumeric characters replaced by `-`**. Each line is a JSON object for a message, tool use, or metadata entry.

**The entry format is internal and changes between versions.** Scripts parsing these files directly **can break on any release**.

### Configuration

| To | Set | Where |
|---|---|---|
| Move storage off `~/.claude` | `CLAUDE_CONFIG_DIR` | Environment |
| Change the 30-day retention | `cleanupPeriodDays` | `settings.json` |
| Suppress transcript writes in all modes | `CLAUDE_CODE_SKIP_PROMPT_HISTORY` | Environment |
| Suppress writes for one non-interactive run | `--no-session-persistence` | CLI flag with `-p` |

### The supported interfaces

`/export` opens a menu to copy the conversation to the clipboard or save it as plain text, with messages and tool outputs rendered readably. Pass a filename to skip the menu.

For scripts, pick by what triggers them:

| Trigger | Interface |
|---|---|
| **Run Claude once and capture the result** | `claude -p --output-format json` or `stream-json`: result, session ID, usage, cost |
| **Ask an existing session a question** | `claude -p --resume <id>` with a follow-up prompt |
| **React to session events** | The `transcript_path` field hooks and status line commands receive. **A `SessionEnd` hook can archive the transcript** |
| **Embed in an app** | The Agent SDK |

```bash
claude -p --resume <session-id> --output-format json "summarize what we changed" | jq -r '.result'
```

---

## 9. Gotchas

1. **Sessions persist the conversation; checkpointing persists files.** Two separate systems with separate recovery paths.
2. **`-p` and SDK sessions do not appear in the picker**, though you can resume them by ID.
3. **Session ID lookup is scoped to the current project and its worktrees.** Run it from where the session started.
4. **`plan` and `bypassPermissions` are never restored on resume.** Bypass must be re-enabled at launch.
5. **`--mcp-config`, `--settings`, `--plugin-dir`, `--fallback-model`, and `--add-dir` are not restored.** Settings files are re-read.
6. **`/add-dir` directories are not restored**, though the picker still uses them to find the session.
7. **A resumed goal keeps its condition but resets its counters.**
8. **Background Bash and monitor tasks do not survive a resume.**
9. **A default display name and a generated title are not resume handles.** Only a name you set is.
10. **Accepting a plan silently names the session** unless you already set a name.
11. **`/clear` keeps a name you set but discards a generated title.**
12. **`/branch` carries session permission grants; `--fork-session` does not**, because it is a separate process.
13. **Backgrounded work follows the process into the branch**, so its output lands in the new branch rather than the original.
14. **Two terminals on one session without forking interleave into one transcript.**
15. **`--resume <name>` opens the picker on ambiguity; `/resume <name>` errors instead.**
16. **A picker failure exits code 1 from the CLI but is non-fatal from `/resume`.**
17. **Sessions whose first prompt was `/loop` are hidden from the picker.**
18. **Selecting a session from an unrelated project copies a command rather than resuming.**
19. **The resume-from-summary dialog appears past ~1 hour idle and 100K tokens**, and the cache has already expired either way.
20. **Checkpointing tracks only Write, Edit, and NotebookEdit.** Bash file changes are unrecoverable through rewind.
21. **Subagent edits are not checkpointed**, except a foreground `context: fork` skill.
22. **External and concurrent-session edits are not captured** unless they touch the same files.
23. **Rewind skips symlinked and hard-linked tracked paths** as of v2.1.216, and reports the count.
24. **Checkpoints expire with their session**, default 30 days.
25. **The JSONL format is internal and changes between releases.** Use `/export`, `--output-format json`, or `transcript_path`.
26. **`<project>` in the transcript path replaces every non-alphanumeric character with `-`.**

---

## Reference links

- Manage sessions: https://code.claude.com/docs/en/sessions
- Checkpointing: https://code.claude.com/docs/en/checkpointing
- Agent SDK file checkpointing: https://code.claude.com/docs/en/agent-sdk/file-checkpointing
- Worktrees: https://code.claude.com/docs/en/worktrees
- Context window: https://code.claude.com/docs/en/context-window
- Non-interactive mode: https://code.claude.com/docs/en/headless
- Agent view: https://code.claude.com/docs/en/agent-view
- Permission modes: https://code.claude.com/docs/en/permission-modes
- Model configuration: https://code.claude.com/docs/en/model-config
- Scheduled tasks: https://code.claude.com/docs/en/scheduled-tasks
- Statusline: https://code.claude.com/docs/en/statusline
- Best practices: https://code.claude.com/docs/en/best-practices
- Full docs index: https://code.claude.com/docs/llms.txt
