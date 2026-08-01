# Claude Code: Headless Mode and the Agent SDK Reference

A study guide to running Claude Code programmatically: print mode, bare mode, output formats and structured schemas, the stream-json event protocol, process lifecycle, permission strategies for unattended runs, and CI patterns.

Verified against code.claude.com/docs/en/headless.

---

## 1. Three surfaces, one engine

The **Agent SDK** gives you the same tools, agent loop, and context management that power Claude Code:

| Surface | Use when |
|---|---|
| **CLI (`claude -p`)** | Scripts, cron jobs, CI/CD |
| **Python package** | Embedding in a Python program |
| **TypeScript package** | Embedding in a TS/JS program |

The packages add structured outputs, tool approval callbacks, and native message objects. This guide covers the CLI.

```bash
claude -p "Find and fix the bug in auth.py" --allowedTools "Read,Edit,Bash"
```

**All CLI options work with `-p`.**

---

## 2. Bare mode

**Without `--bare`, `claude -p` loads the same context an interactive session would**: hooks, skills, plugins, MCP servers, auto memory, and CLAUDE.md from the working directory and `~/.claude`.

That is usually wrong for automation. **A hook in a teammate's `~/.claude` or an MCP server in the project's `.mcp.json` will run.** Bare mode never reads them, so **only flags you pass explicitly take effect** — the same result on every machine.

```bash
claude --bare -p "Summarize this file" --allowedTools "Read"
```

**In bare mode Claude has Bash, file read, and file edit.** Load anything else deliberately:

| To load | Use |
|---|---|
| System prompt additions | `--append-system-prompt`, `--append-system-prompt-file` |
| Settings | `--settings <file-or-json>` |
| MCP servers | `--mcp-config <file-or-json>` |
| Custom agents | `--agents <json>` |
| A plugin | `--plugin-dir <path>`, `--plugin-url <url>` |

**Bare mode skips OAuth and keychain reads.** For Anthropic auth, set `ANTHROPIC_API_KEY` or configure an `apiKeyHelper` in the JSON you pass to `--settings`. Bedrock, Agent Platform, and Foundry use their usual provider credentials.

**The docs recommend `--bare` for scripted and SDK calls, and say it will become the default for `-p` in a future release.** Worth adopting now.

---

## 3. Output formats

| Format | Returns |
|---|---|
| `text` | Plain text. **Default** |
| `json` | Structured JSON: `result`, `session_id`, metadata, `total_cost_usd` and a per-model cost breakdown |
| `stream-json` | Newline-delimited JSON, one event per line |

**The `json` cost breakdown means scripted callers can track spend per invocation without consulting the usage dashboard.**

### Structured output against a schema

```bash
claude -p "Extract the main function names from auth.py" \
  --output-format json \
  --json-schema '{"type":"object","properties":{"functions":{"type":"array","items":{"type":"string"}}},"required":["functions"]}'
```

The schema result lands in **`structured_output`**, alongside the usual metadata.

**An invalid schema exits with `Error: --json-schema is not a valid JSON Schema`** plus the validator's diagnostic. Claude Code **accepts the `format` keyword but treats it as an annotation and does not enforce it.**

Before v2.1.205, an invalid schema was **silently ignored** and returned unstructured text, and any schema containing `format` was treated as invalid. If you are on an older version, a schema that appears to do nothing is the symptom.

### Parsing

```bash
claude -p "Summarize this project" --output-format json | jq -r '.result'
claude -p "..." --output-format json --json-schema '...' | jq '.structured_output'
```

---

## 4. Streaming

```bash
claude -p "Explain recursion" --output-format stream-json --verbose --include-partial-messages
```

**`stream-json` requires `--verbose`; token-level deltas additionally require `--include-partial-messages`.**

**The last line is a `result` message** with the final text, cost, and session metadata.

**A slow consumer no longer truncates the response** (v2.1.214+): Claude Code waits for queued output to drain, scaling with the backlog, capped at 30 seconds. Before that the wait was about two seconds, **which could cut off the end of a large response.**

