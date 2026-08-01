# Claude Code: Tools Reference

A study guide to Claude Code's built-in tools: the full catalog, the rule formats that reference them, and the per-tool behaviors that determine what actually happens when Claude calls one.

Verified against code.claude.com/docs/en/tools-reference.

---

## 1. Why the names matter

**The tool names are the exact strings you use in permission rules, subagent tool lists, and hook matchers.** Get one wrong and the rule silently matches nothing.

To disable a tool entirely, add its name to `permissions.deny`.

Two ways to extend:

- **MCP server** adds new tools
- **A skill** does *not* add a tool entry; it runs through the existing `Skill` tool

Reading the permission column correctly: it shows whether the tool prompts **in default mode for paths inside the working directory**. File-access tools marked "No" — `Read`, `Grep`, `Glob` — **still prompt for paths outside** the working and additional directories. `Bash` is marked "Yes" but runs a built-in read-only set without prompting.

---

## 2. The catalog

### File and search

| Tool | Purpose | Prompts |
|---|---|---|
| `Read` | Read file contents with line numbers | No |
| `Write` | Create or overwrite a whole file | Yes |
| `Edit` | Exact string replacement | Yes |
| `NotebookEdit` | Modify Jupyter cells by `cell_id` | Yes |
| `Glob` | Find files by name pattern | No |
| `Grep` | Search file contents (ripgrep) | No |
| `LSP` | Language-server code intelligence | No |

### Execution

| Tool | Purpose | Prompts |
|---|---|---|
| `Bash` | Shell commands | Yes |
| `PowerShell` | PowerShell commands natively | Yes |
| `Monitor` | Background watch feeding output lines back to Claude | Yes |

### Delegation and orchestration

| Tool | Purpose | Prompts |
|---|---|---|
| `Agent` | Spawn a subagent in its own context window | No |
| `Skill` | Execute a skill in the main conversation | Yes |
| `Workflow` | Run a dynamic workflow orchestrating many subagents | Yes |
| `SendMessage` | Message a teammate, or resume a subagent by ID or name | No |
| `TaskStop` | Stop a background task, teammate, or named agent by ID | No |

### Planning and worktrees

| Tool | Purpose | Prompts |
|---|---|---|
| `EnterPlanMode` | Switch to plan mode | No |
| `ExitPlanMode` | Present a plan for approval and exit | Yes |
| `EnterWorktree` | Create an isolated worktree and switch into it | Yes |
| `ExitWorktree` | Return to the original directory | No |

### Tasks and scheduling

| Tool | Purpose | Prompts |
|---|---|---|
| `TaskCreate` / `TaskGet` / `TaskList` / `TaskUpdate` | Task list management | No |
| `TaskOutput` | Background task output. **Deprecated** in favor of `Read` on the output file path | No |
| `TodoWrite` | Session checklist. **Disabled by default** since v2.1.142 in favor of the Task tools; re-enable with `CLAUDE_CODE_ENABLE_TASKS=0` | No |
| `CronCreate` / `CronDelete` / `CronList` | Session-scoped scheduled prompts, restored on resume if unexpired | No |
| `ScheduleWakeup` | Reschedules the next self-paced `/loop` iteration. **Claude calls this, not you** | No |

### Web and MCP

| Tool | Purpose | Prompts |
|---|---|---|
| `WebFetch` | Fetch a URL and extract per a prompt | Yes |
| `WebSearch` | Search the web, returns titles and URLs only | Yes |
| `ToolSearch` | Load deferred tools when tool search is on | No |
| `WaitForMcpServers` | Wait for connecting MCP servers. **Only appears when tool search is disabled** | No |
| `ListMcpResourcesTool` / `ReadMcpResourceTool` | MCP resource listing and reading | No |

### Interaction and delivery

| Tool | Purpose | Prompts |
|---|---|---|
| `AskUserQuestion` | Multiple-choice questions | No |
| `PushNotification` | Desktop notification, and phone push under Remote Control | No |
| `SendUserFile` | Send files from the session to your device | No |
| `Artifact` | Publish HTML/Markdown as a private page on claude.ai | Yes |
| `ReportFindings` | Structured code-review findings | No |
| `RemoteTrigger` | Manage Routines on claude.ai; backs `/schedule` | No |
| `ShareOnboardingGuide` | Upload `ONBOARDING.md` and return a share link | Yes |

