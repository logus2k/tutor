# Claude Code: Sandboxing and Security Reference

A study guide to Claude Code's OS-level Bash sandbox and its broader security posture: the two isolation layers, sandbox modes, credential protection, managed enforcement, the prompt-injection threat model, and the documented limitations.

Verified against code.claude.com/docs/en/sandboxing and /security.

---

## 1. What the sandbox is and is not

The sandbox lets Claude run most shell commands without stopping to ask permission. Instead of approving each command, **you define which files and network domains commands can touch, and the operating system enforces that boundary** for every Bash command and its child processes.

### The critical distinction from permissions

| | Permissions | Sandboxing |
|---|---|---|
| **Controls** | Which tools Claude Code can use | What a Bash command can access once it runs |
| **Applies to** | Every tool: Bash, Read, Edit, WebFetch, MCP | **Bash commands and their child processes only** |
| **Enforced** | Before the command runs, based on the command string | By the OS, on the running process |
| **Holds when** | The model chose something the string analysis understood | **Regardless of what the model chose**, and even if an allowed command does more than its name suggests |

That last row is the whole point. Permission rules reason about a command string; the sandbox constrains the process. A `Bash(npm test *)` allow rule cannot know what `npm test` actually does; the sandbox does not need to.

**`/sandbox` is not a permission mode.** Compare:

| | Controls | What replaces the prompt |
|---|---|---|
| `/sandbox` | What a Bash command can access once it runs | The sandbox boundary itself, in auto-allow mode |
| Auto mode | Whether each tool call runs | A classifier reviewing actions |
| `--dangerously-skip-permissions` | Whether each tool call runs | **Nothing** |

The sandbox's **auto-allow mode** and the permission system's **auto mode** are different things that can be combined: auto-allow approves Bash because the boundary contains it; auto mode uses a classifier to judge intent.

---

## 2. Platform support and setup

| Platform | Mechanism | Setup |
|---|---|---|
| macOS | **Seatbelt** | Nothing to install |
| Linux | **bubblewrap** | `bubblewrap` and `socat` packages |
| WSL2 | bubblewrap | Same as Linux |
| WSL1 | **Not supported** | bubblewrap needs kernel features WSL2 has |
| Native Windows | **Not supported** | Run inside WSL2 |

```bash
sudo apt-get install bubblewrap socat   # Ubuntu/Debian
sudo dnf install bubblewrap socat       # Fedora
```

Dependencies checked: `ripgrep` (bundled with the native binary), `bubblewrap`, `socat`, and an **optional seccomp filter** that adds Unix domain socket blocking. Install the filter with `npm install -g @anthropic-ai/sandbox-runtime`.

**The dependency check runs at startup**, so restart Claude Code after installing packages before `/sandbox` will detect them.

The `/sandbox` panel has three tabs, plus **Dependencies** when something is missing:

| Tab | Purpose |
|---|---|
| **Mode** | Auto-allow versus regular permissions |
| **Overrides** | Whether failed commands may fall back to unsandboxed (`allowUnsandboxedCommands`) |
| **Config** | Resolved sandbox settings |
| **Dependencies** | Appears when a package is missing. **When a required one is missing it is the only tab shown**; when only the optional seccomp filter is missing it appears alongside the others |

### Ubuntu 24.04+ AppArmor

The default AppArmor policy prevents bubblewrap creating user namespaces. Check:

```bash
sysctl kernel.apparmor_restrict_unprivileged_userns
```

`0` or "No such file or directory" means you are fine. `1` means add a profile:

```bash
sudo tee /etc/apparmor.d/bwrap > /dev/null <<'EOF'
abi <abi/4.0>,
include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/bwrap>
}
EOF
sudo systemctl reload apparmor
```

**The profile applies to `bwrap` itself, not to the commands it runs inside the sandbox.**

### WSL2 specifics

Check with `wsl -l -v`. A `Sandboxing requires WSL2` message means you are on WSL1.

**On WSL2, sandboxed commands cannot launch Windows binaries**: `cmd.exe`, `powershell.exe`, anything under `/mnt/c/`. WSL hands these to the Windows host over a Unix socket, which the sandbox blocks. Add such commands to `excludedCommands`.

### Where the mode is saved

Selecting a mode in the panel writes to **`.claude/settings.local.json`**, scoped to the current project, and Claude Code adds that file to your global gitignore. For all projects, set `sandbox.enabled: true` in `~/.claude/settings.json`.

