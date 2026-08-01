# Claude Code: Memory and CLAUDE.md Reference

A study guide to the two memory systems: CLAUDE.md files you write, and auto memory Claude writes for itself. Covers the hierarchy, load order, imports, `.claude/rules/` with path scoping, auto memory internals, organizational deployment, and troubleshooting.

Verified against code.claude.com/docs/en/memory.

---

## 1. The two systems

Each session begins with a fresh context window. Two mechanisms carry knowledge across sessions.

| | CLAUDE.md files | Auto memory |
|---|---|---|
| **Who writes it** | You | Claude |
| **Contains** | Instructions and rules | Learnings and patterns |
| **Scope** | Project, user, or org | Per repository, shared across worktrees |
| **Loaded into** | Every session | Every session (first 200 lines or 25KB) |
| **Use for** | Coding standards, workflows, architecture | Build commands, debugging insights, discovered preferences |

**The sentence that governs everything below:** both are **context, not enforced configuration**. To block an action regardless of what Claude decides, use a PreToolUse hook.

A related mechanical detail from the troubleshooting section: **CLAUDE.md content is delivered as a user message after the system prompt, not as part of the system prompt itself.** Claude reads it and tries to follow it, but there is no guarantee of strict compliance, especially for vague or conflicting instructions.

Subagents can maintain their own auto memory via the `memory:` frontmatter field, which is a separate directory from the main session's.

---

## 2. When to write to CLAUDE.md

The docs frame it as "the place you write down what you'd otherwise re-explain." Concrete triggers:

- Claude makes the same mistake a **second** time
- A code review catches something Claude should have known about this codebase
- You type the same correction you typed last session
- A new teammate would need the same context to be productive

**What belongs there**: facts Claude should hold in **every** session — build commands, conventions, project layout, "always do X" rules.

**What does not**: a multi-step procedure, or anything that matters only for one part of the codebase. Those go to a **skill** or a **path-scoped rule**.

---

## 3. The hierarchy

Listed in **load order, broadest to most specific**, so a project instruction appears in context *after* a user instruction.

| Scope | Location | Shared with |
|---|---|---|
| **Managed policy** | macOS: `/Library/Application Support/ClaudeCode/CLAUDE.md`<br>Linux/WSL: `/etc/claude-code/CLAUDE.md`<br>Windows: `C:\Program Files\ClaudeCode\CLAUDE.md` | All users in the organization |
| **User instructions** | `~/.claude/CLAUDE.md` | Just you, all projects |
| **Project instructions** | `./CLAUDE.md` **or** `./.claude/CLAUDE.md` | Team, via source control |
| **Local instructions** | `./CLAUDE.local.md` | Just you, current project |

The two project locations are **alternatives for the same scope**, not a hierarchy.

### How files load

Claude Code **walks up the directory tree** from your working directory, checking each level for `CLAUDE.md` and `CLAUDE.local.md`. Running in `foo/bar/` loads `foo/bar/CLAUDE.md`, `foo/CLAUDE.md`, and any `CLAUDE.local.md` alongside them.

**All discovered files are concatenated, not overridden.** Ordering:

- **Across the tree**: filesystem root down to your working directory, so `foo/CLAUDE.md` appears before `foo/bar/CLAUDE.md`. **Instructions closer to where you launched are read last.**
- **Within a directory**: `CLAUDE.local.md` is appended **after** `CLAUDE.md`, so your personal notes are the last thing read at that level

**Subdirectories below your working directory are different**: they are discovered but **not loaded at launch**. They are included when Claude reads files in those subdirectories.

### Verifying what loaded

`/memory` lists memory file **locations** across scopes, including entries for files that do not exist yet. **`/context` shows what actually loaded** into the current session, under **Memory files**. When debugging, `/context` is the one that answers the question.

### HTML comments

