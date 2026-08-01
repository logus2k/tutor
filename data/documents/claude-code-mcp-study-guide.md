# Claude Code: MCP Reference

A study guide to how Claude Code implements and integrates with the Model Context Protocol: transports, configuration, scopes, authentication, permissions, context scaling, enterprise control, and the server-author surface.

Verified against the official documentation at code.claude.com/docs (mcp, managed-mcp, settings, claude-directory, plugins-reference).

---

## 1. What MCP is here

MCP is an open standard for connecting AI agents to external tools and data. **Claude Code acts as an MCP client.** Servers expose three kinds of capability, and Claude Code consumes all three:

| Capability | How it surfaces in Claude Code |
|---|---|
| **Tools** | Called like built-in tools, named `mcp__<server>__<tool>` |
| **Resources** | Referenced with `@server:protocol://path` in your prompt |
| **Prompts** | Become slash commands: `/mcp__servername__promptname` |

Plus two Claude Code extensions to the base picture:

| Extension | What it does |
|---|---|
| **Elicitation** | A server requests structured input from you mid-task; Claude Code renders a dialog |
| **Channels** | A server declares `claude/channel` and pushes messages into your session unprompted |

The rule of thumb from the docs: connect a server when you find yourself copying data into chat from another tool. Once connected, Claude reads and acts on that system directly instead of working from what you paste.

Browse reviewed connectors in the Anthropic Directory (claude.ai/directory). Note the caveat: Anthropic reviews Directory connectors against listing criteria but **does not security-audit or manage any MCP server.** Servers that fetch external content expose you to prompt injection risk.

---

## 2. Transports

| Transport | `type` value | When to use | OAuth support |
|---|---|---|---|
| **HTTP** | `http` (alias `streamable-http`) | Recommended for remote servers. Most widely supported | Yes |
| **stdio** | `stdio` (the default when `type` is absent) | Local processes, direct system access, custom scripts | No |
| **SSE** | `sse` | **Deprecated.** Only for services that expose nothing else | Yes |
| **WebSocket** | `ws` | Remote servers that push events unprompted | No, header-only auth |

Two configuration traps:

1. **A JSON entry with a `url` but no `type` is an error.** Claude Code reads a typeless entry as stdio, skips the server, and reports `MCP server "<name>" has a "url" but no "type"; add "type": "http" (or "sse" / "ws")`. Before v2.1.202 this surfaced as the much less helpful `command: expected string, received undefined`.
2. **`--transport` does not accept `ws`.** Configure WebSocket servers with `add-json` or by editing `.mcp.json` directly.

### Adding servers

```bash
# HTTP (recommended)
claude mcp add --transport http notion https://mcp.notion.com/mcp

# HTTP with a bearer token
claude mcp add --transport http secure-api https://api.example.com/mcp \
  --header "Authorization: Bearer your-token"

# SSE (deprecated)
claude mcp add --transport sse asana https://mcp.asana.com/sse

# stdio: note the -- separator
claude mcp add --env AIRTABLE_API_KEY=YOUR_KEY --transport stdio airtable \
  -- npx -y airtable-mcp-server

# WebSocket: add-json only
claude mcp add-json events-server \
  '{"type":"ws","url":"wss://mcp.example.com/socket","headers":{"Authorization":"Bearer YOUR_TOKEN"}}'
```

**The `--` separator matters.** Everything after it is passed to the server untouched. Without it, Claude Code tries to parse the server's own flags as its own:

- `claude mcp add --transport stdio myserver -- npx server` runs `npx server`
- `claude mcp add --env KEY=value --transport stdio myserver -- python server.py --port 8080` runs `python server.py --port 8080` with `KEY=value` in the environment

Related quirk: `--env` accepts multiple `KEY=value` pairs, so if the server name comes directly after `--env` the CLI reads the name as another pair and rejects it. Put at least one other option between them.

Short forms: `-t` for `--transport`, `-H` for `--header`, `-s` for `--scope`, `-e` for `--env`.

### JSON server schema

```json
{
  "mcpServers": {
    "stdio-example": {
      "type": "stdio",
      "command": "/path/to/binary",
      "args": ["--flag", "value"],
      "env": { "KEY": "value" }
    },
    "http-example": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "headers": { "Authorization": "Bearer ${TOKEN}" },
      "headersHelper": "/opt/bin/get-headers.sh",
      "timeout": 600000,
      "alwaysLoad": true,
      "oauth": {
        "clientId": "your-client-id",
        "callbackPort": 8080,
        "scopes": "channels:read chat:write",
        "authServerMetadataUrl": "https://auth.example.com/.well-known/openid-configuration"
      }
    }
  }
}
```

`ws` entries accept the same `url`, `headers`, `headersHelper`, `timeout`, and `alwaysLoad` fields as `http`.

### Reserved server names

`workspace`, `claude-in-chrome`, `computer-use`, `Claude Preview`, `Claude Browser`. A configuration defining one of these is skipped at load time with a warning; `claude mcp add` rejects it outright. `Claude Browser` became reserved in v2.1.205.

Server names added through `claude mcp` commands may contain only letters, numbers, hyphens, and underscores.

---

## 3. Scopes and precedence

### The three user-controlled scopes

| Scope | Loads in | Shared with team | Stored in |
|---|---|---|---|
| **local** (default) | Current project only | No | `~/.claude.json`, under that project's path |
| **project** | Current project only | Yes, via version control | `.mcp.json` at the project root |
| **user** | All your projects | No | `~/.claude.json` |

```bash
claude mcp add --transport http stripe https://mcp.stripe.com                      # local
claude mcp add --transport http shared --scope project https://example.com/mcp     # project
claude mcp add --transport http hubspot --scope user https://mcp.hubspot.com/anthropic
```

**A naming trap worth memorising**: "local scope" for MCP means `~/.claude.json` in your **home** directory, not `.claude/settings.local.json` in your project. These are unrelated concepts that share a word. Older versions also called local scope "project" and user scope "global".

Local scope writes into the project's entry inside `~/.claude.json`:

```json
{
  "projects": {
    "/path/to/your/project": {
      "mcpServers": {
        "stripe": { "type": "http", "url": "https://mcp.stripe.com" }
      }
    }
  }
}
```

### Full precedence order

When the same server is defined in more than one place, Claude Code connects **once**, using the highest-precedence definition. **The entire entry from that source is used; fields are not merged across scopes.**

1. Local scope
2. Project scope
3. User scope
4. Plugin-provided servers
5. claude.ai connectors

