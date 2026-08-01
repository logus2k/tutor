# Claude Code: Skills Reference

A study guide to Skills in Claude Code: what they are, where they live, how they load, how to author them well, how to control invocation, how to test them, and how to distribute them.

Verified against code.claude.com/docs/en/skills and the cross-product authoring guidance at platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices.

---

## 1. What a skill is

A skill is a `SKILL.md` file with instructions. Claude adds it to its toolkit and uses it when relevant, or you invoke it directly with `/skill-name`.

The trigger for creating one, straight from the docs: **when you keep pasting the same instructions, checklist, or multi-step procedure into chat, or when a section of CLAUDE.md has grown into a procedure rather than a fact.**

The economics are the point. Unlike CLAUDE.md content, a skill's body loads **only when used**, so long reference material costs almost nothing until you need it.

### Commands have been merged into skills

`.claude/commands/deploy.md` and `.claude/skills/deploy/SKILL.md` both create `/deploy` and work the same way. Existing `commands/` files keep working and support the same frontmatter. Skills add:

- A directory for supporting files
- Frontmatter to control who invokes them
- Automatic model-driven loading

**If a skill and a command share a name, the skill wins.** New workflows should be skills.

Claude Code skills follow the [Agent Skills](https://agentskills.io) open standard, which works across multiple AI tools. Claude Code extends it with invocation control, subagent execution, and dynamic context injection.

---

## 2. Choosing skills over the alternatives

| Mechanism | Loads | Runs in | Best for |
|---|---|---|---|
| **CLAUDE.md** | Every session, always | Main context | Stable facts, conventions, commands |
| **Rules** (`.claude/rules/*.md`) | Session start, or when a `paths:` glob matches | Main context | Topic-scoped guidance |
| **Skill** | Description always; body when invoked | Main context (or a fork) | Procedures, checklists, reusable prompts, reference material |
| **Subagent** | On delegation | Isolated context window | Work whose verbose middle should not reach you |
| **Hook** | On a lifecycle event | A shell process | Deterministic enforcement |
| **MCP server** | At connection | External process | Access to systems Claude cannot otherwise reach |

The layering rule worth memorising: **skill teaches the how, hook enforces the rule, subagent isolates the work.**

Corollary from the docs: if a skill "stops influencing behavior," the content is usually still in context and the model is simply choosing other approaches. Strengthen the description and instructions, or move the requirement to a hook where it is enforced deterministically.

---

## 3. Anatomy

Each skill is a **directory** with `SKILL.md` as the entrypoint:

```text
my-skill/
├── SKILL.md           # Main instructions (required)
├── reference.md       # Detailed docs, loaded when needed
├── examples.md        # Usage examples, loaded when needed
└── scripts/
    └── helper.py      # Executed, not loaded
```

`SKILL.md` has two parts: YAML frontmatter between `---` markers, and markdown content.

```yaml
---
description: Summarizes uncommitted changes and flags anything risky. Use when the user asks what changed, wants a commit message, or asks to review their diff.
---

## Current changes

!`git diff HEAD`

## Instructions

Summarize the changes above in two or three bullet points, then list any risks
you notice such as missing error handling, hardcoded values, or tests that need
updating. If the diff is empty, say there are no uncommitted changes.
```

All frontmatter fields are optional. Only `description` is recommended, because that is what Claude matches against.

---

## 4. Where skills live

| Location | Path | Applies to |
|---|---|---|
| Enterprise | Managed settings directory | All users in the organization |
| Personal | `~/.claude/skills/<name>/SKILL.md` | All your projects |
| Project | `.claude/skills/<name>/SKILL.md` | This project only |
| Plugin | `<plugin>/skills/<name>/SKILL.md` | Where the plugin is enabled |

**Precedence: enterprise > personal > project.** Note this is the reverse of the settings hierarchy, where project overrides user, so it is easy to get backwards.

A skill at any of these levels also **overrides a bundled skill of the same name**. A `code-review` skill in your project replaces the bundled `/code-review`.

Plugin skills use a `plugin-name:skill-name` namespace, so they cannot conflict.

### Discovery rules

Three separate mechanisms:

1. **Parent directories**: project skills load from `.claude/skills/` in your starting directory and every parent up to the repository root. Starting in a subdirectory still picks up root skills.
2. **Nested directories (lazy)**: skills in `.claude/skills/` **below** your starting directory are **not loaded at startup.** They load the first time Claude reads or edits a file inside that subdirectory, then stay available. Until then they do not appear in autocomplete and cannot be invoked by name.
3. **Additional directories**: `--add-dir` and `/add-dir` load `.claude/skills/` from the added directory. **This is an exception to the usual rule** that `--add-dir` grants file access rather than configuration discovery. The exception applies only to those two; the `permissions.additionalDirectories` setting grants file access only and does **not** load skills. Other `.claude/` configuration such as commands and output styles is not loaded from added directories either.

### Nested name collisions (monorepos)

If a nested skill shares a name with another, **both stay available**:

- The nested one gets a directory-qualified name: `apps/web:deploy`
- Its description states which directory it applies to
- Claude picks the variant matching the files it is working on

Typing `/deploy` runs the project-root skill. `/apps/web:deploy` runs the nested variant explicitly.

Subtle and useful (v2.1.203+): when the **unqualified** name is invoked, Claude Code appends a list of the directory-qualified variants to the loaded content with an instruction to also invoke any variant whose directory holds the files in play. A nested skill therefore still applies even when only the plain name is typed.

### Symlinks

A `<skill-name>` entry in the enterprise, personal, or project locations can be a symlink to a directory elsewhere. Claude Code follows it and reads `SKILL.md` from the target, and if the same target is reachable from multiple locations it loads the skill once. Plugin skills handle symlinks differently.

### Live change detection

Claude Code watches skill directories. Adding, editing, or removing a skill under `~/.claude/skills/`, the project `.claude/skills/`, or a `.claude/skills/` inside an `--add-dir` directory is picked up **within the session, no restart**.

Two exceptions:

- Creating a **top-level skills directory that did not exist** at session start requires a restart
- Live detection covers `SKILL.md` text only. For a skill folder that is also a plugin, changes to `hooks/`, `.mcp.json`, `agents/`, and `output-styles/` need `/reload-plugins`

### Cowork and cloud sessions

**Cowork sessions and cloud sessions (including routines) do not read `~/.claude/skills/` on your machine.**

- Cowork loads the skills enabled for your claude.ai account, synced at session start. Manage them from **Customize** in the Desktop app sidebar or claude.ai skills settings
- Cloud sessions additionally load project skills committed to the cloned repository's `.claude/skills/`
- A personal-only skill invoked by a routine reports "not found", because each routine run is a fresh remote session
- **Desktop scheduled tasks are different**: they run locally and load skills like any other local session

To make a personal skill available: enable it for your claude.ai account (Cowork and cloud), or commit it to the repo / ship it in a plugin declared in the repository's `.claude/settings.json` (cloud only; plugins enabled only in user settings do not transfer).

### Skill folders as plugins

Add `.claude-plugin/plugin.json` to a skill folder and it loads as a plugin named `<name>@skills-dir`, letting it bundle agents, hooks, and MCP servers. In a project's `.claude/skills/`, this requires accepting the workspace trust dialog first.

---

## 5. How a skill gets its command name

This trips people up, because `name` does not always control the command.

| Skill location | Command name comes from | Example |
|---|---|---|
| `~/.claude/skills/` or `.claude/skills/` directory | **Directory name** | `.claude/skills/deploy-staging/SKILL.md` → `/deploy-staging` |
| Nested `.claude/skills/` with a clashing name | Subdirectory path + skill directory name | `apps/web/.claude/skills/deploy/` → `/apps/web:deploy` |
| File under `.claude/commands/` | File name without extension | `.claude/commands/deploy.md` → `/deploy` |
| Plugin `skills/` subdirectory | Frontmatter `name` or directory name, namespaced | `my-plugin/skills/review/` → `/my-plugin:review`, or `/my-plugin:fancy` with `name: fancy` |
| Plugin root `SKILL.md` | Frontmatter `name`, falling back to the plugin directory name | `my-plugin/SKILL.md` with `name: review` → `/my-plugin:review` |

**In personal and project skills, `name` sets only the display label in listings.** The command still comes from the directory.

**In plugin skills, `name` replaces the last segment** and the plugin prefix stays. The bare `/fancy` also works unless another command claims it. Before v2.1.216 the frontmatter name replaced the whole command, so the prefix disappeared from the menu.

Cross-product naming rules from the Agent Skills standard: `name` is max 64 characters, lowercase letters, numbers, and hyphens only, no XML tags, and cannot contain the reserved words "anthropic" or "claude".

---

## 6. Frontmatter reference

Boolean fields accept `yes`, `no`, `on`, `off`, `1`, `0` in any case as well as `true` / `false` (v2.1.218+).

| Field | Purpose |
|---|---|
| `name` | Display label in listings. Defaults to the directory name. See section 5 for how it interacts with the command name |
| `description` | What the skill does **and when to use it**. Falls back to the first paragraph of content. Combined `description` + `when_to_use` is truncated at **1,536 characters** in the listing, so put the key use case first |
| `when_to_use` | Extra trigger phrases or example requests. Appended to `description` and counts toward the same cap |
| `argument-hint` | Autocomplete hint, e.g. `[issue-number]` or `[filename] [format]` |
| `arguments` | Named positional arguments for `$name` substitution. Space-separated string or YAML list; names map to positions in order |
| `disable-model-invocation` | `true` stops Claude auto-loading it. Also blocks preloading into subagents, and (v2.1.196+) blocks a scheduled task firing with the skill as prompt |
| `user-invocable` | `false` hides it from the `/` menu |
| `allowed-tools` | Tools usable **without permission prompts during the invoking turn**. Grant clears on your next message |
| `disallowed-tools` | Tools **removed from the pool** while the skill is active. Clears on your next message. Cannot remove `EndConversation` while any other tool remains |
| `model` | Model while the skill is active, for the rest of the turn only. Not saved to settings. `inherit` keeps the current one. A value excluded by `availableModels` is ignored |
| `effort` | `low`, `medium`, `high`, `xhigh`, `max` while the skill is active |
| `context` | `fork` runs the skill in a forked subagent |
| `agent` | Which subagent type, when `context: fork` is set. Defaults to `general-purpose` |
| `background` | Only with `context: fork`. `false` waits for the result in the invoking turn. Default `true` (v2.1.218+) |
| `hooks` | Hooks scoped to this skill's lifecycle |
| `paths` | Glob patterns limiting **automatic** activation to work on matching files. Same format as path-specific rules |
| `shell` | `bash` (default) or `powershell` for `` !`command` `` and ` ```! ` blocks |

Full-featured example:

```yaml
---
name: deploy
description: Deploy the application to production
when_to_use: Use when the user says ship it, cut a release, or push to prod
argument-hint: "[environment]"
arguments: [environment]
disable-model-invocation: true
allowed-tools: Bash(git *), Bash(./scripts/deploy.sh *)
model: opus
effort: high
context: fork
agent: general-purpose
background: false
paths:
  - "deploy/**"
---
```

---

## 7. String substitutions

| Variable | Expands to |
|---|---|
| `$ARGUMENTS` | All arguments as typed. **If absent from the content, arguments are appended as `ARGUMENTS: <value>`** |
| `$ARGUMENTS[N]` | Argument by 0-based index |
| `$N` | Shorthand for `$ARGUMENTS[N]`, so `$0` is the first |
| `$name` | Named argument declared in `arguments`, mapped by position |
| `${CLAUDE_SESSION_ID}` | Current session ID |
| `${CLAUDE_EFFORT}` | `low`, `medium`, `high`, `xhigh`, or `max`. Ultracode reports as `xhigh` |
| `${CLAUDE_SKILL_DIR}` | Directory containing `SKILL.md`. For plugin skills, the skill's subdirectory, not the plugin root |
| `${CLAUDE_PROJECT_DIR}` | Project root, same value hooks and MCP servers receive |

Quoting and edge cases:

- Indexed arguments use **shell-style quoting**. `/my-skill "hello world" second` gives `$0` = `hello world`, `$1` = `second`. `$ARGUMENTS` is always the full string as typed
- An indexed placeholder with no matching argument (`$2` when one was passed) **stays in the content unchanged**. A named placeholder with no argument expands to an **empty string**
- Escape a literal `$` before a digit, `ARGUMENTS`, or a declared name with a backslash: `\$1.00`. Only a single backslash directly before the token escapes it; `\\$1` leaves both backslashes and still expands

### The bundled-script pattern

`${CLAUDE_SKILL_DIR}` and `${CLAUDE_PROJECT_DIR}` substitute in **two** places: the markdown content **and** Bash rules in `allowed-tools`. Using the same variable in both lets a skill run its own bundled script with no permission prompt:

```yaml
---
name: render-chart
description: Render a chart from a CSV file
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/render.sh *)
---

