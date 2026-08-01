# Claude Code: CLI Reference

A study guide to the Claude Code command line: subcommands, flags, in-session commands, keyboard interaction, and the configuration surface that flags interact with.

Verified against code.claude.com/docs (cli-reference, commands, agent-view, settings, skills, sub-agents, workflows).

**One caveat before you rely on this.** The official docs are the authority and Claude Code ships multiple releases a week. Two specifics worth internalizing:

- **`claude --help` does not list every flag.** A flag's absence from `--help` does not mean it is unavailable.
- **`/help` inside a session is the live list of commands available to you.** Availability varies by platform, plan, and environment.

Treat this document as a structured snapshot to study from, and check the docs when a detail is load-bearing.

---

## 1. Invocation shapes

```bash
claude                                   # interactive session
claude "explain this project"            # interactive with an initial prompt
claude -p "explain this function"        # print mode: query, print, exit
cat logs.txt | claude -p "explain"       # piped content as context
claude -c                                # continue most recent conversation here
claude -c -p "check for type errors"     # continue in print mode
claude -r "auth-refactor" "finish this"  # resume by ID or name
```

Mistyped subcommands are caught: `claude udpate` prints `Did you mean claude update?` and exits without starting a session.

One parsing quirk (v2.1.199+): `claude --dangerously-skip-permissions daemon <subcommand>` routes to `daemon`, which matters when `claude` is aliased to include that flag. Only a leading `--dangerously-skip-permissions` or `--allow-dangerously-skip-permissions` routes this way; any other leading flag starts an interactive session instead.

---

## 2. Subcommands

### Session and installation

| Command | Purpose |
|---|---|
| `claude update` | Update to the latest version |
| `claude install [version]` | Install or reinstall the native binary. Accepts `2.1.118`, `stable`, or `latest` |
| `claude doctor` | Read-only install and settings diagnostics from the terminal, no session. Includes install health, settings-file validation errors, Remote Control eligibility. The in-session `/doctor` can also **apply** fixes |
| `claude setup-token` | Generate a long-lived OAuth token for CI and scripts. Prints without saving. Requires a Claude subscription |

### Authentication

| Command | Purpose |
|---|---|
| `claude auth login` | Sign in. `--email` pre-fills, `--sso` forces SSO, `--console` signs in with Anthropic Console for API billing instead of a subscription |
| `claude auth logout` | Log out |
| `claude auth status` | JSON status. `--text` for human-readable. **Exit code 0 if logged in, 1 if not** |

### Background sessions

| Command | Purpose |
|---|---|
| `claude agents` | Open agent view. `--cwd <path>` scopes to that directory; `--json` prints active sessions as JSON (`--json --all` includes completed). Accepts `--permission-mode`, `--model`, `--effort`, `--agent` as dispatch defaults, plus `--settings`, `--add-dir`, `--plugin-dir`, `--mcp-config`. Requires an interactive terminal |
| `claude attach <id>` | Attach to a background session in this terminal |
| `claude logs <id>` | Print a background session's recent output |
| `claude stop <id>` | Stop a background session. Also `claude kill` |
| `claude respawn <id>` | Restart a session, running or stopped, conversation intact. `--all` for every running session, e.g. to pick up an updated binary |
| `claude rm <id>` | Remove from the list. The transcript stays local and remains available through `--resume` |
| `claude daemon status` | Supervisor state, version, socket directory, worker count. **Exits 1 if not running** |
| `claude daemon stop --any` | Stop the supervisor and its sessions. `--keep-workers` leaves sessions running for the next supervisor |

### MCP and plugins

| Command | Purpose |
|---|---|
| `claude mcp` | Configure MCP servers (see the MCP reference for subcommands) |
| `claude mcp login <name>` | Run a server's OAuth flow without the `/mcp` panel. `--no-browser` for SSH |
| `claude mcp logout <name>` | Clear stored OAuth credentials |
| `claude plugin` | Manage plugins. Alias `claude plugins` |

### Auto mode and maintenance

| Command | Purpose |
|---|---|
| `claude auto-mode defaults` | Print the built-in auto-mode classifier rules as JSON. `--label <prefix>` filters by label prefix, case-insensitive |
| `claude auto-mode config` | Your effective auto-mode config with settings applied |
| `claude auto-mode reset` | Remove the `autoMode` section from user settings. Prompts; `-y`/`--yes` skips. Managed and `--settings` rules still apply |
| `claude project purge [path]` | Delete all local state for a project: transcripts, task lists, debug logs, file-edit history, prompt history lines, and the `~/.claude.json` entry. `--dry-run`, `-y`/`--yes`, `-i`/`--interactive`, `--all`. Omit the path for an interactive picker |
| `claude heapdump` equivalent | See `/heapdump` in section 5 |