**By default, if the sandbox cannot start, Claude Code warns and runs commands unsandboxed.** Set `sandbox.failIfUnavailable: true` to make that a hard failure.

---

## 3. Sandbox modes

**Auto-allow mode**: a sandboxable command runs inside the sandbox and is approved automatically. Commands that cannot be sandboxed fall back to the regular permission flow.

**Regular permissions mode**: every Bash command goes through the normal flow even when sandboxed.

**Both modes enforce identical filesystem and network restrictions.** The only difference is auto-approval.

### What still applies in auto-allow mode

- **Explicit deny rules are always respected**
- **`rm`/`rmdir` targeting `/`, home, or critical system paths** still prompt, or go to the classifier in auto mode (v2.1.218+)
- **Content-scoped ask rules** like `Bash(git push *)` still force a prompt, even for sandboxed commands
- **A bare `Bash` ask rule, or `Bash(*)`, is skipped for sandboxed commands.** It still applies to commands that fall back. **In plan mode the skip does not apply** (v2.1.212+): a bare ask rule prompts for sandboxed commands including read-only ones

Auto-allow works **independently of your permission mode**, with plan mode as the one exception. Even outside accept-edits mode, sandboxed Bash commands that modify files inside the boundary run without prompting, **even when file edit tools would normally require approval**.

In plan mode (v2.1.212+), auto-allow does **not** widen approvals: commands outside the built-in read-only set prompt, or go to the classifier when auto mode is available with `useAutoModeDuringPlan` on.

### $TMPDIR asymmetry

The session temp directory is writable by default alongside the working directory, and **`$TMPDIR` is set to it for sandboxed commands**. Unsandboxed commands inherit your shell's `$TMPDIR` unchanged, so **the two resolve to different directories**. To pass temp files between them, write under the working directory instead.

### The escape hatch

When a command fails because of sandbox restrictions, **Claude analyzes the failure and may retry with the `dangerouslyDisableSandbox` parameter**. The retried command runs outside the sandbox through the regular permission flow: a prompt in default mode, the classifier in auto mode.

To be prompted on every unsandboxed retry even in auto mode, add an ask rule for `Bash(dangerouslyDisableSandbox:true)`.

To remove the escape hatch, set `allowUnsandboxedCommands: false`. The `/sandbox` Overrides tab calls this **Strict sandbox mode**: the parameter is completely ignored and everything must run sandboxed or be listed in `excludedCommands`.

---

## 4. Filesystem isolation

Defaults:

| Behavior | Default |
|---|---|
| **Write** | Working directory and subdirectories, plus the session temp directory |
| **Read** | **The entire computer**, except denied directories |
| **Blocked** | Modifying anything outside the write scope, including `~/.bashrc` and `/bin/` |

**Read the default read behavior twice.** It still allows reading `~/.aws/credentials` and `~/.ssh/`. Use `sandbox.credentials` or `denyRead` to block them.

**Git worktrees**: when the working directory is a linked worktree, the sandbox also allows writes to the main repository's shared `.git` directory so `git commit` can update refs and the index. **Writes to `hooks/` and `config` inside it remain denied.**

### Path prefixes

| Prefix | Meaning |
|---|---|
| `/` | **Absolute** from filesystem root |
| `~/` | Relative to home |
| `./` or none | Project root for project settings, or `~/.claude` for user settings |

**This syntax differs from Read and Edit permission rules**, which use `//path` for absolute and `/path` for project-relative. Sandbox paths use standard conventions: `/tmp/build` is genuinely absolute. Do not carry habits between the two.

Arrays **merge across settings scopes**; paths from every scope combine rather than replace.

### Overlap resolution

**The more specific path wins**, and this cuts both ways:

| Rules | Result |
|---|---|
| `denyRead: ["~/"]` + `allowRead: ["~/projects"]` | `~/projects` readable, rest of home blocked. **A narrower allow re-opens part of a denied region** |
| `allowRead: ["~/"]` + `denyRead: ["~/.env"]` | `~/.env` blocked, rest readable. **An exact deny holds inside a wider allow**, so a broad allow cannot silently re-expose a secret |

```json
{
  "sandbox": {
    "enabled": true,
    "filesystem": { "denyRead": ["~/"], "allowRead": ["."] }
  }
}
```

**Placement matters here.** The `.` resolves to the project root only because this lives in project settings. In `~/.claude/settings.json` it would resolve to `~/.claude` and project files would stay blocked.

### Disabling the filesystem layer