Run `${CLAUDE_SKILL_DIR}/scripts/render.sh <csv-file>` to render the chart.
```

The `allowed-tools` substitution requires v2.1.129+; on earlier versions the rule stays a literal string and never matches, so the command still prompts. `${CLAUDE_PROJECT_DIR}` substitution requires v2.1.196+.

---

## 8. Dynamic context injection

`` !`<command>` `` runs a shell command **before the skill content reaches Claude**. The output replaces the placeholder, so Claude receives data, not a command to run.

```yaml
---
name: pr-summary
description: Summarize changes in a pull request
context: fork
agent: Explore
allowed-tools: Bash(gh *)
---

## Pull request context
- PR diff: !`gh pr diff`
- PR comments: !`gh pr view --comments`
- Changed files: !`gh pr diff --name-only`

## Your task
Summarize this pull request...
```

Order of operations: each command executes → output replaces the placeholder → Claude receives the fully rendered prompt. **This is preprocessing, not something Claude executes.**

Rules that bite:

- **Substitution runs once over the original file.** Command output is inserted as plain text and **not re-scanned**, so a command cannot emit a placeholder for a later pass
- The inline form is recognized **only when `!` is at line start or immediately after whitespace**. `KEY=!`cmd`` is left as literal text and does not run
- For multi-line commands use a fenced block opened with ` ```! `:

````markdown
## Environment
```!
node --version
npm --version
git status --short
```
````

Policy control: `"disableSkillShellExecution": true` replaces each command with `[shell command execution disabled by policy]` for skills and commands from user, project, plugin, and additional-directory sources. **Bundled and managed skills are unaffected.** Most useful in managed settings.

Tip from the docs: include `ultrathink` anywhere in the skill content to request deeper reasoning when it runs.

---

## 9. Controlling who invokes

| Frontmatter | You can invoke | Claude can invoke | When loaded into context |
|---|---|---|---|
| (default) | Yes | Yes | Description always in context; full body loads on invocation |
| `disable-model-invocation: true` | Yes | No | **Description not in context**; body loads when you invoke |
| `user-invocable: false` | No | Yes | Description always in context; body loads on invocation |

Setting both makes the skill unreachable.

Guidance:

- **`disable-model-invocation: true`** for anything with side effects or where timing matters: `/commit`, `/deploy`, `/send-slack-message`. You do not want Claude deciding to deploy because the code looks ready. It also **saves context**, since the description is not loaded
- **`user-invocable: false`** for background knowledge that is not an action. A `legacy-system-context` skill explains an old system; `/legacy-system-context` is not a meaningful thing for a user to type

Important distinction: **`user-invocable` controls menu visibility only, not Skill tool access.** Use `disable-model-invocation: true` to block programmatic invocation.

`paths:` is a third, softer control: with it set, Claude auto-loads the skill only when working with matching files. It limits automatic activation, not manual invocation.

---

## 10. Content lifecycle and context economics

This section explains most surprising skill behavior.

**When invoked, the rendered `SKILL.md` enters the conversation as a single message and stays for the rest of the session.** Claude Code does **not** re-read the file on later turns.

Consequences to internalize:

1. **Write standing instructions, not one-time steps**, for anything that should apply throughout a task.
2. **Every line is a recurring token cost.** State what to do rather than narrating how or why.
3. **Permissions do not persist with content.** An `allowed-tools` grant clears on your next message even though the instructions remain.

### Re-invocation

When Claude re-invokes a skill whose **rendered content is identical** to the copy already in context, Claude Code adds a short "already loaded" note rather than a second copy. When the content **differs** (arguments changed, or a dynamic-context command produced new output), the full content is appended again. Before v2.1.202, every re-invocation appended another full copy.

### Compaction

Auto-compaction carries invoked skills forward within a token budget:

- The **most recent invocation of each skill** is re-attached after the summary
- **First 5,000 tokens of each** are kept
- Re-attached skills share a combined budget of **25,000 tokens**
- The budget fills starting from the **most recently invoked**, so older skills can be dropped entirely if you invoked many

If a large skill seems to lose influence after compaction, re-invoke it.

### The skill listing budget

Claude Code loads a listing of skill **names and descriptions** so Claude knows what is available. The listing **always contains every name**, but descriptions get shortened to fit a character budget.

- Budget scales at **1% of the model's context window**
- On overflow, descriptions are dropped **starting with the skills you invoke least**, so frequently used skills keep their full text
- `/doctor` estimates the listing's context cost and names the biggest contributors
- A debug-log warning is written on overflow (`--debug`)
- The Skills row in `/context` reports the size **after** the budget is applied (correct since v2.1.196)

Levers:

| Lever | Effect |
|---|---|
| `skillListingBudgetFraction` | Raise the budget, e.g. `0.02` for 2% |
| `SLASH_COMMAND_TOOL_CHAR_BUDGET` | Fixed character count instead of a fraction |
| `skillOverrides` set to `"name-only"` | Frees budget by listing a skill without a description |
| `skillListingMaxDescChars` | Changes the per-entry 1,536-character cap |
| Trimming `description` / `when_to_use` | Put the key use case first |

---

## 11. Tool pre-approval and restriction

### allowed-tools

Grants permission for listed tools **during the invoking turn**, so Claude uses them without prompting. It **does not restrict** anything: every other tool remains callable under your normal permission settings.

```yaml
---
name: commit
description: Stage and commit the current changes
disable-model-invocation: true
allowed-tools: Bash(git add *) Bash(git commit *) Bash(git status *)
---
```

Accepts a space- or comma-separated string, or a YAML list.

**Security note**: for skills checked into a project's `.claude/skills/`, `allowed-tools` takes effect only after you accept the workspace trust dialog, the same as permission rules in `.claude/settings.json`. **Review project skills before trusting a repository, since a skill can grant itself broad tool access.**

To pre-approve for the whole session rather than one turn, use permission settings instead.

### disallowed-tools

Removes tools from the pool while the skill is active. Use for autonomous skills that should never call something, such as removing `AskUserQuestion` from a background loop. Clears on your next message. Cannot remove `EndConversation` while any other tool remains. To block across all skills and prompts, use deny rules.

---

## 12. Arguments and stacking

```yaml
---
name: migrate-component
description: Migrate a component from one framework to another
---

Migrate the $0 component from $1 to $2.
Preserve all existing behavior and tests.
```

`/migrate-component SearchBar React Vue` fills in the three positions.

### Stacking (v2.1.199+)

You can stack several skills at the start of one message. `/write-tests /fix-issue 123` loads both and passes `123` as `$ARGUMENTS` to each.

Rules:

- Claude Code expands the first skill plus up to **five more**
- **Expansion stops at the first token that is not an inline user-invocable skill.** A forked skill such as `/code-review`, or one whose arguments may themselves start with a slash such as `/loop`, ends the run there
- That stopping token and everything after it become the argument text for every expanded skill
- `/code-review` runs as a forked subagent from v2.1.218; on earlier versions it ran inline and stacked

---

## 13. Running a skill in a subagent

`context: fork` runs the skill in isolation. **The skill content becomes the prompt that drives the subagent.** It has no access to your conversation history.

```yaml
---
name: deep-research
description: Research a topic thoroughly
context: fork
agent: Explore
---

Research $ARGUMENTS thoroughly:

1. Find relevant files using Glob and Grep
2. Read and analyze the code
3. Summarize findings with specific file references
```

Execution: a new isolated context is created, the subagent receives the skill content as its prompt, `agent` determines model / tools / permissions, and the subagent returns a summary to your main conversation.

### Background vs blocking

The fork **runs in the background by default** (v2.1.218+). Set `background: false` to wait in the invoking turn.

Claude Code waits regardless of the setting when:

- In non-interactive mode (`-p` or the Agent SDK)
- `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`
- You invoke a forked skill while an earlier invocation of the same skill is still running
- A scheduled task fires with the skill as its prompt

Two consequences of backgrounding worth flagging:

1. **A backgrounded fork runs with the narrower background-subagent tool set.** The skill's subagent is a regular agent type, so the exemption for conversation forks does not cover it. If your steps need a tool outside that set, use `background: false`.
2. **A backgrounded fork's edits fall outside your session's checkpoints**, so `/rewind` does not undo them. Use git.

### The critical caveat

**`context: fork` only makes sense for skills with explicit instructions.** A skill containing guidelines like "use these API conventions" with no task gives the subagent knowledge but no actionable prompt, and it returns without meaningful output.

### Skills and subagents in both directions

