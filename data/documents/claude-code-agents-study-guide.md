# Claude Code: Agents and Subagents Reference

A study guide to every agent mechanism in Claude Code: what each one is, when to use it, how to configure it, and how they differ.

Verified against the official documentation at code.claude.com/docs (sub-agents, agent-teams, agent-view, workflows, worktrees).

---

## 1. The landscape

Claude Code has **four ways to run agents** plus **two supporting mechanisms** that are often confused with them.

| Mechanism | What it is | Who orchestrates | Lives where |
|---|---|---|---|
| **Subagents** | Workers spawned inside one session | Claude, turn by turn | The current session |
| **Agent teams** | Peer Claude Code sessions coordinating | A lead session, turn by turn | Separate processes |
| **Agent view / background sessions** | Independent full sessions you dispatch | You | A supervisor process |
| **Dynamic workflows** | A JS script that spawns dozens-to-hundreds of subagents | The script | A workflow runtime |
| *Skills* (supporting) | Instructions Claude follows, no isolation | Claude | Main context |
| *Worktrees* (supporting) | Isolated git checkouts | N/A | Filesystem |

The critical distinction table from the docs:

|  | Subagents | Skills | Agent teams | Workflows |
|---|---|---|---|---|
| What it is | A worker Claude spawns | Instructions Claude follows | A lead supervising peer sessions | A script the runtime executes |
| Who decides what runs next | Claude, turn by turn | Claude, following the prompt | The lead agent, turn by turn | The script |
| Where intermediate results live | Claude's context window | Claude's context window | A shared task list | Script variables |
| What's repeatable | The worker definition | The instructions | The team definition | The orchestration itself |
| Scale | A few per turn | Same as subagents | A handful of long-running peers | Dozens to hundreds per run |
| Interruption | Restarts the turn | Restarts the turn | Teammates keep running | Resumable in the same session |

### Decision questions

1. **Do the workers need to talk to each other?** No, they just report back → subagents. Yes, they need to share findings and challenge each other → agent teams.
2. **Do you want to stay in the conversation?** Yes → subagents. No, dispatch and check back → agent view.
3. **Is the orchestration itself worth codifying and rerunning?** Yes → dynamic workflow.
4. **Do the tasks touch the same files?** Isolate with worktrees. Note that agent teams do **not** isolate teammates in worktrees; partition file ownership manually.
5. **Would a plain prompt or skill do the same job?** Then use that. Subagents are a heavy mechanism, costing a full context window and round-trip overhead.

---

## 2. Subagents: the core mechanism

A subagent is a separate Claude instance spawned by the Agent tool. It runs in its own context window with its own system prompt, tool access, and permissions, does its work in isolation, and returns **only a final summary** to the parent.

The point: keep the noisy middle of a task (file reads, test output, search results) out of your main conversation.

What subagents buy you:

- **Preserve context**: exploration and verbose output stay in the subagent
- **Enforce constraints**: limit which tools a subagent can use
- **Reuse configurations**: user-level subagents work across projects
- **Specialize behavior**: focused system prompts per domain
- **Control costs**: route tasks to cheaper models like Haiku

Note: the Task tool was renamed to **Agent** in v2.1.63. Existing `Task(...)` references still work as aliases.

### 2.1 Built-in subagents

| Agent | Model | Tools | Purpose |
|---|---|---|---|
| **Explore** | Inherits from main conversation, capped at Opus on the Claude API | Read-only; Write and Edit denied | File discovery, code search, codebase exploration |
| **Plan** | Inherits | Read-only | Codebase research during plan mode |
| **general-purpose** | Inherits | Every tool available to subagents | Complex multi-step work needing exploration and modification |
| statusline-setup | Sonnet | - | Invoked by `/statusline` |
| claude-code-guide | Haiku | - | Questions about Claude Code features |

Important behaviors:

- **Explore and Plan skip your CLAUDE.md files and git status** to keep research fast. Every other built-in and custom subagent loads both. There is no setting to change which agents skip them.
- When Claude invokes Explore it specifies a thoroughness level: quick, medium, or very thorough.
- Explore and Plan are **one-shot** and return no agent ID, so they cannot be resumed. Use general-purpose or a custom subagent when you need to continue.
- To keep Explore on a cheap model, define your own `Explore` subagent with `model: haiku`; a user or project subagent named `Explore` overrides the built-in.

Disabling built-ins:

```json
{
  "permissions": {
    "deny": ["Agent(Explore)", "Agent(my-custom-agent)"]
  }
}
```

- Deny the `Agent` tool entirely to prevent all delegation
- `CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS=1` removes only Explore and Plan
- `CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS=1` removes all built-ins in headless / SDK mode
- CLI equivalent: `claude --disallowedTools "Agent(Explore)"`

### 2.2 Where subagent definitions live

| Location | Scope | Priority |
|---|---|---|
| Managed settings `.claude/agents/` | Organization-wide | 1 (highest) |
| `--agents` CLI flag | Current session | 2 |
| `.claude/agents/` | Current project | 3 |
| `~/.claude/agents/` | All your projects | 4 |
| Plugin's `agents/` directory | Where plugin is enabled | 5 (lowest) |

Discovery mechanics worth memorising:

- Project subagents are found by **walking up from the cwd**, scanning every `.claude/agents/` up to the repo root. When nested directories define the same `name`, the definition **closest to the working directory** wins (v2.1.178+).
- Both project and user directories are scanned **recursively**, so `agents/review/security.md` works. Identity comes only from the `name` frontmatter field, not the path.
- Keep `name` values unique across the whole tree. Two files in the same directory with the same name means one is loaded arbitrarily by filesystem read order. `/doctor` reports duplicates.
- Plugin subfolders **do** become part of the identifier: `agents/review/security.md` in plugin `my-plugin` registers as `my-plugin:review:security`.
- `--add-dir` directories are also scanned for `.claude/agents/`.
- Names cannot contain `:` (reserved for plugin scoping) as of v2.1.218.

Hot reload: Claude Code watches `~/.claude/agents/` and `.claude/agents/` and picks up edits within seconds, no restart. Two exceptions need a restart: creating the **first** agent file in a directory that did not exist at session start, and sessions launched with `--disable-slash-commands`.

Plugin security restriction: plugin subagents **ignore** the `hooks`, `mcpServers`, and `permissionMode` fields. Copy the file into `.claude/agents/` if you need them.

### 2.3 File format

```markdown
---
name: code-reviewer
description: Reviews code for quality and best practices
tools: Read, Glob, Grep
model: sonnet
---

You are a code reviewer. When invoked, analyze the code and provide
specific, actionable feedback on quality, security, and best practices.
```

The body becomes the system prompt. Subagents receive **only** this plus basic environment details, not the full Claude Code system prompt.

### 2.4 Complete frontmatter reference

Only `name` and `description` are required.

| Field | Description |
|---|---|
| `name` | Lowercase letters and hyphens, unique. Hooks receive this as `agent_type`. The filename does not have to match. No `:` allowed |
| `description` | When Claude should delegate to this subagent. This is what drives auto-delegation |
| `tools` | Allowlist. Inherits everything available to subagents if omitted |
| `disallowedTools` | Denylist, removed from the inherited or specified list |
| `model` | `sonnet`, `opus`, `haiku`, `fable`, a full model ID, or `inherit`. Defaults to `inherit` |
| `permissionMode` | `default`, `acceptEdits`, `auto`, `dontAsk`, `bypassPermissions`, `plan`, or `manual` (alias for default) |
| `maxTurns` | Maximum agentic turns before the subagent stops |
| `skills` | Skills to preload. Full content injected at startup, not just the description |
| `mcpServers` | MCP servers for this subagent: names of configured servers, or inline definitions |
| `hooks` | Lifecycle hooks scoped to this subagent |
| `memory` | `user`, `project`, or `local`. Enables cross-session learning |
| `background` | `true` to always run as a background task. Unset means Claude chooses; as of v2.1.198 the default is background |
| `effort` | `low`, `medium`, `high`, `xhigh`, `max`. Overrides session effort |
| `isolation` | `worktree` runs the subagent in a temporary git worktree branched from your default branch |
| `color` | `red`, `blue`, `green`, `yellow`, `purple`, `orange`, `pink`, `cyan`. Display color in task list and transcript |
| `initialPrompt` | Auto-submitted first user turn when this agent runs as the **main** session agent |