`sandbox.filesystem.disabled: true` (v2.1.216+) skips filesystem isolation while keeping network isolation. Use it when you sandbox to control **where commands connect** rather than **what they write**.

**The risk, stated plainly in the docs:** with filesystem isolation off and commands auto-allowed, a sandboxed command can write files that later commands run or read — shell startup files, executables on `$PATH`, `~/.claude/settings.json` — and **use them to widen its own access on the next run**. Locking network domains narrows this but does not remove it.

**Which sources may set it**, because it widens capability:

- User settings, managed settings, and `--settings` can. **Project and local settings cannot**, so a checked-out repository cannot switch it off
- **When managed settings configure `sandbox.filesystem` at all, or list any `credentials.files` entry, only managed settings can set it**
- **When `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` is set, `filesystem.disabled` is ignored from every source including managed settings**

What changes when it is off:

- `filesystem.denyRead` and `credentials.files` read protections **stop applying**, since the filesystem layer enforces both. **`credentials.envVars` deny and mask still apply**, because env scrubbing is independent
- Sandboxed commands inherit your shell's `$TMPDIR`
- `autoAllowBashIfSandboxed` still defaults to `true`

---

## 5. Network isolation

Network access goes through a proxy running **outside** the sandbox.

- **No domains are pre-allowed by default.** The first command needing a new domain prompts. As of v2.1.191, approving allows that host **for the rest of the session**
- **`WebFetch` allow rules also pre-allow domains** for the sandbox
- **`strictAllowlist: true`** (v2.1.219+) denies out-of-allowlist hosts instead of prompting. Honored from user, managed, and `--settings` only; **a repository's settings have no effect**. Applies to sandboxed commands only, not in-process tools like WebFetch
- **`allowManagedDomainsOnly`** in managed settings blocks non-allowed domains automatically and honors only managed `allowedDomains` and `WebFetch` allow rules
- **`deniedDomains`** blocks specific domains even when a broader wildcard would permit them
- Restrictions cover **all scripts, programs, and subprocesses**

### TLS and the domain-fronting caveat

**By default the built-in proxy does not terminate or inspect TLS.** It makes its allow decision from the **client-supplied hostname**.

The consequence, quoted in substance from the docs: allowing broad domains such as `github.com` can create exfiltration paths, and **code inside the sandbox can potentially use domain fronting or similar techniques to reach hosts outside the allowlist**. If your threat model needs stronger guarantees, configure a custom proxy that terminates TLS and inspects traffic, and install its CA inside the sandbox.

`network.tlsTerminate` (experimental, v2.1.199+) makes the built-in proxy terminate TLS, which credential masking requires, **but does not add content filtering**.

### Custom proxy

```json
{ "sandbox": { "network": { "httpProxyPort": 8080, "socksProxyPort": 8081 } } }
```

When these ports are set, Claude Code routes sandboxed traffic through your proxy instead of running its own.

---

## 6. Credential protection

`sandbox.credentials` (v2.1.187+) declares files and environment variables to protect from sandboxed commands. Kept separate from general filesystem rules deliberately.

```json
{
  "sandbox": {
    "enabled": true,
    "credentials": {
      "files": [
        { "path": "~/.aws/credentials", "mode": "deny" },
        { "path": "~/.ssh", "mode": "deny" }
      ],
      "envVars": [
        { "name": "GITHUB_TOKEN", "mode": "deny" },
        { "name": "NPM_TOKEN", "mode": "deny" }
      ]
    }
  }
}
```

**There is no built-in credential deny list.** Only what you list is restricted, and the default read policy allows credential files. This is the single most common gap in a fresh sandbox setup.

`deny` on files blocks sandbox reads (part of the filesystem layer, so **it does not apply when filesystem isolation is off**). `deny` on env vars unsets them before each sandboxed command (**independent of the filesystem layer, so it still applies**).

Files support `deny` only. **A `deny` entry only narrows access, so any scope can add one and no scope can remove one another scope added.**

### Masking

`"mode": "mask"` (v2.1.199+) keeps authenticating tools working. `deny` removes the variable entirely, which breaks `gh` or `npm`.

With `mask`, the command sees a **per-session sentinel**. When a request leaves the sandbox for one of the credential's `injectHosts`, **the proxy substitutes the real value**. The command and its logs never hold the real credential; its requests still authenticate.