The three scopes match duplicates **by name**. Plugins and connectors match **by endpoint**, so one pointing at the same URL or command as a server above is treated as a duplicate. When a server you added shadows a claude.ai connector, `/mcp` lists the connector as hidden and shows how to remove the duplicate.

### Project-scope approval

Project servers from `.mcp.json` are **not trusted automatically.** They appear in `claude mcp list` and `claude mcp get` as `⏸ Pending approval (run `claude` to approve)`.

Settings keys that control this:

| Key | Effect |
|---|---|
| `enableAllProjectMcpServers` | Auto-approve all `.mcp.json` servers |
| `enabledMcpjsonServers` | Approve specific server names |
| `disabledMcpjsonServers` | Reject specific server names (always wins) |

Reset approvals with `claude mcp reset-project-choices`.

**Workspace trust interaction (v2.1.196+)**: `claude mcp list` and `claude mcp get` read approvals only from settings files that are **not checked into the repository** until you trust the workspace. A cloned repo cannot approve its own servers, so `enableAllProjectMcpServers` committed to `.claude/settings.json` is ignored in an untrusted folder.

Approvals that still apply in an untrusted folder: your `~/.claude/settings.json`, managed settings, and settings passed with `--settings`. An untracked `.claude/settings.local.json` also applies, but only after you accept a trust dialog (Claude Code has to run git to check whether the file is tracked, and it runs that check only in a trusted folder). The exception is your own configuration home: your home directory, or a directory whose `.claude` you set as `CLAUDE_CONFIG_DIR`.

### Disabling without removing

Toggle a server off in `/mcp`. The choice is recorded **per project** in `~/.claude.json` in one of two disjoint lists:

| List | Covers |
|---|---|
| `disabledMcpServers` | Opt-out for user-configured servers, plugin servers, claude.ai connectors, and built-ins that default to **on** |
| `enabledMcpServers` | Opt-in for built-in servers that default to **off**, such as `computer-use` |

Claude Code consults exactly one list per server, so neither overrides the other. Putting a regular server in `enabledMcpServers` does nothing.

These are unrelated to `enabledMcpjsonServers` / `disabledMcpjsonServers`, which govern `.mcp.json` approval.

---

## 4. Environment variable expansion

Supported in `.mcp.json` (and in the same fields elsewhere):

- `${VAR}` expands to the variable's value
- `${VAR:-default}` expands to `VAR` if set, otherwise the default

Expansion applies to `command`, `args`, `env`, `url`, and `headers`.

```json
{
  "mcpServers": {
    "api-server": {
      "type": "http",
      "url": "${API_BASE_URL:-https://api.example.com}/mcp",
      "headers": { "Authorization": "Bearer ${API_KEY}" }
    }
  }
}
```

If a referenced variable is unset with no default, **the config still loads.** Claude Code reports a missing-variable warning in `claude mcp list` and uses the literal `${VAR}` text.

### CLAUDE_PROJECT_DIR for stdio servers

Claude Code sets `CLAUDE_PROJECT_DIR` in a spawned stdio server's environment, pointing at the project root, so your server can resolve project-relative paths without depending on cwd. Read it from inside the server process: `process.env.CLAUDE_PROJECT_DIR` or `os.environ["CLAUDE_PROJECT_DIR"]`.

Subtlety: this variable is set in the **server's** environment, not Claude Code's own, so referencing it via `${VAR}` expansion in a `.mcp.json` or `~/.claude.json` entry needs a default: `${CLAUDE_PROJECT_DIR:-.}`. Plugin-provided configurations substitute it directly and need no default.

For a server that limits its own filesystem access, implement the MCP `roots/list` request instead. Claude Code answers with the session's launch directory plus every additional working directory granted with `--add-dir`, `/add-dir`, or `additionalDirectories`, and sends `notifications/roots/list_changed` when that set changes (v2.1.203+; before that, only the launch directory was returned and no change notification was sent).

---

## 5. Authentication

### OAuth 2.0

The default path for remote servers. Claude Code marks a server as needing authentication when it responds `401 Unauthorized` or `403 Forbidden`.

```bash
claude mcp add --transport http sentry https://mcp.sentry.dev/mcp
# then, inside a session:
/mcp
```

Behaviors worth knowing:

- On a `401` from a server you already signed in to, Claude Code **refreshes the token, reconnects, and retries once**, flagging the server only if the retry also fails (v2.1.206+ handles transient refresh failures correctly).
- When the server rejects a stored refresh token, a notice points at `/mcp`, whose menu offers **Re-authenticate** (v2.1.195+).
- A startup notice lists servers needing sign-in, so you do not have to open `/mcp` to find out (v2.1.193+).
- **If you configured `headers.Authorization` and the server rejects it, Claude Code reports the connection as failed rather than falling back to OAuth.** Remove the header to use OAuth.
- In non-interactive mode there is no `/mcp`, so with tool search enabled Claude Code tells Claude the server's tools are unavailable pending authorization rather than acting as if the server were unconfigured (v2.1.196+).
- Discovery order: RFC 9728 Protected Resource Metadata at `/.well-known/oauth-protected-resource`, then RFC 8414 at `/.well-known/oauth-authorization-server`. A `WWW-Authenticate` header pointing at the authorization server gets the same treatment.

### Command-line login (v2.1.186+)

```bash
claude mcp login sentry
claude mcp login sentry --no-browser
claude mcp logout sentry
```

`--no-browser` prints the authorization URL instead of opening a browser; paste the full redirect URL back at the prompt. Auto-detected on SSH sessions and Linux without a display server (v2.1.191+). Connect with `ssh -t` so the paste step has an interactive terminal.

### Fixed callback port

```bash
claude mcp add --transport http --callback-port 8080 my-server https://mcp.example.com/mcp
```

By default Claude Code picks a random port. Fix it when the server requires a pre-registered redirect URI of the form `http://localhost:PORT/callback`. Usable with or without `--client-id`.

### Pre-configured OAuth credentials

For servers without Dynamic Client Registration (the error reads "Incompatible auth server: does not support dynamic client registration"). Claude Code also supports Client ID Metadata Documents and discovers those automatically.

```bash
claude mcp add --transport http \
  --client-id your-client-id --client-secret --callback-port 8080 \
  my-server https://mcp.example.com/mcp
```

`--client-secret` prompts with masked input. For CI, set `MCP_CLIENT_SECRET` in the environment to skip the prompt. The secret is stored in the system keychain (macOS) or a credentials file, **not in your config**. These flags apply only to HTTP and SSE.