### 2.5 Tool control

Two filters narrow what a subagent gets.

**Filter 1 (all subagents)** removes these regardless of the `tools` field:

`Agent` (at the depth limit), `AskUserQuestion`, `EndConversation`, `EnterPlanMode`, `ExitPlanMode` (unless `permissionMode: plan`), `ScheduleWakeup`, `TaskOutput`, `WaitForMcpServers`, `Workflow`

**Filter 2 (background subagents only)** keeps every MCP tool but only these built-ins:

`Read`, `Grep`, `Glob`, `Bash`, `PowerShell`, `Edit`, `Write`, `NotebookEdit`, `WebFetch`, `WebSearch`, `TodoWrite`, `Skill`, `ToolSearch`, `EnterWorktree`, `ExitWorktree`, `Monitor`, `TaskStop`, `SendMessage`, `Artifact`

Since background is now the default, **the same definition can resolve to different tools in the foreground and the background.** This is a common source of confusion. Forks skip both filters.

Agent team teammates additionally keep `TaskCreate`, `TaskGet`, `TaskList`, `TaskUpdate`, `CronCreate`, `CronDelete`, `CronList`.

Resolution order: `disallowedTools` is applied first, then `tools` is resolved against what remains. A tool in both is removed.

```yaml
---
name: safe-researcher
description: Research agent with restricted capabilities
tools: Read, Grep, Glob, Bash
---
```

```yaml
---
name: no-writes
description: Inherits the available tools except file writes
disallowedTools: Write, Edit
---
```

MCP server-level patterns work in both fields: `mcp__<server>` or `mcp__<server>__*` grants or removes every tool from that server. In `disallowedTools`, `mcp__*` removes every MCP tool from every server.

If nothing in `tools` resolves, Claude Code refuses to launch the subagent and the Agent tool returns an error naming the unresolved entries (v2.1.208+; earlier versions launched a toolless agent that returned confusing results).

**Restricting which subagents can be spawned** (only applies to an agent running as the main thread via `--agent`):

```yaml
tools: Agent(worker, researcher), Read, Bash
```

This is an allowlist. `Agent` without parentheses allows any. Omitting `Agent` entirely blocks spawning. Inside a *subagent* definition, listing `Agent` enables nesting but the type list in parentheses is ignored.

### 2.6 Model resolution

Order of precedence:

1. `CLAUDE_CODE_SUBAGENT_MODEL` environment variable
2. The per-invocation `model` parameter Claude passes
3. The definition's `model` frontmatter
4. The main conversation's model

Setting `CLAUDE_CODE_SUBAGENT_MODEL=inherit` is the same as leaving it unset (v2.1.196+).

Values are checked against the organization's `availableModels` allowlist; an excluded value is skipped and the inherited model is used.

As of v2.1.198, subagents **inherit the main conversation's extended thinking configuration**. There is no per-subagent thinking setting.

A common cost pattern:

```bash
export CLAUDE_CODE_SUBAGENT_MODEL="claude-sonnet-5"
claude --model claude-opus-5
```

Opus for coordination and judgment, Sonnet for implementation subagents, Haiku set per-agent in frontmatter for search.

### 2.7 Permission modes

| Mode | Behavior |
|---|---|
| `default` | Standard permission checking with prompts |
| `acceptEdits` | Auto-accept file edits and common filesystem commands in the working directory |
| `auto` | A background classifier reviews commands and protected-directory writes |
| `dontAsk` | Auto-deny prompts. Explicitly allowed tools still work |
| `bypassPermissions` | Skip permission prompts |
| `plan` | Read-only exploration |

Inheritance rules:

- If the parent uses `bypassPermissions` or `acceptEdits`, that **takes precedence and cannot be overridden**.
- If the parent uses auto mode, the subagent inherits auto mode and its `permissionMode` frontmatter is **ignored**.

`bypassPermissions` still prompts for explicit `ask` rules, connector tools your org set to `ask`, MCP tools marked `requiresUserInteraction`, and root/home removals like `rm -rf /`. It otherwise allows writes to `.git`, `.config/git`, `.claude`, `.vscode`, `.idea`, `.husky`, `.cargo`, `.devcontainer`, `.yarn`, and `.mvn`.

### 2.8 Scoping MCP servers to a subagent

```yaml
---
name: browser-tester
description: Tests features in a real browser using Playwright
mcpServers:
  # Inline: scoped to this subagent only
  - playwright:
      type: stdio
      command: npx
      args: ["-y", "@playwright/mcp@latest"]
  # Reference by name: reuses an already-configured server
  - github
---

Use the Playwright tools to navigate, screenshot, and interact with pages.
```

Inline servers connect when the subagent starts and disconnect when it finishes. String references share the parent session's connection.

**Key optimization**: defining a server inline here rather than in `.mcp.json` keeps its tool descriptions out of the main conversation's context entirely. The subagent gets the tools; the parent does not pay for them.

Managed MCP restrictions (`allowedMcpServers`, `deniedMcpServers`, `--strict-mcp-config`, `--bare`) apply to subagent-declared servers too as of v2.1.153, with one exception: `--strict-mcp-config` does not filter servers passed inline via `--agents` or the SDK, since those are explicit caller input.

### 2.9 Preloading skills

```yaml
---
name: api-developer
description: Implement API endpoints following team conventions
skills:
  - api-conventions
  - error-handling-patterns
---

Implement API endpoints. Follow the conventions and patterns from the preloaded skills.
```

This controls what is **preloaded**, not what is **accessible**. Without it, the subagent can still discover and invoke skills through the Skill tool. To block skills entirely, omit `Skill` from `tools` or add it to `disallowedTools`.

You cannot preload skills with `disable-model-invocation: true`, including the bundled `/verify` and `/code-review`.

This is the inverse of a skill's `context: fork`, which injects skill content into an agent you specify.

### 2.10 Persistent memory

```yaml
---
name: code-reviewer
description: Reviews code for quality and best practices
memory: project
---

You are a code reviewer. As you review code, update your agent memory with
patterns, conventions, and recurring issues you discover.
```

| Scope | Location | Use when |
|---|---|---|
| `user` | `~/.claude/agent-memory/<name>/` | Learnings apply across all projects |
| `project` | `.claude/agent-memory/<name>/` | Project-specific and shareable via git |
| `local` | `.claude/agent-memory-local/<name>/` | Project-specific but not committed |

`project` is the recommended default.

When memory is enabled:

- Memory read/write instructions are added to the system prompt
- The first 200 lines or 25KB of `MEMORY.md` (whichever comes first) is injected
- Read, Write, and Edit tools are automatically enabled

Subagent memory is part of auto memory. If `autoMemoryEnabled` is false or `CLAUDE_CODE_DISABLE_AUTO_MEMORY` is set, the `memory` field has **no effect at all**.

Usage tips: ask the subagent to consult its memory before starting ("check your memory for patterns you've seen before") and to update it after finishing. Or bake it into the system prompt:

```markdown
Update your agent memory as you discover codepaths, patterns, library
locations, and key architectural decisions. This builds up institutional
knowledge across conversations. Write concise notes about what you found
and where.
```

### 2.11 Hooks

Two places, two purposes.

**In frontmatter**: runs only while that subagent is active, cleaned up when it finishes.

```yaml
---
name: db-reader
description: Execute read-only database queries
tools: Bash
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "./scripts/validate-readonly-query.sh"
---
```