```json
{
  "sandbox": {
    "network": { "tlsTerminate": {}, "allowedDomains": ["*.github.com", "registry.npmjs.org"] },
    "credentials": {
      "envVars": [
        { "name": "GH_TOKEN", "mode": "mask", "injectHosts": ["api.github.com"] },
        { "name": "NPM_TOKEN", "mode": "mask" }
      ]
    }
  }
}
```

Constraints:

- **`network.tlsTerminate` is required**, since the proxy must see request contents. Without it masking **fails closed**: the sentinel reaches the server unchanged and authentication fails. Claude Code reports the misconfiguration at startup
- No `injectHosts` means substitution on **every** host in `allowedDomains`
- **Each `injectHosts` entry must itself be covered by `allowedDomains`**
- Because masking **authorizes the proxy to send your real credential**, `mask` entries, `tlsTerminate`, and `allowPlaintextInject` are honored only from user, managed, and `--settings`. **A repository's settings are ignored**
- **`deny` in any scope beats `mask`**

To strip Anthropic and cloud provider credentials from **all** subprocesses regardless of sandboxing, set `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`.

---

## 7. How the layers combine

Paths and domains from **both** sandbox settings and permission rules merge into the final configuration:

| Setting or rule | Effect |
|---|---|
| `sandbox.filesystem.allowWrite` | Subprocess write access outside the working directory |
| `sandbox.filesystem.denyWrite` / `denyRead` | Block subprocess access |
| `sandbox.filesystem.allowRead` | Re-allow within a `denyRead` region |
| `sandbox.filesystem.disabled` | Turn the whole layer off |
| **`Edit` allow rules** | Grant write access, **the same way `allowWrite` does** |
| **`Read` and `Edit` deny rules** | Block access to files or directories |
| **`WebFetch` allow/deny rules** | Control domain access |
| `sandbox.allowedDomains` | Which domains Bash can reach |
| `sandbox.deniedDomains` | Block despite a broader wildcard |

**Subagents run in the same process as the parent and use the same sandbox configuration.** Bash inside a subagent is sandboxed when the parent has sandboxing on.

### The mutual-dependency warning

Effective sandboxing **requires both layers**:

- **Without network isolation**, a compromised agent could exfiltrate SSH keys
- **Without filesystem isolation**, whether from a permissive policy or from disabling the layer, a compromised agent could **backdoor system resources to gain network access**

When you widen defaults, check that an `allowWrite` path, a broad `allowedDomains` entry, or an `excludedCommands` exception does not undo a restriction on the other side.

---

## 8. Organizational enforcement

```json
{
  "sandbox": {
    "enabled": true,
    "failIfUnavailable": true,
    "allowUnsandboxedCommands": false
  }
}
```

- **`failIfUnavailable`**: a missing dependency **blocks startup** rather than falling back to unsandboxed
- **`allowUnsandboxedCommands: false`**: the escape hatch is ignored entirely

### Boolean versus array keys

This asymmetry determines what a developer can undo:

- **Boolean keys** (`enabled`, `failIfUnavailable`): the managed value wins, local values ignored
- **Array keys** (`excludedCommands`, `allowRead`): **entries merge from every scope**, so a developer can append entries that widen the policy

Locks:

| Setting | Effect |
|---|---|
| `allowManagedReadPathsOnly` | Only managed `allowRead` honored |
| `allowManagedDomainsOnly` | Only managed domains honored; non-allowed blocked without prompting |
| Managed `sandbox.filesystem` or any `credentials.files` entry | Only managed settings may set `filesystem.disabled` |

**`excludedCommands` has no managed-only lockdown.** A developer can always append entries that run additional commands outside the sandbox. **Keep the managed list narrow.**

Also worth planning for: **the sandbox does not run on native Windows**, so scope the configuration to macOS and Linux or require WSL2 / containers.

---

## 9. Security posture beyond the sandbox

### Default architecture

**Strict read-only by default.** Additional actions request explicit permission.

**Working directory boundary**: Claude Code can write only to its start folder and subfolders. Reading outside it with Read, Grep, and Glob is possible **after an approval prompt**. Extend with additional directories to skip the prompt; restrict the broader read access available to read-only Bash commands with sandbox `denyRead` rules, which **apply only when sandboxing is enabled**.

### Prompt-injection safeguards

