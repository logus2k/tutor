# Claude Code: Models, Effort, and Cost Reference

A study guide to model selection and reasoning depth: aliases and resolution, the precedence chain, effort levels and ultracode, extended thinking, extended context, fallback behavior, enterprise restriction, third-party pinning, and prompt caching.

Verified against code.claude.com/docs/en/model-config.

---

## 1. Model aliases

| Alias | Behavior |
|---|---|
| `default` | **Not a model alias.** Clears any override and reverts to the recommended model for your account type, or the organization default |
| `best` | Fable 5 where your organization has access, otherwise the latest Opus |
| `fable` | Claude Fable 5, for the hardest and longest-running tasks |
| `opus` | Latest Opus, for complex reasoning |
| `sonnet` | Latest Sonnet, for daily coding |
| `haiku` | Fast and efficient, for simple tasks |
| `sonnet[1m]` / `opus[1m]` | 1M token context window |
| `opusplan` | **`opus` during plan mode, `sonnet` for execution** |

### Aliases resolve differently per provider

| Provider | `opus` | `sonnet` |
|---|---|---|
| Anthropic API | Opus 5 | Sonnet 5 |
| Claude Platform on AWS | Opus 5 | **Sonnet 4.6** |
| Amazon Bedrock, Google Cloud's Agent Platform | Opus 5 | **Sonnet 4.5** |
| Microsoft Foundry | **Opus 4.6** | **Sonnet 4.5** |

Where an alias resolves to an older model, select the full name explicitly or set `ANTHROPIC_DEFAULT_OPUS_MODEL` / `ANTHROPIC_DEFAULT_SONNET_MODEL`.

**Aliases update over time.** To pin, use a full model name like `claude-opus-5` or the environment variable.

Version requirements: **Opus 5 needs v2.1.219+, Sonnet 5 needs v2.1.197+, Opus 4.8 needs v2.1.154+, Fable 5 needs v2.1.170+.**

### Account-type defaults

| Account | `default` resolves to |
|---|---|
| Max, Team Premium, Enterprise pay-as-you-go, Anthropic API | Opus 5 |
| Claude Platform on AWS, Amazon Bedrock, Agent Platform | Opus 5 |
| Pro, Team Standard, Enterprise subscription seats | Sonnet 5 |
| Microsoft Foundry | Sonnet 4.5 |

**Fable 5 is not the default on any account type.** Sessions use it only after you choose it. Choosing it with `/model` **saves it as your selected model**, so later sessions start on Fable 5 until you change.

### Working with Fable 5

Suited to **tasks larger than a single sitting**. It sustains long autonomous sessions, investigates before acting, and verifies its work more often than smaller models. The docs' guidance:

- **Describe the outcome, not the steps.** Hand it the result and let it plan the path; pair with `/goal` to keep it working until the outcome holds
- **Hand it ambiguous problems.** Root-cause investigations, outage debugging, architecture decisions
- **Skip verification reminders.** It verifies itself with less prompting
- **Size up larger tasks.** Give it work you would normally break into pieces

**Not available under zero data retention.** On the Anthropic API, the picker lists it only after the server reports availability, but **`/model fable` checks with the server directly**, so the selection can succeed before the picker shows it.

---

## 2. Setting the model

Priority order, highest first:

1. **`/model <alias|name>`** during a session
2. **`claude --model <alias|name>`** at startup
3. **`ANTHROPIC_MODEL`** environment variable
4. **`model`** field in settings

**As of v2.1.153, `/model` saves your choice as the default for new sessions.** In the picker: `Enter` switches and saves; **`s` switches for this session only**. Typing `/model <name>` behaves like `Enter`. In `-p` mode it applies to the current session only.

**`--model` and `ANTHROPIC_MODEL` apply only to the session you launch with them.** To run different models in different terminals simultaneously, launch each with its own flag rather than switching with `/model`.

### Resume behavior

**Resumed sessions keep the model from when the transcript was saved**, regardless of your current `model` setting. This prevents another session's `/model` choice from changing the model on resume.

Exceptions: a retired or `availableModels`-excluded model falls through to normal precedence; `--model`, `ANTHROPIC_MODEL`, and (v2.1.195+) `ANTHROPIC_DEFAULT_OPUS_MODEL`-family variables still take precedence; and **on providers using deployment IDs (Bedrock, Agent Platform, Foundry) the transcript model is not restored at all.**

