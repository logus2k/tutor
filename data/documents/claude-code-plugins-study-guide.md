# Claude Code: Plugins and Marketplaces Reference

A study guide to Claude Code's distribution layer: what a plugin bundles, marketplace sources and management, installation scopes, the plugin cache, auto-updates, organizational restrictions, and the trust model.

Verified against code.claude.com/docs/en/discover-plugins, with manifest and component detail cross-checked against the plugins reference.

---

## 1. What a plugin is

**A self-contained directory bundling extension components into one installable unit**: skills, agents, hooks, MCP servers, LSP servers, output styles, themes, monitors, and workflows.

The decision is **distribution, not capability**. A standalone `.claude/` directory supports the same features. Ask instead: does this need to be **versioned, shared, and installable**?

| Situation | Use |
|---|---|
| Skills only you use | `.claude/skills/` — plugins add packaging overhead |
| Config your team needs consistently | Plugin, or `extraKnownMarketplaces` in project settings |
| Something you want to publish | Plugin in a marketplace |

**The one functional tradeoff**: plugin skills are namespaced. `commit-commands` provides `/commit-commands:commit`, not `/commit`.

A marketplace is separately **a catalog someone else maintains**. Using one is two steps: **add the marketplace** (registers the catalog, installs nothing), then **install individual plugins**. Like adding an app store versus downloading apps.

---

## 2. Structure

```text
my-plugin/
├── .claude-plugin/
│   └── plugin.json        # The ONLY thing that goes in here
├── commands/              # Legacy slash-command markdown
├── agents/                # Subagent definitions
├── skills/
│   └── code-reviewer/
│       └── SKILL.md
├── hooks/
│   └── hooks.json
├── .mcp.json              # MCP server definitions
├── .lsp.json              # LSP server configurations
├── settings.json          # Default settings (agent key only)
└── scripts/               # Hook and utility scripts
```

**The most common structural mistake: putting `commands/`, `agents/`, `skills/`, or `hooks/` inside `.claude-plugin/`.** Only `plugin.json` goes there; every component directory sits at the plugin root.

The manifest is **optional**. Without it, Claude Code auto-discovers components from the default locations. With it, you can point elsewhere:

```json
{
  "name": "plugin-name",
  "version": "1.2.0",
  "description": "Brief description",
  "author": { "name": "...", "email": "...", "url": "..." },
  "homepage": "...",
  "repository": "...",
  "license": "MIT",
  "keywords": ["..."],
  "commands": ["./custom/commands/special.md"],
  "agents": ["./agents/security-reviewer.md"],
  "skills": "./custom/skills/",
  "hooks": "./config/hooks.json",
  "mcpServers": "./mcp-config.json",
  "outputStyles": "./styles/",
  "lspServers": "./.lsp.json"
}
```

**A field-type trap**: `agents` takes an **array of file paths**, not a directory string. `"agents": "./agents/"` fails validation while `"agents": ["./agents/x.md"]` passes. Other fields accept directories.

### Path placeholders

| Placeholder | Resolves to |
|---|---|
| `${CLAUDE_PLUGIN_ROOT}` | The plugin's installation directory. **Changes on every update** |
| `${CLAUDE_PLUGIN_DATA}` | Persistent data directory, **survives updates** |
| `${CLAUDE_PROJECT_DIR}` | The project root |

**Never hardcode paths containing your username, home directory, or project location.** That is the single most common reason a plugin works locally and breaks after installation.

Use `${CLAUDE_PLUGIN_ROOT}` for bundled files and `${CLAUDE_PLUGIN_DATA}` for installed dependencies and state that should outlive an update.

---

## 3. Marketplaces

### The three Anthropic marketplaces

| Marketplace | Added | Notes |
|---|---|---|
| `claude-plugins-official` | **Automatically at startup** | Curated by Anthropic; inclusion at their discretion |
| `claude-community` | **Manually**: `/plugin marketplace add anthropics/claude-plugins-community` | Third-party plugins that passed automated validation and safety screening. **Each pinned to a specific commit SHA** |
| `claude-code-plugins` (demo) | Manually: `/plugin marketplace add anthropics/claude-code` | Example plugins |