**Block-level HTML comments (`<!-- maintainer notes -->`) are stripped before injection.** Use them for human maintainers without spending context tokens. Comments inside code blocks are preserved, and comments remain visible when you open the file with the Read tool.

### Additional directories

By default, CLAUDE.md files from `--add-dir` directories are **not** loaded. Set `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1`:

```bash
CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1 claude --add-dir ../shared-config
```

This loads `CLAUDE.md`, `.claude/CLAUDE.md`, `.claude/rules/*.md`, and `CLAUDE.local.md`. **`CLAUDE.local.md` is skipped if you exclude `local` from `--setting-sources`.**

---

## 4. Writing effective instructions

**Size**: target **under 200 lines per file**. Longer files consume more context and **reduce adherence**. If growing large, use path-scoped rules. **Splitting into `@path` imports helps organization but does not reduce context**, since imports load at launch.

**Structure**: markdown headers and bullets. Claude scans structure the way readers do.

**Specificity**: write what can be verified.

| Instead of | Write |
|---|---|
| "Format code properly" | "Use 2-space indentation" |
| "Test your changes" | "Run `npm test` before committing" |
| "Keep files organized" | "API handlers live in `src/api/handlers/`" |

**Consistency**: **if two rules contradict, Claude may pick one arbitrarily.** Review your CLAUDE.md files, nested ones, and `.claude/rules/` periodically. In monorepos use `claudeMdExcludes`.

### /init

Generates a starting CLAUDE.md by analyzing the codebase. **If one already exists, `/init` suggests improvements rather than overwriting.**

`CLAUDE_CODE_NEW_INIT=1` enables an interactive multi-phase flow: it asks which artifacts to set up (CLAUDE.md, skills, hooks), explores with a subagent, fills gaps with follow-up questions, and **presents a reviewable proposal before writing any files**.

`/init` also reads Cursor rules (`.cursor/rules/`, `.cursorrules`) and Copilot rules (`.github/copilot-instructions.md`). With `CLAUDE_CODE_NEW_INIT=1` it additionally reads `AGENTS.md`, `.devin/rules/`, `.windsurf/rules/` or `.windsurfrules`, and `.clinerules`.

---

## 5. Imports

`@path/to/import` expands and loads the file **at launch, alongside the CLAUDE.md that references it**.

```text
See @README for project overview and @package.json for available npm commands.

# Additional Instructions
- git workflow @docs/git-instructions.md
```

Mechanics:

- Both relative and absolute paths allowed. **Relative paths resolve relative to the file containing the import, not the working directory**
- Recursive imports up to **four hops**
- **Parsing skips Markdown code spans and fenced code blocks.** To mention a path without importing, wrap in backticks: `` `@README` `` stays literal; `@README` imports

### The external-import approval dialog

An import in a **project-level** memory file is **external** when its path resolves outside your working directory. The first time Claude Code encounters external imports in a project, it shows an approval dialog listing the files. **If you decline, the imports stay disabled and the dialog does not appear again.**

**Imports in user-scope files (`~/.claude/CLAUDE.md`, `~/.claude/rules/`) skip the dialog**, because they are files you wrote yourself.

The dialog exists to protect you from files other people commit to a shared project.

### The worktree pattern

A gitignored `CLAUDE.local.md` **only exists in the worktree where you created it**. To share personal instructions across worktrees, import from home instead:

```text
# Individual Preferences
- @~/.claude/my-project-instructions.md
```

### AGENTS.md

**Claude Code reads `CLAUDE.md`, not `AGENTS.md`.** If your repo uses AGENTS.md, import it:

```markdown
@AGENTS.md

## Claude Code

Use plan mode for changes under `src/billing/`.
```

A symlink works when you need no Claude-specific content:

```bash
ln -s AGENTS.md CLAUDE.md
```

**On Windows, symlinks need Administrator or Developer Mode**, so use the import.

---

## 6. Rules: `.claude/rules/`

Modular topic files, optionally scoped to paths.

