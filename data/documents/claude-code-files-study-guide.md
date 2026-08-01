# Claude Code: Complete File Reference

A study guide to every file and directory Claude Code reads or writes, where each one lives, what it does, and what it looks like.

Verified against the official documentation at https://code.claude.com/docs/en/claude-directory and https://code.claude.com/docs/en/settings

---

## 1. Mental model

There are **five** places configuration can come from. Learn these first, everything else hangs off them.

| Tier | Root | Who it affects | In git? |
|---|---|---|---|
| Managed | OS system paths, MDM policy, or server-delivered | Everyone in the org | No, deployed by IT |
| Global / User | `~/.claude/` and `~/.claude.json` | You, in every project | No |
| Project | `<repo>/.claude/` and a few repo-root files | Everyone on the repo | Yes |
| Local | `<repo>/.claude/settings.local.json` | You, in this repo only | No, gitignored |
| Runtime | CLI flags, environment variables | This session only | N/A |

Two important consequences:

1. `.claude` is a folder name that exists in **two different places** with mostly the same contents: `~/.claude/` (yours) and `<repo>/.claude/` (the team's).
2. Not everything lives inside a `.claude/` folder. `~/.claude.json`, `CLAUDE.md`, `.mcp.json`, `.worktreeinclude`, and `CLAUDE.local.md` are all outside it.

On Windows, `~/.claude` resolves to `%USERPROFILE%\.claude`.
If `CLAUDE_CONFIG_DIR` is set, every `~/.claude` path lives under that directory instead.

---

## 2. Precedence

### Settings precedence (highest wins)

```
1. Managed settings        (cannot be overridden by anything)
2. CLI arguments           (--settings, --permission-mode, --model, ...)
3. .claude/settings.local.json
4. .claude/settings.json
5. ~/.claude/settings.json
```

Two nuances worth memorising:

- **Scalar keys override.** `model`, `outputStyle`, `editorMode`: the highest-precedence file that defines the key wins.
- **Array keys merge.** `permissions.allow`, `permissions.deny`, `allowedHttpHookUrls`: entries combine across all scopes rather than replacing each other. `fallbackModel` is the exception, it does not merge.

### Memory precedence

CLAUDE.md files do **not** merge key-by-key. Global and project CLAUDE.md are both loaded into context simultaneously; when instructions conflict, project-level instructions take priority as a matter of instruction, not of file resolution.

### Reload behaviour

Claude Code watches settings files and hot-reloads most keys mid-session, including `permissions`, `hooks`, and `apiKeyHelper`. Two keys are read only at session start:

- `model` (use `/model` to change mid-session)
- `outputStyle` (part of the system prompt, rebuilt on `/clear` or restart)

---

## 3. Project scope

Lives in your repository. Most of it should be committed.

### 3.1 Repo root files (NOT inside `.claude/`)

#### `CLAUDE.md`

Project instructions loaded into context at the start of every session. Also works at `.claude/CLAUDE.md` if you prefer a clean repo root.

Target under 200 lines. Longer files still load in full but adherence drops. Open it mid-session with `/memory`.

```markdown
# Project conventions

## Commands
- Build: `npm run build`
- Test: `npm test`
- Lint: `npm run lint`

## Stack
- TypeScript with strict mode
- React 19, functional components only

## Rules
- Named exports, never default exports
- Tests live next to source: `foo.ts` -> `foo.test.ts`
- All API routes return `{ data, error }` shape
```

Supports `@path/to/file.md` import syntax, recursive up to 5 hops. Imports are not evaluated inside code spans or code blocks.

Claude Code reads memory recursively: starting from the cwd it walks up to (but not including) `/`, picking up every `CLAUDE.md` it finds. Useful in monorepos.

#### `CLAUDE.local.md`

Your private per-project preferences, loaded alongside `CLAUDE.md`. You create it manually and gitignore it yourself. Considered legacy: the docs now recommend `@` imports instead, because those work better across worktrees.

#### `.mcp.json`

Project-scoped MCP servers, shared with the team. Lives at the repo root, not inside `.claude/`.

```json
{
  "mcpServers": {
    "notion": {
      "command": "npx",
      "args": ["-y", "@notionhq/notion-mcp-server"],
      "env": {
        "NOTION_TOKEN": "${NOTION_TOKEN}"
      }
    }
  }
}
```

Use `${VAR}` references for secrets so tokens never land in the file. Servers connect at session start; tool schemas are deferred and loaded on demand via tool search.

Control approval from settings with `enableAllProjectMcpServers`, `enabledMcpjsonServers`, or `disabledMcpjsonServers`.

#### `.worktreeinclude`

Lists gitignored files to copy from the main repository into each new git worktree. Worktrees are fresh checkouts, so untracked files like `.env` are missing by default.

```
# Local environment
.env
.env.local

# API credentials
config/secrets.json
```

Uses `.gitignore` pattern syntax. Only files that match a pattern **and** are gitignored get copied. Git-only: if you use a `WorktreeCreate` hook for another VCS, this file is not read.

---

### 3.2 `<repo>/.claude/` contents

#### `settings.json`

Enforced configuration: permissions, hooks, env vars, model defaults. Unlike CLAUDE.md which is guidance Claude reads, these are enforced whether Claude cooperates or not.

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "permissions": {
    "allow": [
      "Bash(npm test *)",
      "Bash(npm run *)"
    ],
    "deny": [
      "Bash(rm -rf *)",
      "Read(./.env)",
      "Read(./secrets/**)"
    ]
  },
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1"
  },
  "hooks": {
    "PostToolUse": [{
      "matcher": "Edit|Write",
      "hooks": [{
        "type": "command",
        "command": "jq -r '.tool_input.file_path' | xargs npx prettier --write"
      }]
    }]
  }
}
```

Common top-level keys: `permissions`, `hooks`, `statusLine`, `model`, `env`, `outputStyle`, `fallbackModel`, `cleanupPeriodDays`, `disableAllHooks`, `apiKeyHelper`.

Adding `$schema` gives autocomplete and inline validation in editors that support JSON schema.

Note: user, project, and local settings files are validated **strictly**. A file that fails validation is rejected as a whole. Managed settings parse tolerantly instead, stripping only the invalid entry.

#### `settings.local.json`

Same schema, your personal overrides, not committed. Claude Code adds `**/.claude/settings.local.json` to your global git excludes the first time it writes one.

```json
{
  "permissions": {
    "allow": [
      "Bash(docker *)"
    ]
  }
}
```

This is also where in-session "don't ask again" permission approvals get saved.

As of v2.1.211 the file is read and written at the **git repository root**, resolved through worktrees to the main checkout, so one file covers every subdirectory and worktree. It stays in the starting directory in three cases: outside a git repo, when the repo root is your home directory, and in Agent SDK sessions.

#### `rules/*.md`

Project instructions split into topic files that can load conditionally. A rule without `paths:` loads at session start like CLAUDE.md; a rule with `paths:` loads only when Claude reads a matching file. Subdirectories are discovered automatically (`.claude/rules/frontend/react.md`).

```markdown
---
paths:
  - "**/*.test.ts"
  - "**/*.test.tsx"