**The in-app submission forms add plugins to the community marketplace, not the official one.**

If the official marketplace fails to add itself (a blocked network, typically), add it manually. `Marketplace "claude-plugins-official" not found` means it is not registered; a plugin "not found in the marketplace" means your local catalog is stale — refresh with `/plugin marketplace update`.

### Adding sources

| Source | Form |
|---|---|
| GitHub | `/plugin marketplace add owner/repo` |
| Any git host | `/plugin marketplace add https://gitlab.com/company/plugins.git` |
| SSH | `git@gitlab.com:company/plugins.git` |
| Specific ref | Append `#v1.0.0` |
| Local directory | `./my-marketplace` |
| Direct manifest | `./path/to/marketplace.json` |
| Remote URL | `https://example.com/marketplace.json` |

Two syntax requirements for git URLs:

- **Include the `.git` suffix**, or Claude Code treats the URL as a direct link to a hosted `marketplace.json`
- **Include `https://`.** As of v2.1.196, a bare host like `gitlab.com/company/plugins.git` is **rejected as an invalid GitHub `owner/repo` shorthand** with an error telling you to add the prefix. Earlier versions misread it and failed at clone time

**Shortcuts**: `/plugin market` for `/plugin marketplace`, and `rm` for `remove`.

**URL-based marketplaces have limitations** versus git-based ones, notably around plugins with relative paths.

### Managing

| Command | Effect |
|---|---|
| `/plugin marketplace list` | List configured marketplaces |
| `/plugin marketplace update <name>` | Refresh the catalog |
| `/plugin marketplace remove <name>` | **Removing a marketplace uninstalls every plugin you installed from it** |

The marketplace catalog lives at **`.claude-plugin/marketplace.json`** in the repository root. **Marketplace source and plugin source are independent**: one catalog can reference plugins from a dozen repositories, each pinned to a different branch or commit, so the catalog and the plugins evolve separately.

---

## 4. Installing

```shell
/plugin install plugin-name@marketplace-name
/reload-plugins
```

### Scopes

| Scope | Effect |
|---|---|
| **User** | You, across all projects |
| **Project** | All collaborators; **adds the plugin to `.claude/settings.json`** |
| **Local** | You, in this repository only, not shared |
| **Managed** | Installed by administrators via managed settings. **Cannot be modified** |

Non-interactive: `claude plugin install formatter@your-org --scope project`. **The shell command defaults to user scope** unless you pass `--scope`.

### The details pane

Before installing, review:

- **Context cost** — tokens the plugin adds to the window **every turn** (v2.1.143+)
- **Last updated** (v2.1.144+)
- **Will install** — the exact commands, agents, skills, hooks, MCP servers, and LSP servers it adds (v2.1.145+)

**Local and custom marketplaces may not provide these**, showing "Components will be discovered at installation" instead. That absence is itself a signal about how much you can review before committing.

The same inventory from the shell: `claude plugin details <name>`.

### The identity subtlety

In `plugin-name@marketplace-name`, **`plugin-name` is the `name` in the marketplace entry, which can differ from the `name` in the plugin's own `plugin.json`.**

As of v2.1.195, Enable and Disable work for plugins whose two names differ, and the commands accept either name. **On earlier versions, disabling such a plugin reports `already disabled` and leaves it enabled** — a confusing failure worth recognizing.

---

## 5. Managing installed plugins

`/plugin` → **Installed** tab, grouped by scope and **sorted so problems appear first**: load errors and unresolved dependencies at top, then favorites, with disabled plugins collapsed at the bottom.

- `f` favorites or unfavorites
- Type to filter by name or description
- `Enter` opens the detail view to enable, disable, or uninstall

Direct commands:

```shell
/plugin list [--enabled|--disabled]
/plugin disable plugin-name@marketplace-name
/plugin enable plugin-name@marketplace-name
/plugin uninstall plugin-name@marketplace-name
```

**`/plugin disable`, `enable`, and `uninstall` open the panel to apply the change and leave it open.** Press Esc before typing another command. **For scripting, use the `claude plugin` shell commands**, which do not open the panel.

### Uninstalling a project-enabled plugin