### Provider availability gaps

Several tools are **unavailable on Amazon Bedrock, Google Cloud's Agent Platform, and Microsoft Foundry**, because they route through Anthropic-hosted infrastructure: `Monitor`, `PushNotification`, `SendUserFile`, `RemoteTrigger`, `ScheduleWakeup`. `WebSearch` works on the Claude API, Claude Platform on AWS, and Microsoft Foundry; on Google Cloud's Agent Platform it needs Claude 4+; **Amazon Bedrock does not expose it at all**.

`Monitor` is also unavailable when `DISABLE_TELEMETRY` or `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` is set.

### EndConversation

Ends the current session. Its availability is unusually narrow, requiring **all** of:

- Claude Code **v2.1.213+**
- Model in the **Opus 4.8, Sonnet 5, or Fable 5** families or later
- **An interactive terminal session**, including a `claude` session in an IDE's integrated terminal (which is how the JetBrains plugin runs). Other surfaces do not get it
- **Not a `--bare` session**, since bare mode loads only shell and file tools
- **Not** on Amazon Bedrock, Claude Platform on AWS, Google Cloud's Agent Platform, Microsoft Foundry, or a cloud-gateway sign-in

Two behaviors that make it an exception throughout the permission system:

- **A deny list that removes every other tool without matching `EndConversation` leaves it in place**
- **Subagents never get it.** Background tasks sharing the main tool list see it, but calling it there ends nothing

---

## 3. Rule formats

All configuration surfaces accept the same `ToolName(specifier)` format:

- `permissions.allow` / `.deny` and `/permissions`
- `--allowedTools` / `--disallowedTools`
- Agent SDK `allowedTools` / `disallowedTools`
- A subagent's `tools` / `disallowedTools` frontmatter
- A skill's `allowed-tools` frontmatter
- A hook's `if` condition

**Tools share specifier formats**, which is the part worth memorizing:

| Rule format | Applies to |
|---|---|
| `Bash(npm run *)` | **Bash and Monitor** |
| `PowerShell(Get-ChildItem *)` | PowerShell |
| `Read(~/secrets/**)` | **Read, Grep, Glob, and LSP** |
| `Edit(/src/**)` | **Edit, Write, and NotebookEdit** |
| `Skill(deploy *)` | Skill |
| `Agent(Explore)` | Agent |
| `WebFetch(domain:example.com)` | WebFetch |
| `WebSearch` | WebSearch, **no specifier form exists** |

Tools not listed, such as `ExitPlanMode` or `ShareOnboardingGuide`, accept **only the bare name**.

Two coupling rules:

- **An `Edit(...)` allow rule also grants read access to the same path.** No matching `Read(...)` rule needed
- **A `Read(...)` deny rule also blocks Edit on that path** (v2.1.208+), including creating a new file there, because editing requires reading the result back

**Hook `matcher` fields use bare tool names, not the parenthesized format.** The `if` field uses the rule format. Mixing these up is a common failure.

---

## 4. Bash

Each command runs in a **separate process**.

### What persists

| | Persists? |
|---|---|
| **Working directory (`cd`)** | Yes in the main session, **as long as it stays inside the project or an additional directory** |
| **Environment variables** | **No.** An `export` in one command is gone in the next |
| **Aliases and shell functions** | **Yes** |

The `cd` carry-over has a documented failure mode: **if `cd` lands outside those directories, Claude Code resets to the project directory** and appends `Shell cwd was reset to <dir>` to the result. **Subagent sessions never carry over working-directory changes.** Disable carry-over entirely with `CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR=1`.

Aliases work because **at session start Claude Code sources `~/.zshrc`, `~/.bashrc`, or `~/.profile`**, captures the resulting aliases, functions, and shell options, and applies them to every Bash command.

For environments: **activate your virtualenv or conda environment before launching Claude Code.** For persistent variables, use `CLAUDE_ENV_FILE` or a SessionStart hook.

### Limits

| Limit | Default | Ceiling | Override |
|---|---|---|---|
| **Timeout** | 2 minutes | 10 minutes per command | `BASH_DEFAULT_TIMEOUT_MS`, `BASH_MAX_TIMEOUT_MS` |
| **Output** | 30,000 characters | **150,000 hard ceiling** | `BASH_MAX_OUTPUT_LENGTH` |