Verify with `claude mcp get <name>`.

### Restricting OAuth scopes

```json
{
  "mcpServers": {
    "slack": {
      "type": "http",
      "url": "https://mcp.slack.com/mcp",
      "oauth": { "scopes": "channels:read chat:write search:read" }
    }
  }
}
```

A single space-separated string, RFC 6749 §3.3 format. This is **the supported way** to restrict a server to a security-approved subset. It takes precedence over both `authServerMetadataUrl` and discovered `/.well-known` scopes.

As of v2.1.196, when `oauth.scopes` is unset, Claude Code requests the scope from the server's `WWW-Authenticate` header or protected resource metadata, and sends no `scope` parameter when neither provides one. It no longer requests the full `scopes_supported` catalog, which used to make identity providers reject the authorization with `invalid_scope`.

If the authorization server advertises `offline_access`, Claude Code appends it so tokens refresh without a new sign-in. A later `403 insufficient_scope` triggers re-authentication with the **same** pinned scopes, so widen `oauth.scopes` yourself when a tool needs more.

### Overriding metadata discovery

```json
{ "oauth": { "authServerMetadataUrl": "https://auth.example.com/.well-known/openid-configuration" } }
```

Must be `https://`. Use it when the server's standard endpoints error or you route discovery through an internal proxy. Metadata fetched this way **does** supply its `scopes_supported` as the requested scopes.

### Dynamic headers (non-OAuth schemes)

For Kerberos, short-lived tokens, internal SSO.

```json
{
  "mcpServers": {
    "internal-api": {
      "type": "http",
      "url": "https://mcp.internal.example.com",
      "headersHelper": "/opt/bin/get-mcp-auth-headers.sh"
    }
  }
}
```

Inline form also works:

```json
{ "headersHelper": "echo '{\"Authorization\": \"Bearer '\"$(get-token)\"'\"}'" }
```

Requirements and behavior:

- Must write a JSON object of string key-value pairs to stdout
- Runs in a shell with a **10-second timeout**, from the session's cwd. Use an absolute path or a `PATH` command
- Dynamic headers override static `headers` of the same name
- **Runs fresh on every connection** with no caching. Your script owns any token reuse
- On a `401` or `403` from a tool call, Claude Code re-runs the helper, reconnects, and retries once (v2.1.193+)
- At project or local scope it runs **only after you accept the workspace trust dialog**, since it executes arbitrary shell

Environment variables Claude Code sets for the helper:

| Variable | Value |
|---|---|
| `CLAUDE_CODE_MCP_SERVER_NAME` | The server name |
| `CLAUDE_CODE_MCP_SERVER_URL` | The server URL |
| `CLAUDE_PLUGIN_ROOT` | Plugin root, only when a plugin provides the server |

For plugin-provided servers the helper's working directory is the plugin root, so relative paths resolve inside the plugin (v2.1.195+). A plugin `headersHelper` **cannot** reference `${user_config.*}` values, because the command is shell-parsed; put those in `headers` instead, which is not.

---

## 6. claude.ai connectors

If you signed into Claude Code with a claude.ai account, servers you added at claude.ai/customize/connectors are automatically available. On Team and Enterprise plans only admins can add them.