### Other entry points

| Command | Purpose |
|---|---|
| `claude gateway --config gateway.yaml` | Start the self-hosted Claude apps gateway server for SSO and policy in front of Bedrock, Google Cloud's Agent Platform, or Microsoft Foundry. v2.1.195+ |
| `claude remote-control` | Start a Remote Control server in server mode, no local interactive session |
| `claude ultrareview [target]` | Run ultrareview non-interactively. Prints findings, **exits 0 on success or 1 on failure**. `--json` for the raw payload, `--timeout <minutes>` overrides the 30-minute default |

---

## 3. Flags

### Session lifecycle

| Flag | Effect |
|---|---|
| `--continue`, `-c` | Load the most recent conversation in this directory. Includes sessions that added it with `/add-dir` |
| `--resume`, `-r` | Resume by ID or name, or open an interactive picker. The picker and name search include `/add-dir` sessions; **passing a session ID searches only the current project directory and its worktrees**. Background sessions appear marked `bg` (v2.1.144+) |
| `--fork-session` | On resume, create a new session ID instead of reusing the original |
| `--session-id` | Use a specific session ID. Must be a valid UUID |
| `--name`, `-n` | Display name for the session, shown in `/resume` and the terminal title. Resume with `claude --resume <name>` |
| `--from-pr` | Open the session picker filtered to a pull request. Accepts a PR number, GitHub or GHE PR URL, GitLab MR URL, or Bitbucket PR URL |
| `--teleport` | Resume a web session in your local terminal |
| `--cloud` | Create a new web session on claude.ai with the task description. `--remote` is a deprecated alias |

### Print mode and output

| Flag | Effect |
|---|---|
| `--print`, `-p` | Print the response without interactive mode |
| `--output-format` | `text`, `json`, or `stream-json` |
| `--input-format` | `text` or `stream-json` |
| `--verbose` | Full turn-by-turn output. Overrides the `viewMode` setting for this session |
| `--include-partial-messages` | Partial streaming events. Requires `--print` and `--output-format stream-json` |
| `--include-hook-events` | Hook lifecycle events in the stream. `SessionStart` and `Setup` are always included. Requires `stream-json` |
| `--forward-subagent-text` | Emit subagent text and thinking as `assistant`/`user` messages with `parent_tool_use_id` set, so you can reconstruct each subagent transcript. Without it you get only subagent `tool_use` and `tool_result` blocks. Requires `--print` and `stream-json`. v2.1.211+ |
| `--replay-user-messages` | Re-emit stdin user messages on stdout. Requires **stream-json on both sides** |
| `--prompt-suggestions` | Emit a `prompt_suggestion` message after each turn. Requires `--print`, `stream-json`, and `--verbose` |
| `--json-schema` | Validated JSON output matching a schema after the agent completes. Print mode only. Invalid schemas exit with an error as of v2.1.205 |

### Limits and budgets (print mode only)

| Flag | Effect |
|---|---|
| `--max-turns` | Cap agentic turns. **Exits with an error at the limit.** No limit by default. With `--input-format stream-json`, a message sent mid-turn stays queued and runs as its own turn with its own limit (v2.1.205+) |
| `--max-budget-usd` | Dollar cap on API spend. **Subagent spend counts toward it.** At the cap, spawning fails with `Budget limit reached` and running background subagents are stopped (v2.1.217+) |
| `--no-session-persistence` | Do not save the session to disk; it cannot be resumed. Print mode only |

### Permissions and tools

| Flag | Effect |
|---|---|
| `--permission-mode` | `default`, `acceptEdits`, `plan`, `auto`, `dontAsk`, `bypassPermissions`, or `manual` (alias for default, v2.1.200+). Overrides `defaultMode` |
| `--dangerously-skip-permissions` | Equivalent to `--permission-mode bypassPermissions` |
| `--allow-dangerously-skip-permissions` | Add `bypassPermissions` to the `Shift+Tab` cycle **without starting in it**, so you can begin in `plan` and switch later |
| `--allowedTools` / `--allowed-tools` | Tools that execute without prompting. Uses permission rule syntax |
| `--disallowedTools` / `--disallowed-tools` | Deny rules. **A bare tool name removes the tool from context**: `"Edit"` removes Edit, `"*"` removes every tool, `"mcp__*"` removes every MCP tool. A scoped rule like `Bash(rm *)` leaves the tool available and denies only matching calls |
| `--tools` | Restrict built-in tools. `""` disables all, `"default"` allows all, or `"Bash,Edit,Read"`. **Does not affect MCP tools**: pair with `--disallowedTools "mcp__*"`, or `--strict-mcp-config` without `--mcp-config` |
| `--permission-prompt-tool` | An MCP tool to handle permission prompts in non-interactive mode. Waits for its server to connect, up to the 30-second `MCP_TIMEOUT` (v2.1.206+). **Cannot approve MCP tools marked as requiring user interaction**; an `allow` is converted to a deny |
| `--add-dir` | Additional working directories. Grants file access; most `.claude/` config is **not** discovered there, with skills as the exception. Validates each path |

