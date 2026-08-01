# Claude Code: Context Window and Compaction Reference

A study guide to what occupies Claude's context window, what each mechanism costs, what you can and cannot see in your terminal, what survives compaction, and how to manage context as the primary constraint.

Verified against code.claude.com/docs/en/context-window, with cross-references to /memory, /skills, /sub-agents, and /hooks.

---

## 1. The premise

The context window holds **everything Claude knows about your session**: your instructions, files it reads, its own responses, and **content that never appears in your terminal**.

That last category is the reason this subject matters. Most of what fills the window is invisible to you, so intuition built from watching the terminal is systematically wrong about where the tokens went.

---

## 2. What loads before you type anything

A realistic startup, with the docs' own representative token counts against a 200K window:

| Item | ~Tokens | Visible? | Notes |
|---|---|---|---|
| **System prompt** | 4,200 | Hidden | Core instructions for behavior, tool use, formatting. Always first |
| **Auto memory (`MEMORY.md`)** | 680 | Hidden | First 200 lines or 25KB, whichever comes first |
| **Environment info** | 280 | Hidden | Working directory, platform, shell, OS, git-repo flag. **Git branch, status, and recent commits load as a separate block at the very end of the system prompt** |
| **MCP tool names (deferred)** | 120 | Hidden | Names only. Full schemas stay deferred, loaded on demand via tool search |
| **Skill descriptions** | 450 | Hidden | One-line descriptions. Full bodies load only on use |
| **`~/.claude/CLAUDE.md`** | 320 | Hidden | Your global preferences |
| **Project CLAUDE.md** | 1,800 | Hidden | Usually the largest startup item you control |

That is roughly **7,850 tokens before your first keystroke**, and none of it appears in your terminal.

Your own setup may add more: an **output style** or text from **`--append-system-prompt`**, both of which go into the system prompt the same way.

Two exclusions worth noting:

- **Skills with `disable-model-invocation: true` are not in the startup listing at all.** They cost zero context until you invoke them with `/name`
- **MCP schemas are deferred by default.** `ENABLE_TOOL_SEARCH=auto` loads them upfront when they fit within 10% of the window; `false` loads everything

---

## 3. Visibility tiers

Every item in context falls into one of three tiers, and the mismatch between them is the core lesson.

| Tier | Meaning |
|---|---|
| **Shown in your terminal** | The actual content appears |
| **One-liner** | You see a brief mention, not the content |
| **Invisible** | Nothing appears at all |

Worked examples from the documented session:

| Event | Tokens | You see |
|---|---|---|
| Your prompt | 45 | The full text |
| `Read src/api/auth.ts` | **2,400** | "Read auth.ts" |
| `Read src/lib/tokens.ts` | 1,100 | A one-liner |
| Path-scoped rule loading | 380 | "Loaded .claude/rules/api-conventions.md", **not the rule content** |
| `grep "refreshToken"` | 600 | That the command ran, not the output |
| `npm test` output | 1,200 | "Running npm test..." and the pass count |
| PostToolUse hook (prettier) | 120 | **Nothing** |
| Claude's analysis | 800 | The full text |

**Your prompt was 45 tokens; the first file read was 2,400.** The proportion is the point: most of Claude's context is project knowledge, not your words.

---

## 4. What each mechanism costs

### File reads dominate

They are the largest single category in a working session. The docs' guidance: **be specific in prompts** ("fix the bug in auth.ts") so Claude reads fewer files, and **delegate research-heavy work to a subagent**.

### Path-scoped rules load silently

A rule with `paths: src/api/**` loads **automatically when Claude reads a matching file**. You see a one-line "Loaded" notice; the content is invisible. This is cheap and useful, but it means context can grow from rules you forgot you wrote.

### Hooks enter context through one field only

A PostToolUse hook reports back via **`hookSpecificOutput.additionalContext`**. That field enters context.

**Plain stdout on exit 0 does not** — it goes to the debug log only. And **exit code 2 surfaces stderr as an error but cannot block**, since the tool already ran.

**Hook output enters context without truncation**, so keep it concise. Every matching tool event fires the hook again: two edits mean two hook payloads.

### Bang commands

`!git status` runs in your shell and **both the command and its output enter context** as part of your message. Useful for grounding Claude in command output without Claude running it.

