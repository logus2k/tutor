# Claude Code: Hooks Reference

A study guide to Claude Code hooks: the lifecycle and all 30 events, the three-level configuration schema, five handler types, matcher semantics, the exit-code and JSON protocols, decision control per event, and how hooks interact with the permission system.

Verified against code.claude.com/docs/en/hooks and /hooks-guide.

---

## 1. Why hooks exist

Everything else in Claude Code is a suggestion. CLAUDE.md, skills, and prompts are high-quality, usually followed, **never guaranteed**. Hooks are the deterministic shell around that probabilistic core: certain actions always happen rather than relying on the model to choose to run them.

The decision rule, restated from the docs' own framing:

| Need | Use |
|---|---|
| Something Claude should **know** | CLAUDE.md, rules |
| Something Claude should **do**, taught as a procedure | Skill |
| Something that **must always happen** | Hook |
| Judgment rather than a deterministic rule | Prompt hook or agent hook |
| Work that should stay out of your context | Subagent |

Hooks are user-defined **shell commands, HTTP endpoints, MCP tool calls, LLM prompts, or subagents** that execute automatically at specific lifecycle points.

---

## 2. The lifecycle

Events fall into three cadences:

- **Once per session**: `SessionStart`, `SessionEnd`
- **Once per turn**: `UserPromptSubmit`, `Stop`, `StopFailure`
- **On every tool call** inside the agentic loop: `PreToolUse`, `PostToolUse` — **except `EndConversation` calls, which skip both**

### All events

| Event | Fires |
|---|---|
| `SessionStart` | A session begins or resumes |
| `Setup` | `--init-only`, or `--init`/`--maintenance` in `-p` mode. One-time CI/script preparation |
| `UserPromptSubmit` | You submit a prompt, before Claude processes it |
| `UserPromptExpansion` | A user-typed command expands into a prompt. Can block |
| `PreToolUse` | Before a tool call executes. Can block |
| `PermissionRequest` | A tool call needs a permission decision |
| `PermissionDenied` | A call is denied by the auto mode classifier |
| `PostToolUse` | After a tool call succeeds |
| `PostToolUseFailure` | After a tool call fails |
| `PostToolBatch` | After a batch of parallel tool calls resolves, before the next model call |
| `Notification` | Claude Code sends a notification |
| `MessageDisplay` | While assistant message text is displayed |
| `SubagentStart` | A subagent is spawned |
| `SubagentStop` | A subagent finishes |
| `TaskCreated` | A task is being created via `TaskCreate` |
| `TaskCompleted` | A task is being marked complete |
| `Stop` | Claude finishes responding |
| `StopFailure` | The turn ends due to an API error. **Output and exit code are ignored** |
| `TeammateIdle` | An agent team teammate is about to go idle |
| `InstructionsLoaded` | A CLAUDE.md or `.claude/rules/*.md` file loads into context |
| `ConfigChange` | A configuration file changes during a session |
| `CwdChanged` | The working directory changes, e.g. Claude runs `cd` |
| `FileChanged` | A watched file changes on disk |
| `WorktreeCreate` | A worktree is being created. **Replaces default git behavior** |
| `WorktreeRemove` | A worktree is being removed |
| `PreCompact` | Before context compaction |
| `PostCompact` | After compaction completes |
| `Elicitation` | An MCP server requests user input during a tool call |
| `ElicitationResult` | After you respond to an elicitation, before the response returns to the server |
| `SessionEnd` | A session terminates |

---

## 3. Configuration schema

Three levels of nesting:

1. A **hook event** to respond to
2. A **matcher group** filtering when it fires
3. One or more **hook handlers** that run when matched

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "if": "Bash(rm *)",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/block-rm.sh",
            "args": []
          }
        ]
      }
    ]
  }
}
```

The docs use precise terms: **hook event** for the lifecycle point, **matcher group** for the filter, **hook handler** for the thing that runs. "Hook" alone means the general feature.

### Resolution walkthrough

Given `Bash "rm -rf /tmp/build"` against the config above:

1. `PreToolUse` fires; JSON goes to the hook on stdin
2. The matcher `"Bash"` matches the tool name, so the group activates
3. The `if` condition `"Bash(rm *)"` matches, so the handler spawns. **Had the command been `npm test`, the script would never run**, avoiding the process-spawn cost
4. The script prints a `permissionDecision` of `"deny"`
5. Claude Code blocks the call and shows Claude the reason

**Exit 0 with no output means "no decision", not "approve."** The normal permission flow still applies. A hook can deny, but staying silent does not grant.

### Hook locations

| Location | Scope | Shareable |
|---|---|---|
| `~/.claude/settings.json` | All your projects | No |
| `.claude/settings.json` | Single project | Yes, committable |
| `.claude/settings.local.json` | Single project | No, gitignored |
| Managed policy settings | Organization-wide | Yes, admin-controlled |
| Plugin `hooks/hooks.json` | When the plugin is enabled | Yes |
| Skill or agent frontmatter | While the component is active | Yes |

**Hook entries merge across settings levels rather than replacing each other.** User, project, and local settings add their own hooks without removing managed ones.

Hooks from settings, managed policy, and plugins **also run inside subagents**. Tool events fire the same configured hooks, and the input carries `agent_id` and `agent_type`.

Enterprise: `allowManagedHooksOnly` blocks user, project, and plugin hooks. **Hooks from plugins force-enabled in managed `enabledPlugins` are exempt**, so admins can distribute vetted hooks through an org marketplace.

HTTP allowlists apply to hooks from **every** source, including managed policy:

- `allowedHttpHookUrls`: when defined at any level, an HTTP hook runs only if its URL matches the merged allowlist
- `httpHookAllowedEnvVars`: when defined, only those variables interpolate into hook headers

---

## 4. Matchers

**How a matcher is evaluated depends on the characters it contains.** This is the single most misunderstood part of the system.

| Matcher value | Evaluated as | Example |
|---|---|---|
| `"*"`, `""`, or omitted | Match all | Fires on every occurrence |
| Only letters, digits, `_`, `-`, spaces, `,`, `\|` | **Exact string**, or list separated by `\|` or `,` | `Bash` matches only Bash; `Edit\|Write` and `Edit, Write` each match either exactly |
| Contains **any other character** | **JavaScript regex, unanchored** | `^Notebook` matches names starting with Notebook; `mcp__memory__.*` matches every tool from that server |

The regex path uses `RegExp.prototype.test`, which succeeds on a match **anywhere** in the value. `Edit.*` matches both `Edit` and `NotebookEdit`. Wrap in `^` and `$` for whole-string matching.

Version gates: comma separators and whitespace tolerance need v2.1.191+. **Hyphens in the exact-match set need v2.1.195+** — on earlier versions `code-reviewer` is an unanchored regex that also fires for `senior-code-reviewer`.

**`FileChanged` and `StopFailure` use a narrower exact-match set**: letters, digits, `_`, and `|` only. A hyphen, space, or comma keeps them on the regex path, and only `|` separates alternatives.

### What each event matches on

| Event | Matcher filters | Values |
|---|---|---|
| `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PermissionRequest`, `PermissionDenied` | tool name | `Bash`, `Edit\|Write`, `mcp__.*` |
| `SessionStart` | how the session started | `startup`, `resume`, `clear`, `compact`, `fork` |
| `Setup` | which flag triggered it | `init`, `maintenance` |
| `SessionEnd` | why it ended | `clear`, `resume`, `logout`, `prompt_input_exit`, `bypass_permissions_disabled`, `other` |
| `Notification` | notification type | `permission_prompt`, `idle_prompt`, `auth_success`, `elicitation_dialog`, `elicitation_complete`, `elicitation_response`, `agent_needs_input`, `agent_completed` |
| `SubagentStart`, `SubagentStop` | agent type | `general-purpose`, `Explore`, `Plan`, custom names, or plugin-scoped like `^my-plugin:reviewer$` |
| `PreCompact`, `PostCompact` | compaction trigger | `manual`, `auto` |
| `ConfigChange` | config source | `user_settings`, `project_settings`, `local_settings`, `policy_settings`, `skills` |
| `StopFailure` | error type | `rate_limit`, `overloaded`, `authentication_failed`, `oauth_org_not_allowed`, `billing_error`, `invalid_request`, `model_not_found`, `server_error`, `max_output_tokens`, `unknown` |
| `InstructionsLoaded` | load reason | `session_start`, `nested_traversal`, `path_glob_match`, `include`, `compact` |
| `UserPromptExpansion` | command name | your skill or command names |
| `Elicitation`, `ElicitationResult` | MCP server name | your configured server names |
| `FileChanged` | **literal filenames to watch** | `.envrc\|.env` |
| `UserPromptSubmit`, `PostToolBatch`, `Stop`, `TeammateIdle`, `TaskCreated`, `TaskCompleted`, `WorktreeCreate`, `WorktreeRemove`, `MessageDisplay`, `CwdChanged` | **no matcher support** | always fires |

A `matcher` on a no-matcher event is **silently ignored**.

### Matching MCP tools

MCP tools follow `mcp__<server>__<tool>`.

**The `.*` is required.** A matcher like `mcp__memory` contains only exact-match characters, so it is compared as an exact string and **matches no tool**.

- `mcp__memory__.*` — all tools from `memory`
- `mcp__brave-search__.*` — works even with a hyphenated server name
- `mcp__.*__write.*` — any `write`-prefixed tool from any server

**Plugin-bundled MCP servers use a scoped segment**: `mcp__plugin_<plugin-name>_<server-name>__<tool>`. A matcher written against the bare server key **never fires** for these. For plugin `my-plugin` bundling server key `db`, use `mcp__plugin_my-plugin_db__.*`.

### The `if` field

Filters an individual handler using **permission rule syntax**, matching tool name and arguments together. This is finer than `matcher`, which filters the group by tool name only.

```json
{ "type": "command", "if": "Bash(git *)", "command": "./check-git-policy.sh" }
```

Rules:

- **Exactly one permission rule.** No `&&`, `||`, or lists. Use a separate handler per condition
- **Only evaluated on tool events**: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PermissionRequest`, `PermissionDenied`. **On any other event, a hook with `if` set never runs**
- Single-segment directory patterns match only at the working directory as of v2.1.214. `"Edit(src/**)"` no longer matches `src` at any depth; use `"Edit(**/src/**)"`

Bash `if` matching behavior:

| Pattern | Command | Runs? | Why |
|---|---|---|---|
| `Bash(git *)` | `FOO=bar git push` | yes | leading assignments stripped |
| `Bash(git *)` | `npm test && git push` | yes | each subcommand checked |
| `Bash(rm *)` | `echo $(rm -rf /)` | yes | `$()` and backtick contents checked |
| `Bash(rm *)` | `echo $(date)` | no | no subcommand matches |
| `Bash(git push *)` | `echo $(date)` | **yes** | patterns specifying more than the command name run anyway on `$()`, backticks, or `$VAR` |

**The filter fails open** when the command cannot be parsed. Because `if` is best-effort, **use the permission system, not a hook `if`, to enforce a hard allow or deny.**

---

## 5. Handler types