Note the `--allowedTools` versus `--tools` distinction: the first controls what runs **without prompting**, the second controls what is **available at all**.

### Model, effort, and prompts

| Flag | Effect |
|---|---|
| `--model` | Alias (`sonnet`, `opus`, `haiku`, `fable`) or full model name. Overrides the `model` setting and `ANTHROPIC_MODEL` |
| `--fallback-model` | Comma-separated chain tried in order when the primary is overloaded or retired. Overrides the `fallbackModel` setting |
| `--effort` | `low`, `medium`, `high`, `xhigh`, `max`, or `ultracode` (v2.1.203+). `ultracode` starts at `xhigh` with automatic workflow orchestration. Overrides `effortLevel`, does not persist |
| `--advisor <model>` | Enable the advisor tool for this session. `opus`, `sonnet`, or a full ID. `claude --advisor fable` exits with an error |
| `--betas` | Beta headers in API requests. API key users only |
| `--system-prompt` | Replace the entire system prompt |
| `--system-prompt-file` | Replace with file contents |
| `--append-system-prompt` | Append to the default prompt |
| `--append-system-prompt-file` | Append file contents |
| `--append-subagent-system-prompt` | Append to **every** subagent's system prompt, including nested. Only in `-p` mode. v2.1.205+ |
| `--exclude-dynamic-system-prompt-sections` | Move per-machine sections (working directory, environment info, memory paths, git-repo flag) into the first user message, improving prompt-cache reuse across users and machines. Only with the default system prompt |

**System prompt decision rule.** `--system-prompt` and `--system-prompt-file` are mutually exclusive; the append flags combine with either. Append when Claude should remain a coding assistant that also follows your extra rules, which preserves default tool guidance, safety instructions, and coding conventions. Replace when the surface, identity, or permission model differs from Claude Code's, and accept that you take responsibility for everything the default prompt was providing.

For persistent personas, use output styles. For project conventions, use CLAUDE.md. These flags apply only to the current invocation.

### Configuration loading

| Flag | Effect |
|---|---|
| `--settings` | Path to a settings JSON file or an inline JSON string. Overrides the same keys in your settings files for this session; omitted keys keep file values. **Regular file, max 2 MiB** |
| `--setting-sources` | Comma-separated sources to load: `user`, `project`, `local` |
| `--mcp-config` | Load MCP servers from JSON files or strings, space-separated |
| `--strict-mcp-config` | Only use `--mcp-config` servers, ignoring all other MCP configuration |
| `--plugin-dir` | Load a plugin from a directory or `.zip` for this session. **One path per flag**, repeat for more |
| `--plugin-url` | Fetch a plugin `.zip` from a URL for this session |
| `--agents` | Define subagents as JSON. Same field names as frontmatter plus `prompt` |
| `--agent` | Run the session as a named agent, overriding the `agent` setting |
| `--channels` | MCP servers whose channel notifications to listen for. Space-separated `plugin:<name>@<marketplace>` entries. Research preview |
| `--dangerously-load-development-channels` | Enable channels off the approved allowlist for local development. Prompts for confirmation |

### Startup modes

| Flag | Effect |
|---|---|
| `--bare` | Minimal mode: skip auto-discovery of hooks, skills, plugins, MCP servers, auto memory, and CLAUDE.md so scripted calls start faster. Claude keeps Bash, file read, and file edit. Sets `CLAUDE_CODE_SIMPLE` |
| `--safe-mode` | Start with **all customizations disabled** to troubleshoot a broken configuration: CLAUDE.md, skills, plugins, hooks, MCP servers, custom commands and agents, output styles, workflows, custom themes, keybindings, status line and file-suggestion commands, LSP servers, auto memory. **Authentication, model selection, built-in tools, and permissions work normally**, which is the difference from `--bare`. Managed settings policy still applies. v2.1.169+ |
| `--init` | Run Setup hooks with the `init` matcher before the session. Print mode only |
| `--maintenance` | Run Setup hooks with the `maintenance` matcher. Print mode only |
| `--init-only` | Run Setup and `SessionStart` hooks, then exit without a conversation |
| `--disable-slash-commands` | Disable all skills and commands for this session. Also stops directory watching for skills and agents |