A `Stop` hook in frontmatter is automatically converted to `SubagentStop` at runtime when the agent runs as a subagent.

Frontmatter hooks from a **project-level** subagent require workspace trust for the containing folder (v2.1.218+). User-level and `--agents` definitions do not. Until trusted, the subagent runs but the hooks are skipped and an error goes to the debug log.

**In settings.json**: session-wide hooks that also fire inside subagents, plus two lifecycle events.

| Event | Matcher input | Fires |
|---|---|---|
| `SubagentStart` | Agent type name | When a subagent begins |
| `SubagentStop` | Agent type name | When a subagent completes |

```json
{
  "hooks": {
    "SubagentStart": [
      { "matcher": "db-agent", "hooks": [{ "type": "command", "command": "./scripts/setup-db-connection.sh" }] }
    ],
    "SubagentStop": [
      { "hooks": [{ "type": "command", "command": "./scripts/cleanup-db-connection.sh" }] }
    ]
  }
}
```

Matcher gotcha: hyphenated matchers like `db-agent` match exactly on v2.1.195+. On earlier versions they are unanchored regexes and also fire for `prod-db-agent`. Plugin-scoped names contain a colon and are always evaluated as unanchored regexes, so anchor them: `^my-plugin:db-agent$`.

The design principle to internalize: **`tools` is the coarse "which tools" control; hooks are the fine "which arguments" control.** Allow Bash but block writes, run a formatter after every edit, protect a path.

Example validation script (exit code 2 blocks and returns the message to Claude):

```bash
#!/bin/bash
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

if echo "$COMMAND" | grep -iE '\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE)\b' > /dev/null; then
  echo "Blocked: Only SELECT queries are allowed" >&2
  exit 2
fi

exit 0
```

Remember `chmod +x` on macOS and Linux, or the hook fails instead of blocking. On Windows, write it in PowerShell and add `shell: powershell` to the hook entry.

---

## 3. Invoking subagents

Four escalating patterns.

### Automatic delegation

Claude decides based on your request, the `description` field, and current context. Include "use proactively" in the description to encourage it.

### Natural language

```text
Use the test-runner subagent to fix failing tests
Have the code-reviewer subagent look at my recent changes
```

No special syntax; Claude typically delegates but is not forced to.

### @-mention (guarantees which subagent)

Type `@` and pick from the typeahead:

```text
@"code-reviewer (agent)" look at the auth changes
```

Manual form: `@agent-<name>`, or `@agent-my-plugin:code-reviewer` for plugin agents. While typing this form the typeahead shows files, but the mention still resolves on submit.

Important: the @-mention controls **which** subagent runs, not **what prompt it receives**. Claude still writes the task prompt.

### Session-wide (`--agent`)

```bash
claude --agent code-reviewer
claude --agent my-plugin:security-reviewer
claude --agent my-plugin:review:security
```

The main thread itself takes on that subagent's system prompt, tool restrictions, and model. The system prompt **replaces** the default Claude Code system prompt entirely, like `--system-prompt`. CLAUDE.md and project memory still load through the normal message flow. The agent name shows as `@<name>` in the startup header.

The choice persists across `--resume`. If the agent no longer exists on resume, the session continues with defaults and warns.

Project default:

```json
{ "agent": "code-reviewer" }
```

The CLI flag overrides the setting.

### CLI-defined (`--agents`)

Session-only, never written to disk. Useful for testing and automation.

```bash
claude --agents '{
  "code-reviewer": {
    "description": "Expert code reviewer. Use proactively after code changes.",
    "prompt": "You are a senior code reviewer. Focus on code quality, security, and best practices.",
    "tools": ["Read", "Grep", "Glob", "Bash"],
    "model": "sonnet"
  },
  "debugger": {
    "description": "Debugging specialist for errors and test failures.",
    "prompt": "You are an expert debugger. Analyze errors, identify root causes, and provide fixes."
  }
}'
```

Accepts the same fields as frontmatter, with `prompt` in place of the markdown body.

---

## 4. Foreground vs background

- **Foreground**: blocks the main conversation until complete. Permission prompts pass through to you directly.
- **Background**: runs concurrently while you keep working. Permission prompts surface in your main session, naming the asking subagent. Approve to continue, or Esc to deny that one call without stopping the subagent (v2.1.186+).

**As of v2.1.198, background is the default.** Claude runs a subagent in the foreground only when it needs the result before continuing. Remember the reduced background tool set from section 2.5.

Background results reach Claude as a **completion notification in a later turn**. Claude waits for that notification before reporting results; if you ask about progress first, it reports the subagent as still running (v2.1.211+).

Controls:

- Ask Claude to run something in the background or foreground
- `Ctrl+B` backgrounds a running task
- `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` disables background tasks entirely, and takes precedence over fork mode
- Completed background subagents stay listed in `/tasks`, marked done, until the session cleans up (v2.1.208+). Failed or stopped ones leave the list

### API errors

As of v2.1.199, a subagent cut off by an API error reports the failure rather than returning the error text as findings:

- **Foreground**: partial output is returned with a note that the subagent was cut off. A subagent that produced nothing (or only tool calls) fails with `Agent terminated early due to an API error`.
- **Background**: marked failed; the message to Claude names the error and includes the last output, so partial work is not lost.

---

## 5. Subagent context

### What loads at startup

Each subagent starts with a fresh, isolated context. It does **not** see your conversation history, previously invoked skills, or files Claude already read. Claude writes a delegation message summarizing the task.

A non-fork subagent's initial context contains:

- **System prompt**: its own prompt plus environment details, not the full Claude Code system prompt
- **Task message**: Claude's delegation prompt
- **CLAUDE.md hierarchy**: every level the main conversation loads, including `~/.claude/CLAUDE.md`, project rules, `CLAUDE.local.md`, and managed policy files. *Explore and Plan skip this*
- **Git status**: snapshot from the start of the parent session. Absent outside a git repo or when `includeGitInstructions` is false. *Explore and Plan skip this*
- **Preloaded skills**: full content of anything in the `skills` field
- **Sibling roster**: a system reminder listing `main` and every other named agent, each a valid `SendMessage` target (v2.1.206+). Appears only when `SendMessage` is in the tools and at least one other agent has a name. It is a snapshot taken at start, so later-named agents do not appear

### What never reaches a subagent

- **Output style**: a subagent runs its own system prompt, so your output style does not shape it (except in a fork)
- **Auto memory**: the main conversation's auto memory is not loaded. Use the `memory` field instead
- **Context window size**: sized by the subagent's own model, not the parent's. Delegating to a smaller model gives a smaller window

Practical consequence: the main conversation reads Explore and Plan results with full CLAUDE.md context, so most rules do not need to reach the subagent. If one must ("ignore the `vendor/` directory"), restate it in the delegation prompt.

### Resuming subagents

Each invocation creates a new instance with fresh context. To continue, ask Claude to resume it:

```text
Use the code-reviewer subagent to review the authentication module
[completes]

Continue that code review and now analyze the authorization logic
[Claude resumes with full prior context]
```

Mechanics:

- Claude uses `SendMessage` with the agent's ID or name as `to`. This does not require agent teams to be enabled; only structured team-protocol messages do
- Resumed subagents retain full history: all previous tool calls, results, and reasoning
- A completed subagent that receives a `SendMessage` **auto-resumes in the background**, no new `Agent` invocation
- A subagent **you** stopped (with `x` in `/tasks` or an SDK `stop_task`) does not auto-resume; `SendMessage` returns a refusal. Type into its transcript to clear the stop (v2.1.191+)
- `SendMessage` verifies a name still refers to the same agent it reached earlier. If a re-spawned agent took the name, the send is refused and the error names what the name now reaches (v2.1.199+). Address by agent ID to reach the original
- A subagent treats messages from its launching agent as normal task direction, including mid-task corrections. Two hard limits: no agent message counts as your permission approval, and no agent message can change permission settings, CLAUDE.md, or configuration