Over the output limit, Claude Code **saves the full output to a file in the session directory** and gives Claude the path plus a short preview from the start.

### Background and auto-backgrounding

`run_in_background: true` starts a command as a background task. List and stop with `/tasks`. In `-p` mode, background tasks end shortly after the run's final result.

**When a command reaches its timeout without finishing, Claude Code moves it to the background rather than stopping it.** Two exceptions and one important consequence:

- **Never auto-backgrounds a command starting with `sleep`**
- `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` disables it
- **A `cd`, `pushd`, `popd`, or `chdir` inside a backgrounded command never carries over**, and the result says so explicitly, so Claude does not act on a directory change that did not happen

---

## 5. Edit

**Exact string replacement. No regex, no fuzzy matching.**

Three checks must pass, preceded by a `Read` deny-rule refusal (v2.1.208+):

1. **Read-before-edit**: Claude must have read the file in this conversation. **A read cut short with a `PARTIAL view` notice does not count.** Opus 4.6, Haiku 4.5, and older models always require it; **newer models can edit an unread file when reading it would not need a permission prompt and the Read tool is available**
2. **Match**: `old_string` must appear **exactly** as written. **One character of whitespace or indentation is enough to miss**
3. **Uniqueness**: it must appear **exactly once**. Otherwise Claude supplies more surrounding context or sets `replace_all: true`

### The changed-file relaxation

A file that changed on disk after Claude last read it **can still be edited** when `old_string` matches current content exactly and unambiguously and Claude Code can read the file without prompting. Matching against current content is what makes this safe, and **the result notes that the file carries other changes** so Claude re-reads before edits that depend on surrounding context.

Otherwise — stale `old_string`, or multiple matches without `replace_all` — Claude reads again first. Before v2.1.208, Claude Code refused **any** edit to an unread or changed file.

### Bash satisfies read-before-edit, partially

Viewing with Bash counts when the command is `cat`, `head`, `tail`, `sed -n 'X,Yp'`, `grep`, `egrep`, or `fgrep` on **a single file with no pipes or redirects**. Piped output and other commands do not count.

**This affects edit eligibility only, not permissions.** And the two lists differ: `egrep` and `fgrep` count for read-before-edit but are **not** checked against Read deny rules.

Read and Edit deny rules apply to Bash file commands Claude Code recognizes, **but not to arbitrary subprocesses** like a Python or Node script that opens files itself. For OS-level coverage, enable the sandbox.

---

## 6. Glob and Grep

The division: **Glob finds files by name; Grep finds lines inside them.**

### Glob

Standard syntax with `**` for recursion. Results are **sorted by modification time and capped at 100 files**; on truncation Claude sees a flag and can narrow.

**Glob does not respect `.gitignore` by default**, so it finds gitignored files alongside tracked ones. This **differs from Grep**, which skips them. Change it with `CLAUDE_CODE_GLOB_NO_IGNORE=false`.

### Grep

Built on **ripgrep, using ripgrep regex, not POSIX grep**. Metacharacters need escaping: finding Go's `interface{}` takes the pattern `interface\{\}`.

Three output modes:

| Mode | Returns |
|---|---|
| `files_with_matches` | Paths only. **Default** |
| `content` | Matching lines with file and line number |
| `count` | Per-file counts plus a total |

Scoping: `glob` (e.g. `**/*.tsx`) or `type` (e.g. `py`, `rust`). `multiline: true` matches across line boundaries; by default patterns match within a single line.

**Grep respects `.gitignore`.** To search a gitignored file, pass its path directly.

Error-reporting improvements in v2.1.208 worth knowing, because the old behavior was actively misleading: a rejected pattern now returns ripgrep's diagnostic instead of **`No files found`**, and the `count` total now covers every match even when `head_limit` or `offset` truncate the listing.

---

## 7. Read

Returns contents **with line numbers**. Claude is instructed to always pass absolute paths.

**Read only reads files, not directories.** Claude uses `ls` via Bash for directory listings.

### Pagination and the PARTIAL view

By default Read starts at the beginning. When a whole-file read exceeds the token limit, it returns the first page with a **`PARTIAL view` notice** telling Claude how much it got and how to continue with `offset` and `limit`.