`--bare` for speed in scripts; `--safe-mode` for diagnosis. They are not interchangeable.

### Parallelism and integrations

| Flag | Effect |
|---|---|
| `--worktree`, `-w` | Start in an isolated worktree at `<repo>/.claude/worktrees/<name>`. Auto-generates a name if omitted. Pass `#<number>` or a GitHub PR URL to fetch that PR from `origin` and branch from it |
| `--tmux` | Create a tmux session for the worktree. **Requires `--worktree`.** Uses iTerm2 native panes when available; `--tmux=classic` for traditional tmux |
| `--bg`, `--background` | Start as a background agent and return immediately. Prints the session ID and management commands. **Cannot be combined with `-p`/`--print`** (v2.1.198+) |
| `--exec` | Run a shell command as a PTY-backed background job instead of a Claude session. Use with `--bg` |
| `--teammate-mode` | `in-process` (default), `auto`, `tmux`, or `iterm2`. Default changed from `auto` in v2.1.179 |
| `--remote-control`, `--rc` | Interactive session with Remote Control enabled. Optional name |
| `--remote-control-session-name-prefix` | Prefix for auto-generated Remote Control names. Defaults to hostname |
| `--ide` | Auto-connect to the IDE on startup when exactly one valid IDE is available |
| `--chrome` / `--no-chrome` | Enable or disable Chrome browser integration |

### Diagnostics and accessibility

| Flag | Effect |
|---|---|
| `--debug` | Debug mode with optional category filtering: `"api,hooks"` or `"!statsig,!file"` |
| `--debug-file <path>` | Write debug logs to a path. Implicitly enables debug mode. Takes precedence over `CLAUDE_CODE_DEBUG_LOGS_DIR` |
| `--ax-screen-reader` | Flat screen-reader-friendly output, no decorative borders or animations. Forces the classic renderer, so `tui` has no effect. Takes precedence over `CLAUDE_AX_SCREEN_READER` and `axScreenReader` |
| `--version`, `-v` | Print the version |

---

## 4. Print mode patterns

```bash
# One-shot query
claude -p "explain the purpose of config.yaml"

# Pipe content in as context
git diff main --name-only | claude -p "review these changed files for security issues"
tail -200 app.log | claude -p "identify the root cause"

# Structured output for pipelines
claude -p "list all REST endpoints" --output-format json

# Streaming for incremental processing
claude -p "query" --output-format stream-json --verbose

# Bounded, reproducible, scriptable
claude --bare -p "query" \
  --max-turns 5 \
  --max-budget-usd 1.00 \
  --permission-mode dontAsk \
  --output-format json

# Structured output against a schema
claude -p --json-schema '{"type":"object","properties":{"findings":{"type":"array"}}}' "audit this file"

# Multi-user cache-friendly scripted runs
claude -p --exclude-dynamic-system-prompt-sections --append-system-prompt-file ./rules.txt "query"
```

Output format flags shape only the final output; they do not change how Claude reasons through the problem.

---

## 5. In-session commands

Type `/` to see everything available to you, or `/` plus letters to filter.

Three mechanics worth knowing:

1. **A command is only recognized at the start of your message.** Text after the name becomes arguments.
2. **Skills are the exception** (v2.1.199+): `/skill-a /skill-b do XYZ` loads every skill named at the start and passes the trailing text to each. Up to six chained.
3. **A command sent while Claude is responding queues** and runs after the turn. `/status`, `/tasks`, and `/usage` run immediately without interrupting.

Two marked kinds in the docs:

- **Skill**: a bundled skill, a prompt handed to Claude, which Claude can also invoke automatically. `/verify` and `/code-review` run **only when you invoke them** (v2.1.215+)
- **Workflow**: a bundled dynamic workflow that fans out across many subagents in the background

`<arg>` is required, `[arg]` optional.

### Setup and configuration