**They are fetched only when your active authentication method is a claude.ai subscription login.** They do not load when `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `apiKeyHelper`, a third-party provider (Bedrock, Google Cloud's Agent Platform), or a `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` is active, **even if you previously ran `/login`**. If a connector is missing, run `/status` to see which method is active, unset it, then `/login`.

From v2.1.161, connectors you have never signed in to collapse behind a `Show unused connectors` row so an org-provisioned list does not fill the panel.

Some Anthropic-hosted connectors (Microsoft 365, Gmail, Google Calendar) do not support local OAuth from Claude Code, because the upstream identity provider only accepts the redirect URL claude.ai registered. `/mcp` directs you to connect them in claude.ai settings instead (v2.1.162+).

### Organization controls on connector tools

Read at startup and enforced locally. `/mcp` shows which setting applies per tool.

| Setting | Behavior |
|---|---|
| **ask** | Prompts on every call with `Your organization requires approval for this tool`. Prompts even in `acceptEdits`, `auto`, and `bypassPermissions`. Never offers "remember". Allow rules do not skip it. In `dontAsk` mode the call is **denied** |
| **blocked** | Filtered out before Claude sees it; never appears in the tool list |

Requires v2.1.129+; earlier versions ignore the settings.

### Disabling connectors

```json
{ "disableClaudeAiConnectors": true }
```

**Any-source-true semantics**: `true` anywhere wins. A committed project `.claude/settings.json` can opt a repo out of cloud connectors, but a project-level `false` cannot re-enable what a user or policy `true` disabled. Servers passed via `--mcp-config` are unaffected.

Shell equivalent: `ENABLE_CLAUDEAI_MCP_SERVERS=false claude`

To block individual connectors, add them to `deniedMcpServers` by display name (`"claude.ai Slack"`) or URL pattern. To toggle one off for the current project only, use `/mcp`.

Note the Claude Code on the web exception: connectors there are provisioned by the remote host and arrive as explicit `--mcp-config` entries, so `disableClaudeAiConnectors` does not apply and connector URLs are rewritten through the session proxy (so a `serverUrl` deny pattern targeting the vendor URL will not match). Manage those from claude.ai org settings.

---

## 7. Plugin-provided servers

Plugins define MCP servers in `.mcp.json` at the plugin root or inline in `plugin.json`.

```json
{
  "mcpServers": {
    "database-tools": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/db-server",
      "args": ["--config", "${CLAUDE_PLUGIN_ROOT}/config.json"],
      "env": { "DB_URL": "${DB_URL}" }
    }
  }
}
```

Path placeholders and where substitution applies:

| Placeholder | Resolves to |
|---|---|
| `${CLAUDE_PLUGIN_ROOT}` | The plugin's installation directory |
| `${CLAUDE_PLUGIN_DATA}` | The plugin's persistent state directory |
| `${CLAUDE_PROJECT_DIR}` | The stable project root |

Substituted in: `command`, `args`, `env` for stdio; `url`, `headers`, `headersHelper` for http/sse/ws (`headersHelper` since v2.1.195).

Lifecycle: connected automatically at session startup for enabled plugins. Enabling or disabling a plugin mid-session requires `/reload-plugins`. On reload, Claude Code **keeps live connections** for plugin servers whose configuration is unchanged.

### The plugin tool-naming trap

Tools from a plugin-bundled server carry both the plugin name and the server key:

```
mcp__plugin_<plugin-name>_<server-name>__<tool-name>
```

Any character outside `A-Z a-z 0-9 _ -` is replaced with `_`. For a `query` tool on the `database-tools` server in plugin `my-plugin`:

```
mcp__plugin_my-plugin_database-tools__query
```

Use this full name in permission rules, a skill's `allowed-tools`, a subagent's `tools` field, or a hook matcher. **A hook matcher written against the bare server key, such as `mcp__database-tools__.*`, never fires for a plugin-bundled server.**

The server itself registers under the scoped name `plugin:<plugin-name>:<server-name>`, for example `plugin:my-plugin:database-tools`. Use that where a configured server name is expected, such as an `mcp_tool` hook's `server` field.

You add and remove plugin servers by installing or uninstalling the plugin, not with `/mcp` commands, though you can still toggle an installed one off in `/mcp`.

---

## 8. Enterprise control

Two independent mechanisms, often combined.

### Pattern selection

| Pattern | What it does | Configure |
|---|---|---|
| **Disable MCP** | No servers load anywhere | `managed-mcp.json` with an empty server map |
| **Fixed deployment** | Everyone gets the same set, cannot add others | `managed-mcp.json` with the servers you want |
| **Approved catalog** | Publish a list; users add what they want, anything else blocked | `allowedMcpServers` + `allowManagedMcpServersOnly: true` |
| **Plugin servers only** | Servers only from plugins | `strictPluginOnlyCustomization` with `mcp` in the list |
| **Soft allowlist** | Allowlist users can broaden themselves | `allowedMcpServers` without `allowManagedMcpServersOnly` |
| **Denylist only** | Block known-bad, allow everything else | `deniedMcpServers` |

Claude Code has **no built-in server registry** users can browse. For the approved-catalog pattern, publish the `claude mcp add` commands on an internal wiki, or distribute servers as plugins through a managed marketplace so users can install from `/plugin`.

### managed-mcp.json (exclusive control)

Deploying it means Claude Code loads **only** the servers that file defines. Users cannot add, modify, or use any other server, **including plugin-provided ones**. It also suppresses claude.ai connectors by default.

| Platform | Path |
|---|---|
| macOS | `/Library/Application Support/ClaudeCode/managed-mcp.json` |
| Linux and WSL | `/etc/claude-code/managed-mcp.json` |
| Windows | `C:\Program Files\ClaudeCode\managed-mcp.json` |

Same format as a project `.mcp.json`. It is a standalone file, so it **cannot** be delivered through server-managed settings; deploy it with MDM, Group Policy, Intune, Jamf, or any process with admin privileges.

Disable MCP entirely:

```json
{ "mcpServers": {} }
```

Users see no servers in `/mcp`, and `claude mcp add` fails with the enterprise-policy error. Previously configured servers stop loading with **no warning that policy is the reason**.

**Credentials caveat**: any user on the machine can read this file, so never put API keys in `env` blocks. Use `${VAR}` expansion from each user's environment, OAuth or per-user headers, or `headersHelper`.

Validate on a managed machine:

```bash
claude mcp list                                            # should show only managed servers
claude mcp add --transport http test https://example.com/mcp
# expected: Cannot add MCP server: enterprise MCP configuration is active
#           and has exclusive control over MCP servers
```

To load claude.ai connectors alongside the managed set, set `"allowAllClaudeAiMcps": true` (v2.1.149+). It affects **only** claude.ai connectors; plugin servers stay suppressed. It is read only from admin-controlled policy tiers, so users cannot re-enable connectors themselves.

### Allowlists and denylists

These **filter** what loads; they are not a registry. A server still has to be added by a user, a plugin, or `managed-mcp.json` first. Both lists also filter servers passed with `--mcp-config`.

Each entry is an object with a single key:

| Key | Matches | Use for |
|---|---|---|
| `serverUrl` | Remote URL, exact or with `*` wildcards | HTTP and SSE |
| `serverCommand` | The exact command and arguments | stdio |
| `serverName` | The user-assigned label, exact match only | Either, but see the warning |

Unset is not the same as empty:

| Setting | Unset | Empty array | Populated |
|---|---|---|---|
| `allowedMcpServers` | All allowed | **No servers allowed** | Only matching allowed |
| `deniedMcpServers` | None blocked | None blocked | Matching blocked |

**`serverName` is not a security control.** The name is the label a user assigns, so a user can call any server `github`. For claude.ai connectors the name is a display name that can change. Use `serverCommand` or `serverUrl` for enforcement.

Validation differs between the lists: in `deniedMcpServers`, `serverName` accepts any non-empty string (so you can block `"claude.ai Slack"`); in `allowedMcpServers` it is limited to letters, numbers, hyphens, and underscores, so use `serverUrl` to allowlist a connector.

### How a server is evaluated

Three checks in order, applied to **every** server including managed ones:

1. **Merge the lists.** Entries from every settings source combine. With `allowManagedMcpServersOnly: true`, only the managed allowlist is kept. **The denylist always merges from every source**, so users can always block a server for themselves.
2. **Check the denylist.** A match by URL, command, or name blocks the server. Nothing overrides a denylist match.
3. **Check the allowlist.** If unset anywhere, everything that passed the denylist loads. If set:

| Server type | Allowed when it matches |
|---|---|
| Remote (HTTP/SSE) | A `serverUrl` entry. A `serverName` match counts **only** when the allowlist contains no `serverUrl` entries |
| stdio | A `serverCommand` entry. A `serverName` match counts **only** when the allowlist contains no `serverCommand` entries |

That last rule is the important design: adding one `serverUrl` entry makes URL matching mandatory for all remote servers, closing the "rename it to an allowed name" hole.

Three matching rules:

- **Commands match exactly**, every argument in order. `["npx", "-y", "server"]` does not match `["npx", "server"]` or `["npx", "-y", "server", "--flag"]`
- **`serverCommand` and `serverUrl` expand `${VAR}` before matching**, on both the policy entry and the server config. `serverName` never expands. Because expansion reads Claude Code's own process environment, a policy entry referencing a variable expands to whatever a user sets, so **use literal URLs and commands for entries you rely on for enforcement**
- **URLs support `*` anywhere**, including the scheme. Hostname matching is case-insensitive and ignores a trailing FQDN dot; paths stay case-sensitive

| Pattern | Allows |
|---|---|
| `https://mcp.example.com/*` | All paths on a domain |
| `https://mcp.example.com` | Also all paths; a pattern with no path matches any path |
| `https://*.example.com/*` | Any subdomain |
| `http://localhost:*/*` | Any port on localhost |
| `*://mcp.example.com/*` | Any scheme to a domain |