### Model name validation

When a switch comes through the Agent SDK's `setModel()` or an app like Desktop, Claude Code validates the string first (v2.1.200+). On the Anthropic API it recognizes an alias, a picker entry, any name starting with `claude-`, or a value you configured as a custom option or in `modelOverrides`.

**The check runs only on the Anthropic API**, and **does not cover `--model`, `ANTHROPIC_MODEL`, or the `model` setting** — a typo there fails on the first request instead.

### Retirement warnings

A requested model with a scheduled retirement date or automatic remapping produces a warning naming it, as a startup notice interactively and **on stderr in `-p` mode with text output** (v2.1.182+). It covers subagent frontmatter too. **The stderr warning is suppressed for `json` and `stream-json`** — read the actual model from the result message's `modelUsage` field instead.

### Picker prices

Prices appear when talking to the Anthropic API, directly or through a proxying gateway. **On third-party providers and the Claude apps gateway, rows show no price** because your provider determines what you pay. **The price is a display label only.**

---

## 3. Effort levels

Effort controls **adaptive reasoning**: the model decides whether and how much to think on each step based on complexity.

| Model | Levels |
|---|---|
| Fable 5, Opus 5, Sonnet 5, Opus 4.8, Opus 4.7 | `low`, `medium`, `high`, `xhigh`, `max` |
| Opus 4.6, Sonnet 4.6 | `low`, `medium`, `high`, `max` (**no `xhigh`**) |
| Anything else | No effort support |

**Setting an unsupported level falls back to the highest supported level at or below it.** `xhigh` runs as `high` on Opus 4.6.

**The default is `high` on every model that supports effort, except Opus 4.7, which defaults to `xhigh`.**

### Choosing

| Level | When |
|---|---|
| `low` | Short, scoped, latency-sensitive tasks that are **not intelligence-sensitive** |
| `medium` | Cost-sensitive work that can trade off some intelligence |
| `high` | Balanced. The default |
| `xhigh` | Deeper reasoning at higher token spend |
| `max` | **May show diminishing returns and is prone to overthinking. Test before adopting broadly** |
| `ultracode` | `xhigh` plus dynamic workflow orchestration. Session-only |

**The effort scale is calibrated per model**, so the same level name does not represent the same underlying value across models.

### Persistence and the model-default hold

**`low`, `medium`, `high`, `xhigh` persist across sessions** when set interactively. **`max` applies to the current session only**, except via `CLAUDE_CODE_EFFORT_LEVEL`.

**The hold**: when you first run Fable 5, Opus 4.8, or Opus 4.7, Claude Code applies **that model's default effort even if you previously set a different level**, and holds it across sessions until you make an explicit choice. **Opus 5 has no such hold**; a previously set level carries over.

While the hold is in force, a non-interactive `/effort` reports **`Not applied`** — pass `--effort` at launch instead.

### Setting it

| Method | Notes |
|---|---|
| `/effort` | No argument opens a slider; a level sets directly; `auto` resets to the model default |
| Arrow keys in `/model` | Adjust the slider while selecting |
| `--effort <level>` | Single session |
| `CLAUDE_CODE_EFFORT_LEVEL` | **Takes precedence over everything else** |
| `effortLevel` in settings | **`max` and `ultracode` are not accepted here** |
| `effort` in skill or subagent frontmatter | Applies while that component is active |

Precedence: **environment variable → your configured level → model default.** Frontmatter overrides the session level but **not the environment variable**.

**`effortLevel` in managed settings is a starting default, not enforcement.** Users can change it per session; the managed value re-asserts in new sessions. For actual enforcement, use organization effort limits.

### Ultracode

**Not a model effort level.** It sends `xhigh` to the model **and** has Claude orchestrate dynamic workflows for substantive tasks. Session-only.

Turn it on with `/effort ultracode`, `claude --effort ultracode` (v2.1.203+), or `"ultracode": true` via `--settings` or an SDK control request.