| Command | Purpose |
|---|---|
| `/init` | Generate a starter `CLAUDE.md`. `CLAUDE_CODE_NEW_INIT=1` gives an interactive flow covering skills, hooks, and personal memory |
| `/memory` | Edit CLAUDE.md files, toggle auto-memory, view auto-memory entries |
| `/config [key=value ...]` | Settings interface. From v2.1.181, `key=value` sets directly, e.g. `/config thinking=false`; from v2.1.182, shorthand like `/config theme=dark`. Works in `-p` and via Remote Control. `/config --help` lists every settable key. Alias `/settings` |
| `/permissions` | Allow, ask, and deny rules by scope, working directories, recent auto-mode denials. Alias `/allowed-tools` |
| `/hooks` | View hook configurations for tool events |
| `/mcp [reconnect <server>\|enable\|disable [<server>\|all]]` | MCP connections and OAuth. In `-p`, no argument prints a text status summary instead of the list |
| `/plugin [subcommand]` | Plugin menu, or `list`, `install`, `enable`, `disable` |
| `/keybindings` | Open your keyboard shortcuts file |
| `/add-dir <path>` | Add a working directory for file access. Tab-completes partial paths. Most `.claude/` config is not discovered from it |
| `/cd <path>` | **Move** the session to a new working directory. Preserves the prompt cache by appending the new CLAUDE.md as a message rather than rebuilding the system prompt. Relocates project storage so `--resume` finds it there. Prompts for trust. Restrict with `Cd` permission rules. v2.1.169+ |
| `/ide` | Manage IDE integrations |
| `/chrome` | Claude in Chrome settings |
| `/install-github-app`, `/install-slack-app` | Install the respective integrations |

### During a task

| Command | Purpose |
|---|---|
| `/plan [description]` | Enter plan mode, optionally starting immediately on the described task |
| `/model [model]` | Switch model and **save as default**. No argument opens a picker; `s` on a row switches for this session only. Confirms when the conversation has prior output, since the next response re-reads history without cached context |
| `/effort [level\|auto]` | `low`, `medium`, `high`, `xhigh`, `max`, `ultracode`. `max` and `ultracode` are session-only. `auto` resets to model default. Takes effect immediately |
| `/fast [on\|off]` | Toggle fast mode |
| `/advisor [model\|off]` | Enable or disable the advisor tool |
| `/context [all]` | Context usage as a colored grid, with optimization suggestions and capacity warnings. `all` expands the per-item breakdown in fullscreen |
| `/compact [instructions]` | Summarize the conversation to free context. Pass focus instructions |
| `/btw [question]` | Side question that does not enter the conversation. Without a question, reopens the overlay on your most recent one (v2.1.212+) |
| `/goal [condition\|clear]` | Claude keeps working across turns until the condition is met. `clear`, `stop`, `off`, `reset`, `none`, or `cancel` ends it early |
| `/diff` | Interactive diff viewer: left/right switch between git diff and individual turns, up/down browse files, Enter opens a file diff, Esc returns |
| `/copy [N]` | Copy the last response, or the Nth-latest. Interactive picker for code blocks; `w` writes to a file instead of the clipboard, useful over SSH |
| `/export [filename]` | Export the conversation as plain text |
| `/focus` | Focus view: last prompt, one-line tool-call summary with diffstats, final response. Persists across sessions; `viewMode` overrides. Fullscreen only |
| `/color [color\|default]` | Prompt bar color for this session. Syncs to claude.ai/code under Remote Control |

### Parallel and background work

| Command | Purpose |
|---|---|
| `/tasks` | Background work in this session, including finished subagents |
| `/background [prompt]` | Detach the session to run as a background agent, freeing the terminal. Optional final instruction. Alias `/bg` |
| `/fork [prompt]` | **Copy** the conversation into a new background session while you keep working here. v2.1.212+ |
| `/subtask <task>` | Hand a side task to an in-session forked subagent that reports back into this conversation |
| `/branch [name]` | Branch the conversation and **switch into the branch**, preserving the original for `/resume` |
| `/batch <instruction>` | **Skill.** Decompose a large change into 5 to 30 independent units, one background subagent per unit in its own worktree, each opening a PR. Requires git |
| `/loop [interval] [prompt]` | **Skill.** Run a prompt repeatedly while the session is open. No interval means Claude self-paces. No prompt runs a maintenance check or `.claude/loop.md`. Alias `/proactive` |
| `/deep-research <question>` | **Workflow.** Fan out web searches, cross-check sources, synthesize a cited report |
| `/remote-control` | Make this session available for Remote Control from claude.ai. Alias `/rc` |
| `/desktop` | Continue in the Desktop app. macOS or x64 Windows with a subscription. Alias `/app` |
| `/autofix-pr [prompt]` | Spawn a cloud session that watches the branch's PR and pushes fixes when CI fails or reviewers comment. Requires `gh` |