| Safeguard | What it does |
|---|---|
| Permission system | Sensitive operations require approval |
| Context-aware analysis | Detects harmful instructions by analyzing the full request |
| Input sanitization | Prevents command injection |
| **Network command approval** | `curl`, `wget`, and similar are **not auto-approved by default**. To block entirely, add them to `permissions.deny` |
| **Isolated context windows** | **Web fetch uses a separate context window** to avoid injecting malicious prompts |
| **Command injection detection** | Suspicious Bash commands require manual approval **even if previously allowlisted** |
| **Fail-closed matching** | Unmatched commands default to manual approval |
| Natural language descriptions | Complex commands come with explanations |
| Secure credential storage | macOS Keychain when available; file permissions on Windows and Linux |

### Trust verification

First-time codebase runs and new MCP servers require trust verification. Two caveats:

- **Trust verification is disabled when running non-interactively with `-p`**
- **Starting Claude Code directly in your home directory holds trust for the session only**, never written to disk, so the prompt reappears each launch. **There is no setting to persist it.** Start from a project subdirectory instead

### Windows WebDAV

Anthropic recommends **against** enabling WebDAV or allowing access to paths such as `\\*` that may contain WebDAV subdirectories. WebDAV is deprecated by Microsoft, and **enabling it may let Claude Code trigger network requests to remote hosts, bypassing the permission system**.

### MCP trust model

The docs are direct: **Anthropic reviews Directory connectors against listing criteria but does not security-audit or manage any MCP server.** Write your own or use providers you trust, and configure permissions per server.

### Cloud execution

Claude Code on the web adds: isolated Anthropic-managed VMs per session, network access limited by default and configurable, **credential protection through a proxy using a scoped credential inside the sandbox translated to your real GitHub token**, **git push restricted to the current working branch**, audit logging, and automatic VM reclamation.

**Remote Control is different**: the web interface connects to a process on your local machine. All code execution and file access stays local; traffic goes through the Anthropic API over TLS, and while connected the transcript is stored on Anthropic servers to sync across devices. **No cloud VMs or sandboxing are involved.** The connection uses multiple short-lived, narrowly scoped credentials expiring independently.

### Practices the docs recommend

Working with untrusted content: review commands before approval; **avoid piping untrusted content directly to Claude**; verify changes to critical files; **use VMs for scripts and tool calls involving external web services**; report suspicious behavior with `/feedback`.

Team: managed settings for standards, share permission configs in version control, monitor with OpenTelemetry, and **audit or block settings changes during sessions with `ConfigChange` hooks**.

Vulnerability reports go through Anthropic's HackerOne program, not public disclosure.

---

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| Host-not-allowed errors | Grant permission when prompted; the host is added to the allowed list |
| **`jest` hangs or fails** | `watchman` is incompatible. Use `jest --no-watchman` |
| **Go CLIs fail TLS on macOS** (`gh`, `gcloud`, `terraform`) | Seatbelt breaks their TLS verification. Add to `excludedCommands`, or with a MITM proxy and custom CA set `enableWeakerNetworkIsolation: true` |
| **`open`, `osascript`, browser auth fail with error `-600`** | Apple Events are blocked by default. `allowAppleEvents: true` from user, managed, or CLI settings only. **This removes code-execution isolation** |
| **`docker` fails** | Incompatible. Add `docker *` to `excludedCommands` |
| **bubblewrap fails inside a container** | It cannot mount a fresh `/proc`. `enableWeakerNestedSandbox: true`, only when the outer container already provides the boundary |
| Seccomp filter missing | `npm install -g @anthropic-ai/sandbox-runtime`, then restart |
| **`--dangerously-skip-permissions` fails as root** | Blocked as root or sudo on Linux and macOS. Skipped inside a recognized sandbox. Use the dev container config, which runs as non-root |

---

## 11. Documented limitations

The docs are explicit that **sandboxing reduces risk but is not a complete isolation boundary**.

**Network filtering**: no TLS termination or inspection by default, so encrypted contents are not examined. Domain fronting is possible. `tlsTerminate` enables masking but adds no content filtering. **You are responsible for ensuring only trusted domains are allowed.**

**Unix socket escalation**: `allowUnixSockets` can grant access to powerful services. Allowing `/var/run/docker.sock` **effectively grants host access**.

**Filesystem escalation**: broad write permissions to `$PATH` directories, system config, or `.bashrc`/`.zshrc` can lead to code execution in other security contexts.

**Linux weakening**: `enableWeakerNestedSandbox` makes the sandbox work in Docker without privileged namespaces or where unprivileged user namespaces are disabled. It **considerably weakens security** and should be used only when isolation is enforced elsewhere.