---

# Testing Rules

- Use descriptive test names: "should [expected] when [condition]"
- Mock external dependencies, not internal modules
- Clean up side effects in afterEach
```

Start splitting CLAUDE.md into rules when it approaches 200 lines.

#### `skills/<name>/SKILL.md`

Reusable prompts invoked with `/name` or auto-invoked by Claude when the task matches the `description`. Each skill is a folder: `SKILL.md` plus any supporting files.

```markdown
---
description: Reviews code changes for security vulnerabilities, authentication gaps, and injection risks
disable-model-invocation: true
argument-hint: <branch-or-path>
---

## Diff to review

!`git diff $ARGUMENTS`

Audit the changes above for:

1. Injection vulnerabilities (SQL, XSS, command)
2. Authentication and authorization gaps
3. Hardcoded secrets or credentials

Use checklist.md in this skill directory for the full review checklist.
```

Key mechanics:

- `$ARGUMENTS` substitutes everything typed after the skill name; `$0`, `$1` for positional access.
- `` !`...` `` runs a shell command and injects its output into the prompt before Claude sees it. Blocked when `disableSkillShellExecution` is set.
- `disable-model-invocation: true` makes it user-only. `user-invocable: false` hides it from the `/` menu but lets Claude invoke it.
- `${CLAUDE_SKILL_DIR}` resolves to the skill directory, for scripts in bash injection.
- Bundled supporting files (`checklist.md`, templates, scripts) are read on demand.

#### `commands/*.md`

Single-file prompts. `commands/deploy.md` creates `/deploy`, the same way `skills/deploy/SKILL.md` does. Same mechanism, less structure. If a skill and command share a name, the skill wins.

```markdown
---
argument-hint: <issue-number>
---

!`gh issue view $ARGUMENTS`

Investigate and fix the issue above.

1. Trace the bug to its root cause
2. Implement the fix
3. Write or update tests
4. Summarize what you changed and why
```

The docs now recommend writing new workflows as skills. Commands remain supported.

#### `agents/*.md`

Subagent definitions. Each runs in its own fresh context window with its own system prompt, tool access, and optionally its own model.

```markdown
---
name: code-reviewer
description: Reviews code for correctness, security, and maintainability
tools: Read, Grep, Glob
---

You are a senior code reviewer. Review for:

1. Correctness: logic errors, edge cases, null handling
2. Security: injection, auth bypass, data exposure
3. Maintainability: naming, complexity, duplication

Every finding must include a concrete fix.
```

`description` tells Claude when to delegate automatically. `tools:` restricts tool access. The body becomes the system prompt. Type `@` to pick an agent from autocomplete and delegate directly.

Optional frontmatter keys worth knowing: `memory: project | local | user` (see below), `model:`, `isolation: worktree`.

#### `workflows/*.js`

Dynamic workflow scripts that spawn and coordinate many subagents. Each file becomes a `/<name>` command, loaded at startup. These are written by Claude and saved from `/workflows` with `s`, rather than authored by hand. A project workflow takes precedence over a personal one with the same name.

#### `output-styles/*.md`

Project-scoped output styles. Usually personal (so usually in `~/.claude/output-styles/`), but put one here if the team shares a style. See section 4 for the format.

#### `agent-memory/<agent-name>/MEMORY.md`

Persistent memory for subagents declaring `memory: project`. Committed and shared with the team. The subagent writes and maintains this itself.

```markdown
# code-reviewer memory

## Patterns seen
- Project uses custom Result<T, E> type, not exceptions
- Auth middleware expects Bearer token in Authorization header
- Tests use factory functions in test/factories/

## Recurring issues
- Missing null checks on API responses (src/api/*)
- Unhandled promise rejections in background jobs
```

First 200 lines (capped at 25KB) are loaded into the subagent system prompt at start.

Related variants:

| Frontmatter | Directory | Shared |
|---|---|---|
| `memory: project` | `.claude/agent-memory/<name>/` | Yes, committed |
| `memory: local` | `.claude/agent-memory-local/<name>/` | No, gitignored |
| `memory: user` | `~/.claude/agent-memory/<name>/` | No, all projects |

This is a **different feature** from main-session auto memory, which lives in `~/.claude/projects/`.

---

## 4. Global scope

### 4.1 `~/.claude.json`

A file, not a directory. This is the single most common source of confusion.

Holds application state that does not belong in `settings.json`: OAuth session, theme, per-project trust decisions, your personal MCP servers, IDE toggles, and various caches. Mostly managed through `/config` rather than hand-edited.

```json
{
  "autoConnectIde": true,
  "externalEditorContext": true,
  "mcpServers": {
    "my-tools": {
      "command": "npx",
      "args": ["-y", "@example/mcp-server"]
    }
  }
}
```

The `projects` key tracks per-project state (trust dialog acceptance, last-session metrics). Note that permission rules you approve in-session go to `.claude/settings.local.json`, not here.

MCP scopes: user scope (all projects) and local scope (per-project, not committed) both live here. Team-shared servers go in `.mcp.json` at the repo root.

### 4.2 `~/.claude/` contents

Same structure as the project directory, applied everywhere:

- `CLAUDE.md` - personal preferences across all projects
- `settings.json` - your defaults, lowest precedence
- `rules/` - user-level rules
- `skills/` - personal skills
- `commands/` - personal commands
- `agents/` - personal subagents
- `workflows/` - personal workflows
- `output-styles/` - personal output styles
- `agent-memory/` - `memory: user` subagent memory

Plus three that are global-only:

#### `keybindings.json`

Rebind interactive CLI shortcuts. Run `/keybindings` to create or open it. Hot-reloaded on edit. Ctrl+C, Ctrl+D, Ctrl+M, and Caps Lock are reserved.

```json
{
  "$schema": "https://www.schemastore.org/claude-code-keybindings.json",
  "bindings": [
    {
      "context": "Chat",
      "bindings": {
        "ctrl+e": "chat:externalEditor",
        "ctrl+u": null
      }
    }
  ]
}
```

`null` unbinds. `context` scopes a binding to a part of the CLI.

#### `themes/*.json`

Custom color themes. Read at session start, hot-reloaded on change, listed in `/theme`. Selecting one stores `custom:<slug>` as your theme preference.

```json
{
  "name": "Dracula",
  "base": "dark",
  "overrides": {
    "claude": "#bd93f9",
    "error": "#ff5555",
    "success": "#50fa7b"
  }
}
```

#### `projects/<project>/memory/` (auto memory)

Claude's notes to itself, per project, accumulated across sessions without you writing anything. On by default, toggle with `/memory` or `autoMemoryEnabled`.

`MEMORY.md` is the index, loaded at session start (first 200 lines or 25KB, whichever comes first):

```markdown
# Memory Index

## Project
- [build-and-test.md](build-and-test.md): npm run build (~45s), Vitest, dev server on 3001
- [architecture.md](architecture.md): API client singleton, refresh-token auth

## Reference
- [debugging.md](debugging.md): auth token rotation and DB connection troubleshooting
```

Topic files are split out when `MEMORY.md` grows, and read on demand:

```markdown
---
name: Debugging patterns
description: Auth token rotation and database connection troubleshooting for this project
type: reference
---

## Auth Token Issues
- Refresh token rotation: old token invalidated immediately
- If 401 after refresh: check clock skew between client and server
```

Relocate with `autoMemoryDirectory`. From project or local settings that key is honored only after you accept the workspace trust dialog, since a cloned repo could otherwise supply it.

### 4.3 Output styles (format)

A markdown file whose body is appended to the system prompt. By default it also **drops** the built-in software-engineering task instructions, which is what lets you repurpose Claude Code for non-coding work.

```markdown
---
description: Explains reasoning and asks you to implement small pieces
keep-coding-instructions: true
---

After completing each task, add a brief "Why this approach" note
explaining the key design decision.

When a change is under 10 lines, ask the user to implement it
themselves by leaving a TODO(human) marker instead of writing it.
```

Select with `/config` or the `outputStyle` setting, using the filename without `.md` (or the `name` frontmatter field). Built-in styles Explanatory and Learning ship with Claude Code. Changes take effect on the next session, because the system prompt is fixed at startup for caching.

---

## 5. Managed / enterprise scope

Cannot be overridden by user or project settings. Five delivery mechanisms, all using the same JSON format.

### 5.1 File-based

| OS | Directory |
|---|---|
| macOS | `/Library/Application Support/ClaudeCode/` |
| Linux and WSL | `/etc/claude-code/` |
| Windows | `C:\Program Files\ClaudeCode\` |

Files placed there:

- `managed-settings.json`
- `managed-mcp.json`
- `managed-settings.d/*.json` - drop-in fragments, so separate teams can deploy independent policy without editing one file

Merge order follows the systemd convention: `managed-settings.json` first as the base, then every `*.json` in `managed-settings.d/` sorted alphabetically on top. Later files override scalars, arrays concatenate and de-duplicate, objects deep-merge. Use numeric prefixes (`10-telemetry.json`, `20-security.json`) to control order. Dotfiles are ignored.

The legacy Windows path `C:\ProgramData\ClaudeCode\managed-settings.json` is no longer supported as of v2.1.75.

Enterprise policy `CLAUDE.md` historically lived in the same directories. The current mechanism is the `claudeMd` key in managed settings.

### 5.2 MDM / OS policy

- macOS: `com.anthropic.claudecode` managed preferences domain, deployed via configuration profiles (Jamf, Kandji, etc). Plist top-level keys mirror `managed-settings.json`.
- Windows machine-level: `HKLM\SOFTWARE\Policies\ClaudeCode`, value `Settings` (REG_SZ or REG_EXPAND_SZ) containing JSON.
- Windows user-level: `HKCU\SOFTWARE\Policies\ClaudeCode`, lowest policy priority.

### 5.3 Server-managed

Delivered remotely at sign-in from the claude.ai admin console or a self-hosted Claude apps gateway. Cached locally at `~/.claude/remote-settings.json`.

### 5.4 Managed-only keys worth knowing

`allowedMcpServers`, `deniedMcpServers`, `allowManagedMcpServersOnly`, `allowManagedHooksOnly`, `allowManagedPermissionRulesOnly`, `claudeMd`, `blockedMarketplaces`, `strictKnownMarketplaces`, `disableSideloadFlags`, `forceLoginMethod`, `forceLoginOrgUUID`, `channelsEnabled`, `availableModels`, `enforceAvailableModels`, `forceRemoteSettingsRefresh`.

Security-enforcement fields fail **closed** when invalid (an invalid `allowedMcpServers` becomes an empty allowlist). `requiredMinimumVersion` and `requiredMaximumVersion` fail **open** by design, so a bad policy push cannot brick the CLI.

---

## 6. Plugins

### 6.1 Installed plugin storage

Everything lives under `~/.claude/plugins/`:

```
~/.claude/plugins/
  known_marketplaces.json      marketplace state, once per user
  marketplaces/<name>/...      cloned marketplace repos
  cache/<marketplace>/<plugin>/<version>/...
```

Managed by `claude plugin` and `/plugin` commands. Orphaned versions are deleted 14 days after an update or uninstall. Do not delete this directory by hand.

For containers and CI, `CLAUDE_CODE_PLUGIN_SEED_DIR` points at a pre-populated directory mirroring this structure. Multiple seeds separate with `:` on Unix, `;` on Windows.

### 6.2 Authoring a plugin

```
my-plugin/
  .claude-plugin/
    plugin.json          required manifest
  .mcp.json              optional MCP servers
  commands/              optional
  agents/                optional
  skills/                optional
  hooks/                 optional
  README.md
```

`plugin.json` carries name (lowercase kebab-case), description, semantic version, author, repository, license.

Plugins are **copied** to a cache location on install, so they cannot reference files outside their own directory with `../` paths. Use symlinks if you need to share files across plugins.

`${CLAUDE_PLUGIN_ROOT}` resolves to the installed plugin directory inside scripts and hook commands.

### 6.3 Authoring a marketplace

`.claude-plugin/marketplace.json` at the repository root:

```json
{
  "name": "your-marketplace-name",
  "owner": { "name": "Your Name" },
  "version": "1.0.0",
  "description": "What your marketplace is for",
  "plugins": [
    {
      "name": "your-plugin",
      "source": "./plugins/your-plugin",
      "version": "0.1.0"
    }
  ]
}
```

`source` accepts a local path, a GitHub `owner/repo` shorthand, or a `{ "source": "url", "url": "..." }` object pointing at an external repository, optionally pinned with `ref` or `sha`.

The `name` field of a plugin entry is an immutable slug. Renaming breaks existing installs; use the top-level `renames` map instead.

---

## 7. Runtime application data

Everything below is written by Claude Code as you work, under `~/.claude/`. **All plaintext.** Anything that passes through a tool lands in a transcript: file contents, command output, pasted text.

### 7.1 Swept automatically

Deleted at startup once older than `cleanupPeriodDays` (default 30, minimum 1).

| Path | Contents |
|---|---|
| `projects/<project>/<session>.jsonl` | Full conversation transcript: every message, tool call, tool result |
| `projects/<project>/<session>/subagents/` | Subagent transcripts |
| `projects/<project>/<session>/tool-results/` | Large tool outputs spilled to files |
| `file-history/<session>/` | Pre-edit file snapshots for `/rewind`. Keeps the 100 most recent checkpoints |
| `plans/` | Plan-mode plan files |
| `debug/` | Per-session debug logs, only with `--debug` or `/debug` |
| `paste-cache/`, `image-cache/` | Large pastes and attached images |
| `session-env/` | Per-session environment metadata |
| `tasks/` | Per-session task lists |
| `shell-snapshots/` | Aliases, functions, shell options captured at startup and applied by the Bash tool |
| `backups/` | Timestamped copies of `~/.claude.json` before config migrations |
| `feedback-bundles/` | Redacted transcript archives from `/feedback` |
| `todos/`, `statsig/`, `logs/` | Legacy, no longer written. The sweep removes them |

`sessions/` holds one small file per running session for concurrency and crash detection. Not age-swept; removed when the session exits.

### 7.2 Kept until you delete them

| Path | Contents |
|---|---|
| `history.jsonl` | Every prompt you have typed, with timestamp and project path. Drives up-arrow recall |
| `stats-cache.json` | Aggregated token and cost counts for `/usage` |
| `remote-settings.json` | Cached server-managed settings, refreshed each launch |
| `cache/changelog.md` | Cached changelog for post-update release notes |
| `policy-limits.json` | Cached feature policy for your organization |

### 7.3 Reducing exposure

Transcripts are not encrypted at rest. OS file permissions are the only protection. If a tool reads a `.env` or a command prints a credential, that value is on disk.

- Lower `cleanupPeriodDays`
- Set `CLAUDE_CODE_SKIP_PROMPT_HISTORY` to skip writing transcripts and prompt history entirely
- In non-interactive mode, pass `--no-session-persistence` with `-p`, or `persistSession: false` in the Agent SDK
- Use `permissions.deny` rules to block reads of credential files

### 7.4 Clearing project state

```bash
claude project purge ~/work/my-repo --dry-run   # preview the plan
claude project purge ~/work/my-repo             # confirm once, then delete
claude project purge ~/work/my-repo --yes       # scriptable
claude project purge --all                      # every project
```

Requires v2.1.124 or later. Deletes transcripts and auto memory under `projects/`, per-session `tasks/`, `debug/`, `file-history/` entries, matching lines in `history.jsonl`, and the project's entry in `~/.claude.json`. Leaves `shell-snapshots/` and `backups/` alone since they are not project-scoped.

**Never delete** `~/.claude.json`, `~/.claude/settings.json`, or `~/.claude/plugins/`. Those hold auth, preferences, and installed plugins.

---

## 8. Environment variables that move files

| Variable | Effect |
|---|---|
| `CLAUDE_CONFIG_DIR` | Relocates the entire `~/.claude` tree |
| `CLAUDE_CODE_PLUGIN_SEED_DIR` | Pre-populated plugin directory for containers and CI |
| `CLAUDE_CODE_SKIP_PROMPT_HISTORY` | Stops transcript and prompt-history writes |
| `CLAUDE_PLUGIN_ROOT` | Set by Claude Code: installed plugin directory |
| `CLAUDE_SKILL_DIR` | Set by Claude Code: current skill directory |
| `CLAUDE_PROJECT_DIR` | Set by Claude Code: project root. Anchor hook script paths with this |

Some environment variables take precedence over their equivalent setting, but this varies per variable. Check the env vars reference for each one.

---

## 9. Master cheat sheet

| File | Scope | Commit | Purpose |
|---|---|---|---|
| `CLAUDE.md` | project + global | yes | Instructions loaded every session |
| `CLAUDE.local.md` | project | no | Private per-project instructions (legacy) |
| `.claude/rules/*.md` | project + global | yes | Topic-scoped, optionally path-gated instructions |
| `.claude/settings.json` | project + global | yes | Permissions, hooks, env, model |
| `.claude/settings.local.json` | project | no | Personal overrides + saved approvals |
| `.mcp.json` | project | yes | Team-shared MCP servers |
| `.worktreeinclude` | project | yes | Gitignored files to copy into worktrees |
| `.claude/skills/<n>/SKILL.md` | project + global | yes | Reusable prompts, `/name` or auto-invoked |
| `.claude/commands/*.md` | project + global | yes | Single-file prompts |
| `.claude/output-styles/*.md` | project + global | yes | Custom system-prompt sections |
| `.claude/agents/*.md` | project + global | yes | Subagent definitions |
| `.claude/workflows/*.js` | project + global | yes | Multi-subagent orchestration scripts |
| `.claude/agent-memory/<n>/` | project + global | yes | Subagent persistent memory |
| `.claude/agent-memory-local/<n>/` | project | no | Subagent memory, `memory: local` |
| `~/.claude.json` | global | no | App state, OAuth, UI toggles, personal MCP |
| `~/.claude/keybindings.json` | global | no | Keyboard shortcuts |
| `~/.claude/themes/*.json` | global | no | Custom color themes |
| `~/.claude/projects/<p>/memory/` | global | no | Auto memory, Claude's own notes |
| `~/.claude/plugins/` | global | no | Installed plugins and marketplaces |
| `managed-settings.json` | system | n/a | Enterprise-enforced settings |
| `managed-mcp.json` | system | n/a | Enterprise-enforced MCP servers |
| `.claude-plugin/plugin.json` | plugin repo | yes | Plugin manifest |
| `.claude-plugin/marketplace.json` | marketplace repo | yes | Marketplace catalog |

---

## 10. Verification commands

| Command | What it tells you |
|---|---|
| `/status` | The `Setting sources` line lists every settings file loaded. A file with broken JSON does not appear |
| `/doctor` or `claude doctor` | Resolved settings, plus stripped managed entries with source file and field |
| `/memory` | Which memory files are loaded; opens CLAUDE.md for editing |
| `/config` | Tabbed settings UI. From v2.1.181, `/config key=value` sets one option directly |
| `/permissions` | Live permission management without editing JSON |
| `/keybindings` | Creates or opens `keybindings.json` with a schema reference |
| `/theme` | Lists themes including custom ones |
| `/workflows` | Run and save dynamic workflows |
| `claude mcp list`, `claude mcp get` | Resolved MCP servers across scopes |

If something is not taking effect, the docs have a symptom-first lookup table at https://code.claude.com/docs/en/debug-your-config

---

## 11. Common gotchas

1. `~/.claude.json` is a file next to the `~/.claude/` directory, not inside it.
2. `.mcp.json` and `.worktreeinclude` sit at the repo root, not inside `.claude/`.
3. Array settings merge across scopes; scalar settings override. This surprises people with `permissions.deny`.
4. `model` and `outputStyle` do not hot-reload; everything else mostly does.
5. Project `settings.json` allow rules require the workspace trust step. `settings.local.json` allow rules do not, unless the repo itself supplied that file.
6. If a skill and a command share a name, the skill wins.
7. Subagent memory (`.claude/agent-memory/`) and auto memory (`~/.claude/projects/`) are two different features that both produce a file called `MEMORY.md`.
8. A malformed user, project, or local settings file is rejected **whole**. Only managed settings degrade gracefully.
9. `.claude/hooks/` is a convention, not a mechanism. Hooks are declared in `settings.json`; scripts can live anywhere. Anchor paths with `$CLAUDE_PROJECT_DIR`.
10. Everything in `~/.claude/projects/` is plaintext, including any secret a tool happened to read.

---

## Reference links

- .claude directory explorer: https://code.claude.com/docs/en/claude-directory
- Settings: https://code.claude.com/docs/en/settings
- Memory: https://code.claude.com/docs/en/memory
- Skills: https://code.claude.com/docs/en/skills
- Subagents: https://code.claude.com/docs/en/sub-agents
- Hooks: https://code.claude.com/docs/en/hooks
- Permissions: https://code.claude.com/docs/en/permissions
- MCP: https://code.claude.com/docs/en/mcp
- Plugin marketplaces: https://code.claude.com/docs/en/plugin-marketplaces
- Debug your config: https://code.claude.com/docs/en/debug-your-config
- Full docs index: https://code.claude.com/docs/llms.txt