Transcripts live at `~/.claude/projects/{project}/{sessionId}/subagents/agent-{agentId}.jsonl`. They persist independently: main-conversation compaction does not affect them, they survive a restart within the same session, and they are deleted after `cleanupPeriodDays`.

### Auto-compaction

Subagents auto-compact using the same logic as the main conversation. `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` applies. Compaction is logged in the transcript:

```json
{
  "type": "system",
  "subtype": "compact_boundary",
  "compactMetadata": { "trigger": "auto", "preTokens": 167189 }
}
```

---

## 6. Forks

A fork is a subagent that **inherits the entire conversation** instead of starting fresh. It drops input isolation but keeps output isolation: the fork's tool calls stay out of your conversation and only its final result comes back.

Start one with `/subtask` (v2.1.212+; it was `/fork` on v2.1.161 through v2.1.211):

```text
/subtask draft unit tests for the parser changes so far
```

| | Fork | Named subagent |
|---|---|---|
| Context | Full conversation history | Fresh, with the prompt you pass |
| System prompt and tools | Same as main session | From its definition, filtered for background |
| Model | Same as main session | From its `model` field |
| Permissions | Prompts surface in your terminal | Prompts surface in your main session |
| Prompt cache | **Shared with main session** | Separate cache |

The shared prompt cache makes forking meaningfully cheaper than a fresh subagent for tasks needing the same context.

Use a fork when a named subagent would need too much background to be useful, or when you want to try several approaches in parallel from the same starting point.

Fork specifics:

- Forks skip both tool filters and receive the main conversation's exact tool pool
- A fork cannot spawn further forks
- `CLAUDE_CODE_FORK_SUBAGENT=1` enables fork mode explicitly, `0` disables it everywhere including server-side rollout. With it on, **every** subagent runs in the background and the `background` frontmatter field has no effect
- Claude can pass `isolation: "worktree"` when spawning a fork
- Do not confuse `/subtask` (forked subagent) with `/fork` (copies the whole session into a new *background session*, v2.1.212+)

### Observing running forks

Panel below the prompt input:

| Key | Action |
|---|---|
| `↑` / `↓` | Move between rows |
| `Enter` | Open the fork's transcript and send follow-ups |
| `x` | Dismiss a finished fork or stop a running one |
| `Esc` | Return focus to the prompt input |

With a transcript open, follow-up messages and skills go to that agent, but built-in commands still run in your main conversation. `/model` and `/fast` show a notice that they change the main conversation (v2.1.199+).

---

## 7. Limits

Three separate limits, each with its own variable. Learn which is which.

| Limit | Default | Variable | What it caps |
|---|---|---|---|
| Depth | 3 layers below main | `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` | How deeply subagents nest |
| Session total | 200 per session | `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION` | Total spawned over a session |
| Concurrency | 20 running | `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` | How many run at once |

### Depth / nesting

By default a subagent can spawn its own subagents, up to three layers below the main conversation. At the limit Claude Code withholds `Agent` from every subagent except a fork (a fork keeps the tool listed but it returns an error).

Set to `1` to turn nesting off:

```json
{ "env": { "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "2" } }
```

Nested subagents suit a delegated task that itself splits into parallel subtasks: a reviewer that dispatches a verifier per finding, so intermediate output never reaches your main conversation. Only the top-level summary returns to you.

To stop one specific subagent from nesting, omit `Agent` from its `tools` or add it to `disallowedTools`.

The panel shows the full tree: each row displays a `(+N)` descendant count, and opening a row shows siblings and direct children with a path back to `main`.

Version history worth knowing: v2.1.172-v2.1.216 allowed five layers with no configuration; v2.1.217-v2.1.218 defaulted to one; v2.1.219 raised the default to three.

### Session total

Every subagent spawned with the Agent tool counts: nested ones, forks, background ones, and subagents that a workflow's agents spawn with the Agent tool. An in-session `/subtask` fork counts too but is never *blocked* by the limit. A `/fork` background session does not count (separate session, separate budget). Agents a workflow script spawns with `agent()` do not count; workflows have their own per-run limit. Finished subagents still count.

At the limit the Agent tool fails with `Subagent spawn limit reached` and Claude is told to finish the work directly. `/clear` resets the count, unless surviving work (like a running workflow) carries it over.

### Concurrency

At 20 running, spawning fails with `Concurrent subagent limit reached` and Claude is told not to retry. Spawning succeeds again when the count drops.

Occupying slots without being blocked by the limit: an in-session `/subtask` fork, and **resuming** a finished subagent (which takes a fresh slot without checking, so resumes can push the count past the limit).

Sessions with ultracode active are exempt.

Workflow agents and agent team teammates follow their own limits.

---

## 8. Subagent output scanning

Claude Code scans each subagent's final report before Claude reads it, because a subagent may have read files, web pages, or command output you never reviewed, and text from those sources can carry instructions aimed at the main conversation.

The scan **never removes or rewords anything**. It makes two visible changes:

- **Backslash insertion** into text imitating Claude Code's own output, such as a `<system-reminder>` tag or a line starting with `Human:` or `Assistant:`, so the imitation reads as ordinary text
- **A marker line** starting with `[harness: subagent output matched instruction-shaped pattern(s):` when the report imitates such a tag or mentions permission settings like `bypassPermissions` or `--dangerously-skip-permissions`

The scan does not judge maliciousness and does not change what an instruction in a report can do: a tool call the report leads Claude to make still goes through permission checks and sandboxing. It is not a substitute for restricting what a subagent can reach.

Requires v2.1.210+.

---

## 9. Agent teams

**Experimental and disabled by default.** Enable with:

```json
{ "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" } }
```

One session acts as the **team lead**. Teammates are separate Claude Code instances, each with its own context window, that **communicate directly with each other** and share a task list.

### Subagents vs agent teams

|  | Subagents | Agent teams |
|---|---|---|
| Context | Own window; results return to caller | Own window; fully independent |
| Communication | Report to the main agent only | Teammates message each other directly |
| Coordination | Main agent manages all work | Shared task list with self-coordination |
| Best for | Focused tasks where only the result matters | Complex work requiring discussion |
| Token cost | Lower | Higher: each teammate is a separate instance |

The one-line rule: **subagents for results, teams for collaboration.**

Strongest use cases: research and review, new modules or features with clean ownership boundaries, debugging with competing hypotheses, and cross-layer coordination.

### Starting a team

Natural language after enabling:

```text
I'm designing a CLI tool that helps developers track TODO comments across
their codebase. Spawn three teammates to explore this from different angles:
one on UX, one on technical architecture, one playing devil's advocate.
```

Claude may use subagents instead. Subagents appear in the same panel as teammates, so the panel alone does not confirm a team formed. Ask again and explicitly request an agent team.

Teammates are spawned two ways: you request them, or Claude proposes them and you confirm. Claude never spawns without your approval.

### Display modes

- **in-process** (default): all teammates in your main terminal. Arrow keys select, Enter views and messages, `x` stops, Ctrl+T toggles the task list
- **split panes**: requires tmux or iTerm2 with the `it2` CLI

```json
{ "teammateMode": "auto" }
```

Values: `in-process`, `auto`, `tmux`, `iterm2`. Session flag: `claude --teammate-mode auto` (experimental, not in `--help`). Split panes are unsupported in VS Code's integrated terminal, Windows Terminal, and Ghostty.

### Models and effort

Teammates do **not** inherit the lead's `/model` selection by default. Set **Default teammate model** in `/config`, or pick "Default (leader's model)". Teammates do inherit the lead's effort level.

A teammate's model and fast mode are fixed at spawn. `/model` and `/fast` while viewing a teammate change the lead. `/effort` does apply to the viewed teammate's later turns.