**Neither `effortLevel` nor `CLAUDE_CODE_EFFORT_LEVEL` accepts it.** When `CLAUDE_CODE_EFFORT_LEVEL` is set to anything other than `xhigh`, requests run at that level and **ultracode's orchestration stays inactive**, with a warning.

**When workflows are turned off, `--effort ultracode` sets `xhigh` only.**

### ultrathink

Include **`ultrathink`** anywhere in a prompt for deeper reasoning on that turn without changing your session setting. Claude Code recognizes the keyword and adds an in-context instruction; **the effort level sent to the API is unchanged.**

**"think", "think hard", and "think more" are passed through as ordinary prompt text** and are not recognized as keywords.

---

## 4. Extended thinking

On adaptive-reasoning models, **effort is the primary control**; these settings turn thinking on or off and control display.

| Control | How |
|---|---|
| Toggle for this session | `Option+T` (macOS) or `Alt+T` |
| Global default | `/config` thinking toggle, saved as `alwaysThinkingEnabled` |
| Disable regardless of effort | `MAX_THINKING_TOKENS=0`. **On third-party providers this omits the `thinking` parameter instead, and adaptive-reasoning models may still think** |

**Thinking cannot be turned off on Fable 5.** The session toggle, `alwaysThinkingEnabled`, and `MAX_THINKING_TOKENS=0` all have no effect there.

Thinking output is collapsed by default; `Ctrl+O` toggles verbose. **Interactive Anthropic API sessions receive redacted thinking blocks by default** — set `showThinkingSummaries: true` for full summaries.

**You are charged for all thinking tokens generated, even when collapsed or redacted.**

### Adaptive versus fixed budgets

**Fable 5, Sonnet 5, and Opus 4.7+ always use adaptive reasoning.** The fixed budget mode and `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING` do not apply.

On **Opus 4.6 and Sonnet 4.6**, `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1` reverts to the fixed budget controlled by `MAX_THINKING_TOKENS`.

---

## 5. Extended context

Fable 5, Sonnet 5, Opus 4.6+, and Sonnet 4.6 support a **1M token window**. On the Anthropic API, **Fable 5, Sonnet 5, and Opus 4.7+ always run at 1M.**

| Plan | Opus at 1M | Sonnet 4.6 at 1M |
|---|---|---|
| Max, Team, Enterprise | **Included** | Requires usage credits |
| Pro | Requires usage credits | Requires usage credits |
| API and pay-as-you-go | Full access | Full access |

**Sonnet 4.6 at 1M is not part of the automatic upgrade** and requires credits on every subscription plan, including Max.

**The 1M window uses standard pricing with no premium beyond 200K.**

Selection: the `[1m]` suffix on aliases or full names (`/model opus[1m]`, `/model claude-opus-4-8[1m]`). Disable entirely with `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`, which removes 1M variants from the picker.

### Sonnet 5 is special

**Always 1M on the Anthropic API. No 200K variant, no `[1m]` suffix to select, no usage credits on any plan.** Sessions auto-compact at **about 967K tokens** by default; change with `CLAUDE_CODE_AUTO_COMPACT_WINDOW`.

**Two configurations budget it at 200K instead**:

- **An LLM gateway**, because Claude Code cannot verify 1M support. Select "Sonnet 5 (1M context)" in the picker, which maps to `sonnet[1m]`
- **`CLAUDE_CODE_DISABLE_1M_CONTEXT=1`**

### opusplan and context

The plan-mode Opus phase uses the same window as the `opus` setting. **On tiers where Opus auto-upgrades to 1M, `opusplan` gets it in plan mode too.** To force 1M for both phases off an auto-upgrade tier, use `opusplan[1m]`.

---

## 6. Fallback

Two distinct mechanisms, easily confused.

### Fallback model chains — availability-based

For an overloaded or unavailable primary, or another non-retryable server error. **Authentication, billing, rate-limit, request-size, and transport errors never trigger a switch.**

```bash
claude --fallback-model sonnet,haiku
```

```json
{ "fallbackModel": ["claude-sonnet-5", "claude-haiku-4-5"] }
```

- **The switch lasts for the current turn only**; your next message tries the primary again
- **Capped at three models** after duplicate removal; extras ignored
- The flag takes precedence over the setting; `"default"` expands to the default model
- **Claude Code does not confirm the chain at startup and `/status` does not display it.** The notice on switch is the first visible sign one is configured