Filtering to just the text:

```bash
claude -p "Write a poem" --output-format stream-json --verbose --include-partial-messages | \
  jq -rj 'select(.type == "stream_event" and .event.delta.type? == "text_delta") | .event.delta.text'
```

### Subagent messages

Subagent messages appear as `assistant` and `user` messages whose **`parent_tool_use_id`** is the ID of the spawning tool call. **Main-conversation messages carry `null` there.**

**By default only subagent `tool_use` and `tool_result` blocks are emitted.** Add `--forward-subagent-text` or `CLAUDE_CODE_FORWARD_SUBAGENT_TEXT` (v2.1.211+) to also get text and thinking blocks, so you can reconstruct each subagent's transcript.

**With either enabled, messages forward from every nesting depth**, and a nested subagent's messages carry the ID of the Agent tool call that spawned it, letting you rebuild the full tree. **Before v2.1.219, nested subagent messages did not appear at all.**

### system/init

Reports session metadata: model, tools, MCP servers, plugins. **It is the first event unless startup events precede it** — `plugin_install` events with `CLAUDE_CODE_SYNC_PLUGIN_INSTALL`, or `hook_started` / `hook_progress` / `hook_response` while a `SessionStart` or `Setup` hook runs.

Those hook events **stream live as the hook produces them**, except in v2.1.169 through v2.1.203 where they arrived in one batch after completion.

**`capabilities`** (v2.1.205+) is an array of protocol behavior names such as `interrupt_receipt_v1`. **Feature-detect on this instead of comparing version strings**, and ignore values you do not recognize. Absent on earlier versions.

### Failing CI on load errors

Two field pairs on `system/init` exist precisely for this, and both **omit the error key entirely when there are none**, so a non-empty array is a clean gate condition:

| Field | Contents |
|---|---|
| `plugins` | Loaded plugins, each `name` and `path` |
| `plugin_errors` | Load-time errors: `plugin`, `type`, `message`. Covers unsatisfied dependency versions and `--plugin-dir` failures. **Affected plugins are demoted and absent from `plugins`** |
| `mcp_servers` | Servers in the session, each `name` and `status` |
| `mcp_server_errors` | `--mcp-config` entries skipped by validation (v2.1.219+): `name`, `type`, `message`. Types include `unknown_type`, `url_missing_type`, `invalid_config`, `reserved_name` |

**The important behavior: Claude Code validates `--mcp-config` entries at startup, skips the invalid ones, and the run continues and exits cleanly.** A server that never loaded does not fail the run on its own.

**The stderr warning is not a reliable signal either**: it prints when you run by hand, but **when stderr is redirected or captured by a CI runner or SDK host, no warning appears** and the skipped entries are reported only in `mcp_server_errors`.

### system/api_retry

Emitted before each retryable failure retry.

| Field | Notes |
|---|---|
| `attempt` | Starting at 1 |
| `max_retries` | Total permitted |
| `retry_delay_ms` | Until the next attempt |
| `error_status` | HTTP status, or **`null` for connection errors with no HTTP response** |
| `error` | Category: `authentication_failed`, `oauth_org_not_allowed`, `billing_error`, `rate_limit`, `overloaded`, `invalid_request`, `model_not_found`, `server_error`, `max_output_tokens`, `unknown` |

---

## 5. Process lifecycle

### Background tasks at exit

**A background Bash task is terminated about five seconds after Claude returns its final result and stdin closes.** The grace period lets a task finishing right after the result still deliver output. Before v2.1.163, **a never-exiting background process held the invocation open indefinitely.**

**Background subagents and workflows are exempt from the five-second grace**, because their result is part of the final output, so `claude -p` waits for them. **From v2.1.182 that wait is capped at ten minutes**; adjust with `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`, or set `0` for no limit.

### SIGTERM