### Reusing subagent definitions

```text
Spawn a teammate using the security-reviewer agent type to audit the auth module.
```

The teammate honors that definition's `tools` allowlist and `model`, and the body is **appended** to the teammate's system prompt as additional instructions rather than replacing it.

Not applied on this path: `skills` and `mcpServers`. Teammates load those from project and user settings like a regular session. Team coordination tools (`SendMessage`, task tools) are always available regardless of `tools`.

### Plan approval

```text
Spawn an architect teammate to refactor the authentication module.
Require plan approval before they make any changes.
```

The teammate works read-only until the lead approves. Rejected plans send the teammate back to plan mode to revise and resubmit. The lead decides autonomously, so give it criteria in your prompt ("only approve plans that include test coverage").

### Permissions

Teammates start with the lead's permission settings, including `--dangerously-skip-permissions`. You can change individual modes after spawning but not at spawn time.

Security model: a `SendMessage` from another agent is labeled as coming from another Claude session, not from you. A teammate **cannot** approve a permission prompt or supply consent on your behalf, and a denied teammate cannot relay the action to another teammate to bypass the check. In auto mode the classifier treats a relayed approval claim as untrusted input.

Teammate permission prompts appear in the lead session; approve them there. Plan approval is the designed exception, granted by the lead without prompting you.

### Task list

Three states: pending, in progress, completed. Tasks can depend on other tasks; a pending task with unresolved dependencies cannot be claimed. Claiming uses file locking to prevent races. The lead can assign explicitly, or teammates self-claim the next unassigned unblocked task.

### Quality gates

| Hook | Fires | Exit code 2 |
|---|---|---|
| `TeammateIdle` | A teammate is about to go idle | Sends feedback and keeps it working |
| `TaskCreated` | A task is being created | Prevents creation, sends feedback |
| `TaskCompleted` | A task is being marked complete | Prevents completion, sends feedback |

### On-disk architecture

| Path | Contents |
|---|---|
| `~/.claude/teams/{team-name}/config.json` | Team config: runtime state, session IDs, tmux pane IDs, `members` array |
| `~/.claude/teams/{team-name}/inboxes/{agent-name}.json` | Per-agent mailbox |
| `~/.claude/tasks/{team-name}/` | Shared task list |

The team name is `session-` plus the first eight characters of the session ID. Do not edit `config.json` by hand; it is overwritten on the next state update. The team config directory is removed when the session ends; the task list persists so resumed sessions keep their tasks, governed by `cleanupPeriodDays`.

There is no project-level equivalent. A `.claude/teams/teams.json` in your project is just an ordinary file.

The lead's `members` entry always carries agent type `team-lead`. Teammates can read this file to discover each other.

### Sizing and cost

Agent teams use significantly more tokens than a single session, scaling with the number of active teammates. Rough guidance:

- **3-5 teammates** for most workflows
- **5-6 tasks per teammate** keeps everyone productive. 15 independent tasks suggests 3 teammates
- Three focused teammates often outperform five scattered ones
- Task sizing: too small means coordination overhead exceeds benefit; too large means long stretches without check-ins; just right is a self-contained unit with a clear deliverable

### Known limitations

- **No session resumption with in-process teammates**: `/resume` and `/rewind` do not restore them. The lead may try to message teammates that no longer exist
- **Task status can lag**: teammates sometimes fail to mark tasks complete, blocking dependents
- **Shutdown can be slow**: teammates finish the current request or tool call first
- **One team per session**, scoped to that session
- **No nested teams**: teammates cannot spawn teammates
- **No background subagents from in-process teammates**: an in-process teammate's subagents run in the foreground, because a teammate's background work cannot outlive the lead's process
- **Lead is fixed**: no promotion or transfer of leadership
- **Permissions set at spawn**

### Troubleshooting

- A disappeared teammate row is **hidden, not stopped**. Idle rows hide 30 seconds after the whole panel goes idle. More than three idle collapses into an `N idle agents` row that Enter expands. Message the teammate by name to bring it back
- Too many permission prompts: pre-approve common operations before spawning
- Teammates stopping on errors: select and press Enter to inspect, then give instructions directly or spawn a replacement
- Lead shutting down early: tell it to keep going, or to wait for teammates before proceeding
- Orphaned tmux sessions: `tmux ls` then `tmux kill-session -t <name>`

---

## 10. Agent view and background sessions

Requires v2.1.139+, research preview.

Agent view (`claude agents`) is one screen for every background session across every project. Each background session is a **full Claude Code conversation** hosted by a per-user supervisor process, so it keeps running with no terminal attached.

Note that subagents and teammates a session spawns are **not** listed as separate rows.

### Core loop

```bash
claude agents                        # open the view
claude agents --cwd ~/projects/app   # scope to one project tree
```

| Action | How |
|---|---|
| Dispatch | Type a prompt, press Enter. Each prompt starts a **new** session, not a follow-up |
| Peek | `Space` on a row: last output or the pending question, with a reply input |
| Attach | `Enter` or `→`: full interactive session |
| Detach | `←` on an empty prompt, `Ctrl+Z`, `/exit`, or double `Ctrl+C`. Never stops the session |
| Bring an existing session in | `/bg` inside it, or `←` on an empty prompt |
| End a session from inside | `/stop` |

### Session states

| State | Icon | Meaning |
|---|---|---|
| Working | Animated | Actively running tools or generating |
| Needs input | Yellow | Waiting on a question, permission decision, sandbox prompt, MCP elicitation, or managed-settings prompt |
| Idle | Dimmed | Nothing to do, ready for your next prompt |
| Completed | Green | Finished successfully |
| Failed | Red | Ended with an error |
| Stopped | Grey | Stopped with `Ctrl+X`, `claude stop`, or an external process end |

Separately, the **icon shape** shows the process:

| Shape | Meaning |
|---|---|
| `✻` / animated `✽` | Process alive, replies immediately |
| `∙` | Process exited. Peek, reply, or attach and Claude restarts from where it left off |
| `✢` | A `/loop` session sleeping between iterations |

### Dispatch modifiers

| Input | Effect |
|---|---|
| `<agent-name> <prompt>` | First word matching a subagent name runs it as the session's main agent |
| `@<agent-name>` | Explicit subagent mention anywhere in the prompt |
| `@<repo>` | Run the session in that repository |
| `/<command>` | Suggest skills and commands to dispatch as the prompt |
| `! <command>` | Run a shell command as a background job instead of a Claude session |
| `#<number>` or PR URL | Select the session already working on that PR |
| `Shift+Enter` | Dispatch and immediately attach |

Precedence note: when an `@name` matches both a subagent and a sibling repository, the **subagent wins**. The bare first-word match also applies, so a prompt starting with a subagent name dispatches that subagent.

### From the shell

```bash
claude --bg "investigate the flaky SettingsChangeDetector test"
claude --agent code-reviewer --bg "address review comments on PR 1234"
claude --bg --name "flaky-test-fix" "investigate the flaky test"
claude --bg --exec 'pytest -x'          # shell job, no model invoked
```

`--bg` cannot be combined with `-p` / `--print`.

| Command | Purpose |
|---|---|
| `claude agents` | Open agent view |
| `claude agents --json` | Print sessions as JSON and exit (add `--all` for completed) |
| `claude attach <id>` | Attach in this terminal |
| `claude logs <id>` | Print recent output |
| `claude stop <id>` | Stop a session (alias `claude kill`) |
| `claude respawn <id>` / `--all` | Restart with conversation intact, e.g. onto an updated binary |
| `claude rm <id>` | Remove from the list |
| `claude daemon status` | Supervisor state, version, socket dir, worker count |
| `claude daemon stop --any` | Stop the supervisor. `--keep-workers` leaves sessions running |

### Key shortcuts