### Skills

The startup listing is descriptions only. Invoking a skill loads its **full body**. A user-only skill (`disable-model-invocation: true`) costs **nothing until invoked**, which is the strongest argument for that flag on side-effect skills like commit, deploy, or send-message.

---

## 5. Subagents as a context mechanism

This is the largest lever available.

When Claude delegates research, the subagent gets **a fresh, separate context window**. From the documented example:

| In the subagent's context | Tokens |
|---|---|
| Its own system prompt (shorter than the main one) | 900 |
| Project CLAUDE.md (**its own copy**) | 1,800 |
| MCP tools + skills | 970 |
| Task prompt written by Claude | 120 |
| `Read session.ts` | 2,200 |
| `Read timeouts.ts` | 800 |
| `Read config/*.ts` | 3,100 |

**The subagent read 6,100 tokens of files. What returned to your context was a 420-token summary**, plus a small metadata trailer with token counts and duration.

Details worth internalizing:

- **The subagent loads CLAUDE.md too**, same file and content, but **against its own context**. The built-in Explore and Plan agents skip it for a smaller footprint
- It starts **without your conversation history and without the main session's auto memory**
- With `memory:` in its frontmatter, it loads **its own separate `MEMORY.md`** instead
- It gets most of the parent's tools, **minus several that do not apply in a nested context**: plan-mode controls, background-task tools, and by default the Agent tool itself to prevent recursion

---

## 6. Compaction

Compaction replaces the conversation with a **structured summary**. What the summary keeps, per the docs:

> your requests and intent, key technical concepts, files examined or modified with important code snippets, errors and how they were fixed, pending tasks, and current work

**It replaces the verbatim conversation: full tool outputs and intermediate reasoning are gone.** Claude can still reference the work but **will not have the exact code it read earlier**.

As of v2.1.198, **the summarization request inherits your session's extended thinking configuration**. Thinking affects only how the summary is produced; your session settings are unchanged afterward.

### What survives

| Mechanism | After compaction |
|---|---|
| System prompt and output style | **Unchanged**; not part of message history |
| Project-root CLAUDE.md and unscoped rules | **Re-injected from disk** |
| Auto memory | **Re-injected from disk** |
| **Rules with `paths:` frontmatter** | **Lost** until a matching file is read again |
| **Nested CLAUDE.md in subdirectories** | **Lost** until a file in that subdirectory is read again |
| **Invoked skill bodies** | Re-injected, capped at **5,000 tokens per skill and 25,000 total; oldest dropped first** |
| **Skill descriptions listing** | **Not re-injected.** Only skills you actually invoked are preserved |
| Hooks | Not applicable; hooks run as code, not context |

The underlying principle: **anything read from disk comes back; anything that entered message history gets summarized away.**

Path-scoped rules and nested CLAUDE.md files load into **message history** when their trigger file is read, so compaction summarizes them with everything else. **If a rule must persist, drop the `paths:` frontmatter or move it into the project-root CLAUDE.md.**

### Skill truncation

Large skills are truncated to fit the per-skill cap, and the oldest invoked skills drop once the total budget is exceeded. **Truncation keeps the start of the file**, which is a concrete authoring instruction: **put the most important instructions near the top of `SKILL.md`.**

---

## 7. Managing context

Claude Code compacts **automatically** as you approach the limit, so a full window does not end your session. The automatic pass works like manual `/compact`.

Three moves you can make first:

**Compact with a focus.** `/compact focus on the auth bug fix` before starting a long new task. **The summary keeps what you choose instead of what the automatic pass guesses is important.** This is the most direct control available.

**Clear between tasks.** `/clear` when switching to unrelated work. Old conversation crowds out the files you need next **and costs tokens on every message**.

**Delegate large reads.** Send research to a subagent so file contents stay in its window.

### Extended context

Fable 5, Sonnet 5, Opus 4.6 and later, and Sonnet 4.6 support a **1 million token** context window. Availability varies by plan, and most require selecting a `[1m]` model variant. **Sonnet 5 runs at 1M with no `[1m]` variant to select**, and has its own auto-compaction thresholds plus an LLM gateway exception.

**Compaction works the same way at the larger limit.** A bigger window delays the problem; it does not change the mechanics.