A read that passes an explicit `offset` or `limit` and **still** exceeds the limit returns an error. A read with an explicit `limit` **stops as soon as the selected lines exceed what could ever fit** and errors without loading the rest, suggesting a smaller `limit` or Grep when a single line is that large. Before v2.1.208 the whole range loaded into memory first, so **a file with an extremely long single line could exhaust memory**.

Empty file: a notice that it exists but is empty. `offset` past the end: a notice with the line count.

### Non-text types

| Type | Behavior |
|---|---|
| **Images** | Returned as **visual content Claude can see**, not raw bytes. Large images are resized and recompressed. As of v2.1.196, an image still over 500KB after resize is **re-encoded as JPEG at reduced quality with pixel dimensions unchanged** |
| **PDFs** | Short ones whole; over 10 pages, read in ranges with `pages`, up to 20 at a time |
| **Jupyter notebooks** | All cells with outputs, including code, markdown, visualizations |

Practical note from the docs: if Claude misses fine pixel detail in a large image, **ask it to crop the region of interest first**, for example with ImageMagick via Bash.

---

## 8. Write

Creates or **overwrites a whole file**. It does not append or merge.

**If the target exists, Claude must have read it at least once in this conversation.** A Write to an unread existing file fails. **This does not apply to new files.**

Bash viewing satisfies this under the same rules as Edit.

**For partial changes, Claude uses Edit.**

---

## 9. WebFetch and WebSearch

### WebFetch is lossy by design

It takes a URL and a prompt, fetches, **converts HTML to Markdown**, and **runs the prompt against the content using a small, fast model**. For most fetches, **Claude receives that model's answer, not the raw page.** The conversion step is not configurable.

The consequence stated plainly in the docs: **a result saying a page does not mention something may only mean the prompt did not ask about it.** Fetch again with a more specific prompt, or use `curl` via Bash for the unprocessed page.

Other behaviors:

- **HTTP URLs are automatically upgraded to HTTPS**
- Large pages are truncated to a fixed character limit before processing
- **Responses are cached for 15 minutes**
- **A redirect to a different host is not followed.** WebFetch returns a text result naming the original and the target; Claude fetches the new URL in a second call

Permissions: in default and `acceptEdits`, it prompts on a new domain, **except for a built-in set of preapproved documentation domains**. `auto` and `bypassPermissions` skip the prompt. **An explicit `WebFetch(domain:...)` rule in `deny`, `ask`, or `allow` takes precedence over the preapproved set**, so you can block or gate a preapproved domain.

Headers: `User-Agent` beginning with `Claude-User`, and an `Accept` header preferring Markdown so servers with content negotiation can return it directly.

**Sandbox network rules are separate.** A domain you want a sandboxed process to reach still needs its own sandbox rule.

### WebSearch

Returns **titles and URLs only**. It does not fetch pages; Claude follows up with WebFetch.

**May issue up to eight backend searches per call**, refining internally. Scope with `allowed_domains` or `blocked_domains` — **the two cannot be combined in one call**.

**The search backend is not configurable.** For a different provider, add an MCP server exposing a search tool.

---

## 10. Monitor

Lets Claude **watch something in the background and react mid-conversation** without pausing. Tail a log and flag errors, poll a PR or CI job, watch a directory, track a long-running script, or consume a WebSocket feed.

For most watches Claude writes a small script, runs it in the background, and receives each output line as it arrives. **You keep working and Claude interjects when an event arrives.** Stop it by asking Claude to cancel or by ending the session.

**Monitor uses the same permission rules as Bash**, so `Bash(...)` allow and deny patterns apply here too. The WebSocket source has its own prompt.

### WebSocket source (v2.1.195+)

Takes a `ws` input in place of `command`; **a single call cannot combine the two**.

| Field | Required | Notes |
|---|---|---|
| `url` | Yes | `ws://` or `wss://`, **no embedded credentials or whitespace, ASCII only** |
| `protocols` | No | Valid subprotocol tokens, **no duplicates** |

Event handling:

- **Text messages**: one event each, even multi-line
- **Binary messages**: **not passed through**. Claude gets a placeholder like `[binary frame, 512 bytes]`
- **Messages larger than 1 MiB**: **the watch ends**. Subscribe to a filtered feed where one exists
- **Socket close**: the watch ends and Claude receives the close code

**Opening a WebSocket prompts for approval, and the prompt offers no way to skip future prompts for the same host.**