| Approach | System prompt | Task | Also loads |
|---|---|---|---|
| Skill with `context: fork` | From the agent type | SKILL.md content | CLAUDE.md, except when the agent is Explore or Plan |
| Subagent with `skills:` field | Subagent's markdown body | Claude's delegation message | Preloaded skills + CLAUDE.md |

So `agent: Explore` on a forked skill means the subagent sees only the SKILL.md content and Explore's own system prompt, since Explore and Plan skip CLAUDE.md and git status.

---

## 14. Supporting files and progressive disclosure

```text
my-skill/
├── SKILL.md      # overview and navigation (required)
├── reference.md  # detailed API docs, loaded when needed
├── examples.md   # usage examples, loaded when needed
└── scripts/
    └── helper.py # executed, not loaded
```

Reference them from `SKILL.md` so Claude knows what each contains and when to load it:

```markdown
## Additional resources

- For complete API details, see [reference.md](reference.md)
- For usage examples, see [examples.md](examples.md)
```

**Keep `SKILL.md` under 500 lines.** Move detailed reference material to separate files.

### Three organizing patterns

**Pattern 1: high-level guide with references.** Quick start inline, everything else linked.

**Pattern 2: domain-specific organization.** When a skill spans domains, split by domain so Claude reads only what the question needs:

```text
bigquery-skill/
├── SKILL.md              # overview and navigation
└── reference/
    ├── finance.md
    ├── sales.md
    ├── product.md
    └── marketing.md
```

A question about sales metrics reads only `sales.md`; the others cost zero tokens.

**Pattern 3: conditional details.** Basic content inline, advanced linked (`For tracked changes, see REDLINING.md`).

### Two structural rules

1. **Keep references one level deep from SKILL.md.** Claude may only *partially* read files reached through nested references, using something like `head -100` to preview rather than reading fully. Every reference file should link directly from `SKILL.md`.
2. **For reference files over 100 lines, add a table of contents at the top**, so Claude sees the full scope even when previewing with a partial read.

---

## 15. Restricting Claude's skill access

Three mechanisms, in increasing granularity.

**Deny the Skill tool entirely:**

```text
# in deny rules
Skill
```

**Allow or deny specific skills** with permission rules. Syntax: `Skill(name)` for exact match, `Skill(name *)` for prefix match with arguments.

```text
Skill(commit)
Skill(review-pr *)
Skill(deploy *)
```

**Hide individual skills** with `disable-model-invocation: true`, which removes them from Claude's context entirely.

A few built-in commands are reachable through the Skill tool (`/init`, `/review`, `/security-review`); others such as `/compact` are not.

### skillOverrides

Controls visibility from settings instead of frontmatter, for skills whose `SKILL.md` you do not want to edit (a shared project repo, for example). The `/skills` menu writes it: highlight a skill, press `Space` to cycle states, `Enter` to save to `.claude/settings.local.json`.

| Value | Listed to Claude | In `/` menu |
|---|---|---|
| `"on"` | Name and description | Yes |
| `"name-only"` | Name only | Yes |
| `"user-invocable-only"` | Hidden | Yes |
| `"off"` | Hidden | Hidden |

```json
{
  "skillOverrides": {
    "legacy-context": "name-only",
    "deploy": "off"
  }
}
```

A skill absent from `skillOverrides` is treated as `"on"`. As of v2.1.199, `"off"` also hides the skill from command lists advertised to Remote Control and Agent SDK callers, and invoking it by full name returns the `skillOverrides` error.

**Plugin skills are not affected by `skillOverrides`.** Manage those through `/plugin`.

---

## 16. Bundled skills

Claude Code ships with prompt-based skills: `/doctor`, `/code-review`, `/batch`, `/debug`, `/loop`, `/claude-api`, and others. They give Claude detailed instructions and let it orchestrate with its tools, unlike most built-in commands which execute fixed logic.

Claude invokes some automatically; **`/verify` and `/code-review` run only when you invoke them** (v2.1.215+), so longer checks do not spend time and tokens unasked.

Turn them off with `disableBundledSkills`, which disables everything except `/doctor`. To hide `/doctor` too, use `DISABLE_DOCTOR_COMMAND` or a `skillOverrides` entry of `"doctor": "off"`.

### The run / verify trio

| Skill | Purpose |
|---|---|
| `/run` | Launch and drive your app to see a change working |
| `/verify` | Build and run your app to confirm a change does what it should, without falling back to tests or type checks |
| `/run-skill-generator` | Teach `/run` and `/verify` how to build and launch your project |

All three need v2.1.145+.

`/run` and `/verify` work without setup by inferring the launch from your project type and your README, `package.json`, or `Makefile`. That inference degrades for projects needing a database, an env file, a graphical session, or a multi-step build.

`/run-skill-generator` records the recipe instead: it gets your app running from a clean environment, captures the install commands, env vars, and launch script, and commits a per-project skill at `.claude/skills/run-<name>/`. Run it once per project, and again when the build changes.

`/verify` can also self-record: without a recipe it writes what worked to `.claude/skills/verify/SKILL.md` at the repo root, or in the touched package directory in a monorepo (v2.1.200+). At the repo root, the recorded skill **replaces the bundled `/verify`**. Claude edits it only when a run was steered wrong, so it is safe to commit without per-session diffs.

---

## 17. Authoring best practices

### Concise is key

The context window is a public good. Your skill shares it with the system prompt, conversation history, other skills' metadata, and the actual request.