Asks which scope you mean (v2.1.203+):

- **Disable for you alone** — writes an override to `.claude/settings.local.json`, leaving it installed for the project
- **Uninstall for everyone** — removes it from the shared `.claude/settings.json`

Before v2.1.203, only the local disable was offered.

### Not used recently

Collects marketplace plugins you installed yourself but have not used in **at least two weeks across at least 10 sessions** (v2.1.187+), with a **Last used** line per plugin. These still cost startup and context.

**Two categories are never listed as unused**: managed or `--plugin-dir` plugins, and plugins contributing **a theme, output style, monitor, or workflow**, since those deliver value without an invocation to track.

**The whole feature is hidden when your organization sets `strictKnownMarketplaces`.**

An LSP plugin counts as used when its server delivers diagnostics or answers a navigation request (v2.1.203+). The first session on a version that tracks this **resets the usage record of LSP plugins with no recorded use**, so an older install is not judged on data from before tracking existed. Before v2.1.206, that first session could list an actively used LSP plugin as unused.

### /reload-plugins

Applies install, enable, and disable changes without restarting, reporting counts for plugins, skills, agents, hooks, MCP servers, and LSP servers.

**A counting quirk**: the skills count covers only each plugin's `commands/` directory, **not its `skills/` directory**, so the summary can report `0 skills` even when skills reloaded fine.

**The cache cost**: newly loaded components announce themselves in appended content while existing history still reads from cache. **But a plugin providing MCP servers whose tools are not deferred by tool search invalidates the cache entirely**, so the next request re-reads the whole conversation. In that case `/reload-plugins` **warns and does not apply the reload**; pass `--force` to proceed (v2.1.163+).

---

## 6. Auto-updates

Claude Code refreshes marketplace data and updates installed plugins **after your session starts, with a random delay of up to ten minutes**, so the running session keeps the versions it loaded at launch. Updated plugins trigger a notification to run `/reload-plugins`, or load on next launch.

**Defaults differ by source**: official Anthropic marketplaces have auto-update **on**; third-party and local development marketplaces have it **off**.

Toggle per marketplace in `/plugin` → **Marketplaces** → select → Enable/Disable auto-update. Administrators can set `"autoUpdate": true` on an `extraKnownMarketplaces` entry in managed settings.

To disable everything: `DISABLE_AUTOUPDATER`. **To keep plugin updates while disabling Claude Code updates**:

```bash
export DISABLE_AUTOUPDATER=1
export FORCE_AUTOUPDATE_PLUGINS=1
```

---

## 7. Team distribution

```json
{
  "extraKnownMarketplaces": {
    "my-team-tools": {
      "source": { "source": "github", "repo": "your-org/claude-plugins" }
    }
  },
  "enabledPlugins": {
    "my-team-plugin@my-team-tools": { "enabled": true, "scope": "project" }
  }
}
```

**When team members trust the repository folder, Claude Code prompts them to install these marketplaces and plugins.**

The important behavior change in v2.1.195: **this install step applies on every path that loads plugins.** A plugin that only the project's settings enable, from an external source such as a GitHub repo or npm package, **does not load until the member installs it**. Until then Claude Code reports it as not installed and shows the `claude plugin install` command.

So `enabledPlugins` in project settings **declares intent, not a guarantee that the plugin is active** for everyone who clones.

---

## 8. The plugin cache

**Claude Code copies plugins to a cache directory rather than using them in place**, for security and verification.

What gets copied:

- **Marketplace plugins with relative paths**: the path in the `source` field is copied **recursively**. `"source": "./plugins/my-plugin"` copies the entire `./plugins` directory
- **Plugins with `.claude-plugin/plugin.json`**: the implicit root, meaning the directory containing `.claude-plugin/`, copied recursively

**The consequence: paths referencing files outside the plugin directory do not work after installation.** This is the mechanism behind the "files not found after installation" symptom.

If plugin skills do not appear at all: `rm -rf ~/.claude/plugins/cache`, restart, reinstall.

---

## 9. Organizational restrictions

Managed-only settings governing plugins:

| Setting | Effect |
|---|---|
| `strictKnownMarketplaces` | Controls which marketplace sources users can add and install from |
| `blockedMarketplaces` | Blocklist, **checked before downloading**, so blocked sources never touch the filesystem |
| `disableSideloadFlags` | Rejects `--plugin-dir`, `--plugin-url`, `--agents`, `--mcp-config` at startup. **Without it, users bypass `strictKnownMarketplaces` for a single run** |
| `strictPluginOnlyCustomization` | Blocks skills, agents, hooks, and MCP from user and project sources, so they come only from plugins or managed settings. `true` locks all four; an array like `["skills", "hooks"]` locks named ones |
| `allowManagedHooksOnly` | Only managed, SDK, and force-enabled-plugin hooks load. **Plugins force-enabled in managed `enabledPlugins` are exempt**, so admins can distribute vetted hooks |
| `pluginTrustMessage` | Custom text appended to the trust warning before installation |
| `pluginSuggestionMarketplaces` | Allowlists marketplaces whose plugins can be pinned as **suggested for this directory** (v2.1.154+) |

Note the pairing: `strictKnownMarketplaces` without `disableSideloadFlags` is incomplete, because the sideload flags route around it.

---

## 10. Security

The docs state it plainly, twice:

> **Plugins and marketplaces are highly trusted components that can execute arbitrary code on your machine with your user privileges.**

> **Anthropic doesn't control what MCP servers, files, or other software are included in plugins and can't verify that they work as intended.**

Practical posture: **treat a plugin as a dependency you did not write, because that is exactly what it is.** Plugins ship hooks and MCP servers running with your privileges.

- Only add marketplaces you trust
- **Read the "Will install" section before confirming**
- **Prefer catalogs that pin plugins to reviewed commits** — the community marketplace does
- Remember that the community marketplace's screening is **automated validation and safety screening**, not a guarantee

---

## 11. Code intelligence plugins

These enable the built-in **LSP tool**, giving Claude definition jumps, reference finding, and immediate type errors after edits.

| Language | Plugin | Binary you install separately |
|---|---|---|
| C/C++ | `clangd-lsp` | `clangd` |
| C# | `csharp-lsp` | `csharp-ls` |
| Go | `gopls-lsp` | `gopls` |
| Java | `jdtls-lsp` | `jdtls` |
| Kotlin | `kotlin-lsp` | `kotlin-language-server` |
| Lua | `lua-lsp` | `lua-language-server` |
| PHP | `php-lsp` | `intelephense` |
| Python | `pyright-lsp` | `pyright-langserver` |
| Rust | `rust-analyzer-lsp` | `rust-analyzer` |
| Swift | `swift-lsp` | `sourcekit-lsp` |
| TypeScript | `typescript-lsp` | `typescript-language-server` |

**The plugin does not install the binary.** `Executable not found in $PATH` in the Errors tab means exactly that.

What Claude gains:

- **Automatic diagnostics** after every edit, so type errors and missing imports surface **without running a compiler**, and Claude fixes them in the same turn
- **Code navigation** more precise than grep-based search

Press **Ctrl+O** on a "Found 3 new diagnostic issues in 2 files" indicator to read them yourself.

Troubleshooting: verify the binary is on `$PATH`; **`rust-analyzer` and `pyright` can consume significant memory on large projects**, so disable and fall back to Claude's search tools if needed; monorepo false positives on internal imports do not affect Claude's ability to edit.

---

## 12. Other official plugin categories

**External integrations** bundling pre-configured MCP servers: `github`, `gitlab`, `atlassian`, `asana`, `linear`, `notion`, `figma`, `vercel`, `firebase`, `supabase`, `slack`, `sentry`.

**`security-guidance`** reviews each change for common vulnerabilities and instructs Claude to fix what it finds in the same session.

**Development workflows**: `commit-commands`, `pr-review-toolkit`, `agent-sdk-dev`, `plugin-dev`.

**Output styles**: `explanatory-output-style`, `learning-output-style`.

---

## 13. Development and troubleshooting

Test locally without installing:

```bash
claude --plugin-dir ./my-plugin
```

**One path per flag**; repeat for several. `--plugin-url` fetches a `.zip`.