Claude Code **denies URLs pointing at private, link-local, or cloud-metadata addresses**, including hostnames that resolve to one. It also denies `sandbox.network.deniedDomains` hosts, and under `allowManagedDomainsOnly`, anything outside the managed allowlist.

Plugins can declare monitors that start automatically when active.

---

## 11. PowerShell

| Platform | Availability |
|---|---|
| **Windows without Git Bash** | Enabled automatically |
| **Windows with Git Bash** | Rolling out progressively |
| **Linux, macOS, WSL** | **Opt-in**, requires PowerShell 7+ with `pwsh` on `PATH` |

```json
{ "env": { "CLAUDE_CODE_USE_POWERSHELL_TOOL": "1" } }
```

On Windows set it to `0` to opt out of the rollout.

Windows auto-detects `pwsh.exe` (PS 7+) falling back to `powershell.exe` (5.1). **When enabled, Claude treats PowerShell as the primary shell**, with Bash still available for POSIX scripts when Git Bash is installed.

### Execution policy

Claude Code spawns PowerShell with **`-ExecutionPolicy Bypass` at process scope only**, so `.ps1` scripts and module imports work on default Windows installs **without changing the machine's policy**. Process-scope bypass **does not override Group Policy `MachinePolicy` or `UserPolicy`**, so enterprise policies still apply. To respect the effective policy instead, set `CLAUDE_CODE_POWERSHELL_RESPECT_EXECUTION_POLICY=1`.

### Three separate shell settings

| Setting | Effect | Requires the tool enabled? |
|---|---|---|
| `"defaultShell": "powershell"` in settings | Routes interactive `!` commands | **Yes** |
| `"shell": "powershell"` on a command hook | Runs that hook in PowerShell | **No** — hooks spawn PowerShell directly |
| `shell: powershell` in skill frontmatter | Runs `` !`command` `` blocks | **Yes** |

Exit-code parity (v2.1.196+): exit 1 from `grep`, `egrep`, `fgrep`, `git grep` means no matches, and exit 1 from `git diff` means differences exist. These are **not reported as failures**.

**Preview limitations**: PowerShell profiles are not loaded, and **sandboxing is not supported on Windows**.

---

## 12. Other tool behaviors

### Agent

Returns **a single text result**. The parent sees neither intermediate tool calls nor outputs.

Tool resolution:

| Fields set | Result |
|---|---|
| Neither | Inherits every parent tool |
| `tools` only | Only the listed tools |
| `disallowedTools` only | Every parent tool except those |
| **Both** | **`disallowedTools` takes precedence**; a tool in both is removed |

**When `tools` resolves to nothing, the Agent tool returns an error listing the entries instead of launching** (v2.1.208+). Before that it launched toolless and returned confusing results.

**Launching does not itself prompt.** Claude Code checks the subagent's own calls as it runs. Foreground subagents show prompts inline; background ones surface them in your main session naming the asker, with Esc denying one call without stopping the subagent (v2.1.186+).

### NotebookEdit

Targets cells by `cell_id`. **It does not do string replacement across the notebook the way Edit does.**

| Mode | Effect |
|---|---|
| `replace` | Overwrite the cell source. **Default** |
| `insert` | Add a cell after the target. **With no `cell_id`, it goes at the start.** Requires `cell_type` |
| `delete` | Remove the target |

**Permission rules use the `Edit(...)` path format**, so `Edit(notebooks/**)` covers it.

### LSP

Jump to definition, find references, type info at a position, list file symbols, workspace symbol search, find implementations, trace call hierarchies. **After each file edit it automatically reports type errors and warnings**, so Claude can fix issues without a separate build step.

**Inactive until you install a code intelligence plugin for your language.** The plugin bundles the configuration; **you install the server binary separately.**

### AskUserQuestion

**Questions stay open until answered; no idle timeout by default.** Set `askUserQuestionTimeout` to `60s`, `5m`, or `10m` for auto-continue, which submits any options already selected and tells Claude you may be away. A countdown appears for the last 20 seconds, and **any keypress restarts the timer**, as does a focused window on terminals reporting focus.

**The timeout applies only to `AskUserQuestion`. Permission prompts, including plan approval, never auto-resolve on idle.**

### EnterWorktree

**A `path` outside `.claude/worktrees/` prompts for approval** (v2.1.206+), since it moves the session's working directory and write access. New-worktree creation and paths under `.claude/worktrees/` do not prompt.