| Type | What it does |
|---|---|
| `command` | Runs a shell command. JSON on stdin, results via exit code and stdout |
| `http` | POSTs the JSON to a URL. Results via the response body |
| `mcp_tool` | Calls a tool on an already-connected MCP server. Text output treated like stdout |
| `prompt` | Single-turn LLM evaluation returning a yes/no JSON decision |
| `agent` | Spawns a subagent that can use tools to verify before deciding. **Experimental** |

**All matching hooks run in parallel**, and identical handlers are deduplicated: command hooks by command string plus `args`, HTTP hooks by URL.

Handlers run in the current directory with Claude Code's environment. `$CLAUDE_CODE_REMOTE` is `"true"` in remote web environments. `$CLAUDE_CODE_BRIDGE_SESSION_ID` holds the Remote Control session ID while connected (v2.1.199+).

### Common fields

| Field | Description |
|---|---|
| `type` | Required |
| `if` | Permission-rule filter, tool events only |
| `timeout` | Seconds. Defaults below |
| `statusMessage` | Custom spinner message while running |
| `once` | Runs once per session then is removed. **Only honored in skill frontmatter**; ignored in settings files and agent frontmatter |

**Timeout defaults:**

| Type | Default |
|---|---|
| `command`, `http`, `mcp_tool` | 600s |
| ...on `UserPromptSubmit` | 30s |
| ...on `MessageDisplay` | 10s |
| `prompt` | 30s |
| `agent` | 60s |
| Any type on `SessionEnd` | **All share a 1.5s budget**, raised to match a longer per-hook `timeout`, up to 60s |

### Command hooks: exec form versus shell form

| Field | Description |
|---|---|
| `command` | Shell command, or with `args`, the executable to spawn |
| `args` | Argument list. **Its presence switches to exec form** |
| `async` | Runs in background without blocking |
| `asyncRewake` | Background, and **wakes Claude on exit code 2**. Implies `async`. stderr (or stdout if stderr is empty) is shown to Claude as a system reminder |
| `shell` | `"bash"` or `"powershell"`. Defaults to bash, or powershell on Windows without Git Bash. **Does not require `CLAUDE_CODE_USE_POWERSHELL_TOOL`** since hooks spawn PowerShell directly. Ignored when `args` is set |

**Exec form** (`args` present): `command` resolves as an executable on `PATH` and is spawned directly. **No shell**, so each `args` element is one argument exactly as written, and special characters pass through verbatim. Path placeholders substitute as plain strings.

**Shell form** (`args` absent): the string goes to `sh -c`, Git Bash on Windows, or PowerShell. The shell tokenizes, expands variables, and interprets pipes and redirects.

**Rule of thumb: set `args` whenever the hook references a path placeholder.** Omit it only when you need pipes or `&&`.

```json
{ "type": "command", "command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/format.js", "--fix"] }
```

The shell equivalent needs quoting: `"node \"${CLAUDE_PLUGIN_ROOT}\"/scripts/format.js --fix"`.

**Windows caveat**: exec form needs `command` to resolve to a real executable such as a `.exe`. The `.cmd` and `.bat` shims npm and friends install in `node_modules/.bin` **cannot be spawned without a shell**. Invoke the underlying script with `node` directly, which works everywhere because `node.exe` is a real binary.

Both forms export `CLAUDE_PROJECT_DIR`, `CLAUDE_PLUGIN_ROOT`, and `CLAUDE_PLUGIN_DATA` as environment variables on the spawned process.

Plugin `${user_config.*}` values substitute **in exec form only**. A shell-form plugin hook referencing them **fails with an error instead of running**. Read `$CLAUDE_PLUGIN_OPTION_<KEY>` from the environment instead, or add `args` to switch forms.

### HTTP hooks

| Field | Description |
|---|---|
| `url` | Required, POST target |
| `headers` | Values support `$VAR_NAME` / `${VAR_NAME}` interpolation |
| `allowedEnvVars` | **Required for any interpolation to work.** Unlisted references become empty strings |

**Error handling differs from command hooks**: non-2xx responses, connection failures, and timeouts are all **non-blocking** and execution continues. **HTTP status codes alone cannot block.** To block, return a 2xx with a JSON body containing the decision.

### MCP tool hooks

| Field | Description |
|---|---|
| `server` | Configured server name. For plugin-bundled servers use the scoped `plugin:<plugin-name>:<server-name>`, **not** the bare key. **The server must already be connected**; the hook never triggers OAuth or a connection flow |
| `tool` | Tool name |
| `input` | Arguments. Strings support `${path}` substitution from the hook input, e.g. `"${tool_input.file_path}"` |

Available on every event once servers are connected. **`SessionStart` and `Setup` typically fire before servers finish connecting**, so hooks there should expect a "not connected" error on first run.

### Prompt and agent hooks

| Field | Description |
|---|---|
| `prompt` | Required. `$ARGUMENTS` is the hook input JSON. Escape literals with a backslash |
| `model` | Defaults to a fast model (Haiku) |

Both return `{"ok": true/false, "reason": "..."}`.

On `"ok": false`:

- **`Stop` / `SubagentStop`**: the reason is fed back so Claude keeps working
- **`PreToolUse`**: the call is denied; **by default the turn ends** and the reason appears as a chat warning. Set `continueOnBlock: true` to return the reason as the tool error so Claude can adjust and continue (v2.1.210 changed this default)
- **`PostToolUse`**: the turn ends by default; `continueOnBlock: true` continues it
- **`PostToolBatch`, `UserPromptSubmit`, `UserPromptExpansion`**: the turn ends with a warning line

Agent hooks additionally get up to **50 tool-use turns**.