Two removals before the walk:

- **Entries outside `availableModels` are dropped**
- **During compaction, Claude Code will not fall back to a smaller context window** than the primary's, since summarizing there would truncate the conversation first. If every fallback is smaller, compaction shows the original error

### Automatic model fallback — content-based

Fable 5 and Opus 5 run **safety classifiers for cybersecurity and biology**. A flagged request re-runs on a fallback model with a notice.

| Model refusing | Cybersecurity flag | Biology flag |
|---|---|---|
| **Fable 5** | Re-runs on **Opus 4.8** | Re-runs on **Opus 5** |
| **Opus 5** | Re-runs on **Opus 4.8** | **Refusal** — Opus 5 runs its own biology classifiers with no fallback |

**After a fallback, the session continues on the fallback model.** Run `/model` to return.

Category-based fallback requires v2.1.219+. Before that, every flagged Fable 5 request re-ran on the provider's default Opus, and Opus 5 was not a fallback source.

**When the fallback target is blocked by `availableModels`, no fallback occurs** and the flagged request ends with a refusal.

#### The first-request surprise

**Fallback can trigger on the first request of a session**, before you send anything unusual, because that request carries workspace context: **your CLAUDE.md content and git status**. A repository containing security or biology material can trip the classifier on context alone.

Diagnose with **`claude --safe-mode`**, which disables CLAUDE.md, skills, MCP servers, and hooks. **Git status and directory names are not customizations and are still included.**

#### Asking before switching

`/config` → turn off **Switch models when a message is flagged**, or set `switchModelsOnFlag: false`. A flagged request then pauses with two options: switch, or edit the prompt and retry on the current model.

Exceptions where no prompt appears: a flagged category with no fallback (biology on Opus 5), a blocked fallback target, non-interactive mode and SDK integrations (**the turn ends with a refusal**), and mobile web sessions where editing is unsupported.

#### Security research and biology

Offensive security, penetration testing, CTF exercises, and biology-adjacent codebases **trigger fallback frequently, often on the first request**. On Fable 5, substantive biology work moves the session to Opus 5 at the first flag, and later biology flags **end in refusals there**.

**This is expected routing for these domains, not an account flag.** For Fable-class capability in this work, the docs point to trusted access programs via your Anthropic account team.

---

## 7. Enterprise restriction

### availableModels

An allowlist in managed or policy settings. Entries match **a family (`sonnet`), a version prefix (`claude-sonnet-4-5`), or a full ID**.

It applies to: the main session model (`/model`, `--model`, `ANTHROPIC_MODEL`, the `model` setting, restored models), alias resolution via the `ANTHROPIC_DEFAULT_*_MODEL` variables, fast mode, subagent models including `CLAUDE_CODE_SUBAGENT_MODEL`, skill and command `model` frontmatter, the advisor model, and the background-agent dispatch picker.

Blocked selections behave differently by origin:

| Set via | Result |
|---|---|
| `/model` | **Rejected with an error** |
| `--model`, `ANTHROPIC_MODEL`, `model` setting | **Replaced at startup with a warning**, session starts on the default |
| Subagent, skill, or command override | **Falls back to inherited or default** rather than failing |
| `advisorModel` setting | **Advisor disabled for the session** |
| `--advisor` flag | **Claude Code exits with an error at launch** |

On the Anthropic API and Claude Platform on AWS, **a family alias resolves to the newest permitted version**. With `["sonnet", "claude-opus-4-6"]`, `/model opus` selects Opus 4.6 with a substitution notice. **On providers with deployment IDs, a blocked alias is rejected or replaced instead.**

Changes Claude Code makes on your behalf are checked too: fallback chain entries outside the list are dropped; plan-mode upgrades use the newest permitted version or are skipped; automatic fallback to an excluded target does not run; the auto-mode classifier's Sonnet 5 default applies only if permitted; fast mode is refused when the resulting model is excluded.

### The Default-model gap

**`availableModels` alone does not constrain the Default option.** A user selecting Default gets the account-type or organization default **regardless of the allowlist**.

`enforceAvailableModels: true` (v2.1.175+) extends the allowlist to Default, which then resolves to **the first `availableModels` entry naming an allowed, available model.**

