# Claude Code: Integrations Reference

A study guide to running Claude Code outside your own terminal: GitHub Actions, GitLab CI/CD, Claude Code on the web, Remote Control, the desktop app, IDE extensions, and scheduled work.

GitHub Actions is verified in depth against code.claude.com/docs/en/github-actions. The surface sections are assembled from the CLI, commands, and tools references; each links to its own page for detail this guide does not attempt to duplicate.

---

## 1. The surface map

| Surface | Where code runs | Reach it with |
|---|---|---|
| **CLI** | Your machine | `claude` |
| **GitHub Actions** | GitHub's runners | `@claude` mention or a workflow prompt |
| **GitLab CI/CD** | GitLab runners | Pipeline job |
| **Claude Code on the web** | **Anthropic-managed VMs** | claude.ai, `--cloud`, `/autofix-pr` |
| **Remote Control** | **Your machine**, driven from claude.ai | `--remote-control`, `/remote-control` |
| **Desktop app** | Your machine | `/desktop` |
| **IDE extensions** | Your machine | `--ide`, `/ide` |
| **Mobile** | Whatever the session is attached to | `/mobile` |

**The distinction that matters most**: Claude Code on the web executes on Anthropic infrastructure, while **Remote Control is just a remote driver for a process on your own machine.** Same web interface, completely different trust and data model.

---

## 2. GitHub Actions

Built on the Agent SDK. A `@claude` mention in a PR or issue lets Claude analyze code, create pull requests, implement features, and fix bugs. **For automatic reviews on every PR without a trigger, that is GitHub Code Review, a different feature.**

### Setup

**Quick**: run `/install-github-app` in the terminal. It installs the Claude GitHub App, then walks through workflow files and the API key secret.

As of v2.1.187 you can choose **Skip for now** to stop with only the App installed and return later by running the command again. Earlier versions proceed straight to workflow selection.

Constraints: **you must be a repository admin**, the app requests **read & write on Contents, Issues, and Pull requests**, and **the quickstart is only available for direct Claude API users** — Bedrock and Agent Platform need the manual path.

**Manual**: install from `github.com/apps/claude`, add `ANTHROPIC_API_KEY` to repository secrets, copy `examples/claude.yml` into `.github/workflows/`.

### The v1 configuration surface

| Parameter | Notes |
|---|---|
| `prompt` | Instructions, **plain text or a skill invocation**. Optional: omitted on issue/PR comments, Claude responds to the trigger phrase |
| `claude_args` | **Any Claude Code CLI argument**, passed through |
| `plugin_marketplaces` | Newline-separated marketplace Git URLs |
| `plugins` | Newline-separated plugin names installed before execution |
| `anthropic_api_key` | Required for direct API, **not for Bedrock or Agent Platform** |
| `github_token` | For API access |
| `trigger_phrase` | Default `@claude` |
| `use_bedrock` / `use_vertex` | Provider selection |

```yaml
- uses: anthropics/claude-code-action@v1
  with:
    anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
    prompt: "Your instructions here"
    claude_args: "--max-turns 5 --model claude-sonnet-5"
```

**Mode is auto-detected.** With a `prompt`, the action runs immediately in automation mode; without one on comment events, it waits for the trigger phrase.

**`--max-turns` defaults to 10** in the action.

### Invoking skills

The `prompt` input accepts a skill invocation:

- **Repository skill** in `.claude/skills/`: run `actions/checkout` **before** the action step, then pass `/skill-name`
- **Plugin skill**: install via `plugin_marketplaces` and `plugins`, then pass the **namespaced** `/plugin-name:skill-name`

```yaml
- uses: anthropics/claude-code-action@v1
  with:
    anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
    plugin_marketplaces: "https://github.com/anthropics/claude-code.git"
    plugins: "code-review@claude-code-plugins"
    prompt: "/code-review:code-review ${{ github.repository }}/pull/${{ github.event.pull_request.number }}"
```

### Upgrading from beta

**v1.0 introduces breaking changes.** Four required edits:

1. `@beta` → `@v1`
2. **Delete `mode:`** — now auto-detected
3. `direct_prompt` → `prompt`
4. Move CLI options into `claude_args`