Use **prompt** hooks when the input JSON is enough to decide. Use **agent** hooks when you must verify against actual codebase state.

### Path placeholders

| Placeholder | Resolves to |
|---|---|
| `${CLAUDE_PROJECT_DIR}` | Project root. Also set for stdio MCP servers and plugin LSP servers |
| `${CLAUDE_PLUGIN_ROOT}` | Plugin installation directory. **Changes on each plugin update** |
| `${CLAUDE_PLUGIN_DATA}` | Plugin persistent data directory, for state that survives updates |

Prefer exec form with these. In shell form, wrap each in double quotes.

---

## 6. Input

### Common input fields

| Field | Description |
|---|---|
| `session_id` | Session identifier |
| `prompt_id` | UUID of the prompt being processed. **Matches the `prompt.id` OpenTelemetry attribute**, so hook output correlates with telemetry. Absent until first user input (v2.1.196+) |
| `transcript_path` | Path to conversation JSON. **Written asynchronously and may lag the in-memory conversation.** Hooks needing the current turn's final text should use `last_assistant_message` on `Stop`/`SubagentStop` instead |
| `cwd` | Working directory when the hook fired |
| `permission_mode` | `"default"`, `"plan"`, `"acceptEdits"`, `"auto"`, `"dontAsk"`, `"bypassPermissions"`. **The Manual mode arrives as `"default"`, never `"manual"`** |
| `effort` | Object with `level`: `"low"` through `"max"`. Ultracode reports as `"xhigh"`. Also available as `$CLAUDE_EFFORT` |
| `hook_event_name` | The event that fired |
| `agent_id` | Present only inside a subagent call |
| `agent_type` | Agent name. For custom subagents this is the frontmatter `name`, **not the filename**. For plugin subagents, the scoped identifier |

**Only `SessionStart` hooks can receive a `model` field, and it is not guaranteed.** There is no `$CLAUDE_MODEL` variable. A hook inherits `$ANTHROPIC_MODEL` from your shell, but that does not change when you switch with `/model`.

**Claude Code removes `OTEL_*` exporter variables from every subprocess it spawns**, including hooks.

Example `PreToolUse` stdin:

```json
{
  "session_id": "abc123",
  "prompt_id": "550e8400-e29b-41d4-a716-446655440000",
  "transcript_path": "/home/user/.claude/projects/.../transcript.jsonl",
  "cwd": "/home/user/my-project",
  "permission_mode": "default",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": { "command": "npm test", "run_in_background": false },
  "tool_use_id": "toolu_01ABC123..."
}
```

---

## 7. Output: exit codes

| Exit code | Meaning |
|---|---|
| **0** | Success. stdout parsed for JSON output. For most events stdout goes to the debug log only |
| **2** | Blocking error. **stdout and any JSON are ignored**; stderr is fed to Claude as an error |
| **Anything else** | Non-blocking error for most events. The transcript shows `<hook name> hook error` plus the first stderr line; execution continues |

**The biggest footgun in the whole system:** exit code 1, the conventional Unix failure code, **blocks nothing**. If your hook enforces policy, use `exit 2`. The single exception is `WorktreeCreate`, where **any** non-zero code aborts creation.

**stdout reaches Claude only on three events**: `UserPromptSubmit`, `UserPromptExpansion`, and `SessionStart`. Everywhere else, use `additionalContext`.

A hook that exits 2 while printing JSON that fails schema validation **still blocks** (v2.1.214+); stderr becomes the reason and the validation failure goes to the debug log.

### Exit code 2 behavior per event

| Event | Can block? | Effect of exit 2 |
|---|---|---|
| `PreToolUse` | Yes | Blocks the tool call |
| `PermissionRequest` | Yes | Denies the permission |
| `UserPromptSubmit` | Yes | Blocks processing and **erases the prompt** |
| `UserPromptExpansion` | Yes | Blocks the expansion |
| `Stop` | Yes | Prevents stopping, continues the conversation |
| `SubagentStop` | Yes | Prevents the subagent stopping |
| `TeammateIdle` | Yes | Keeps the teammate working |
| `TaskCreated` | Yes | **Rolls back** the task creation |
| `TaskCompleted` | Yes | Prevents completion |
| `ConfigChange` | Yes | Blocks the change (**except `policy_settings`**) |
| `PostToolBatch` | Yes | Stops the agentic loop before the next model call |
| `PreCompact` | Yes | Blocks compaction |
| `Elicitation` | Yes | Denies the elicitation |
| `ElicitationResult` | Yes | Blocks the response (becomes decline) |
| `WorktreeCreate` | Yes | **Any non-zero code** fails creation |
| `PostToolUse` | No | Shows stderr to Claude; the tool already ran |
| `PostToolUseFailure` | No | Shows stderr to Claude |
| `PermissionDenied` | No | Ignored; the denial already happened. Use JSON `retry: true` |
| `StopFailure` | No | **Output and exit code entirely ignored** |
| `Notification`, `SubagentStart`, `SessionStart`, `Setup`, `SessionEnd`, `CwdChanged`, `FileChanged`, `PostCompact`, `WorktreeRemove` | No | stderr to user only |
| `InstructionsLoaded` | No | Exit code ignored |
| `MessageDisplay` | No | Original text is displayed |

For `SessionStart`, `Setup`, and `SubagentStart`, exit-2 stderr renders as a `<hook name> hook error` notice (v2.1.199+). **Claude does not see it**, and for `SubagentStart` the notice appears in the subagent's own transcript.

### HTTP response handling