**Default assumption: Claude is already very smart.** Challenge each piece of information:

- Does Claude really need this explanation?
- Can I assume Claude knows this?
- Does this paragraph justify its token cost?

Good, roughly 50 tokens:

````markdown
## Extract PDF text

Use pdfplumber for text extraction:

```python
import pdfplumber

with pdfplumber.open("file.pdf") as pdf:
    text = pdf.pages[0].extract_text()
```
````

Bad, roughly 150 tokens, explaining what a PDF is and why pdfplumber was chosen.

### Set appropriate degrees of freedom

Match specificity to the task's fragility.

| Freedom | Form | Use when |
|---|---|---|
| **High** | Text instructions | Multiple approaches valid, decisions depend on context |
| **Medium** | Pseudocode or parameterized scripts | A preferred pattern exists, some variation is fine |
| **Low** | Specific scripts, few or no parameters | Fragile operations, consistency critical, exact sequence required |

The analogy from the docs: a **narrow bridge with cliffs** needs exact instructions and guardrails (database migrations); an **open field with no hazards** needs general direction and trust (code reviews).

Low-freedom example:

````markdown
## Database migration

Run exactly this script:

```bash
python scripts/migrate.py --verify --backup
```

Do not modify the command or add additional flags.
````

### Writing descriptions

The description drives discovery and is the single highest-leverage field.

**Always write in third person.** It is injected into the system prompt, and inconsistent point of view causes discovery problems.

- Good: "Processes Excel files and generates reports"
- Avoid: "I can help you process Excel files"
- Avoid: "You can use this to process Excel files"

**Include both what it does and when to use it**, with terms users actually say:

```yaml
description: Extract text and tables from PDF files, fill forms, merge documents. Use when working with PDF files or when the user mentions PDFs, forms, or document extraction.
```

```yaml
description: Generate descriptive commit messages by analyzing git diffs. Use when the user asks for help writing commit messages or reviewing staged changes.
```

Avoid: "Helps with documents", "Processes data", "Does stuff with files".

### Naming

Consider **gerund form** (verb + -ing): `processing-pdfs`, `analyzing-spreadsheets`, `managing-databases`, `testing-code`, `writing-documentation`.

Acceptable alternatives: noun phrases (`pdf-processing`), action-oriented (`process-pdfs`).

Avoid: vague (`helper`, `utils`, `tools`), overly generic (`documents`, `data`, `files`), reserved words, and inconsistent patterns within one collection.

### Workflows and feedback loops

Break complex operations into sequential steps, and for particularly complex ones **provide a checklist Claude can copy into its response and check off**:

```markdown
## Research synthesis workflow

Copy this checklist and track your progress:

- [ ] Step 1: Read all source documents
- [ ] Step 2: Identify key themes
- [ ] Step 3: Cross-reference claims
- [ ] Step 4: Create structured summary
- [ ] Step 5: Verify citations
```

The feedback-loop pattern, **run validator → fix errors → repeat**, is called out as greatly improving output quality:

```markdown
## Document editing process

1. Make your edits to `word/document.xml`
2. Validate immediately: `python ooxml/scripts/validate.py unpacked_dir/`
3. If validation fails: review the error, fix the XML, run validation again
4. Only proceed when validation passes
5. Rebuild: `python ooxml/scripts/pack.py unpacked_dir/ output.docx`
```

The "validator" does not need to be a script. A `STYLE_GUIDE.md` plus a checklist works the same way.

### Content guidelines

**Avoid time-sensitive information.** Instead of "before August 2025, use the old API", use a current section plus a collapsed "old patterns" section:

```markdown
## Current method

Use the v2 API endpoint: `api.example.com/v2/messages`

## Old patterns

<details>
<summary>Legacy v1 API (deprecated 2025-08)</summary>
The v1 API used: `api.example.com/v1/messages`. No longer supported.
</details>
```

**Use consistent terminology.** Pick one term and keep it: always "API endpoint", not a mix of "URL", "API route", "path". Consistency helps Claude parse instructions.

### Common patterns

- **Template pattern**: provide the output format, matching strictness to need ("ALWAYS use this exact template" versus "a sensible default, use your judgment")
- **Examples pattern**: for output quality that depends on style, give input/output pairs. Examples convey desired style better than descriptions
- **Conditional workflow pattern**: guide through decision points ("Creating new content? → Creation workflow. Editing? → Editing workflow"). Push large branches into separate files

### Anti-patterns

- **Windows-style paths.** Always forward slashes: `scripts/helper.py`, never `scripts\helper.py`
- **Too many options.** Not "you can use pypdf, or pdfplumber, or PyMuPDF, or...". Give a default with an escape hatch: "Use pdfplumber. For scanned PDFs requiring OCR, use pdf2image with pytesseract instead"
- **Deeply nested references** (section 14)
- **Assuming tools are installed.** State the install command explicitly

### Skills with executable code

- **Solve, do not defer.** Handle error conditions in your scripts rather than letting them fail for Claude to figure out
- **No voodoo constants.** Document why `REQUEST_TIMEOUT = 30`, not `TIMEOUT = 47`. If you do not know the right value, how will Claude?
- **Prefer pre-made scripts over generated code.** More reliable, saves tokens (the code never enters context), saves time, ensures consistency
- **Make execution intent explicit**: "Run `analyze_form.py` to extract fields" (execute) versus "See `analyze_form.py` for the extraction algorithm" (read as reference). Execution is usually preferred
- **Create verifiable intermediate outputs.** The plan-validate-execute pattern: analyze → create a plan file → validate the plan with a script → execute → verify. Use for batch operations, destructive changes, complex validation, and high-stakes work. Make validation scripts verbose with specific errors ("Field 'signature_date' not found. Available fields: ...")
- **Use visual analysis** where inputs can be rendered as images; Claude's vision helps with layouts
- **Name files descriptively**: `form_validation_rules.md`, not `doc2.md`