```text
your-project/
├── .claude/
│   ├── CLAUDE.md
│   └── rules/
│       ├── code-style.md
│       ├── testing.md
│       └── security.md
```

All `.md` files are discovered **recursively**, so subdirectories like `frontend/` work.

**Rules without `paths:` load at launch with the same priority as `.claude/CLAUDE.md`.**

**Rules versus skills**: rules load into context every session or when matching files open. For task-specific instructions that do not need to be in context all the time, **use a skill**, which loads only when invoked or matched.

Project rules are skipped if you exclude `project` from `--setting-sources`. Before v2.1.211, on-demand rules loaded even when `project` was excluded.

### Path-specific rules

```markdown
---
paths:
  - "src/api/**/*.ts"
---

# API Development Rules

- All API endpoints must include input validation
- Use the standard error response format
```

**Path-scoped rules trigger when Claude reads a matching file, not on every tool use.** As of v2.1.198, matching also works through symlinked paths to the project directory.

| Pattern | Matches |
|---|---|
| `**/*.ts` | All TypeScript files, any directory |
| `src/**/*` | Everything under `src/` |
| `*.md` | Markdown in the project root |
| `src/components/*.tsx` | Components in one directory |

### The brace-expansion budget

Brace expansion works and multiplies: `src/*.{ts,tsx}` expands to two patterns, `{a,b}/{c,d}/*.{ts,tsx}` to eight.

**A rule's whole `paths` list shares one budget of 1,000 expanded patterns and 4 MiB.** Patterns without braces do not count against it. **Any pattern exceeding the budget is used unexpanded, and its literal braces match no files.** Before v2.1.217 a `paths` value with many brace groups **stalled or crashed the CLI at startup**.

### The bracket trap

Glob syntax treats `[` as the start of a bracket expression. **A pattern with a `[` that cannot be read as one, such as `photos [2024/**`, is invalid: it matches nothing**, though the rule's other patterns keep working. Escape it: `photos \[2024/**`.

Before v2.1.207, one invalid pattern **made the Read tool fail for every file the rule was evaluated against**.

### Symlinks and user rules

`.claude/rules/` supports symlinks, resolved and loaded normally, with **circular symlinks detected and handled gracefully**:

```bash
ln -s ~/shared-claude-rules .claude/rules/shared
ln -s ~/company-standards/security.md .claude/rules/security.md
```

`~/.claude/rules/` applies to every project. **User-level rules load before project rules**, giving project rules higher priority.

---

## 7. Auto memory

Claude saves notes for itself as it works: build commands, debugging insights, architecture notes, style preferences, workflow habits. **Claude does not save something every session** — it decides based on whether the information would be useful later.

### Enable and disable

On by default. Toggle in `/memory`, which saves `autoMemoryEnabled` to `~/.claude/settings.json`. Per project:

```json
{ "autoMemoryEnabled": false }
```

Environment variable: `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`.

### Storage

`~/.claude/projects/<project>/memory/`, where `<project>` is **derived from the git repository**, so **all worktrees and subdirectories of the same repo share one memory directory**. Outside a repo, the project root is used.

```text
~/.claude/projects/<project>/memory/
├── MEMORY.md          # Index, loaded every session
├── debugging.md       # Loaded on demand
├── api-conventions.md
└── ...
```

Relocate with `autoMemoryDirectory`, readable from **any** settings scope. Must be absolute or start with `~/`. **When set in project or local settings, it is honored only after you accept the workspace trust dialog** — the same gate that governs hooks.

**Auto memory is machine-local.** Not shared across machines or cloud environments.

### The 200-line / 25KB limit

**The first 200 lines of `MEMORY.md`, or the first 25KB, whichever comes first, load at the start of every conversation. Content beyond that is not loaded.**

As of v2.1.210, after Claude writes to `MEMORY.md` Claude Code measures it:

- **Near a limit**: Claude Code reminds Claude to shorten — one line per entry, detail into topic files, merge or drop stale entries
- **Over a limit**: the write succeeds, but Claude Code returns an **error telling Claude to rewrite the index**, because everything past the limit is dropped on the next load

As of v2.1.211, **the check measures only what loads**: YAML frontmatter and block-level HTML comments are stripped first and do not count. Before that, frontmatter alone could trigger the error even when the real content fit.

**This limit applies only to `MEMORY.md`. CLAUDE.md files load in full regardless of length**, though shorter files produce better adherence.

**Topic files are not loaded at startup.** Claude reads them on demand with its standard file tools.

### The `modified` timestamp

As of v2.1.214, when Claude writes a memory file **that begins with YAML frontmatter**, Claude Code records the write time in a `modified` field as an ISO 8601 timestamp. It shows how current a fact is, to you and to Claude when reading it back.

**Any file that has frontmatter gets the field on the next write, including files created on earlier versions. Claude Code never adds frontmatter to a file that has none.**

### Subagent interaction

**The main conversation's auto memory is not loaded into subagents.** The exception is a **fork**, which inherits the parent conversation and system prompt. A subagent's own auto memory, via the `memory:` field, is a separate directory.

"Saved 2 memories" or "Recalled 2 memories" in the interface means Claude is actively reading or writing that directory.

---

## 8. Organizational deployment

### Managed CLAUDE.md

Deploy at the managed policy location via MDM, Group Policy, Ansible, or similar. **Applies to every session on the machine, in every repository, and cannot be excluded by individual settings.**

### The `claudeMd` key

Put managed CLAUDE.md content directly in `managed-settings.json`:

```json
{ "claudeMd": "Always run `make lint` before committing.\nNever push directly to main." }
```

Same precedence as a managed CLAUDE.md file. **Honored only in managed and policy settings**; setting it in user, project, or local settings has no effect.

### Settings versus CLAUDE.md

| Concern | Configure in |
|---|---|
| Block tools, commands, file paths | Managed settings: `permissions.deny` |
| Enforce sandbox isolation | Managed settings: `sandbox.enabled` |
| Environment variables, provider routing | Managed settings: `env` |
| Authentication method and org lock | Managed settings: `forceLoginMethod`, `forceLoginOrgUUID` |
| Code style and quality guidelines | Managed CLAUDE.md |
| Data handling and compliance reminders | Managed CLAUDE.md |
| Behavioral instructions | Managed CLAUDE.md |

**Settings rules are enforced by the client regardless of what Claude decides. CLAUDE.md is not a hard enforcement layer.**

### claudeMdExcludes

For monorepos where ancestor CLAUDE.md files are irrelevant:

```json
{
  "claudeMdExcludes": [
    "**/monorepo/CLAUDE.md",
    "/home/user/monorepo/other-team/.claude/rules/**"
  ]
}
```

**Patterns match against absolute file paths** using glob syntax. Configurable at any settings layer, and **arrays merge across layers**.

**Managed policy CLAUDE.md files cannot be excluded.**

---

## 9. Troubleshooting

### Claude is not following my CLAUDE.md

1. **Run `/context`** and check **Memory files**. If it is missing there, Claude cannot see it
2. Check the file is in a location that gets loaded for your session
3. Make instructions more specific
4. **Look for conflicting instructions** across files; Claude may pick one arbitrarily

Escalation paths:

- **Must run at a specific point** (before every commit, after each edit) → write it as a **hook**
- **Wanted at the system prompt level** → `--append-system-prompt`, though it must be passed every invocation, so it suits scripts better than interactive use

**Debugging tip**: use the `InstructionsLoaded` hook to log exactly which instruction files load, when, and why. This is the tool for debugging path-specific rules and lazy-loaded subdirectory files.

### I do not know what auto memory saved

`/memory` → auto memory folder. Plain markdown you can read, edit, or delete.