| Response | Treated as |
|---|---|
| 2xx, empty body | Exit 0 with no output |
| 2xx, plain text | Success; text added as context |
| 2xx, JSON | Parsed with the same JSON output schema |
| Non-2xx | Non-blocking error |
| Connection failure or timeout | Non-blocking error |

---

## 8. Output: JSON

**Choose one approach per hook.** Either exit codes alone, or exit 0 and print JSON. **JSON is only processed on exit 0.**

Your stdout must contain **only** the JSON object. A shell profile that prints on startup will break parsing (see section 12).

**Output strings, including `additionalContext`, `systemMessage`, and plain stdout, are capped at 10,000 characters.** Overflow is saved to a file and replaced with a preview and path.

Three kinds of fields: **universal** fields, **top-level `decision`/`reason`**, and **`hookSpecificOutput`** (which requires `hookEventName`).

### Universal fields

| Field | Default | Description |
|---|---|---|
| `continue` | `true` | `false` stops Claude entirely after the hook. **Takes precedence over event-specific decisions** |
| `stopReason` | none | Shown to the user when `continue` is false. **Not shown to Claude** |
| `suppressOutput` | `false` | Hides stdout from the transcript; still in the debug log |
| `systemMessage` | none | Warning shown to the user |
| `terminalSequence` | none | Escape sequence for Claude Code to emit on your behalf |

For `PreToolUse` and `PostToolUse`, `continue: false` applies even when the tool call fails or completes mid-stream.

### Terminal notifications

Hooks run **without a controlling terminal** on macOS and Linux as of v2.1.139, so writing to `/dev/tty` fails. Windows has no `/dev/tty` at all. Return `terminalSequence` and Claude Code emits it through its own write path: race-free, works in tmux and screen, works on Windows.

Allowlist: OSC `0`, `1`, `2` (titles), OSC `9` (iTerm2, ConEmu, Windows Terminal, WezTerm, including `9;4` taskbar progress), OSC `99` (Kitty), OSC `777` (urxvt, Ghostty, Warp), and bare BEL. **Anything else is rejected and the field ignored**, including CSI cursor and color sequences, OSC 8 hyperlinks, OSC 52 clipboard writes, and OSC 1337. The allowlist is deliberately restricted so a hook can never corrupt an on-screen prompt.

```bash
#!/bin/bash
input=$(cat)
title="Claude Code"
body=$(jq -r '.message // "Needs your attention"' <<<"$input")
seq=$(printf '\033]777;notify;%s;%s\007' "$title" "$body")
jq -nc --arg seq "$seq" '{terminalSequence: $seq}'
```

### additionalContext

Passes a string into Claude's context, wrapped in a system reminder and inserted where the hook fired. **Claude reads it on the next model request; it does not appear as a chat message.**

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "This file is generated. Edit src/schema.ts and run `bun generate` instead."
  }
}
```

Where the reminder lands:

| Events | Position |
|---|---|
| `SessionStart`, `Setup`, `SubagentStart` | Start of the conversation, before the first prompt |
| `UserPromptSubmit`, `UserPromptExpansion` | Alongside the submitted prompt |
| `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch` | Next to the tool result |
| `Stop`, `SubagentStop` | End of the turn; the conversation continues so Claude can act on it |

**Write it as factual statements, not imperative system instructions.** "The deployment target is production" reads as project information. Text framed as out-of-band system commands **can trigger Claude's prompt-injection defenses**, which makes Claude surface the text to you instead of treating it as context.

Two more behaviors worth knowing:

- For static instructions, prefer CLAUDE.md. It loads without running a script.
- **The injected text is saved in the transcript.** On `--continue` or `--resume`, Claude Code **replays the saved text rather than re-running the hook** for past turns, so timestamps and commit SHAs go stale. `SessionStart` hooks do re-run on resume with `source` set to `"resume"` or `"fork"`.

Nesting matters: for `UserPromptSubmit`, `additionalContext` must be **inside `hookSpecificOutput`**. At the top level it is silently ignored.

### Decision control by event

| Events | Pattern | Key fields |
|---|---|---|
| `UserPromptSubmit`, `UserPromptExpansion`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch`, `Stop`, `SubagentStop`, `ConfigChange`, `PreCompact` | Top-level `decision` | `decision: "block"`, `reason`. Stop/SubagentStop also accept `additionalContext` for non-error feedback |
| `TeammateIdle`, `TaskCreated`, `TaskCompleted` | Exit code or `continue: false` | Exit 2 blocks with stderr feedback |
| `PreToolUse` | `hookSpecificOutput` | `permissionDecision` (allow/deny/ask/defer), `permissionDecisionReason`, `updatedInput` |
| `PermissionRequest` | `hookSpecificOutput` | `decision.behavior` (allow/deny), `decision.updatedInput`, `decision.updatedPermissions` |
| `PermissionDenied` | `hookSpecificOutput` | `retry: true` tells the model it may retry |
| `WorktreeCreate` | path return | Command hook prints the path on stdout; HTTP returns `hookSpecificOutput.worktreePath`. **Failure or missing path fails creation** |
| `Elicitation`, `ElicitationResult` | `hookSpecificOutput` | `action` (accept/decline/cancel), `content` |
| `MessageDisplay` | `hookSpecificOutput` | `displayContent` replaces on-screen text. **Display-only**: the transcript and what Claude sees keep the original |
| `SessionStart`, `Setup`, `SubagentStart` | Context only | `additionalContext`; SessionStart also `initialUserMessage`, `watchPaths`, `sessionTitle`, `reloadSkills` |
| `WorktreeRemove`, `Notification`, `SessionEnd`, `PostCompact`, `InstructionsLoaded`, `StopFailure`, `CwdChanged`, `FileChanged` | None | Side effects only |