Worked example:

```json
{
  "allowManagedMcpServersOnly": true,
  "allowedMcpServers": [
    { "serverUrl": "https://api.githubcopilot.com/*" },
    { "serverUrl": "https://*.internal.example.com/*" },
    { "serverCommand": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "."] }
  ],
  "deniedMcpServers": [
    { "serverName": "dangerous-server" },
    { "serverUrl": "https://*.untrusted.example.com/*" }
  ]
}
```

### What users see when blocked

| Restriction | Message |
|---|---|
| `managed-mcp.json` present, user runs `claude mcp add` | `Cannot add MCP server: enterprise MCP configuration is active and has exclusive control over MCP servers` |
| Server on denylist, user runs `claude mcp add` | `Cannot add MCP server "<name>": server is explicitly blocked by enterprise policy` |
| Server not on allowlist, user runs `claude mcp add` | `Cannot add MCP server "<name>": not allowed by enterprise policy` |
| Previously configured server now blocked | **Silently disappears** from `/mcp` and `claude mcp list`, no warning |

That last row is the operational hazard: tell affected users which servers are blocked when you roll out a restriction.

### Monitoring

With OpenTelemetry export configured, set `OTEL_LOG_TOOL_DETAILS=1` to include MCP server and tool names in tool events, then aggregate in your collector.

### Configuration summary

| Surface | Controls | Deliverable through server-managed settings |
|---|---|---|
| `managed-mcp.json` | Fixed set, exclusive control | **No**, standalone file only |
| `allowedMcpServers` | Allowlist | Yes |
| `deniedMcpServers` | Denylist | Yes |
| `allowManagedMcpServersOnly` | Locks allowlist to managed sources | Yes, managed tiers only |
| `allowAllClaudeAiMcps` | Loads connectors alongside managed set | Yes, managed tiers only |

Note that `allowManagedMcpServersOnly` is separate from `allowManagedPermissionRulesOnly`, which locks permission rules only and does **not** enforce the MCP allowlist.

---

## 9. Permissions and tool naming

MCP tool names follow `mcp__<server>__<tool>`. Permission rules use double-underscore form with no parenthesized argument:

```json
{
  "permissions": {
    "allow": [
      "mcp__github",                    // every tool from the github server
      "mcp__github__*",                 // same, glob form
      "mcp__db__query"                  // one specific tool
    ],
    "deny": [
      "mcp__dangerous-server",
      "ToolSearch"
    ]
  }
}
```

Rule semantics to keep straight:

- Server-level rules (`mcp__server`) grant or block everything from that server
- MCP tool-name globs (`mcp__server__*`) are supported. **Non-MCP tool-name globs are rejected in allow rules**, and deny rules accept `*` in the tool-name position to deny every tool
- Unknown tool names in deny rules produce a startup warning, so misspellings do not silently create ineffective policy
- Plugin servers need the full `mcp__plugin_<plugin>_<server>__<tool>` form (see section 7)

Three annotations and settings can force a prompt regardless of your permission mode:

1. An org-level **ask** setting on a claude.ai connector tool
2. A server's `anthropic/requiresUserInteraction` annotation
3. Explicit `ask` rules in your own permission config

All three prompt even in `acceptEdits`, `auto`, and `bypassPermissions`, offer no "don't ask again", and are **denied** in `dontAsk` mode.

### Scoping MCP to a subagent

A subagent's `mcpServers` frontmatter field takes either names of already-configured servers or full inline definitions:

```yaml
---
name: browser-tester
description: Tests features in a real browser using Playwright
mcpServers:
  - playwright:
      type: stdio
      command: npx
      args: ["-y", "@playwright/mcp@latest"]
  - github
---
```

Inline servers connect when the subagent starts and disconnect when it finishes; string references share the parent's connection.

**Context optimization**: defining a server inline here rather than in `.mcp.json` keeps its tool descriptions out of the main conversation entirely.

Managed restrictions, `--strict-mcp-config`, and `--bare` cover subagent-declared servers too (v2.1.153+), with one exception: `--strict-mcp-config` does not filter servers passed inline via `--agents` or the SDK, since those are explicit caller input. Plugin subagents ignore the `mcpServers` field entirely.

---

## 10. Tool search and context scaling

**Tool search is enabled by default.** MCP tool definitions are deferred rather than loaded upfront; only tool names and server instructions load at session start, and Claude searches for the tools a task needs. Only tools Claude actually uses enter context.

Consequence: adding more servers has minimal context impact. Claude Code imposes **no fixed per-server tool cap**; the practical limit is your context budget.

### Configuration

| `ENABLE_TOOL_SEARCH` | Behavior |
|---|---|
| (unset) | All MCP tools deferred. Falls back to upfront loading on Google Cloud's Agent Platform, when `ANTHROPIC_BASE_URL` is a non-first-party host, or on a Microsoft Foundry deployment hosted on Azure |
| `true` | All deferred, sending the beta header even on those platforms. Requests fail if the model or proxy does not support `tool_reference` blocks |
| `auto` | Threshold mode: load upfront if tools fit within 10% of the context window, defer the overflow |
| `auto:N` | Threshold mode with a custom percentage, N from 0 to 100 |
| `false` | All loaded upfront |

Requirements and overrides:

- Needs a model supporting `tool_reference` blocks: Sonnet 4.5, Haiku 4.5, Opus 4.5, and later
- `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS` keeps tool search off and **`ENABLE_TOOL_SEARCH` cannot override it**, because it strips the required beta header
- Microsoft Foundry deployments hosted on Azure reject tool search server-side; `ENABLE_TOOL_SEARCH` cannot override that either
- Disable the tool specifically with `"permissions": { "deny": ["ToolSearch"] }`

### Exempting a server from deferral

```json
{
  "mcpServers": {
    "core-tools": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "alwaysLoad": true
    }
  }
}
```

Every tool from that server loads at session start regardless of `ENABLE_TOOL_SEARCH`. Available on all server types, v2.1.121+. A server can also mark individual tools with `"anthropic/alwaysLoad": true` in `_meta`.

**Side effect**: `alwaysLoad: true` blocks startup until the server connects, capped at the 5-second connect timeout, because the tools must be present when the first prompt is built. Other servers keep connecting in the background.

### Waiting on connecting servers