The three fork-shaped commands are easy to confuse:

| Command | Result |
|---|---|
| `/branch` | You switch into a copy; the original waits in `/resume` |
| `/fork` | A copy runs as a separate **background session**; you stay here |
| `/subtask` | A forked **subagent** runs and reports back into this conversation |

### Review and shipping

| Command | Purpose |
|---|---|
| `/code-review [level] [--fix] [--comment] [target]` | **Skill.** Review the diff for correctness bugs and cleanup opportunities. Levels `low` through `max`, plus `ultra` for a deep cloud review. `--fix` applies findings, `--comment` posts inline GitHub PR comments |
| `/simplify` | Cleanup-only review that applies fixes without hunting for bugs. v2.1.154+ |
| `/security-review` | Check the diff for security vulnerabilities |
| `/review` | Fast single-pass read-only review of a GitHub pull request |
| `/verify` | **Skill.** Build and run your app to confirm a change works, without falling back to tests or type checks |
| `/run` | **Skill.** Launch and drive your app to see a change working |
| `/run-skill-generator` | **Skill.** Record how to build and launch this project as `.claude/skills/run-<name>/` |
| `/dataviz [request]` | **Skill.** Chart and dashboard design guidance with palette validation. v2.1.198+ |
| `/design-sync [hint]`, `/design-login` | Convert and upload your React design system to Claude Design |

### Recovery and diagnostics

| Command | Purpose |
|---|---|
| `/rewind` | Roll code and conversation back to a checkpoint, or summarize part of the conversation. Also reachable with Esc Esc |
| `/doctor` | **Skill.** Setup checkup that diagnoses and can fix: install health, duplicate installs, `PATH`, unparseable settings, unused skills / MCP servers / plugins versus context cost, slow hooks, newer versions. Deduplicates and trims CLAUDE.md, migrating always-loaded guidance into skills and nested files. Offers auto mode as default and pre-approval of frequently denied read-only commands. **Reports first, confirms before changing anything.** Alias `/checkup` |
| `/debug [description]` | **Skill.** Enable debug logging from this point and troubleshoot by reading the log |
| `/bug [report]` | Report a bug or share the conversation, with a consent screen. Without Anthropic credentials, writes to `~/.claude/feedback-bundles/`. Alias `/share` |
| `/feedback [report]` | Product feedback, same dialog and rules as `/bug` |
| `/heapdump` | Heap snapshot and memory breakdown to `~/Desktop`. **Contains your full conversation and credentials, so do not share it** |
| `/release-notes` | Changelog in a version picker. Notes appear in your transcript **without entering the conversation** (v2.1.208+) |
| `/reload-plugins [--force]` | Reload active plugins without restarting. Warns and skips when the reload would invalidate the prompt cache, unless `--force` |
| `/reload-skills` | Re-scan skill and command directories. v2.1.152+ |
| `/insights` | Report analyzing your sessions: project areas, interaction patterns, friction points |
| `/recap` | One-line summary of the current session on demand |

### Session boundaries

| Command | Purpose |
|---|---|
| `/clear [name]` | New conversation with empty context. A name labels the previous one in `/resume`. Aliases `/reset`, `/new` |
| `/resume` | Return to an earlier conversation |
| `/rename` | Rename the current conversation |
| `/teleport` | Pull a web session into this terminal |
| `/exit` | Exit. In an attached background session this **detaches** and the session keeps running. Alias `/quit` |
| `/stop` | End a background session from inside it |

### Account and display

| Command | Purpose |
|---|---|
| `/login`, `/logout` | Sign in and out |
| `/status` | Account and system status, including the `Setting sources` line |
| `/usage` | Token and cost usage. Alias `/cost` |
| `/upgrade` | Plan upgrade. Not shown on Enterprise |
| `/extra-usage` | Configure extra usage for when rate limits are hit |
| `/privacy-settings` | Pro and Max only |
| `/passes` | Share a free week, if eligible |
| `/theme` | Theme picker |
| `/statusline` | Configure a custom status line |
| `/vim` | Toggle vim editing mode |
| `/terminal-setup` | Install the Shift+Enter binding for newlines |
| `/skills` | Skill menu; `Space` cycles `skillOverrides` states, `Enter` saves |
| `/workflows` | List and control dynamic workflow runs |
| `/mobile` | QR code for the mobile app. Aliases `/ios`, `/android` |
| `/help` | Help and available commands |
| `/powerup` | Interactive lessons with animated demos |
| `/radio` | Claude FM lo-fi radio |