**From within a worktree session, or from a subagent with a pinned working directory such as `isolation: worktree`, only the `path` form is available** and the target must be under that repository's `.claude/worktrees/`.

---

## 13. Checking what is available

Your tool set depends on provider, platform, and settings. In a running session:

```text
What tools do you have access to?
```

Claude gives a conversational summary. **For exact MCP tool names, run `/mcp`.**

**The advisor tool is a server tool the API runs**, not one Claude Code implements. **It has no name you can reference in permission rules or hook matchers.**

---

## 14. Gotchas

1. **Tool names are exact strings.** A typo in a rule, subagent list, or matcher fails silently.
2. **"Permission required: No" means inside the working directory.** `Read`, `Grep`, and `Glob` still prompt outside it.
3. **Specifier formats are shared.** `Read(...)` covers Grep, Glob, and LSP; `Edit(...)` covers Write and NotebookEdit; `Bash(...)` covers Monitor.
4. **`Edit(...)` allow grants read; `Read(...)` deny blocks edit.** The coupling runs both ways.
5. **Hook matchers use bare names; hook `if` uses the rule format.**
6. **`WebSearch` has no specifier form.**
7. **`EndConversation` survives a deny-everything rule** and is never given to subagents.
8. **Environment variables do not persist between Bash commands**, but aliases and functions do.
9. **`cd` outside the allowed directories silently resets** to the project directory.
10. **A `cd` inside an auto-backgrounded command never applies** to later commands.
11. **Bash output over 30,000 characters spills to a file**, and Claude gets a path plus a preview.
12. **`sleep` commands are never auto-backgrounded.**
13. **Edit is exact-match with no fuzzy matching**, and one whitespace character defeats it.
14. **A `PARTIAL view` read does not satisfy read-before-edit.**
15. **`egrep` and `fgrep` count for read-before-edit but are not checked against Read deny rules.** The two lists genuinely differ.
16. **Read/Edit deny rules do not cover arbitrary subprocesses.** A Python script reading the file is unaffected.
17. **Glob ignores `.gitignore`; Grep respects it.** Opposite defaults.
18. **Glob caps at 100 files**, sorted by modification time.
19. **Grep uses ripgrep regex.** `interface{}` needs `interface\{\}`.
20. **WebFetch returns a small model's extraction, not the page.** A "not mentioned" result may just be a bad prompt.
21. **WebFetch does not follow cross-host redirects**, and caches for 15 minutes.
22. **Preapproved WebFetch domains fetch without a prompt** unless you write an explicit rule.
23. **Sandbox network rules are separate from WebFetch rules.**
24. **`WebSearch` returns titles and URLs only**, and cannot combine allowed and blocked domain lists.
25. **Monitor inherits Bash permission rules.**
26. **A WebSocket message over 1 MiB ends the watch**, and binary frames become placeholders.
27. **WebSocket approval cannot be remembered per host.**
28. **PowerShell `-ExecutionPolicy Bypass` is process-scope only** and does not override Group Policy.
29. **`"shell": "powershell"` on a hook works without the PowerShell tool enabled**; the settings and skill equivalents do not.
30. **`TodoWrite` is disabled by default** since v2.1.142.
31. **`TaskOutput` is deprecated** in favor of `Read` on the output path.
32. **`WaitForMcpServers` only exists when tool search is off.**
33. **Several tools are simply absent on Bedrock, Agent Platform, and Foundry.**
34. **The advisor tool cannot be named in any rule.**

---

## Reference links

- Tools reference: https://code.claude.com/docs/en/tools-reference
- Permissions: https://code.claude.com/docs/en/permissions
- Subagents: https://code.claude.com/docs/en/sub-agents
- Hooks guide: https://code.claude.com/docs/en/hooks-guide
- Hooks reference, PreToolUse input: https://code.claude.com/docs/en/hooks
- MCP: https://code.claude.com/docs/en/mcp
- Skills: https://code.claude.com/docs/en/skills
- Sandboxing: https://code.claude.com/docs/en/sandboxing
- Worktrees: https://code.claude.com/docs/en/worktrees
- Scheduled tasks: https://code.claude.com/docs/en/scheduled-tasks
- Environment variables: https://code.claude.com/docs/en/env-vars
- Plugin monitors: https://code.claude.com/docs/en/plugins-reference
- Full docs index: https://code.claude.com/docs/llms.txt