If a request needs tools from a server still connecting, Claude waits. With tool search on, the wait happens inside the `ToolSearch` call. Without it, Claude uses the `WaitForMcpServers` tool instead.

---

## 11. Timeouts, output limits, backgrounding

### Timeouts

| Setting | Scope | Default |
|---|---|---|
| `MCP_TIMEOUT` | Server startup | - |
| `MCP_TOOL_TIMEOUT` | Tool execution wall-clock, all servers | ~28 hours |
| `timeout` (per-server, ms) | Tool execution wall-clock, that server | Overrides `MCP_TOOL_TIMEOUT` |
| `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT` | Idle window | 5 min remote, 30 min stdio |
| `CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS` | Move a long call to a background task | 2 minutes |

Details that matter:

- The per-server `timeout` is a **hard wall-clock limit per tool call**. Progress notifications do not extend it. Values below 1000 are ignored and fall through to `MCP_TOOL_TIMEOUT`
- HTTP, SSE, and claude.ai connector servers have a **second, per-request timer** covering each request to the server's first response byte. It is 60 seconds unless you set the per-server `timeout` or `MCP_TOOL_TIMEOUT` to 60 seconds or higher, which raises it to that value. A lower value does not shorten it, and an unset `MCP_TOOL_TIMEOUT`'s 28-hour default never feeds it. stdio and WebSocket have no per-request timer
- The **idle timeout** aborts a call that sends no response and no progress notification for the window. It applies to every server type except IDE servers and SDK in-process servers (v2.1.187+, extended to stdio in v2.1.203). Set to `0` to disable
- A per-server `timeout` of at least 1000 acts as a **floor** on the idle timeout (v2.1.203+)

### Automatic backgrounding (v2.1.212+)

A main-conversation MCP call still running after two minutes moves to a background task instead of blocking. Claude receives the task ID immediately and keeps working; the result arrives as a task notification. The task appears in `/tasks` and does not survive exiting the session. Per-call limits still apply while it runs in the background.

Calls that never background:

- Calls from subagents (main-conversation only)
- Calls to IDE servers
- Calls in non-interactive mode, unless `CLAUDE_AUTO_BACKGROUND_TASKS=1`
- A call waiting on an open elicitation dialog, deferred until the dialog closes

`CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS=0` or `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` turns it off.

### Output limits

- **Warning threshold**: 10,000 tokens, fixed
- **Default maximum**: 25,000 tokens, raised with `MAX_MCP_OUTPUT_TOKENS`
- Results over the default persist-to-disk threshold are written to disk and replaced with a file reference in the conversation

Server authors can raise a single tool's threshold:

```json
{
  "name": "get_schema",
  "description": "Returns the full database schema",
  "_meta": { "anthropic/maxResultSizeChars": 200000 }
}
```

Ceiling is 500,000 characters. It applies independently of `MAX_MCP_OUTPUT_TOKENS` **for text content**. Tools returning image data are still bound by `MAX_MCP_OUTPUT_TOKENS`, so raising the env var is the only option there.

---

## 12. Reliability behaviors

### Automatic reconnection

HTTP and SSE servers that disconnect mid-session reconnect with exponential backoff: **up to five attempts, starting at one second and doubling.** The server shows as pending in `/mcp` during reconnection, then failed, with a manual retry available. **stdio servers are local processes and are not reconnected automatically.**

The same backoff applies to a failed initial connection. As of v2.1.121, initial connections retry up to three times on transient errors (5xx, connection refused, timeout). Authentication and not-found errors are **not** retried, since they need a configuration change.

Capability discovery requests after a successful connection (`tools/list`, `prompts/list`, `resources/list`) also retry transient errors up to three times with short backoff (v2.1.191+). Auth errors, 4xx, and request timeouts are not retried.

### Reporting failures to Claude