| Symptom | Cause |
|---|---|
| `/plugin` unknown | Old version. Update and restart |
| `/plugin` unavailable in this environment | Use the desktop plugin browser, or declare `enabledPlugins` in settings for cloud sessions |
| Marketplace not loading | Verify the URL is reachable and `.claude-plugin/marketplace.json` exists at that path |
| Install failures | Source URLs reachable, repositories public or accessible |
| Files not found after install | **Paths referencing files outside the plugin directory**, which the cache copy breaks |
| Skills not appearing | `rm -rf ~/.claude/plugins/cache`, restart, reinstall |
| Works locally, breaks installed | **Hardcoded paths.** Use `${CLAUDE_PLUGIN_ROOT}` |

The **Errors** tab in `/plugin` carries load diagnostics.

---

## 14. Gotchas

1. **Only `plugin.json` goes in `.claude-plugin/`.** Component directories live at the plugin root.
2. **`agents` takes an array of file paths, not a directory.** Other fields take directories.
3. **The manifest is optional**; components auto-discover from default locations without it.
4. **Plugin skills are namespaced.** `/commit-commands:commit`, not `/commit`.
5. **`${CLAUDE_PLUGIN_ROOT}` changes on every update.** Use `${CLAUDE_PLUGIN_DATA}` for anything that must survive.
6. **Hardcoded absolute paths are the top cause of install-time breakage.**
7. **Plugins are copied to a cache**, so references outside the plugin directory break.
8. **A relative marketplace source copies its entire parent directory** recursively.
9. **Removing a marketplace uninstalls every plugin from it.**
10. **Git URLs need both `https://` and `.git`.** v2.1.196+ rejects a bare host outright.
11. **The marketplace-entry name can differ from the `plugin.json` name**, and older versions silently fail to disable such plugins.
12. **`/plugin install` requires `/reload-plugins`** to take effect in the current session.
13. **`/plugin disable|enable|uninstall` leaves the panel open.** Use `claude plugin` for scripts.
14. **`/reload-plugins` reports `0 skills` for a plugin's `skills/` directory**, counting only `commands/`.
15. **A reload that would invalidate the MCP prompt cache is skipped with a warning** unless you pass `--force`.
16. **Auto-update is on for official marketplaces, off for third-party and local ones.**
17. **Auto-updates land up to ten minutes after startup** and do not affect the running session.
18. **`enabledPlugins` in project settings does not guarantee the plugin loads.** External-source plugins need an explicit install per member since v2.1.195.
19. **Local and custom marketplaces may not expose context cost or "Will install."**
20. **Context cost is per turn, every turn.** An unused plugin still charges it.
21. **Theme, output style, monitor, and workflow plugins never appear as unused**, so review them manually.
22. **`strictKnownMarketplaces` hides the unused-plugin tracking entirely.**
23. **`strictKnownMarketplaces` without `disableSideloadFlags` is bypassable** in a single run.
24. **Managed-scope plugins cannot be modified by users.**
25. **Anthropic does not verify third-party plugins.** The community marketplace's screening is automated.
26. **LSP plugins do not install the language server binary.**
27. **`rust-analyzer` and `pyright` can be memory-heavy** on large projects.

---

## Reference links

- Discover and install plugins: https://code.claude.com/docs/en/discover-plugins
- Create plugins: https://code.claude.com/docs/en/plugins
- Plugins reference: https://code.claude.com/docs/en/plugins-reference
- Create and distribute a marketplace: https://code.claude.com/docs/en/plugin-marketplaces
- Official marketplace: https://github.com/anthropics/claude-plugins-official
- Community marketplace: https://github.com/anthropics/claude-plugins-community
- Plugin catalog: https://claude.com/plugins
- Settings, plugin settings: https://code.claude.com/docs/en/settings
- Security guidance plugin: https://code.claude.com/docs/en/security-guidance
- Prompt caching: https://code.claude.com/docs/en/prompt-caching
- MCP tool search: https://code.claude.com/docs/en/mcp
- Desktop plugin browser: https://code.claude.com/docs/en/desktop
- Full docs index: https://code.claude.com/docs/llms.txt