### My CLAUDE.md is too large

Path-scoped rules, or trim. **Imports do not help**, since they load at launch.

`/doctor` (v2.1.206+) proposes trims for a checked-in CLAUDE.md: it **cuts content Claude can derive from the codebase** — directory layouts, dependency lists, architecture overviews — and **keeps pitfalls, rationale, and conventions that differ from tool defaults**.

### Instructions seem lost after /compact

**Project-root CLAUDE.md survives**: re-read from disk and re-injected. **Nested CLAUDE.md files are not re-injected automatically**; they reload the next time Claude reads a file in that subdirectory.

If an instruction disappeared, it was either given only in conversation, or lives in a nested file that has not reloaded. Add conversation-only instructions to CLAUDE.md to make them persist.

---

## 10. Gotchas

1. **CLAUDE.md is context, not configuration.** It arrives as a user message after the system prompt. Use hooks for guarantees.
2. **Files concatenate, they do not override.** Closer-to-cwd instructions are read last, and `CLAUDE.local.md` after `CLAUDE.md` at each level.
3. **Subdirectory CLAUDE.md files load lazily**, only when Claude reads a file there.
4. **`/memory` lists locations; `/context` shows what loaded.** Only the second answers "did it load?"
5. **Contradictory instructions get resolved arbitrarily.**
6. **Imports do not save context.** They load at launch like everything else.
7. **Relative import paths resolve against the importing file**, not the working directory.
8. **Backticks prevent an import.** `@README` imports; `` `@README` `` does not.
9. **Declining the external-import dialog is permanent** for that project; it never reappears.
10. **User-scope imports skip the dialog entirely.**
11. **A gitignored `CLAUDE.local.md` exists only in one worktree.** Import from home instead.
12. **Claude Code does not read `AGENTS.md`.** Import or symlink it.
13. **Rules without `paths:` load at launch**, with the same weight as `.claude/CLAUDE.md`.
14. **Brace expansion has a 1,000-pattern / 4 MiB budget.** Over-budget patterns are used unexpanded and match nothing.
15. **An unmatched `[` makes a pattern match nothing.** Escape it.
16. **User rules load before project rules**, so project rules win.
17. **The 200-line / 25KB limit is `MEMORY.md` only.** CLAUDE.md loads in full at any length.
18. **Content past the MEMORY.md limit is silently dropped on load.** The write succeeds; the read truncates.
19. **Frontmatter and HTML comments do not count toward the MEMORY.md limit** as of v2.1.211.
20. **All worktrees of one repo share one auto memory directory**, and it is machine-local.
21. **`autoMemoryDirectory` in project settings needs workspace trust.**
22. **Auto memory does not reach subagents**, except in a fork.
23. **`claudeMd` in managed settings only.** Ignored elsewhere.
24. **Managed CLAUDE.md cannot be excluded** by `claudeMdExcludes`.
25. **Path-scoped rules and nested CLAUDE.md do not survive compaction** until their trigger file is read again.
26. **HTML comments are stripped before injection**, which makes them free maintainer notes.

---

## Reference links

- How Claude remembers your project: https://code.claude.com/docs/en/memory
- Explore the context window: https://code.claude.com/docs/en/context-window
- Monorepos and large repos: https://code.claude.com/docs/en/large-codebases
- Skills: https://code.claude.com/docs/en/skills
- Hooks guide: https://code.claude.com/docs/en/hooks-guide
- InstructionsLoaded hook: https://code.claude.com/docs/en/hooks
- Subagent memory: https://code.claude.com/docs/en/sub-agents
- Settings: https://code.claude.com/docs/en/settings
- Managed settings: https://code.claude.com/docs/en/permissions
- Debug your configuration: https://code.claude.com/docs/en/debug-your-config
- Extend Claude Code (mechanism comparison): https://code.claude.com/docs/en/features-overview
- Full docs index: https://code.claude.com/docs/llms.txt