When a configured server fails to connect, Claude Code tells Claude which one and why, including in `ToolSearch` results that find no matching tool, so Claude reports the failure rather than acting as if the server were never configured. **This requires tool search.** Without it (custom `ANTHROPIC_BASE_URL`, `ENABLE_TOOL_SEARCH=false`, an unsupported model, or Bedrock / Google Cloud's Agent Platform / Microsoft Foundry), connection failures are not reported to Claude.

### Dynamic tool updates

Claude Code supports MCP `list_changed` notifications and refreshes tools, prompts, and resources from that server automatically, no reconnect needed. If a refresh fails, the **previously discovered** capabilities are kept until a later refresh succeeds (v2.1.214+; before that, a transient error emptied the list).

### Root-level schema combinators

Some servers declare a tool's input schema with `anyOf`, `oneOf`, or `allOf` at the schema root. The Claude API does not accept those at the root.

As of v2.1.195, such tools stay available: Claude Code flattens the schema into a single object and prepends a sentence to the description telling Claude which parameter groups belong together.

- `allOf`: properties from every branch merged, each branch's `required` still applies
- `anyOf` / `oneOf`: properties merged, each branch's `required` described in the **description** rather than enforced by the schema

**Your server still receives whatever arguments Claude chose, so keep validating server-side.** When Claude Code cannot produce an acceptable schema, it skips that one tool and leaves the server's others available. Versions before v2.1.195 skipped every such tool.

---

## 13. Resources, prompts, elicitation, channels

### Resources

Type `@` to see resources from all connected servers alongside files. Reference with `@server:protocol://resource/path`:

```text
Can you analyze @github:issue://123 and suggest a fix?
Please review the API documentation at @docs:file://api/authentication
Compare @postgres:schema://users with @docs:file://database/user-model
```

Resources are fetched and included as attachments when referenced. Paths are fuzzy-searchable in autocomplete. Claude Code also automatically provides tools to list and read resources when servers support them.

### Prompts as commands

MCP prompts appear as `/mcp__servername__promptname`:

```text
/mcp__github__list_prs
/mcp__github__pr_review 456
/mcp__jira__create_issue "Bug in login flow" high
```

Arguments are space-separated and parsed against the prompt's defined parameters. Results are injected directly into the conversation. Server and prompt names are normalized, spaces to underscores.

### Elicitation

Servers can request structured input mid-task. **No configuration is required**; dialogs appear automatically.

- **Form mode**: a dialog with server-defined fields
- **URL mode**: opens a browser URL for authentication or approval, then you confirm in the CLI

To auto-respond without showing a dialog, use the `Elicitation` hook.

### Channels

A server declaring the `claude/channel` capability can push messages into your session, so Claude reacts to CI results, monitoring alerts, Telegram messages, Discord chats, or webhook events while you are away. Opt in with the `--channels` flag at startup. Managed settings gate this with `channelsEnabled` and `allowedChannelPlugins`.

---

## 14. Claude Code as an MCP server

```bash
claude mcp serve
```

Exposes Claude Code's own tools (View, Edit, LS, and so on) over stdio to another MCP client. **The command prints nothing** when it starts; a silent, blocked terminal means it is running and waiting.

```json
{
  "mcpServers": {
    "claude-code": {
      "type": "stdio",
      "command": "claude",
      "args": ["mcp", "serve"],
      "env": {}
    }
  }
}
```

If `claude` is not on `PATH`, use the full path from `which claude`, or you get `spawn claude ENOENT`.

**Security note**: this only exposes Claude Code's tools to your client. **Your client is responsible for implementing user confirmation for individual tool calls.**

---

## 15. Guidance for server authors

Building a server for Claude Code specifically, beyond the base protocol:

### Server instructions matter more with tool search

The `instructions` field helps Claude decide when to search for your tools, similar to how skills work. Explain:

- What category of tasks your tools handle
- When Claude should search for them
- Key capabilities

**Claude Code truncates tool descriptions and server instructions at 2KB each.** Keep them concise and put critical details near the start.

### The `_meta` annotations

| Annotation | Effect |
|---|---|
| `anthropic/maxResultSizeChars` | Raises that tool's persist-to-disk threshold, up to 500,000 chars, for text content |
| `anthropic/alwaysLoad` | Exempts that tool from tool-search deferral |
| `anthropic/requiresUserInteraction` | Forces a permission prompt on every call |

```json
{
  "name": "grant_access",
  "description": "Requests access to a protected resource",
  "_meta": { "anthropic/requiresUserInteraction": true }
}
```

Must be the JSON boolean `true`; anything else is ignored. Use it for tools where the prompt **is** the point, such as a consent or access-grant step where auto-approval would mean no human ever agreed. Other tools from the same server keep normal behavior. Requires v2.1.199+.

The prompt has to reach a person. In non-interactive mode with `--permission-prompt-tool`, an `allow` result for a flagged tool is converted to a deny with `MCP tool requires user interaction; not supported via --permission-prompt-tool`. The Agent SDK's `canUseTool` callback **does** receive these and can approve them. On Remote Control, Claude Code withholds one-tap approval and shows the full prompt in the terminal.

### Scaffolding a server

```
/plugin install mcp-server-dev@claude-plugins-official
/mcp-server-dev:build-mcp-server
```

If the marketplace is missing: `/plugin marketplace add anthropics/claude-plugins-official`. If the plugin is missing: `/plugin marketplace update claude-plugins-official`. After install, `/reload-plugins`.

---

## 16. Command reference

| Command | Purpose |
|---|---|
| `claude mcp add [opts] <name> <url\|-- cmd>` | Register a server |
| `claude mcp add-json <name> '<json>'` | Register from a JSON config |
| `claude mcp add-from-claude-desktop` | Import from Claude Desktop (macOS and WSL only) |
| `claude mcp list` | List servers with health status |
| `claude mcp get <name>` | Details for one server, including OAuth config |
| `claude mcp remove <name>` | Remove |
| `claude mcp login <name>` | Run the OAuth flow from the shell (v2.1.186+) |
| `claude mcp logout <name>` | Clear stored credentials |
| `claude mcp reset-project-choices` | Reset `.mcp.json` approvals |
| `claude mcp serve` | Run Claude Code as a stdio MCP server |
| `/mcp` | In-session panel: status, auth, per-project toggles, tool counts |
| `/mcp reconnect <server>`, `/mcp enable`, `/mcp disable` | Work in background sessions without attaching |

Health statuses in `claude mcp list`: `✔ Connected`, `! Needs authentication`, `✘ Failed to connect`, `⏸ Pending approval (run claude to approve)`, `✘ Rejected (see disabledMcpjsonServers in settings)`.

Notes:

- `claude mcp add` writes the config **without validating credentials**, so a placeholder token is accepted and the server fails later. Verify with `/mcp`
- `claude mcp add` with an existing name at the same scope fails with `MCP server <name> already exists in local config`
- **WebSocket servers do not appear in `claude mcp list`**; use `claude mcp get` or `/mcp`
- A remote server with an empty `url` shows as `not configured` and is not connected. Plugins use this for a connector you configure later

### CLI flags

| Flag | Effect |
|---|---|
| `--mcp-config <file-or-json>` | Load servers from a file or inline JSON |
| `--strict-mcp-config` | Use only `--mcp-config` servers, ignoring other configuration |
| `--bare` | Minimal configuration load |
| `--channels` | Opt in to channel-capable servers |
| `--allowedTools` / `--disallowedTools` | Runtime permission rules, accepting `mcp__` forms |

---

## 17. Environment variable reference

| Variable | Effect |
|---|---|
| `ENABLE_TOOL_SEARCH` | `true` / `auto` / `auto:N` / `false`, controls deferred tool loading |
| `MCP_TIMEOUT` | Server startup timeout, ms |
| `MCP_TOOL_TIMEOUT` | Tool execution wall-clock limit, ms |
| `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT` | Idle window, ms. `0` disables |
| `CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS` | Auto-background threshold, ms. `0` disables |
| `MAX_MCP_OUTPUT_TOKENS` | Max tokens per tool result, default 25,000 |
| `MCP_CONNECTION_NONBLOCKING` | `0` waits up to 5s for servers before the init message |
| `ENABLE_CLAUDEAI_MCP_SERVERS` | `false` disables claude.ai connectors for the shell session |
| `MCP_CLIENT_SECRET` | Supplies the OAuth client secret non-interactively |
| `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS` | Forces tool search off, uncoverridable |
| `CLAUDE_AUTO_BACKGROUND_TASKS` | `1` enables auto-backgrounding in non-interactive mode |
| `OTEL_LOG_TOOL_DETAILS` | `1` includes MCP server and tool names in telemetry |
| `CLAUDE_PROJECT_DIR` | Set by Claude Code in a spawned stdio server's environment |
| `CLAUDE_CODE_MCP_SERVER_NAME` / `_URL` | Set for `headersHelper` execution |

## 18. Settings key reference

| Key | Scope | Effect |
|---|---|---|
| `enableAllProjectMcpServers` | Any | Auto-approve all `.mcp.json` servers |
| `enabledMcpjsonServers` | Any | Approve specific `.mcp.json` server names |
| `disabledMcpjsonServers` | Any | Reject specific `.mcp.json` server names |
| `disableClaudeAiConnectors` | Any, any-source-true | Do not fetch claude.ai connectors |
| `allowedMcpServers` | Managed for enforcement | Allowlist by URL, command, or name |
| `deniedMcpServers` | Any, always merges | Denylist by URL, command, or name |
| `allowManagedMcpServersOnly` | Managed only | Ignore non-managed allowlists |
| `allowAllClaudeAiMcps` | Managed only | Load connectors alongside `managed-mcp.json` |
| `strictKnownMarketplaces` | Managed only | Restrict plugin marketplaces |
| `strictPluginOnlyCustomization` | Managed | With `mcp` in the list, servers only from plugins |
| `channelsEnabled` | Managed only | Allow channels for the organization |
| `allowedChannelPlugins` | Managed only | Allowlist of channel plugins |
| `disableSideloadFlags` | Managed only | Reject `--mcp-config`, `--plugin-dir`, `--plugin-url`, `--agents` |
| `permissions.allow` / `.deny` / `.ask` | Any | `mcp__server`, `mcp__server__tool`, `mcp__server__*` |

---

## 19. Troubleshooting

| Symptom | Check |
|---|---|
| Server shows `failed` right after adding | Credentials are not validated at add time. Check the token and endpoint |
| `command: expected string, received undefined` (pre-2.1.202) or a `url` warning | The entry has a `url` but no `type` |
| Server missing from `/mcp` with no error | Blocked by an allowlist or denylist. There is no loud error. Also check `disableClaudeAiConnectors` and `disabledMcpServers` |
| claude.ai connectors missing | `/status` to confirm active auth method; unset `ANTHROPIC_API_KEY` / `apiKeyHelper` and `/login` |
| Connector will not authenticate from `/mcp` | Anthropic-hosted connectors like Gmail must be connected at claude.ai |
| Hook never fires for a plugin server's tools | Use `mcp__plugin_<plugin>_<server>__<tool>`, not the bare server key |
| `spawn claude ENOENT` from `mcp serve` | Use the absolute path from `which claude` |
| stdio server on native Windows using npx | Wrap with `cmd /c` |
| stdio server stops working after a disconnect | stdio servers are not auto-reconnected. Restart the session |
| Tool output truncated or persisted to disk | Raise `MAX_MCP_OUTPUT_TOKENS`, or ask the author for `anthropic/maxResultSizeChars` or pagination |
| Server tools missing on Microsoft Foundry / Azure | Tool search is rejected server-side; tools load upfront instead |
| `Cannot add MCP server: enterprise MCP configuration is active` | `managed-mcp.json` is deployed |
| Import from Claude Desktop skipped a server | The name contains a character outside letters, numbers, hyphens, underscores |

---

## 20. Gotchas

1. **A `url` without a `type` is read as a stdio server** and skipped. Always set `type` explicitly for remote servers.
2. **MCP "local scope" is `~/.claude.json`**, not `.claude/settings.local.json`. Two unrelated meanings of the same word.
3. **The scope precedence uses the whole entry from the winning source.** Fields never merge across scopes.
4. **Scopes match duplicates by name; plugins and connectors match by endpoint.**
5. **`.mcp.json` servers do not self-approve** (tightened in v2.1.196), and a committed `enableAllProjectMcpServers` is ignored in an untrusted folder.
6. **`serverName` in an allowlist is not a security control.** Users choose the name. Use `serverCommand` or `serverUrl`.
7. **One `serverUrl` entry in the allowlist makes URL matching mandatory for all remote servers.** Same for `serverCommand` and stdio. This is intentional and easy to trip over when mixing entry types.
8. **`allowedMcpServers` unset means everything is allowed; an empty array means nothing is.**
9. **The denylist always merges from every source**, including the user's own settings, even under `allowManagedMcpServersOnly`.
10. **`${VAR}` in a policy entry expands from Claude Code's process environment**, which a user controls. Use literals for enforcement.
11. **A server blocked by policy after being configured silently disappears** with no explanation to the user.
12. **`managed-mcp.json` cannot be delivered through server-managed settings**; it is a standalone system file.
13. **`managed-mcp.json` also suppresses plugin servers and claude.ai connectors**, the latter unless you set `allowAllClaudeAiMcps`.
14. **Plugin tool names embed the plugin name**: `mcp__plugin_<plugin>_<server>__<tool>`. Hook matchers against the bare server key never fire.
15. **stdio servers are never auto-reconnected.** Only HTTP and SSE are.
16. **A configured `headers.Authorization` that the server rejects means a failed connection, not an OAuth fallback.**
17. **`headersHelper` runs fresh on every connection with no caching**, in a shell with a 10-second timeout, and only after workspace trust at project scope.
18. **claude.ai connectors load only under subscription auth.** An API key or third-party provider silently removes them.
19. **`alwaysLoad: true` blocks startup** until that server connects, up to 5 seconds.
20. **`CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS` disables tool search and `ENABLE_TOOL_SEARCH` cannot override it.**
21. **Without tool search, connection failures are not reported to Claude**, so it may answer as if the server were never configured.
22. **The 60-second per-request timer for remote servers is separate from the wall-clock timeout** and is only raised, never lowered, by your `timeout` setting.
23. **Root-level schema combinators are flattened, and branch `required` lists become description text rather than schema constraints.** Validate server-side.
24. **Reserved server names** (`workspace`, `claude-in-chrome`, `computer-use`, `Claude Preview`, `Claude Browser`) are silently skipped with a warning.
25. **`claude mcp serve` prints nothing on success.** A silent terminal is the success case.

---

## Reference links

- MCP reference: https://code.claude.com/docs/en/mcp
- MCP quickstart: https://code.claude.com/docs/en/mcp-quickstart
- Managed MCP configuration: https://code.claude.com/docs/en/managed-mcp
- Channels: https://code.claude.com/docs/en/channels
- Channels reference: https://code.claude.com/docs/en/channels-reference
- Plugin components reference: https://code.claude.com/docs/en/plugins-reference
- Permissions: https://code.claude.com/docs/en/permissions
- Settings: https://code.claude.com/docs/en/settings
- Security and threat model: https://code.claude.com/docs/en/security
- Subagents: https://code.claude.com/docs/en/sub-agents
- Monitoring usage: https://code.claude.com/docs/en/monitoring-usage
- Anthropic Directory: https://claude.ai/directory
- MCP specification: https://modelcontextprotocol.io
- Full docs index: https://code.claude.com/docs/llms.txt