| Old beta input | v1.0 |
|---|---|
| `mode` | *Removed* |
| `direct_prompt` | `prompt` |
| `override_prompt` | `prompt` with GitHub variables |
| `custom_instructions` | `claude_args: --append-system-prompt` |
| `max_turns` | `claude_args: --max-turns` |
| `model` | `claude_args: --model` |
| `allowed_tools` | `claude_args: --allowedTools` |
| `disallowed_tools` | `claude_args: --disallowedTools` |
| `claude_env` | `settings` JSON format |

### Cloud providers

For data residency and billing control, run through **Bedrock** or **Google Cloud's Agent Platform**. Both use **OIDC rather than static credentials**, and both recommend **a custom GitHub App** rather than the official one.

**Bedrock** needs: Bedrock model access (in every region for cross-region models), GitHub as an OIDC identity provider (provider URL `https://token.actions.githubusercontent.com`, audience `sts.amazonaws.com`), and an IAM role trusting your specific repository. The secret is `AWS_ROLE_TO_ASSUME`.

**Agent Platform** needs: IAM Credentials, STS, and Agent Platform APIs enabled; a Workload Identity Pool with a GitHub OIDC provider and **repository-specific attribute conditions**; a service account with only the `Vertex AI User` role, ideally **one per repository**; and IAM bindings using **repository-specific principal sets**. Secrets are `GCP_WORKLOAD_IDENTITY_PROVIDER` and `GCP_SERVICE_ACCOUNT`.

Both workflows need `id-token: write` in the permissions block, alongside `contents: write`, `pull-requests: write`, and `issues: write`.

**Two provider-specific details worth noting**: the Bedrock model ID carries a **region prefix** (`us.anthropic.claude-sonnet-4-6`), and on Agent Platform **the project ID comes automatically from the auth step**, so you do not hardcode it.

### Workload Identity Federation for the Anthropic API

WIF exchanges the workflow's GitHub OIDC token for a short-lived Anthropic access token, **with no `ANTHROPIC_API_KEY` secret to create, store, or rotate**.

Three things to get right:

- **The workflow must grant `id-token: write`.** The default GitHub App path already requires it
- **Do not set `anthropic_api_key` or `claude_code_oauth_token` alongside the federation inputs.** A static credential takes precedence and **federation silently will not be used**
- The OIDC token is requested with audience **`https://api.anthropic.com`** by default; match your federation rule to that, or override with `anthropic_oidc_audience`

**One known gap**: inline comment classification (`classify_inline_comments`) currently requires `anthropic_api_key`. Under federation it is skipped and **unconfirmed inline comments are posted directly.**

### Base action versus full action

`claude-code-base-action` is the thinner primitive. **The caller is responsible for ensuring the working directory and prompt are trusted.**

**If your workflow processes untrusted input — issues, fork pull requests, external comments — use `anthropics/claude-code-action` instead.** It provides actor permission checks and restores project configuration from the base ref in PR contexts, and is the supported path for those scenarios.

### Operational notes

**Costs are two-sided**: GitHub Actions minutes on GitHub-hosted runners, plus API tokens. Mitigations the docs recommend: specific `@claude` commands rather than broad ones, an appropriate `--max-turns`, workflow-level timeouts, and **GitHub concurrency controls to limit parallel runs**.

**Troubleshooting:**

| Symptom | Check |
|---|---|
| No response to `@claude` | App installed, workflows enabled, secret set, and **the comment contains `@claude`, not `/claude`** |
| **CI not running on Claude's commits** | You are using the GitHub App or a custom app, **not the Actions user**; triggers include the needed events; app permissions include CI triggers |
| Auth errors | Key valid; for cloud providers, credentials configured and secrets named correctly |

Two behaviors from the action's own FAQ worth knowing: **the Claude GitHub App deliberately lacks workflow write access**, so Claude cannot modify CI/CD configuration; and **Claude only creates and pushes commits** — it does not merge, rebase, force push, or perform other destructive git operations.

**Never commit API keys.** Use `${{ secrets.ANTHROPIC_API_KEY }}` and grant only the permissions the job needs.

---

## 3. GitLab CI/CD

The Agent SDK runs in GitLab pipelines the same way it does in Actions: a job invokes `claude -p` with provider credentials from CI variables.

The `--from-pr` flag and the session picker's PR search **accept GitLab MR URLs** alongside GitHub and Bitbucket, so local sessions can be found from a merge request link.

See `/docs/en/gitlab-ci-cd` for the pipeline configuration specifics.