On `kill`, a process supervisor, or an SDK host closing the session, Claude Code **aborts the in-progress turn, terminates the process tree of any running Bash command, runs `SessionEnd` hooks, and exits with code 143.**

That `SessionEnd` guarantee is worth knowing: cleanup hooks do run on supervised termination.

### stdin

**Piped stdin is capped at 10MB** (v2.1.128+). Exceeding it exits with a clear error and non-zero status. **Write large content to a file and reference the path in your prompt instead.**

**If stdin cannot be read**, for example because the starting process disconnected its end, Claude Code **warns to stderr and continues with the command-line prompt.** Before v2.1.211, an unreadable stdin on Windows **crashed the session or exited silently with no output.**

---

## 6. Permissions for unattended runs

Two strategies.

**Enumerate tools** with `--allowedTools`, using permission rule syntax:

```bash
claude -p "Look at my staged changes and create an appropriate commit" \
  --allowedTools "Bash(git diff *),Bash(git log *),Bash(git status *),Bash(git commit *)"
```

**The space before `*` matters.** `Bash(git diff *)` allows anything starting with `git diff`; `Bash(git diff*)` would also match `git diff-index`.

**Set a session baseline** with a permission mode:

| Mode | Behavior in `-p` |
|---|---|
| `dontAsk` | **Denies anything not in `permissions.allow` or the read-only command set.** The locked-down CI choice |
| `acceptEdits` | Writes files without prompting, plus common filesystem commands (`mkdir`, `touch`, `mv`, `cp`). **Other shell commands and network requests still need an allow entry, or the run aborts when one is attempted** |

**Under `dontAsk`, `AskUserQuestion`, org-`ask` connector tools, and `requiresUserInteraction` MCP tools are denied even when an allow rule matches.**

Without permission flags, `-p` **still prompts** the moment Claude wants to read a file or run a command, which is rarely what automation wants.

---

## 7. Commands in print mode

**User-invoked skills and custom commands work**: include `/skill-name` in the prompt string and Claude Code expands it before running.

**Built-in terminal-only commands such as `/login` are unavailable.**

Argument forms that do work (v2.1.205+): **`/model`, `/effort`, `/fast`, `/color`, and `/rename` accept the value as an argument** (`/model sonnet`), and **`/mcp` with no argument prints a text status summary.**

**To change a setting from a `-p` invocation, pass `key=value` to `/config`** (v2.1.181+), e.g. `/config thinking=false`.

---

## 8. Continuing conversations

```bash
claude -p "Review this codebase for performance issues"
claude -p "Now focus on the database queries" --continue

# Or capture and resume a specific session
session_id=$(claude -p "Start a review" --output-format json | jq -r '.session_id')
claude -p "Continue that review" --resume "$session_id"
```

**Run both commands from the same directory.** Session ID lookup is scoped to the current project directory and its git worktrees.

---

## 9. CI patterns

**Pipe data in, redirect out** — non-interactive mode reads stdin like any command-line tool:

```bash
cat build-error.txt | claude -p 'concisely explain the root cause of this build error' > output.txt
```

**Claude as a project linter**, with a portability detail worth copying:

```json
{
  "scripts": {
    "lint:claude": "git diff main | claude -p \"you are a typo linter. for each typo in this diff, report filename:line on one line and the issue on the next. return nothing else.\""
  }
}
```

**Piping the diff means Claude needs no Bash permission to read it**, and the escaped double quotes keep the script portable to Windows.

**Role framing without losing default behavior:**

```bash
gh pr diff "$1" | claude -p \
  --append-system-prompt "You are a security engineer. Review for vulnerabilities." \
  --output-format json
```

A reasonable production shape combines several of these:

```bash
claude --bare -p "$PROMPT" \
  --permission-mode dontAsk \
  --allowedTools "Read,Bash(git diff *)" \
  --max-turns 5 \
  --max-budget-usd 1.00 \
  --output-format json \
  --json-schema "$SCHEMA"
```