| Shortcut | Action |
|---|---|
| `Space` | Peek panel |
| `Enter` / `→` | Attach |
| `Shift+Enter` | Dispatch and attach |
| `Ctrl+S` | Switch grouping between state and directory |
| `Ctrl+T` | Pin (also keeps the process running while idle) |
| `Ctrl+R` | Rename |
| `Ctrl+X` | Stop; press again within two seconds to delete |
| `Ctrl+G` | Open the dispatch prompt in `$EDITOR` |
| `Tab` | On an empty input, browse all subagents |
| `?` | All shortcuts |

Filters typed into the dispatch input: `a:<name>` (sessions running that agent), `s:<state>` (also `s:blocked`), `#<number>` or PR URL, any other URL.

### File edit isolation

**Every background session moves itself into an isolated git worktree under `.claude/worktrees/` before editing files.** Parallel sessions read the same checkout but each writes to its own.

Claude skips the worktree when the session is already inside a linked worktree, the directory is not a git repo and no `WorktreeCreate` hook is configured, or the write is outside the working directory.

Turn it off per repository:

```json
{ "worktree": { "bgIsolation": "none" } }
```

A session that isolated its changes **commits, pushes its own branch, and opens a draft pull request without asking**. It never pushes to `main` or `master`, never force-pushes or merges, and skips the PR if you said not to or there is no remote. A session editing a checkout it did **not** isolate itself still asks before committing or switching branches.

A subagent spawned by a background session inherits the session's working directory, so its edits land in the session's worktree. Give it its own with `isolation: worktree`.

Deleting: `Ctrl+X` twice removes the worktree **including uncommitted changes**. `claude rm` keeps a worktree with uncommitted changes. Neither removes a worktree with unpushed commits. A worktree you created yourself is always left in place.

### What carries over when backgrounding

Backgrounding starts a fresh process resuming from the saved conversation. These carry over and keep running: background shell commands, backgrounded subagents, dynamic workflows, `/loop` scheduled tasks. A subagent moves together with everything it started, so it carries over only when all of that can move too.

Stopped rather than carried: running monitors, and a backgrounded subagent that owns one.

Launch flags that carry through: `--mcp-config`, `--strict-mcp-config`, `--settings`, `--add-dir`, `--plugin-dir`, `--fallback-model`, `--allow-dangerously-skip-permissions`. Directories added with `/add-dir` also carry.

`CLAUDE_DISABLE_ADOPT=1` stops in-flight work instead of carrying it over.

### Permission mode inheritance

| How the session started | Permission mode |
|---|---|
| Backgrounded with `/bg` or `←` | Keeps the current mode |
| Dispatched from agent view or `claude --bg` | `defaultMode` from that directory's settings, or `permissionMode` from the dispatched subagent's frontmatter |

Defaults for everything dispatched from a view:

```bash
claude agents --permission-mode plan --model opus --effort high
```

`--agent` here sets the subagent used when a dispatch prompt does not name one; it defaults to the `agent` setting, otherwise the built-in catch-all `claude` agent.

`claude --bg --permission-mode bypassPermissions` is refused until you have accepted the bypass disclaimer once interactively.

### Supervisor process

Per-user, separate from your terminal and from agent view. Starts automatically, not managed directly. Key behaviors:

- Keeps one pre-warmed worker so dispatch avoids a cold launch
- Uses the same stored credentials as interactive sessions
- Reads provider selection vars (`CLAUDE_CODE_USE_BEDROCK`, `ANTHROPIC_DEFAULT_*_MODEL`) and `PATH` **from the shell that dispatched each session**
- Does **not** inherit gateway `ANTHROPIC_BASE_URL` from the shell that started the supervisor, unless the supervisor itself was started from that gateway shell **and** the session is dispatched into the dispatching directory. Otherwise set it in the project's `.claude/settings.json` `env` block
- Stops an idle unattached session after about an hour. Pinned sessions (`Ctrl+T`) are exempt
- Restarts a session whose process exits unexpectedly, with safeguards: a session already recorded as done/failed/stopped is not restarted; ending a `←`/`/background` session's process marks it stopped rather than restarting; a restarted session is told it was restarted so it can re-verify time-sensitive context
- Watches the installed binary and restarts into new versions. Restarts only ever move a session onto a *newer* version

### Where state is stored

| Path | Contents |
|---|---|
| `~/.claude/daemon.log` | Supervisor log |
| `~/.claude/daemon/roster.json` | Running background sessions, for reconnect |
| `~/.claude/jobs/<id>/state.json` | Per-session state shown in agent view |
| `~/.claude/jobs/<id>/tmp/` | Per-session scratch; writes here do not prompt |

`CLAUDE_JOB_DIR` is set in each background session to its `~/.claude/jobs/<id>` directory.

Turn it all off: `"disableAgentView": true` or `CLAUDE_CODE_DISABLE_AGENT_VIEW`.

### Limitations

- **Rate limits apply**: ten parallel agents burn quota roughly ten times as fast
- **Sessions are local**: preserved across sleep, stopped by shutdown (they show as failed; attach or peek and they restart from where they left off)
- **Claude-created worktrees are deleted with the session** in agent view

---

## 11. Dynamic workflows

Requires v2.1.154+, all paid plans. A JavaScript script that orchestrates subagents at scale. Claude writes it, a runtime executes it in the background while your session stays responsive.

The essential difference: **a workflow moves the plan into code.** With subagents, skills, and teams, Claude is the orchestrator deciding turn by turn, and every result lands in a context window. A workflow script holds the loop, the branching, and the intermediate results, so Claude's context holds only the final answer.

That also enables repeatable quality patterns: independent agents adversarially reviewing each other's findings, or drafting a plan from several angles and weighing them.

### Triggering

Three ways:

```text
ultracode: audit every API endpoint under src/routes/ for missing auth checks
```

```text
use a workflow to review every file changed in this PR, then merge the findings into one ranked summary
```

```text
/effort ultracode
```

Ultracode combines `xhigh` reasoning effort with automatic workflow orchestration for every substantive task in the session. Start with `claude --effort ultracode`. It resets when you start a new session.

The keyword is an opt-in **only in a prompt you type yourself**. It does not trigger from `-p`, an SDK prompt not stamped as human input, a scheduled task, or a webhook / PR comment relayed into the conversation.

Dismiss the highlight with `Option+W` / `Alt+W`, or backspace right after it.

### Bundled

`/deep-research <question>`: fans out web searches across angles, fetches and cross-checks sources, votes on each claim, returns a cited report with unsurvived claims filtered out. Requires WebSearch. As of v2.1.218 it runs only when you invoke it.

### Watching a run

`/workflows` lists running and completed workflows.

| Key | Action |
|---|---|
| `Enter` / `→` | Drill into a phase, then an agent, to read prompt, tool calls, result |
| `f` | Filter the phase's agent list by status |
| `p` | Pause or resume the run |
| `x` | Stop the selected agent, or the whole workflow |
| `r` | Restart the selected running agent |
| `s` | Save the run's script as a command |

### Permissions

Your permission mode controls only the **launch prompt**. The subagents a workflow spawns always run in `acceptEdits` and inherit your tool allowlist regardless of your session's mode. File edits are auto-approved. Shell commands, web fetches, and MCP tools outside your allowlist can still prompt mid-run, so pre-approve what the agents need on long runs.

| Permission mode | When you are prompted |
|---|---|
| Default, accept edits | Every run, unless you selected "don't ask again" for that workflow in this project |
| Auto | First launch only. Skipped entirely with ultracode on |
| Bypass, `claude -p`, Agent SDK | Never |

### Saving and distributing

`/workflows` → select → `s`. Tab toggles between `.claude/workflows/` (shared) and `~/.claude/workflows/` (personal). The workflow then runs as `/<name>`. If a project and a personal workflow share a name, the project one wins.