---

## 4. Claude Code on the web

Sessions run on **isolated Anthropic-managed VMs**, one per session. The security properties, from the security documentation:

- Network access **limited by default and configurable**
- **Credential protection through a proxy**: a scoped credential inside the sandbox is translated to your real GitHub token outside it
- **`git push` restricted to the current working branch**
- Audit logging, and **automatic VM reclamation**

Entry points:

| Command | Effect |
|---|---|
| `claude --cloud "<task>"` | Create a new web session with that task description |
| `/teleport`, `claude --teleport` | **Pull a web session into your local terminal** |
| `/autofix-pr [prompt]` | Spawn a cloud session that watches a PR and pushes fixes when CI fails or reviewers comment. **Requires `gh`** |
| Plan approval → "refine with Ultraplan" | Continue planning in a cloud session |

Two configuration consequences: **device-deployed managed settings do not reach cloud sessions**, so an `availableModels` allowlist must arrive via server-managed settings; and **`defaultMode: "bypassPermissions"` and `"dontAsk"` are ignored** there, so a repository cannot start a cloud session in bypass mode.

**On mobile web sessions, editing and retrying a flagged prompt is not supported** — switch models, or continue from a desktop browser or the desktop app.

---

## 5. Remote Control

**The web interface connects to a process on your local machine.** All code execution and file access stays local.

| Property | Detail |
|---|---|
| Transport | Anthropic API over TLS |
| Transcript | **Stored on Anthropic servers while connected**, to sync across devices |
| Sandboxing | **None involved** — no cloud VMs |
| Credentials | **Multiple short-lived, narrowly scoped credentials expiring independently** |

Entry points: `claude --remote-control` (or `--rc`) for an interactive session with it enabled, `/remote-control` (or `/rc`) to enable it mid-session, `claude remote-control` for server mode with no local interactive session, and `--remote-control-session-name-prefix` to control auto-generated names (defaulting to the hostname).

Hooks can detect it: **`$CLAUDE_CODE_BRIDGE_SESSION_ID`** holds the Remote Control session ID while connected (v2.1.199+), and `$CLAUDE_CODE_REMOTE` is `"true"` in remote web environments.

`/config` works over Remote Control, and `/color` syncs the prompt bar color to claude.ai/code.

**Eligibility is reported by `claude doctor`.**

---

## 6. Desktop and IDE

**Desktop app**: `/desktop` (alias `/app`) continues the current session there. **macOS or x64 Windows with a subscription.** Notable behaviors covered elsewhere in these guides: **every new desktop session gets its own worktree automatically**, and the Desktop Code tab hosts SSH sessions that **read managed settings from the remote host they run on**.

The desktop app also carries a **plugin browser**, which is the fallback when `/plugin` reports it is unavailable in your environment.

**IDE extensions**: `--ide` auto-connects on startup **when exactly one valid IDE is available**; `/ide` manages integrations. `terminal.type` in telemetry distinguishes `vscode` and `cursor`; `app.entrypoint` distinguishes `claude-vscode`.

One consequential detail from the tools reference: **`EndConversation` requires an interactive terminal session, which includes a `claude` session in an IDE's integrated terminal** — the way the JetBrains plugin runs. Other surfaces do not get it.

**Mobile**: `/mobile` (aliases `/ios`, `/android`) shows a QR code. Background agents can be monitored and dispatched from the mobile app, and `PushNotification` reaches your phone under Remote Control.

---

## 7. Scheduled and triggered work

Several mechanisms, easy to conflate:

| Mechanism | Scope |
|---|---|
| **`CronCreate` / `CronDelete` / `CronList` tools** | **Session-scoped** scheduled prompts. Restored on resume if unexpired |
| **`RemoteTrigger` tool** | Manages **Routines** on claude.ai. Backs `/schedule` |
| **`/loop [interval] [prompt]`** | Repeats while the session is open. **No interval means Claude self-paces** via `ScheduleWakeup`. No prompt runs a maintenance check or `.claude/loop.md` |
| **GitHub Actions `schedule:` cron** | Runs on GitHub's infrastructure, independent of any session |
| **`/autofix-pr`** | Event-driven cloud session reacting to CI and reviews |

**Session-scoped scheduling dies with the session.** For work that must happen whether or not you have Claude Code open, use Routines or CI cron.