Events that can **rewrite** rather than only allow or block:

- `PreToolUse`: `updatedInput` replaces tool arguments before it runs
- `PermissionRequest`: `updatedInput` inside `decision`
- `PostToolUse`: `updatedToolOutput` replaces the result
- `UserPromptSubmit`: **cannot replace the prompt**, only inject alongside it

For redaction, intercept at `PreToolUse` for outbound inputs and `PostToolUse` for inbound results.

### PreToolUse permissionDecision values

- **`"allow"`**: skips the interactive prompt. **Deny and ask rules still apply**, including managed deny lists, as do org-`ask` connector tools and `requiresUserInteraction` MCP tools
- **`"deny"`**: cancels the call and sends the reason to Claude
- **`"ask"`**: shows the normal prompt
- **`"defer"`**: `-p` mode only. Exits the process with the tool call preserved so an SDK wrapper can collect input and resume

### Combining multiple hooks

**Every matching hook runs to completion before results are merged.** One hook returning `deny` does **not** stop sibling hooks from executing, so never rely on a deny to suppress another hook's side effects.

For `PreToolUse` permission decisions the **most restrictive** answer applies, in the order `deny` → `defer` → `ask` → `allow`. `additionalContext` from every hook is kept and passed together.

When multiple `PreToolUse` hooks return `updatedInput`, **the last one to finish wins, and since hooks run in parallel the order is non-deterministic.** Avoid more than one hook modifying the same tool's input.

---

## 9. Hooks and permissions

This is the most important interaction in the system, and it is deliberately asymmetric.

**`PreToolUse` hooks fire before any permission-mode check, in every mode, including `dontAsk` and `bypassPermissions`.** A hook returning `deny` blocks the tool even under `--dangerously-skip-permissions`. This is how you enforce policy users cannot bypass by changing their mode.

**The reverse is not true.** A hook returning `"allow"`:

- does **not** override deny rules from any settings scope, including managed
- does **not** suppress a matching ask rule
- does **not** suppress org-`ask` connector tools or `requiresUserInteraction` MCP tools

**Hooks can tighten restrictions but never loosen them past what permission rules allow.**

One more asymmetry from the permissions side: a hook exiting with code 2 stops the call **before** permission rules are evaluated, so the block applies even when an allow rule would have permitted it.

**The pattern this enables**: allow `"Bash"` broadly in your permission rules and register a PreToolUse hook that rejects the specific commands you want blocked. Prompt-free operation with programmable exclusions.

---

## 10. Environment variables from hooks

`SessionStart`, `Setup`, `CwdChanged`, and `FileChanged` hooks — **and only those four** — receive `CLAUDE_ENV_FILE`, a path where you persist environment variables for subsequent Bash commands.

```bash
#!/bin/bash
if [ -n "$CLAUDE_ENV_FILE" ]; then
  echo 'export NODE_ENV=production' >> "$CLAUDE_ENV_FILE"
  echo 'export PATH="$PATH:./node_modules/.bin"' >> "$CLAUDE_ENV_FILE"
fi
exit 0
```

**Use append (`>>`)** to preserve variables set by other hooks.

To capture everything a setup command changes, diff the exported environment:

```bash
ENV_BEFORE=$(export -p | sort)
source ~/.nvm/nvm.sh
nvm use 20
if [ -n "$CLAUDE_ENV_FILE" ]; then
  comm -13 <(echo "$ENV_BEFORE") <(export -p | sort) >> "$CLAUDE_ENV_FILE"
fi
```

The direnv pattern pairs `SessionStart` with `CwdChanged`:

```json
{
  "hooks": {
    "SessionStart": [{ "hooks": [{ "type": "command", "command": "direnv export bash > \"$CLAUDE_ENV_FILE\"" }] }],
    "CwdChanged": [{ "hooks": [{ "type": "command", "command": "direnv export bash > \"$CLAUDE_ENV_FILE\"" }] }]
  }
}
```

`devbox shellenv` or `devbox global shellenv` substitute directly.

---

## 11. Selected event specifics

### SessionStart

Runs on **every** session, so keep it fast. **Only `command` and `mcp_tool` types are supported.**

Extra output fields:

| Field | Effect |
|---|---|
| `additionalContext` | Context at the start of the conversation |
| `initialUserMessage` | Becomes the **first user turn** in `-p` mode even with no prompt. Unlike `additionalContext`, which attaches to an existing turn, **this creates the turn** |
| `sessionTitle` | Same effect as `/rename`. Applies on `startup`, `resume`, `fork`; ignored on `clear` and `compact` |
| `watchPaths` | Absolute paths to watch for `FileChanged` |
| `reloadSkills` | Re-scans skill and command directories **after SessionStart hooks complete** |

`reloadSkills` exists because **skill discovery normally runs before SessionStart hooks finish**, so files a hook writes would otherwise only appear next session:

```bash
#!/bin/bash
git -C ~/.claude/skills/team-skills pull --quiet 2>/dev/null || \
  git clone --quiet https://git.example.com/your-org/team-skills.git ~/.claude/skills/team-skills
echo '{"hookSpecificOutput": {"hookEventName": "SessionStart", "reloadSkills": true}}'
```

The input `session_title` field lets a hook check before overwriting a title you set explicitly.

Since plain stdout already reaches Claude on this event, a context-only hook can print directly without building JSON.

### Setup

**Does not fire on normal startup.** Only `--init-only`, or `--init`/`--maintenance` combined with `-p`. In an interactive session those two flags currently fire nothing.

`--init-only` runs Setup plus `SessionStart` with the `startup` matcher, then exits. **On success it prints nothing**; confirm with `--debug-file <path>`.