Two safety valves:

- **`enforceAvailableModels` has no effect when `availableModels` is unset or empty.** With `availableModels: []`, named selections are blocked but Default remains usable, so **the setting cannot lock users out of every model**
- When no entry resolves to an allowed model, enforcement is skipped and Default resolves normally, **with a warning visible only under `--debug`**. Keep at least one guaranteed-available entry

A complete restriction combines four things:

```json
{
  "model": "claude-sonnet-4-5",
  "availableModels": ["claude-sonnet-4-5", "haiku"],
  "enforceAvailableModels": true,
  "env": { "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-4-5" }
}
```

**Without `enforceAvailableModels` or the `env` block, a user selecting Default gets the latest release for their tier, bypassing the version pin.** The two cover different scopes: `enforceAvailableModels` makes Default obey the allowlist; the `env` block pins which version a permitted alias resolves to.

### Merge behavior

**When the highest-precedence managed source defines `availableModels`, that list alone applies.** User, project, and local settings cannot extend it, and **admin-deployed managed sources do not merge with each other** — a list in a managed settings file is ignored when server-managed settings deliver any keys. Otherwise lists concatenate and deduplicate.

**Within the effective list, a specific entry disables that family's wildcard.** `["sonnet", "claude-sonnet-4-5"]` allows **only Sonnet 4.5 versions**, not every Sonnet.

### Delivery coverage

| Mechanism | CLI/IDE | Desktop local | Web/mobile/cloud | SDK/non-interactive | Cowork |
|---|---|---|---|---|---|
| **Server-managed settings** | Enforced | Enforced | Enforced | Enforced | **Not delivered** |
| **MDM / managed settings files** | Enforced | Enforced | **Not delivered** | Enforced | Where deployed |

**Cloud sessions run on Anthropic-managed VMs**, so device-deployed settings do not reach them — use server-managed settings. **Third-party providers do not receive server-managed settings** — use MDM there. Server-managed delivery also **requires an organization login or a directly configured API key**, so fleets using an `apiKeyHelper` script need MDM.

**Model pickers hiding excluded models is a convenience; enforcement happens in the session.**

### Organization restrictions and defaults

Separate from `availableModels`, Enterprise admins disable models in the claude.ai admin console. **Delivered with account entitlements, and the server enforces it independently at session creation** (v2.1.187+).

- Applies when a member signs in or uses their own API key. **Organization-scoped credentials like service keys are not tied to a user, so it does not apply to them**
- Org-wide or per role; a member with several roles can use any model one role grants
- **Haiku models are always available and cannot be disabled**, so every member keeps a usable model
- Changes take effect on new requests within about a minute

**Both restrictions apply together.** Not delivered on Bedrock, Agent Platform, Foundry, or Claude Platform on AWS — use `availableModels` there.

An **organization default model** (v2.1.196+) makes Default resolve to a chosen model, labeled **Org default** in the picker. It is **a starting point, not a restriction**: `--model`, `ANTHROPIC_MODEL`, managed settings, `--settings`, and your own settings all take precedence. With override enabled it beats user, project, and local settings, so a `/model` choice applies for the session and the org default returns next launch.

**Read once at startup**, so a mid-session change takes effect next launch.

**Organization effort limits** (v2.1.195+) cap effort per model per role. Levels above the cap are not offered, and naming a higher one runs at the cap. **Interactively and in plain-text `--print` a warning names both levels; with `json`/`stream-json` output or in background agents the clamp is silent.**

---

## 8. Third-party deployments

**Pin model versions before rolling out.** Without pinning, aliases resolve to a built-in default per provider that **can lag the newest release and may not be enabled in a user's account.**

Failure modes differ: **Bedrock and Agent Platform show a notice and fall back** to an earlier version, or to default Sonnet when no Opus is available. **Microsoft Foundry shows errors instead**, having no equivalent startup check.

```bash
export ANTHROPIC_DEFAULT_OPUS_MODEL='us.anthropic.claude-opus-4-8'   # Bedrock
export ANTHROPIC_DEFAULT_OPUS_MODEL='claude-opus-4-8'                # Agent Platform, Foundry
```