One picker consequence worth remembering: **sessions whose first prompt was `/loop` are hidden from the session picker** (v2.1.211+), and on earlier versions an early `/loop` hid the session permanently.

---

## 8. Choosing a surface

| Need | Use |
|---|---|
| Interactive development | CLI |
| Reaction to PR and issue activity | GitHub Actions |
| Automatic review on every PR | GitHub Code Review |
| Deterministic scripted runs | `claude --bare -p` in CI |
| Work while away from your machine, on Anthropic infrastructure | Claude Code on the web |
| Work while away from your machine, **on your own machine** | Remote Control |
| Long-running work you check on later | Background sessions plus agent view |
| Recurring work independent of any session | Routines or CI cron |

---

## 9. Gotchas

1. **Claude Code on the web runs on Anthropic VMs; Remote Control runs on your machine.** Same interface, different trust model.
2. **`/install-github-app` quickstart is direct-API only.** Bedrock and Agent Platform need manual setup.
3. **You must be a repository admin** to install the app and add secrets.
4. **v1.0 is a breaking change from beta.** Four edits, and `mode:` must be deleted rather than updated.
5. **Mode is auto-detected** from whether `prompt` is present.
6. **`@claude`, not `/claude`.** The most common no-response cause.
7. **CI does not run on Claude's commits when the Actions user pushes them.** Use the GitHub App or a custom app.
8. **The Claude GitHub App deliberately lacks workflow write access**, so Claude cannot edit CI configuration.
9. **Claude only creates and pushes commits** — never merges, rebases, or force pushes.
10. **A repository skill needs `actions/checkout` before the action step.**
11. **Plugin skills need the namespaced form**, `/plugin-name:skill-name`.
12. **A static credential silently disables Workload Identity Federation.** Do not set both.
13. **The WIF audience defaults to `https://api.anthropic.com`.** Match your rule.
14. **Inline comment classification is skipped under federation**, and unconfirmed comments post directly.
15. **Use the full action, not the base action, for untrusted input** such as fork PRs and external comments.
16. **Both cloud-provider workflows need `id-token: write`.**
17. **Bedrock model IDs carry a region prefix.**
18. **Costs are two-sided**: Actions minutes plus API tokens. Add concurrency controls.
19. **Device-deployed managed settings never reach cloud sessions.** Use server-managed settings.
20. **Cloud sessions ignore `defaultMode: "bypassPermissions"` and `"dontAsk"`.**
21. **Cloud `git push` is restricted to the current working branch.**
22. **Remote Control stores the transcript on Anthropic servers while connected**, unlike a purely local session.
23. **`--ide` connects only when exactly one valid IDE is available.**
24. **`EndConversation` needs an interactive terminal**, which an IDE integrated terminal satisfies but other surfaces do not.
25. **Cron tools are session-scoped.** Use Routines or CI cron for work that must outlive the session.
26. **A session starting with `/loop` disappears from the picker.**
27. **Flagged-prompt editing is unsupported on mobile web sessions.**

---

## Reference links

- GitHub Actions: https://code.claude.com/docs/en/github-actions
- Claude Code Action repository: https://github.com/anthropics/claude-code-action
- Action setup guide: https://github.com/anthropics/claude-code-action/blob/main/docs/setup.md
- Action security documentation: https://github.com/anthropics/claude-code-action/blob/main/docs/security.md
- Action examples: https://github.com/anthropics/claude-code-action/tree/main/examples
- GitHub Code Review: https://code.claude.com/docs/en/code-review
- GitLab CI/CD: https://code.claude.com/docs/en/gitlab-ci-cd
- Claude Code on the web: https://code.claude.com/docs/en/claude-code-on-the-web
- Desktop app: https://code.claude.com/docs/en/desktop
- Scheduled tasks: https://code.claude.com/docs/en/scheduled-tasks
- Agent view: https://code.claude.com/docs/en/agent-view
- Run Claude Code programmatically: https://code.claude.com/docs/en/headless
- Agent SDK overview: https://code.claude.com/docs/en/agent-sdk/overview
- Security: https://code.claude.com/docs/en/security
- Server-managed settings: https://code.claude.com/docs/en/server-managed-settings
- CLI reference: https://code.claude.com/docs/en/cli-reference
- Full docs index: https://code.claude.com/docs/llms.txt