Monorepos: saving writes to the closest existing `.claude/workflows/` between cwd and the repo root. Workflows load from every such directory along that path, and the one closest to cwd wins on a name collision.

Plugins: place scripts in a `workflows/` directory at the plugin root. They are namespaced: plugin `acme-tools` with `meta.name` of `release-audit` runs as `/acme-tools:release-audit`.

Arguments: a saved workflow reads a global named `args`.

```text
Run /triage-issues on issues 1024, 1025, and 1030
```

### Script shape

```javascript
export const meta = {
  name: 'audit-routes',
  description: 'Audit every route handler for missing auth checks',
}

const found = await agent('List every .ts file under src/routes/.', {
  schema: { type: 'object', required: ['files'], properties: { files: { type: 'array', items: { type: 'string' } } } },
})

const audits = await pipeline(found.files, file =>
  agent(`Audit ${file} for missing authentication checks.`, { label: file }),
)

return audits.filter(Boolean)
```

Plain JavaScript with top-level `await`. `agent()` spawns one subagent; `pipeline()` runs one per list item.

Each run writes its script to a file under your session's directory in `~/.claude/projects/`. Claude receives the path, so you can ask for it, diff it against a previous run, or edit it and ask Claude to relaunch.

### Runtime constraints

| Constraint | Why |
|---|---|
| No mid-run user input | Only agent permission prompts pause a run. For sign-off between stages, run each stage as its own workflow |
| No direct filesystem or shell access from the script | Agents read, write, and run commands. The script only coordinates |
| Up to 16 concurrent agents, fewer on limited CPUs | Bounds local resource use |
| 1,000 agents total per run | Prevents runaway loops |

### Resume semantics

Two rules decide which results survive a stop:

1. An agent still running when you stopped is not saved and starts over.
2. **Replay follows the order agents started.** Cached results stop at the first agent that did not finish, and every agent that started after it runs again, even if it completed.

Consequence: stopping mid fan-out is expensive. If A, B, C, D start in that order and you stop while B is running, on resume A is cached but B, C, and D all rerun. A workflow that fans work out across many small agents preserves more progress than one with long agents.

Resume works within the same session only. Exiting Claude Code mid-workflow means the next session starts it fresh.

### Cost control

Size guideline (v2.1.202+), advice not a cap:

| Value | Agent count Claude aims for |
|---|---|
| `unrestricted` | No guideline |
| `small` | Fewer than 5 |
| `medium` | Fewer than 15 (the default as of v2.1.219) |
| `large` | Fewer than 50 |

Set with `/config workflowSizeGuideline=small` or the `workflowSizeGuideline` settings key (v2.1.219+, takes precedence over `/config`).

A run scheduling more than 25 agents, or projecting past 1.5 million tokens, shows a `Large workflow` warning in the task panel. Advisory only. Your chosen size guideline's agent count replaces the 25 threshold, and ultracode sessions do not show it.

Every agent uses your session's model unless the script routes a stage elsewhere or `CLAUDE_CODE_SUBAGENT_MODEL` is set (which overrides both). Check `/model` before a large run.

Practical advice: gauge spend by running on a small slice first, one directory instead of the whole repo.

### Turning off

`/config` toggle, `"disableWorkflows": true` in settings, or `CLAUDE_CODE_DISABLE_WORKFLOWS=1`. Organization-wide via managed settings.

---

## 12. Worktrees as the isolation layer

Worktrees are not a way to run agents; they isolate file edits so parallel agents do not collide.

```bash
claude --worktree feature-x
claude -w feature-x
```

Created under `.claude/worktrees/<name>/` at the repo root, on a branch named `worktree-<name>`.

Three ways they intersect with agents:

1. **Subagents**: `isolation: worktree` in frontmatter, or ask Claude to "use worktrees for your agents". Branched from your **default branch** by default, not the parent's HEAD. Cleaned up automatically if the subagent makes no changes.
2. **Background sessions**: automatic, before the first file edit.
3. **Sessions you run yourself**: `--worktree`.

Agent teams do **not** isolate teammates in worktrees. Partition file ownership manually.

```yaml
---
name: refactorer
description: Applies mechanical refactors across many files
isolation: worktree
---
```

Working-directory enforcement for `isolation: worktree` subagents has tightened progressively:

- v2.1.203+: a Bash or PowerShell command whose working directory resolves to the main checkout fails with an error
- v2.1.210+: the check covers the whole repository containing the launch directory, plus the main checkout if your session runs in a linked worktree
- v2.1.216+: for Bash, the **command itself** is checked. A command redirecting git into the main checkout fails, whether via `git -C`, `--git-dir`, `GIT_DIR`/`GIT_WORK_TREE`, or a `cd` first. A command too complex to check also fails, with an error telling Claude to split it up. PowerShell gets only the working-directory check

Also: inside a subagent, `cd` does not persist between Bash/PowerShell calls and does not affect the main conversation's working directory.

`.worktreeinclude` at the repo root lists gitignored files (like `.env`) to copy into each new worktree.

Cleanup: `git worktree list` then `git worktree remove <path>`. Orphaned worktrees are also swept at startup using `cleanupPeriodDays`.

---

## 13. Worked examples

### Read-only reviewer

```markdown
---
name: code-reviewer
description: Expert code review specialist. Proactively reviews code for quality, security, and maintainability. Use immediately after writing or modifying code.
tools: Read, Grep, Glob, Bash
model: inherit
---

You are a senior code reviewer ensuring high standards of code quality and security.

When invoked:
1. Run git diff to see recent changes
2. Focus on modified files
3. Begin review immediately

Review checklist:
- Code is clear and readable
- Functions and variables are well-named
- No duplicated code
- Proper error handling
- No exposed secrets or API keys
- Input validation implemented
- Good test coverage
- Performance considerations addressed

Provide feedback organized by priority:
- Critical issues (must fix)
- Warnings (should fix)
- Suggestions (consider improving)

Include specific examples of how to fix issues.
```

### Debugger (can modify)

```markdown
---
name: debugger
description: Debugging specialist for errors, test failures, and unexpected behavior. Use proactively when encountering any issues.
tools: Read, Edit, Bash, Grep, Glob
---

You are an expert debugger specializing in root cause analysis.

When invoked:
1. Capture error message and stack trace
2. Identify reproduction steps
3. Isolate the failure location
4. Implement minimal fix
5. Verify solution works

For each issue, provide:
- Root cause explanation
- Evidence supporting the diagnosis
- Specific code fix
- Testing approach
- Prevention recommendations

Focus on fixing the underlying issue, not the symptoms.
```

### Restricted-by-hook DB reader

```markdown
---
name: db-reader
description: Execute read-only database queries. Use when analyzing data or generating reports.
tools: Bash
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "./scripts/validate-readonly-query.sh"
---

You are a database analyst with read-only access. Execute SELECT queries to answer questions about the data.

You cannot modify data. If asked to INSERT, UPDATE, DELETE, or modify schema, explain that you only have read access.
```

The system prompt tells the subagent to refuse writes; the hook is the backstop that enforces it if the subagent tries anyway.

---

## 14. Patterns

**Isolate high-volume operations.** The single most effective use.

```text
Use a subagent to run the test suite and report only the failing tests with their error messages
```

**Parallel research.**

```text
Research the authentication, database, and API modules in parallel using separate subagents
```

Caution: when subagents complete, their results return to your main conversation. Many subagents each returning detailed results still consumes significant context.

**Chain subagents.**

```text
Use the code-reviewer subagent to find performance issues, then use the optimizer subagent to fix them
```

**Adversarial investigation** (agent teams).

```text
Users report the app exits after one message instead of staying connected.
Spawn 5 agent teammates to investigate different hypotheses. Have them talk to
each other to try to disprove each other's theories, like a scientific
debate. Update the findings doc with whatever consensus emerges.
```

The debate structure fights anchoring. Sequential investigation finds one plausible explanation and stops.