Append `[1m]` for extended context. **The suffix is read per variable, not per model**: on these providers, a model ID without `[1m]` in one variable uses 200K even if another variable sets the same model with it. Claude Code strips the suffix before sending.

### Declaring capabilities

Provider-specific IDs often do not match the patterns Claude Code uses to detect features, **leaving supported features disabled**.

| Capability | Enables |
|---|---|
| `effort` | Effort levels and `/effort` |
| `xhigh_effort` | The `xhigh` level |
| `max_effort` | The `max` level |
| `thinking` | Extended thinking |
| `adaptive_thinking` | Adaptive reasoning |
| `interleaved_thinking` | Thinking between tool calls |

```bash
export ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES='effort,xhigh_effort,max_effort,thinking,adaptive_thinking,interleaved_thinking'
```

**When set, listed capabilities are enabled and unlisted ones disabled.** Unset falls back to built-in detection. `_NAME` and `_DESCRIPTION` companions control picker display.

### modelOverrides

Maps individual Anthropic model IDs to provider-specific strings, for **routing each version to a specific inference profile ARN, Agent Platform version name, or Foundry deployment**.

```json
{
  "modelOverrides": {
    "claude-opus-4-7": "arn:aws:bedrock:...:application-inference-profile/opus-prod",
    "claude-sonnet-4-6": "arn:aws:bedrock:...:application-inference-profile/sonnet-prod"
  }
}
```

Keys must be Anthropic model IDs with exact date suffixes where applicable; **unknown keys are ignored**. On Bedrock they take precedence over auto-discovered inference profiles.

**`availableModels` is evaluated against the Anthropic model ID, not the override value**, so `"opus"` still matches when Opus versions are mapped to ARNs.

As of v2.1.200, overrides also apply to IDs passed through `--model` and the environment variables. **When `availableModels` is set in managed settings, only `modelOverrides` from that managed source apply to those IDs**, and Claude Code never resolves an excluded ID through overrides from any source.

### Custom picker entries

`ANTHROPIC_CUSTOM_MODEL_OPTION` adds a single entry without replacing built-in aliases, with optional `_NAME` and `_DESCRIPTION`. **Claude Code skips validation for this ID**, so any string your endpoint accepts works.

**When `availableModels` is set, include the custom ID in the allowlist**, and note that a custom ID embedding a family name **counts as a specific entry and disables that family's wildcard**.

---

## 9. Prompt caching

Used automatically to optimize performance and cost.

| Variable | Disables caching for |
|---|---|
| `DISABLE_PROMPT_CACHING` | **All models. Takes precedence over the per-model settings** |
| `DISABLE_PROMPT_CACHING_HAIKU` | Haiku |
| `DISABLE_PROMPT_CACHING_SONNET` | Sonnet |
| `DISABLE_PROMPT_CACHING_OPUS` | Opus |
| `DISABLE_PROMPT_CACHING_FABLE` | Fable |

Cache behavior interacts with several things covered elsewhere: switching models mid-conversation **re-reads the full history without cached context** (which is why the picker asks for confirmation), enabling or disabling an MCP-providing plugin invalidates the cache, and `--exclude-dynamic-system-prompt-sections` improves cache reuse across machines.

---

## 10. Checking the current state

- **The session header** shows the model and effort, e.g. "with low effort"
- **The footer** briefly shows effort at startup and on change
- **`/status`** shows model and account information
- **The status line**, if configured
- **`modelUsage`** in the `-p` result message, for the model actually used

**When the active model comes from project or managed settings rather than your selection, the startup header names the settings file that set it.**

---

## 11. Gotchas