### MCP tool references inside a skill

Always use fully qualified names, `ServerName:tool_name`:

```markdown
Use the BigQuery:bigquery_schema tool to retrieve table schemas.
Use the GitHub:create_issue tool to create issues.
```

Without the server prefix, Claude may fail to locate the tool when multiple servers are connected.

### Test with every model you plan to use

Skills are additions to models, so effectiveness depends on the model. What works for Opus may need more detail for Haiku, and what Haiku needs may be over-explanation for Opus.

---

## 18. Evaluating and iterating

**Seeing a skill trigger tells you Claude found it, not that it did what you intended.** Measure two things separately: whether Claude invokes it on the prompts it should, and whether the output matches expectations when it does.

The check for both is a **baseline comparison**: collect realistic prompts, run each in a fresh session with the skill available and again with it disabled via `skillOverrides`, and compare. **A fresh session matters** because leftover context from authoring will mask gaps in the written instructions.

### Evaluation-driven development

Build evaluations **before** writing extensive documentation:

1. **Identify gaps**: run Claude on representative tasks without a skill; document failures
2. **Create evaluations**: build three scenarios testing those gaps
3. **Establish baseline**: measure performance without the skill
4. **Write minimal instructions**: just enough to address the gaps
5. **Iterate**: execute, compare against baseline, refine

Evaluation structure:

```json
{
  "skills": ["pdf-processing"],
  "query": "Extract all text from this PDF file and save it to output.txt",
  "files": ["test-files/document.pdf"],
  "expected_behavior": [
    "Successfully reads the PDF file using an appropriate library or CLI tool",
    "Extracts text from all pages without missing any",
    "Saves the extracted text to output.txt in a clear, readable format"
  ]
}
```

### The skill-creator plugin

```text
/plugin install skill-creator@claude-plugins-official
/reload-plugins
```

Then: `evaluate my summarize-changes skill with skill-creator`.

What it automates:

| Capability | Output |
|---|---|
| Test cases | Prompts, input files, expected behavior in `evals/evals.json` inside the skill directory |
| Isolated runs | One subagent per test case, clean context each, with token count and duration |
| Grading | Pass or fail with evidence in `grading.json` |
| Benchmark | Pass rate, time, tokens for with-skill versus without-skill in `benchmark.json` |
| Version comparison | Blind A/B between two versions of the skill |
| Description tuning | Generates should-trigger and should-not-trigger prompts, measures hit rate, proposes description edits |
| Review viewer | HTML report where you record qualitative feedback the next iteration reads |

### The Claude A / Claude B loop

The most effective development process uses Claude itself. **Claude A** helps you design and refine the skill; **Claude B** is a fresh instance that tests it in real tasks.

Creating a skill:

1. Complete a task normally with Claude A, noticing what context you repeatedly provide
2. Identify the reusable pattern
3. Ask Claude A to create a skill capturing it. Claude understands the format natively; no special prompt needed
4. **Review for conciseness**: "Remove the explanation about what win rate means, Claude already knows that"
5. Improve information architecture: "Put the table schema in a separate reference file"
6. Test with Claude B on similar tasks
7. Iterate with specifics: "When Claude used this, it forgot to filter by date for Q4"

Iterating: use it in real workflows, observe Claude B's behavior, return to Claude A with the specific observation, apply and re-test.

### Observe how Claude navigates

Watch for these signals rather than guessing:

- **Unexpected exploration paths**: your structure is less intuitive than you thought
- **Missed connections**: Claude does not follow references, so links need to be more explicit
- **Overreliance on one file**: that content probably belongs in `SKILL.md`
- **Ignored content**: the file may be unnecessary or poorly signaled

---

## 19. Distribution

| Scope | How |
|---|---|
| **Project** | Commit `.claude/skills/` to version control |
| **Plugin** | A `skills/` directory in your plugin |
| **Managed** | Deploy organization-wide through managed settings |
| **Cowork / cloud** | Enable for your claude.ai account, or commit to the repo |
| **Claude Tag** | Project skills committed to a repo also load when that repo is used in a Tag channel |

---

## 20. Settings, environment, and commands

### Settings keys

| Key | Effect |
|---|---|
| `disableBundledSkills` | Disable all bundled skills except `/doctor` |
| `disableSkillShellExecution` | Replace `` !`cmd` `` with a policy notice for non-bundled, non-managed skills |
| `skillOverrides` | Per-skill visibility: `on`, `name-only`, `user-invocable-only`, `off` |
| `skillListingBudgetFraction` | Fraction of the context window for the skill listing, default 0.01 |
| `skillListingMaxDescChars` | Per-entry description cap, default 1,536 |
| `permissions.deny: ["Skill"]` | Disable all model skill invocation |
| `permissions.allow/deny: ["Skill(name)"]` | Per-skill control |

### Environment variables