Note that command availability and exact arguments move between releases. `/help` and `/config --help` are the live source for your installation.

---

## 6. Keyboard interaction

| Key | Action |
|---|---|
| `Shift+Tab` | Cycle permission modes |
| `Esc` | Interrupt the current response |
| `Esc Esc` | Open the rewind menu |
| `Ctrl+B` | Background the running task |
| `Ctrl+C` | Cancel input; twice on an empty prompt exits |
| `Ctrl+D` | Twice on an empty prompt exits |
| `Ctrl+O` | Transcript mode |
| `Ctrl+T` | Toggle the task list |
| `←` on an empty prompt | Background the session and open agent view; in an attached session, detach |
| `Ctrl+Z` | Detach, returning to where you started |
| `Shift+Enter` | Newline (after `/terminal-setup` on iTerm2 and VS Code) |

The prompt footer's `←` hint counts background agents waiting on you, such as `← 2 agents`, and briefly shows `← 2 done` when background sessions finish with none needing input (v2.1.212+).

Agent view has its own shortcut set; see the agents guide.

---

## 7. Exit codes and scripting signals

| Command | Exit behavior |
|---|---|
| `claude -p "query"` | 0 on success |
| `claude auth status` | 0 if logged in, 1 if not |
| `claude daemon status` | 1 if the supervisor is not running |
| `claude ultrareview` | 0 on success, 1 on failure |
| `claude -p --max-turns N` | Error exit when the turn limit is reached |
| `claude project purge` | Error and exit 1 when no state matches the path |

Useful shell pattern:

```bash
REVIEW=$(git diff | claude -p "Review this diff. Output LGTM if no issues, or list problems.")
if echo "$REVIEW" | grep -q "LGTM"; then
  git commit
else
  echo "$REVIEW"
  exit 1
fi
```

---

## 8. Flag and setting equivalences

Many flags override a persistent setting. Knowing the pairs saves you from re-passing flags.

| Flag | Setting it overrides |
|---|---|
| `--model` | `model` (and `ANTHROPIC_MODEL`) |
| `--fallback-model` | `fallbackModel` |
| `--effort` | `effortLevel` |
| `--permission-mode` | `defaultMode` |
| `--agent` | `agent` |
| `--teammate-mode` | `teammateMode` |
| `--verbose` | `viewMode` |
| `--ax-screen-reader` | `axScreenReader` and `CLAUDE_AX_SCREEN_READER` |
| `--advisor` | `advisorModel` |
| `--add-dir` | `permissions.additionalDirectories` (persistent form) |
| `--allowedTools` / `--disallowedTools` | `permissions.allow` / `.deny` |
| `--settings` | Every key it sets, for that session |

Full precedence, highest first: **managed settings > CLI arguments > `.claude/settings.local.json` > `.claude/settings.json` > `~/.claude/settings.json`**. Array settings like `permissions.allow` merge across scopes; scalar settings take the highest-precedence value.

---

## 9. Environment variables that shape CLI behavior

| Variable | Effect |
|---|---|
| `CLAUDE_CONFIG_DIR` | Relocates the entire `~/.claude` tree, including the supervisor's session store |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` | Authentication. Note these **suppress claude.ai connectors** |
| `CLAUDE_CODE_OAUTH_TOKEN` | Token from `claude setup-token`. Model requests only |
| `ANTHROPIC_MODEL` | Default model, overridden by `--model` |
| `ANTHROPIC_BASE_URL` | Gateway endpoint. Also disables tool search when non-first-party |
| `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` | Provider selection, read from the dispatching shell for background sessions |
| `MCP_TIMEOUT` | MCP server startup timeout, e.g. `MCP_TIMEOUT=10000 claude` |
| `MAX_MCP_OUTPUT_TOKENS` | Max tokens per MCP tool result, default 25,000 |
| `ENABLE_TOOL_SEARCH` | `true` / `auto` / `auto:N` / `false` |
| `CLAUDE_CODE_SIMPLE` | Set by `--bare` |
| `CLAUDE_CODE_SAFE_MODE` | Set by `--safe-mode` |
| `CLAUDE_CODE_SKIP_PROMPT_HISTORY` | Skip transcript and prompt-history writes in any mode |
| `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS` | Disable background tasks, including auto-backgrounded MCP calls and forked skills |
| `CLAUDE_CODE_DISABLE_AGENT_VIEW` | Turn off background agents and agent view |
| `CLAUDE_CODE_NEW_INIT` | Interactive `/init` flow |
| `CLAUDE_CODE_DEBUG_LOGS_DIR` | Debug log location, overridden by `--debug-file` |
| `CLAUDE_JOB_DIR` | Set by Claude Code in each background session |
| `DISABLE_AUTOUPDATER` | Disable auto-updates |
| `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS` | Strips beta headers; also disables tool search uncoverridably |