Cannot block: any non-zero code including 2 surfaces stderr as a notice and continues. **Plain stdout goes to the debug log only**, so use `additionalContext` to reach Claude. Only `command` and `mcp_tool` supported.

Because Setup does not fire on every launch, a plugin needing a dependency **cannot rely on it alone**. The practical pattern is checking on first use and installing on miss, e.g. testing for `${CLAUDE_PLUGIN_DATA}/node_modules`.

### UserPromptSubmit

**30-second default timeout**, not 600, because it blocks model processing.

On timeout the hook is canceled and **all output including `additionalContext` is discarded**; the prompt still reaches Claude without it. v2.1.196+ shows a notice naming the hook.

An **Agent SDK callback** hook on this event that times out **blocks the prompt** instead, because a callback there may be a policy gate that must not fail open.

Two ways to add context on exit 0: plain stdout (shown as hook output in the transcript) or `additionalContext` (injected as a system reminder with no visible entry).

Output fields: `decision: "block"` erases the prompt from context, `reason` shown to the user, `additionalContext`, `sessionTitle`, and `suppressOriginalPrompt` to omit the original text from the block message.

### UserPromptExpansion

**Covers the path `PreToolUse` does not.** A `PreToolUse` hook matching the `Skill` tool fires only when *Claude* calls it; typing `/skillname` directly bypasses `PreToolUse` entirely. `UserPromptExpansion` fires on that direct path.

Input adds `expansion_type` (`slash_command` or `mcp_prompt`), `command_name`, `command_args`, `command_source`, and the original `prompt`.

### InstructionsLoaded

Fires at session start for eager files and again on lazy loads (nested CLAUDE.md, `paths:` rule matches). **Asynchronous, observability-only, no blocking.**

Input: `file_path`, `memory_type` (`User`/`Project`/`Local`/`Managed`), `load_reason`, `globs` (only for `path_glob_match`), `trigger_file_path`, `parent_file_path`.

Use for audit logging and compliance tracking.

### PermissionRequest

Fires when Claude Code is about to ask **you**. Auto-approval example:

```json
{
  "hooks": {
    "PermissionRequest": [
      {
        "matcher": "ExitPlanMode",
        "hooks": [{ "type": "command", "command": "echo '{\"hookSpecificOutput\": {\"hookEventName\": \"PermissionRequest\", \"decision\": {\"behavior\": \"allow\"}}}'" }]
      }
    ]
  }
}
```

**Keep the matcher as narrow as possible.** Matching `.*` or leaving it empty auto-approves every prompt including file writes and shell commands.

`decision.updatedPermissions` can carry a `setMode` entry:

```json
{ "type": "setMode", "mode": "acceptEdits", "destination": "session" }
```

`bypassPermissions` here only applies if the session was launched with bypass already available, and **is never persisted as `defaultMode`**.

Limitations: in `-p` mode the prompt only exists when the SDK's `canUseTool` callback supplies it. **In plain `-p` runs or with `--permission-prompt-tool`, use `PreToolUse` instead.** Background subagents cannot show a prompt in non-interactive mode: hooks still run, and **if no hook returns a decision, the call is denied**.

### Stop

Fires **whenever Claude finishes responding, not only at task completion**. Does not fire on user interrupts. API errors fire `StopFailure` instead.

**The block cap**: Claude Code overrides a Stop hook after it blocks **eight consecutive times** without progress. Guard against it by checking `stop_hook_active`:

```bash
#!/bin/bash
INPUT=$(cat)
if [ "$(echo "$INPUT" | jq -r '.stop_hook_active')" = "true" ]; then
  exit 0
fi
# ... rest of logic
```

Raise the cap with `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`.

### FileChanged

The `matcher` is **a list of literal filenames to watch**, split rather than evaluated as a regex when building the watch list. Same value also filters which groups run.

```json
{ "hooks": { "FileChanged": [{ "matcher": ".envrc|.env", "hooks": [{ "type": "command", "command": "direnv export bash > \"$CLAUDE_ENV_FILE\"" }] }] } }
```

Extend the watch set with `watchPaths` from a `SessionStart` hook.

### Hooks in skills and agents

All events supported. **For subagents, `Stop` hooks are automatically converted to `SubagentStop`.** Scoped to the component's lifetime and cleaned up when it finishes.

```yaml
---
name: secure-operations
description: Perform operations with security checks
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "./scripts/security-check.sh"
---
```

**Frontmatter hooks in a project subagent run only after you accept the workspace trust dialog** for the folder the agent file came from (v2.1.218+).

---

## 12. Operations

### The `/hooks` menu

Read-only browser showing every event with a count, drilling into matchers and handler details. Sources are labeled: `User Settings`, `Project Settings`, `Local Settings`, `Plugin Hooks`, `Session Hooks` (in-memory for this session), `Built-in Hooks` (registered internally by Claude Code).

**To change anything, edit the JSON or ask Claude.**

### Disabling

`"disableAllHooks": true`. **There is no way to disable an individual hook** while keeping it configured.

It respects the managed hierarchy: `disableAllHooks` in user, project, or local settings **cannot disable managed hooks**. Only the managed level can.

### Debugging

The transcript (`Ctrl+O`) shows one line per fired hook: success is silent, blocking errors show stderr, non-blocking errors show a `<hook name> hook error` notice plus the first stderr line.

For full detail, `claude --debug-file /tmp/claude.log` then `tail -f`. Mid-session, `/debug` enables logging and reports the path.