**Authentication for CI**: `claude setup-token` generates a long-lived OAuth token, requiring a Claude subscription. Otherwise use `ANTHROPIC_API_KEY` or provider credentials.

---

## 10. Gotchas

1. **`-p` without `--bare` loads the machine's full local configuration.** A teammate's hook or the project's `.mcp.json` runs.
2. **Bare mode skips OAuth and keychain reads.** Supply credentials explicitly.
3. **Bare mode gives Claude only Bash, file read, and file edit.**
4. **`--bare` will become the `-p` default in a future release.**
5. **`-p` without permission flags still prompts** on the first file read or command.
6. **`stream-json` requires `--verbose`**, and deltas require `--include-partial-messages`.
7. **The space before `*` in a Bash rule is a word boundary.** `Bash(git diff*)` matches `git diff-index`.
8. **`dontAsk` denies `AskUserQuestion` and interaction-requiring MCP tools even with a matching allow rule.**
9. **`acceptEdits` aborts the run when an unapproved shell command is attempted.**
10. **An invalid `--json-schema` was silently ignored before v2.1.205**, returning unstructured text.
11. **`format` in a schema is an annotation, not enforced.**
12. **Structured output lands in `structured_output`, not `result`.**
13. **A slow stream consumer truncated large responses before v2.1.214.**
14. **Subagent text is not forwarded by default**, only `tool_use` and `tool_result`.
15. **Nested subagent messages did not appear at all before v2.1.219.**
16. **`system/init` is not always the first event.** Plugin-install and hook events can precede it.
17. **Feature-detect on `capabilities`, not version strings.**
18. **An invalid `--mcp-config` entry is skipped and the run exits cleanly.** Check `mcp_server_errors` or the failure is invisible.
19. **The stderr warning for skipped servers does not appear when stderr is captured** by a CI runner or SDK host.
20. **Failed plugins are demoted out of `plugins`** and reported only in `plugin_errors`.
21. **The error keys are omitted entirely when empty**, which makes a non-empty check the right CI gate.
22. **Background Bash tasks get five seconds after the final result**, then are killed.
23. **Background subagents and workflows block exit for up to ten minutes** by default.
24. **SIGTERM exits with code 143** and does run `SessionEnd` hooks.
25. **Piped stdin caps at 10MB.** Use a file path for larger input.
26. **An unreadable stdin crashed Windows sessions before v2.1.211.**
27. **`/login` and other terminal-only commands do not work in `-p`.**
28. **`/config key=value` is how you change a setting from a `-p` run.**
29. **`--resume <id>` must run from the session's project directory.**

---

## Reference links

- Run Claude Code programmatically: https://code.claude.com/docs/en/headless
- Agent SDK overview: https://code.claude.com/docs/en/agent-sdk/overview
- Agent SDK quickstart: https://code.claude.com/docs/en/agent-sdk/quickstart
- Agent SDK Python: https://code.claude.com/docs/en/agent-sdk/python
- Agent SDK TypeScript: https://code.claude.com/docs/en/agent-sdk/typescript
- Streaming output: https://code.claude.com/docs/en/agent-sdk/streaming-output
- File checkpointing (SDK): https://code.claude.com/docs/en/agent-sdk/file-checkpointing
- CLI reference: https://code.claude.com/docs/en/cli-reference
- Permission modes: https://code.claude.com/docs/en/permission-modes
- Permissions and rule syntax: https://code.claude.com/docs/en/permissions
- Hooks: https://code.claude.com/docs/en/hooks
- Subagents: https://code.claude.com/docs/en/sub-agents
- Sessions: https://code.claude.com/docs/en/sessions
- GitHub Actions: https://code.claude.com/docs/en/github-actions
- GitLab CI/CD: https://code.claude.com/docs/en/gitlab-ci-cd
- Environment variables: https://code.claude.com/docs/en/env-vars
- Manage costs: https://code.claude.com/docs/en/costs
- Full docs index: https://code.claude.com/docs/llms.txt