| Variable | Effect |
|---|---|
| `SLASH_COMMAND_TOOL_CHAR_BUDGET` | Fixed character budget for the skill listing |
| `DISABLE_DOCTOR_COMMAND` | Hide `/doctor` |
| `CLAUDE_CODE_DISABLE_BUNDLED_SKILLS` | Equivalent to `disableBundledSkills` |
| `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS` | Forces forked skills to block the invoking turn |
| `CLAUDE_CODE_USE_POWERSHELL_TOOL` | Enables the PowerShell tool, required for `shell: powershell` off Windows |

### Commands

| Command | Purpose |
|---|---|
| `/skills` | Skill menu; `Space` cycles `skillOverrides` states, `Enter` saves |
| `/context` | Skills row shows the listing size after budgeting |
| `/doctor` | Estimates listing context cost and names the biggest contributors |
| `/plugin` | Manage plugin skills, which `skillOverrides` does not touch |
| `/reload-plugins` | Required after changing a skill-folder plugin's hooks, MCP, agents, or output styles |

---

## 21. Troubleshooting

**Skill not triggering:**

1. Does the description include keywords users would naturally say?
2. Does it appear when you ask "What skills are available?"
3. Rephrase your request to match the description more closely
4. Invoke directly with `/skill-name`
5. Check `disable-model-invocation` is not set, and `user-invocable` is not `false`
6. Check `paths:` is not scoping it away from the files in play
7. Check `/context` for skill budget warnings
8. For nested skills, confirm Claude has touched a file in that subdirectory

**Malformed frontmatter**: Claude Code loads the body with **empty metadata**, so `/skill-name` still works but Claude has no description to match against. Run with `--debug` to see the parse error. This is a quiet failure mode worth knowing.

**Skill triggers too often**: make the description more specific, or add `disable-model-invocation: true`.

**Descriptions cut short**: see the listing budget in section 10.

**Forked skill returns nothing useful**: the skill is reference content with no task. `context: fork` needs explicit instructions.

**Forked skill's edits cannot be rewound**: backgrounded forks fall outside checkpoints. Use git.

---

## 22. Gotchas

1. **Skill precedence is enterprise > personal > project**, the reverse of the settings hierarchy.
2. **A same-named skill anywhere overrides a bundled skill**, so a project `code-review` silently replaces `/code-review`.
3. **In personal and project skills, frontmatter `name` does not set the command name.** The directory does. In plugin skills it does set the last segment.
4. **Nested `.claude/skills/` are not loaded at startup.** They appear only after Claude touches a file in that subdirectory.
5. **`--add-dir` loads skills but `permissions.additionalDirectories` does not.** Skills are the exception to the additional-directory rule; commands and output styles are not.
6. **Creating a top-level skills directory mid-session needs a restart**; editing existing skills does not.
7. **Skill content persists for the whole session and is never re-read.** Write standing instructions.
8. **`allowed-tools` clears on your next message** even though the content stays.
9. **`allowed-tools` grants, it does not restrict.** Use `disallowed-tools` or deny rules for restriction.
10. **A project skill can grant itself broad tool access via `allowed-tools`.** Review before trusting a repository.
11. **`user-invocable: false` hides from the menu but does not block Skill tool access.** Use `disable-model-invocation` for that.
12. **`disable-model-invocation: true` also blocks preloading into subagents and scheduled-task invocation.**
13. **Dynamic-context substitution runs once and output is not re-scanned.** A command cannot emit another placeholder.
14. **`!` must be at line start or after whitespace.** `KEY=!`cmd`` never runs.
15. **`${CLAUDE_SKILL_DIR}` in `allowed-tools` needs v2.1.129+**, or the rule stays a literal string and never matches.
16. **A missing indexed argument leaves the placeholder in place; a missing named argument becomes an empty string.** Two different behaviors.
17. **Skill stacking stops at the first non-inline skill**, and everything from there on becomes argument text.
18. **Backgrounded forks get the narrower background-subagent tool set** and their edits escape `/rewind`.
19. **`context: fork` with reference-only content produces no output.**
20. **Malformed YAML fails quietly**: the body loads with empty metadata and no description to match on.
21. **Compaction keeps only the first 5,000 tokens of each skill within a shared 25,000-token budget**, filling from the most recent, so older skills can vanish entirely.
22. **The skill listing drops descriptions from your least-used skills first** when it overflows its 1% budget.
23. **Cowork and cloud sessions do not read `~/.claude/skills/`.** Desktop scheduled tasks do.
24. **`skillOverrides` does not affect plugin skills.**
25. **`disableSkillShellExecution` does not affect bundled or managed skills**, only user, project, plugin, and additional-directory ones.

---

## Reference links

- Skills in Claude Code: https://code.claude.com/docs/en/skills
- Skill authoring best practices: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- Agent Skills overview: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- Agent Skills standard: https://agentskills.io
- Evaluating skill output quality: https://agentskills.io/skill-creation/evaluating-skills
- skill-creator plugin: https://github.com/anthropics/claude-plugins-official/tree/main/plugins/skill-creator
- Commands reference: https://code.claude.com/docs/en/commands
- Subagents: https://code.claude.com/docs/en/sub-agents
- Hooks: https://code.claude.com/docs/en/hooks
- Memory: https://code.claude.com/docs/en/memory
- Permissions: https://code.claude.com/docs/en/permissions
- Plugins: https://code.claude.com/docs/en/plugins
- Debug your configuration: https://code.claude.com/docs/en/debug-your-config
- Full docs index: https://code.claude.com/docs/llms.txt