### Inspecting your own session

`/context` gives a **live breakdown by category** with optimization suggestions, including which CLAUDE.md and auto memory files loaded. `/memory` opens those files.

`/context` is the tool that turns this subject from theory into a number. The documented token counts are illustrative; **your actual values vary with CLAUDE.md size, MCP servers, and file lengths.**

---

## 8. The optimization checklist

Ordered roughly by leverage:

1. **Delegate research to subagents.** The single largest saving available, since verbose reads never touch your window
2. **Keep CLAUDE.md under 200 lines.** It loads every session and reduces adherence when long
3. **Move conditional content to path-scoped rules**, so it loads only alongside matching files
4. **Set `disable-model-invocation: true` on side-effect skills.** They cost zero until invoked, and it is the right safety choice anyway
5. **Be specific in prompts** so Claude reads fewer files
6. **Leave MCP tool search at its default.** Deferred schemas keep dozens of servers nearly free
7. **Keep hook `additionalContext` concise.** It enters context untruncated, on every matching event
8. **`/clear` between unrelated tasks** rather than carrying history forward
9. **`/compact` with a focus** before a long new task, rather than waiting for the automatic pass
10. **Put critical instructions at the top of `SKILL.md`**, since post-compaction truncation keeps the start

Two non-solutions worth knowing:

- **`@path` imports do not save context.** They help organization; imported files load at launch regardless
- **A larger context window does not remove compaction.** It moves the boundary

---

## 9. Gotchas

1. **Roughly 8K tokens load before your first keystroke**, none of it visible in your terminal.
2. **Your prompt is tiny compared to what surrounds it.** A single file read commonly costs 50x your message.
3. **A one-liner in your terminal can be thousands of tokens.** Terminal output is not a proxy for context cost.
4. **Path-scoped rules load invisibly** when a matching file is read.
5. **Hook plain stdout on exit 0 does not reach Claude.** Only `additionalContext` does.
6. **Hook output is not truncated** and fires on every matching event.
7. **Bang command output enters context**, both the command and the result.
8. **Subagents load their own copy of CLAUDE.md**, against their own window. Explore and Plan skip it.
9. **Subagents do not inherit the main session's auto memory**, except forks.
10. **The skill descriptions listing is the one startup item not re-injected after compaction.**
11. **Only invoked skills survive compaction**, capped at 5,000 tokens each and 25,000 total, oldest dropped first.
12. **Skill truncation keeps the start of the file.** Front-load `SKILL.md`.
13. **Path-scoped rules and nested CLAUDE.md do not survive compaction** until their trigger file is read again.
14. **Project-root CLAUDE.md and auto memory do survive**, because they are re-read from disk.
15. **The system prompt and output style are untouched** by compaction, being outside message history.
16. **Compaction discards verbatim tool output and intermediate reasoning.** Claude keeps the gist, not the code it read.
17. **`@path` imports do not reduce context.**
18. **`disable-model-invocation: true` removes a skill from the startup listing entirely.**
19. **Old conversation costs tokens on every message**, not once. `/clear` is cheaper than carrying it.
20. **The 1M window does not disable compaction**, and Sonnet 5 reaches it with no variant to select.
21. **The documented token numbers are illustrative.** Run `/context` for yours.

---

## Reference links

- Explore the context window: https://code.claude.com/docs/en/context-window
- How Claude Code works, when context fills up: https://code.claude.com/docs/en/how-claude-code-works
- Memory and CLAUDE.md: https://code.claude.com/docs/en/memory
- Path-specific rules: https://code.claude.com/docs/en/memory
- Skills: https://code.claude.com/docs/en/skills
- Subagents: https://code.claude.com/docs/en/sub-agents
- Hooks guide: https://code.claude.com/docs/en/hooks-guide
- MCP tool search: https://code.claude.com/docs/en/mcp
- Model config and extended context: https://code.claude.com/docs/en/model-config
- Prompt caching: https://code.claude.com/docs/en/prompt-caching
- Reduce token usage: https://code.claude.com/docs/en/costs
- Best practices: https://code.claude.com/docs/en/best-practices
- Extend Claude Code: https://code.claude.com/docs/en/features-overview
- Full docs index: https://code.claude.com/docs/llms.txt