1. **`default` is not a model alias.** It clears overrides and resolves to the account-type or organization default.
2. **Aliases resolve to different versions per provider.** `sonnet` is Sonnet 5 on the API but Sonnet 4.5 on Bedrock.
3. **`/model` saves as your default** since v2.1.153. Press `s` for session-only.
4. **`--model` and `ANTHROPIC_MODEL` are per-launch.** Use them for parallel terminals, not `/model`.
5. **Resumed sessions keep their transcript model**, except on deployment-ID providers where it is not restored at all.
6. **Model-name validation does not cover `--model`, `ANTHROPIC_MODEL`, or the `model` setting.**
7. **Picker prices are display labels only**, and absent on third-party providers.
8. **`xhigh` does not exist on Opus 4.6 or Sonnet 4.6** and silently runs as `high`.
9. **Opus 4.7 defaults to `xhigh`; everything else defaults to `high`.**
10. **The model-default effort hold overrides your saved level** on first use of Fable 5, Opus 4.8, or Opus 4.7. Opus 5 is exempt.
11. **`max` is session-only** unless set via `CLAUDE_CODE_EFFORT_LEVEL`.
12. **`CLAUDE_CODE_EFFORT_LEVEL` beats everything**, including frontmatter.
13. **`effortLevel` in managed settings is not enforcement.** Use organization effort limits.
14. **Ultracode is not an effort level**, is session-only, and is silently neutralized by a non-`xhigh` `CLAUDE_CODE_EFFORT_LEVEL`.
15. **Only `ultrathink` is a recognized keyword.** "think hard" is ordinary text.
16. **Thinking cannot be disabled on Fable 5** by any mechanism.
17. **You pay for thinking tokens even when collapsed or redacted.**
18. **`MAX_THINKING_TOKENS=0` only omits the parameter on third-party providers**, where adaptive models may still think.
19. **Sonnet 5 has no `[1m]` variant** and auto-compacts near 967K.
20. **An LLM gateway silently budgets Sonnet 5 at 200K** unless you pick the 1M row.
21. **Sonnet 4.6 at 1M needs usage credits on every plan**, including Max.
22. **A fallback chain is invisible until it fires.** `/status` does not show it.
23. **Fallback lasts one turn**, and is capped at three models.
24. **Compaction will not fall back to a smaller context window.**
25. **Content-based fallback can fire on the first request**, triggered by CLAUDE.md and git status alone. Diagnose with `--safe-mode`.
26. **Biology flags on Opus 5 have no fallback** and end in refusal.
27. **`availableModels` alone leaves the Default option unrestricted.** You need `enforceAvailableModels`.
28. **A specific entry disables its family wildcard.** `["sonnet", "claude-sonnet-4-5"]` permits only 4.5.
29. **Managed sources do not merge with each other.** Server-managed settings shadow a managed file entirely.
30. **Server-managed settings do not reach Cowork or third-party providers; MDM does not reach cloud sessions.**
31. **Organization restrictions do not apply to organization-scoped service keys.**
32. **Haiku can never be disabled** by organization restrictions.
33. **An organization default is read once at startup.**
34. **Effort-limit clamping is silent under `json` output and in background agents.**
35. **A pinned third-party model may have features disabled** until you declare `_SUPPORTED_CAPABILITIES`.
36. **The `[1m]` suffix is per variable, not per model** on third-party providers.
37. **A custom model ID embedding a family name disables that family's wildcard** in `availableModels`.

---

## Reference links

- Model configuration: https://code.claude.com/docs/en/model-config
- Choosing a model and effort level: https://claude.com/blog/claude-model-and-effort-level-in-claude-code
- Models overview: https://platform.claude.com/docs/en/about-claude/models/overview
- Effort: https://platform.claude.com/docs/en/build-with-claude/effort
- Extended thinking: https://platform.claude.com/docs/en/build-with-claude/extended-thinking
- Context windows by model: https://platform.claude.com/docs/en/build-with-claude/context-windows
- Prompt caching in Claude Code: https://code.claude.com/docs/en/prompt-caching
- Manage costs: https://code.claude.com/docs/en/costs
- Fast mode: https://code.claude.com/docs/en/fast-mode
- Advisor tool: https://code.claude.com/docs/en/advisor
- Workflows and ultracode: https://code.claude.com/docs/en/workflows
- Server-managed settings: https://code.claude.com/docs/en/server-managed-settings
- LLM gateways: https://code.claude.com/docs/en/llm-gateway
- Amazon Bedrock: https://code.claude.com/docs/en/amazon-bedrock
- Google Cloud's Agent Platform: https://code.claude.com/docs/en/google-vertex-ai
- Microsoft Foundry: https://code.claude.com/docs/en/microsoft-foundry
- Environment variables: https://code.claude.com/docs/en/env-vars
- Full docs index: https://code.claude.com/docs/llms.txt