---

## 10. Verification commands

When something is not behaving, these tell you what is actually loaded:

| Command | What it reveals |
|---|---|
| `claude --version` | Installed version, which most version-gated behavior depends on |
| `claude doctor` | Install health, settings-file validation errors, read-only |
| `/doctor` | The same plus fixes, context-cost analysis, and CLAUDE.md trimming |
| `/status` | Active authentication method and the `Setting sources` line, listing each settings file loaded |
| `/config --help` | Every settable key with its options |
| `/context` | What is filling the context window, with the skill-listing size after budgeting |
| `/permissions` | Effective rules by scope |
| `/mcp` | Server status, tool counts, per-tool org controls |
| `claude mcp list` | Server health from the shell |
| `claude agents --json` | Background sessions as structured data |
| `claude daemon status` | Supervisor version mismatch after an update |
| `--debug "api,mcp"` | Category-filtered runtime logging |
| `--safe-mode` | Whether a customization is causing the problem |

The `/status` `Setting sources` line is the fastest way to catch a settings file that is not loading: **a file with broken JSON does not appear at all**, even though it contains settings.

---

## 11. Gotchas

1. **`claude --help` is not the complete flag list.** Absence there proves nothing.
2. **`--allowedTools` and `--tools` do different things.** The first waives prompting; the second restricts availability.
3. **A bare tool name in `--disallowedTools` removes the tool from context**, while a scoped rule leaves it available and denies matching calls.
4. **`--tools` does not touch MCP tools.** Pair with `--disallowedTools "mcp__*"` or `--strict-mcp-config` with no `--mcp-config`.
5. **`--bg` cannot be combined with `-p`**, since `--print` never starts the interactive session agent view attaches to.
6. **`--bare` and `--safe-mode` are different.** Bare skips discovery for speed; safe mode disables customizations while keeping permissions and tools normal.
7. **`--max-budget-usd` counts subagent spend**, and at the cap it stops running background subagents.
8. **`--max-turns` exits with an error** rather than returning what it has.
9. **`--resume <id>` searches only the current project directory and its worktrees**, while the picker and name search are broader.
10. **`--plugin-dir` takes one path per flag.** Repeat it; do not space-separate.
11. **`claude agents` rejects `--add-dir` and `--mcp-config` placed before `agents`** with an `unknown option` error for `--json`; keep them after.
12. **`/clear` versus `/compact`**: clear starts a new conversation, compact frees space within the current one.
13. **`/branch`, `/fork`, and `/subtask` are three different things**, and `/fork` changed meaning at v2.1.212.
14. **`/exit` in an attached background session detaches**; it does not stop the session. Use `/stop` from inside.
15. **`/model` saves as your default** unless you press `s` in the picker for a session-only switch.
16. **`/doctor` can modify your CLAUDE.md and settings.** It reports first and confirms, but know that going in.
17. **`/heapdump` contains your conversation and credentials.**
18. **A settings file with broken JSON silently vanishes from `/status`**, which is how you detect it.
19. **`--settings` overrides only the keys it sets**; omitted keys keep their file values.
20. **Version gating is everywhere in this CLI.** When a documented flag or behavior does not work, check `claude --version` before anything else.

---

## Reference links

- CLI reference: https://code.claude.com/docs/en/cli-reference
- Commands reference: https://code.claude.com/docs/en/commands
- Interactive mode: https://code.claude.com/docs/en/interactive-mode
- Settings: https://code.claude.com/docs/en/settings
- Environment variables: https://code.claude.com/docs/en/env-vars
- Permissions: https://code.claude.com/docs/en/permissions
- Permission modes: https://code.claude.com/docs/en/permission-modes
- Model configuration: https://code.claude.com/docs/en/model-config
- Headless / print mode: https://code.claude.com/docs/en/headless
- Agent view: https://code.claude.com/docs/en/agent-view
- Worktrees: https://code.claude.com/docs/en/worktrees
- Checkpointing: https://code.claude.com/docs/en/checkpointing
- Keybindings: https://code.claude.com/docs/en/keybindings
- Debug your configuration: https://code.claude.com/docs/en/debug-your-config
- Error reference: https://code.claude.com/docs/en/errors
- Changelog: https://code.claude.com/docs/en/release-notes
- Full docs index: https://code.claude.com/docs/llms.txt