**Give teammates enough context.** They load CLAUDE.md, MCP servers, and skills, but not the lead's conversation history.

```text
Spawn a security reviewer teammate with the prompt: "Review the authentication module
at src/auth/ for security vulnerabilities. Focus on token handling, session
management, and input validation. The app uses JWT tokens stored in
httpOnly cookies. Report any issues with severity ratings."
```

### Main conversation vs subagent

Use the **main conversation** when the task needs frequent back-and-forth, multiple phases share significant context, the change is quick and targeted, or latency matters (subagents start fresh and need time to gather context).

Use a **subagent** when the task produces verbose output you do not need, you want to enforce tool restrictions, or the work is self-contained and returns a summary.

Consider a **skill** when you want a reusable prompt that runs in the main conversation context.

For a quick question about something already in your conversation, use `/btw`: it sees your full context, has no tool access, and the answer is discarded rather than added to history.

### Design principles

- **One job per subagent.** Each should excel at one specific task
- **Detailed descriptions.** Claude uses `description` to decide when to delegate. This is the single highest-leverage field
- **Minimal tools.** Grant only what is necessary
- **Version control project subagents.** Share and improve them collaboratively
- **The layering rule**: skill teaches the how, hook enforces the rule, subagent isolates the work
- **Definitions vs ad-hoc**: a definition in `.claude/agents/` is a reusable specialist with a fixed prompt and tools. A bare Agent call is ad-hoc and inherits whatever the lead is doing. Definitions for repeat work, bare calls for one-offs
- **Concurrency sweet spot**: 3 to 5 concurrent subagents for everyday code work. Beyond that you spend more time merging summaries than you save. Dynamic workflows are the exception, for tasks that fan out cleanly
- **Pair with `SubagentStop`** to enforce non-negotiables (tests pass, no secrets in diff, no out-of-scope writes) before the lead folds a result back in

---

## 15. Environment variable reference

| Variable | Effect |
|---|---|
| `CLAUDE_CODE_SUBAGENT_MODEL` | Model for all subagents; highest precedence. `inherit` is the same as unset |
| `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` | Nesting depth below main. Default 3, set 1 to disable nesting |
| `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION` | Total per session. Default 200, no upper bound, cannot be disabled |
| `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` | Concurrent running. Default 20 |
| `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS` | Keeps subagents synchronous; takes precedence over fork mode |
| `CLAUDE_CODE_FORK_SUBAGENT` | `1` enables fork mode, `0` disables everywhere |
| `CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS` | Removes built-in Explore and Plan |
| `CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS` | Removes all built-ins in headless / SDK |
| `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` | Enables agent teams |
| `CLAUDE_CODE_DISABLE_AGENT_VIEW` | Turns off background agents and agent view |
| `CLAUDE_CODE_DISABLE_WORKFLOWS` | Turns off dynamic workflows |
| `CLAUDE_CODE_DISABLE_AUTO_MEMORY` | Also disables subagent `memory:` |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | Compaction threshold; applies to subagents too |
| `CLAUDE_DISABLE_ADOPT` | Stops in-flight work instead of carrying it to a backgrounded session |
| `CLAUDE_CODE_DISABLE_BG_EXIT_HANDOFF` | Stops a background session's work with its process |
| `CLAUDE_JOB_DIR` | Set by Claude Code in each background session |

## 16. Settings key reference

| Key | Effect |
|---|---|
| `agent` | Run the main thread as a named subagent; default for `claude agents` dispatch |
| `teammateMode` | `in-process`, `auto`, `tmux`, `iterm2` |
| `disableAgentView` | Turns off background agents and agent view |
| `disableWorkflows` | Turns off dynamic workflows |
| `workflowSizeGuideline` | `unrestricted`, `small`, `medium`, `large` |
| `worktree.bgIsolation` | `"none"` disables worktree isolation for background sessions |
| `autoMemoryEnabled` | Also gates subagent `memory:` |
| `permissions.deny` | `Agent(<name>)` to block specific subagents, `Agent` to block all delegation |
| `hooks.SubagentStart` / `.SubagentStop` | Subagent lifecycle |
| `hooks.TeammateIdle` / `.TaskCreated` / `.TaskCompleted` | Agent team gates |

## 17. Command reference

| Command | Purpose |
|---|---|
| `/agents` | As of v2.1.198, prints a reminder to ask Claude or edit `.claude/agents/`. It no longer opens the creation wizard |
| `/subtask <task>` | Fork the conversation into a subagent (v2.1.212+) |
| `/fork [prompt]` | Copy the session into a new background session (v2.1.212+) |
| `/bg` / `/background [prompt]` | Move the current conversation into a background session |
| `/tasks` | List running and completed subagents and background work |
| `/workflows` | List and control dynamic workflow runs |
| `/deep-research <question>` | Bundled multi-agent research workflow |
| `/btw <question>` | Side question with full context, no tools, discarded after |
| `/stop` | End a background session from inside it |
| `/loop` | Scheduled repeating task inside a session |

---

## 18. Gotchas

1. **Background is now the default** (v2.1.198+), and background subagents get a **reduced built-in tool set**. The same definition can resolve to different tools depending on where it runs.
2. **Explore and Plan skip CLAUDE.md and git status.** No setting changes this. Restate critical rules in the delegation prompt.
3. **Explore and Plan cannot be resumed.** They return no agent ID.
4. A parent using `bypassPermissions`, `acceptEdits`, or `auto` **overrides** the subagent's `permissionMode` frontmatter.
5. **Plugin subagents silently ignore** `hooks`, `mcpServers`, and `permissionMode`.
6. `disallowedTools` is applied **before** `tools`. A tool in both is removed.
7. **`name` is identity, not the filename.** Duplicate names in one directory load arbitrarily by filesystem read order.
8. Creating the **first** agent file in a directory that did not exist at session start requires a restart. Later edits hot-reload.
9. **Three different subagent limits** (depth, session total, concurrency) with three different variables. Resuming a subagent bypasses the concurrency check.
10. **Subagent memory and auto memory are different features** that both produce `MEMORY.md`. Turning off auto memory silently disables the `memory:` field.
11. **Agent teams do not isolate teammates in worktrees.** Partition file ownership manually or expect overwrites.
12. **Teammates do not inherit the lead's `/model`** by default, but they do inherit its effort level.
13. **`skills` and `mcpServers` are ignored** when a subagent definition runs as a teammate.
14. **Workflow subagents always run in `acceptEdits`** regardless of your session's permission mode. File edits are auto-approved.
15. **Stopping a workflow mid fan-out is expensive.** Replay follows start order, so everything after the first unfinished agent reruns.
16. **A background session opens a draft PR on its own** if it isolated its own worktree. It never pushes to main or force-pushes.
17. **`Ctrl+X` twice deletes a session's worktree including uncommitted changes.** Commit first.
18. **Agent view sessions consume quota independently.** Ten parallel agents burn quota roughly ten times as fast.
19. `/subtask` forks a subagent; `/fork` copies the whole session into a background session. These swapped meanings at v2.1.212.
20. **The `description` field is the delegation contract.** Vague descriptions are the most common reason automatic delegation does not fire.

---

## Reference links

- Run agents in parallel: https://code.claude.com/docs/en/agents
- Subagents: https://code.claude.com/docs/en/sub-agents
- Agent teams: https://code.claude.com/docs/en/agent-teams
- Agent view: https://code.claude.com/docs/en/agent-view
- Dynamic workflows: https://code.claude.com/docs/en/workflows
- Worktrees: https://code.claude.com/docs/en/worktrees
- Hooks: https://code.claude.com/docs/en/hooks
- Skills: https://code.claude.com/docs/en/skills
- Permissions: https://code.claude.com/docs/en/permissions
- Costs: https://code.claude.com/docs/en/costs
- Full docs index: https://code.claude.com/docs/llms.txt