Test a script manually:

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"ls"}}' | ./my-hook.sh
echo $?
```

### Troubleshooting

**Hook not firing:** confirm it appears under the right event in `/hooks`; **matchers are case-sensitive**; verify the event (`PreToolUse` before, `PostToolUse` after); in `-p`, `PermissionRequest` fires only with the SDK callback or inside background subagents.

**Hook error notice:** test manually; use absolute paths or `${CLAUDE_PROJECT_DIR}` for "command not found"; install `jq`; `chmod +x` the script.

**`/hooks` shows nothing:** restart to force a reload if the file watcher missed the change; check for trailing commas or comments (invalid JSON); confirm the file location.

**JSON validation failed** despite valid output. This one is subtle. Shell-form hooks spawn `sh -c` or Git Bash, which is non-interactive but **still sources your profile in some configurations** (Git Bash, or `BASH_ENV` pointing at `~/.bashrc`). An unconditional `echo` there gets prepended to your JSON:

```text
Shell ready on arm64
{"decision": "block", "reason": "Not allowed"}
```

Fix by guarding profile output on interactivity:

```bash
if [[ $- == *i* ]]; then
  echo "Shell ready"
fi
```

### The Bash blind spot

Claude can create or modify files by running shell commands, which no `Edit|Write` matcher sees. For compliance scanning or audit logging, add a **`Stop` hook that scans the working tree once per turn**. For per-call coverage, also match `Bash` and list changes with `git status --porcelain`.

---

## 13. Gotchas

1. **Exit code 1 blocks nothing.** Only exit 2 blocks, except on `WorktreeCreate` where any non-zero code aborts.
2. **Exit 0 with no output is "no decision", not approval.** The permission flow still runs.
3. **Never mix exit 2 and JSON.** JSON is processed only on exit 0.
4. **stdout reaches Claude on only three events**: `UserPromptSubmit`, `UserPromptExpansion`, `SessionStart`. Elsewhere use `additionalContext`.
5. **A matcher's evaluation mode depends on its characters.** `mcp__memory` is an exact string and matches nothing; you need `mcp__memory__.*`.
6. **Regex matchers are unanchored.** `Edit.*` also matches `NotebookEdit`.
7. **`FileChanged` and `StopFailure` use a narrower exact-match set** and only `|` separates alternatives.
8. **Plugin MCP tools need the scoped name.** `mcp__database-tools__.*` never fires for a plugin-bundled server.
9. **`if` on a non-tool event means the hook never runs.**
10. **`if` holds exactly one rule** and **fails open** on unparseable commands. It is not an enforcement mechanism.
11. **A hook `deny` cannot be overridden by any permission mode; a hook `allow` cannot override a deny rule.** Hooks tighten, never loosen.
12. **All matching hooks run to completion even when one denies.** Siblings still produce their side effects.
13. **Parallel `updatedInput` returns race**, and the last to finish wins.
14. **`continue: false` overrides every event-specific decision.**
15. **Output is capped at 10,000 characters**, then spilled to a file.
16. **`additionalContext` is replayed from the transcript on resume**, not re-run, so dynamic values go stale.
17. **Imperative phrasing in `additionalContext` can trigger prompt-injection defenses.** Write facts.
18. **`additionalContext` at the top level is silently ignored** on `UserPromptSubmit`; it must be inside `hookSpecificOutput`.
19. **Hooks have no controlling terminal.** Use `terminalSequence`, not `/dev/tty`.
20. **`CLAUDE_ENV_FILE` exists only on four events**: SessionStart, Setup, CwdChanged, FileChanged.
21. **`OTEL_*` variables are stripped from every hook subprocess.**
22. **There is no `$CLAUDE_MODEL`**, and only `SessionStart` may receive a `model` field.
23. **`transcript_path` lags the in-memory conversation.** Use `last_assistant_message` on Stop.
24. **`Setup` does not fire on normal startup**, and prints nothing on success.
25. **`SessionStart` and `Setup` fire before MCP servers connect**, so `mcp_tool` hooks there fail on first run.
26. **`SessionStart` runs on every session**, including resume and compaction. Keep it fast.
27. **Skill discovery finishes before SessionStart hooks do.** Use `reloadSkills`.
28. **Stop hooks fire on every response**, not just task completion, and are overridden after **eight consecutive blocks**.
29. **`once: true` is honored only in skill frontmatter.**
30. **Exec form is required for `.cmd`/`.bat` avoidance on Windows** and for reliable path placeholders; shell form plugin hooks referencing `${user_config.*}` fail outright.
31. **`SessionEnd` hooks share a 1.5-second budget** across all of them.
32. **`disableAllHooks` cannot disable managed hooks** from a non-managed scope.
33. **A shell profile that echoes unconditionally breaks JSON parsing.**
34. **`Edit|Write` matchers miss file changes made through Bash.** Add a Stop-hook sweep.

---

## Reference links

- Hooks reference: https://code.claude.com/docs/en/hooks
- Automate actions with hooks (guide): https://code.claude.com/docs/en/hooks-guide
- Bash command validator example: https://github.com/anthropics/claude-code/blob/main/examples/hooks/bash_command_validator_example.py
- Permissions: https://code.claude.com/docs/en/permissions
- Permission modes: https://code.claude.com/docs/en/permission-modes
- Settings, hook configuration: https://code.claude.com/docs/en/settings
- Skills: https://code.claude.com/docs/en/skills
- Subagents: https://code.claude.com/docs/en/sub-agents
- Plugins reference: https://code.claude.com/docs/en/plugins-reference
- MCP: https://code.claude.com/docs/en/mcp
- Agent SDK hooks: https://code.claude.com/docs/en/agent-sdk/hooks
- Environment variables: https://code.claude.com/docs/en/env-vars
- Full docs index: https://code.claude.com/docs/llms.txt