**Apple Events**: `allowAppleEvents` lets sandboxed commands **launch other applications unsandboxed with no user prompt**, and send AppleScript to running applications subject to macOS TCC.

**Settings files are protected**: the sandbox denies writes to `settings.json` at every scope and to the managed settings directory, so a sandboxed command cannot modify its own policy — **unless you disable filesystem isolation, which turns these deny rules off**. As of v2.1.210 the deny rules resolve symlinks, so a symlinked settings file cannot be edited through the link.

### Scope boundaries

The sandbox isolates **Bash subprocesses only**:

- **Read, Edit, Write** use the permission system directly, not the sandbox
- **Computer use** runs on your actual desktop, gated by per-app prompts
- **Environment variables** are inherited by default including credentials. Use `sandbox.credentials` or `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`
- **Subagents** share the parent's sandbox configuration

---

## 12. Gotchas

1. **The default read policy allows the entire filesystem**, including `~/.aws/credentials` and `~/.ssh/`. There is **no built-in credential deny list**.
2. **Sandbox path prefixes differ from permission rule paths.** `/tmp/build` is absolute here; in a Read rule it would anchor at the settings source.
3. **`.` resolves relative to the settings source**, so the same `allowRead: ["."]` means different things in project versus user settings.
4. **A narrower allow re-opens a denied region, and an exact deny holds inside a wider allow.** Both directions work.
5. **By default a sandbox that cannot start silently falls back to unsandboxed.** Set `failIfUnavailable`.
6. **`$TMPDIR` differs between sandboxed and unsandboxed commands.** Pass files through the working directory.
7. **The escape hatch exists by default.** Claude may retry a failed command with `dangerouslyDisableSandbox`. Set `allowUnsandboxedCommands: false` to remove it.
8. **The proxy decides from the client-supplied hostname without inspecting TLS**, so domain fronting can reach hosts outside your allowlist.
9. **Masking fails closed without `tlsTerminate`**: authentication breaks rather than leaking, but it does break.
10. **`deny` beats `mask`** in any scope, and repository settings cannot set `mask`, `tlsTerminate`, or `allowPlaintextInject`.
11. **Boolean sandbox keys are managed-wins; array keys merge**, so developers can widen `excludedCommands` and `allowRead` unless you set the managed-only locks.
12. **`excludedCommands` has no managed-only lock at all.**
13. **Disabling filesystem isolation also disables `denyRead` and `credentials.files`**, but not `credentials.envVars`.
14. **`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` overrides `filesystem.disabled` from every source**, including managed settings.
15. **A bare `Bash` ask rule is skipped for sandboxed commands** outside plan mode.
16. **Auto-allow bypasses your permission mode**, so file-modifying Bash runs unprompted even when edit tools would prompt.
17. **WSL2 sandboxed commands cannot launch Windows binaries.** WSL1 and native Windows are unsupported entirely.
18. **`docker` and `watchman` are simply incompatible** with the sandbox.
19. **`allowAppleEvents` removes code-execution isolation**, and `enableWeakerNestedSandbox` considerably weakens Linux isolation.
20. **Allowing `/var/run/docker.sock` through `allowUnixSockets` effectively grants host access.**
21. **Trust verification is disabled under `-p`**, and home-directory trust is never persisted.
22. **Anthropic does not security-audit MCP servers**, including Directory ones.
23. **The sandbox does not cover Read, Edit, Write, or computer use.** Those are permission-system territory.
24. **Restart after installing sandbox dependencies**, since the check runs at startup.

---

## Reference links

- Sandboxing: https://code.claude.com/docs/en/sandboxing
- Sandbox environments (containers, VMs, dev containers): https://code.claude.com/docs/en/sandbox-environments
- Security: https://code.claude.com/docs/en/security
- Permissions: https://code.claude.com/docs/en/permissions
- Permission modes: https://code.claude.com/docs/en/permission-modes
- Settings, sandbox settings: https://code.claude.com/docs/en/settings
- Development containers: https://code.claude.com/docs/en/devcontainer
- Security guidance plugin: https://code.claude.com/docs/en/security-guidance
- Monitoring usage: https://code.claude.com/docs/en/monitoring-usage
- Authentication and credential management: https://code.claude.com/docs/en/authentication
- Example settings: https://github.com/anthropics/claude-code/tree/main/examples/settings
- sandbox-runtime package: https://github.com/anthropic-experimental/sandbox-runtime
- Anthropic Trust Center: https://trust.anthropic.com
- Full docs index: https://code.claude.com/docs/llms.txt
