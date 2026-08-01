# Claude Code: Personalization Reference

A study guide to shaping how Claude Code looks and behaves for you: terminal configuration, themes, output styles, the status line, keybindings and Vim mode, rendering modes, and notifications.

Terminal configuration and themes are verified against code.claude.com/docs/en/terminal-config. Output styles, status line, and keybindings are assembled from the settings, commands, and context-window references and link to their own pages.

---

## 1. Two layers, often confused

| Layer | Owns | Configure with |
|---|---|---|
| **Your terminal** | Color scheme, key signals, fonts, notification delivery | Terminal app settings, `/terminal-setup`, `~/.tmux.conf` |
| **Claude Code** | Which keys it responds to, its own theme tokens, rendering mode, status line, output style | `/theme`, `/keybindings`, `/tui`, `/statusline`, `/config` |

**Claude Code does not control your terminal's color scheme.** The `/theme` command matches Claude Code's own interface to it.

**The docs are explicit that terminal configuration is a troubleshooting page, not a setup requirement**: Claude Code works in any terminal without configuration. Go there when something specific misbehaves.

---

## 2. Multiline input

**`Ctrl+J`, or `\` then Enter, work in every terminal with no setup.** Shift+Enter varies:

| Terminal | Shift+Enter |
|---|---|
| Ghostty, Kitty, iTerm2, WezTerm, Warp, Apple Terminal, Windows Terminal | Works without setup |
| VS Code, Cursor, Devin Desktop, Alacritty, Zed | **Run `/terminal-setup` once** |
| gnome-terminal, JetBrains IDEs (PyCharm, Android Studio) | **Not available.** Use `Ctrl+J` or `\` |

**Run `/terminal-setup` in the host terminal, not inside tmux or screen**, since it writes to the host terminal's configuration. Existing bindings are left alone; a second run reports it is already configured.

**In VS Code, Cursor, and Devin Desktop it also changes two editor settings**: `terminal.integrated.gpuAcceleration` to `"off"` to prevent garbled text, and `terminal.integrated.mouseWheelScrollSensitivity` for fullscreen scrolling. To undo the GPU change, set it back to `"auto"` and reload the window.

**Inside tmux, Shift+Enter needs the tmux configuration too**, even when the outer terminal supports it.

To rebind, map the **`chat:newline`** and **`chat:submit`** actions in your keybindings file — including swapping them so Enter inserts a newline and Shift+Enter submits.

---

## 3. macOS Option key

Shortcuts like Option+Enter and Option+P **do nothing until your terminal sends Option as a modifier.** The setting is usually labeled **"Use Option as Meta Key"**.

| Terminal | How |
|---|---|
| Apple Terminal | Settings → Profiles → Keyboard → "Use Option as Meta Key" |
| iTerm2 | Settings → Profiles → Keys → General → Left and Right Option key to **"Esc+"** |
| VS Code | `"terminal.integrated.macOptionIsMeta": true` |
| Ghostty, Kitty, others | Look for Option-as-Alt or Option-as-Meta in the config file |

**Accepting Claude Code's first-run terminal setup prompt already does this** in Apple Terminal, along with turning off the audible bell.

Two version-specific notes: **in screen reader mode, `/terminal-setup` leaves the bell alone** (v2.1.211+) — before that it turned the bell off even there, and you may need to restore it under Settings → Profiles → Advanced. And **running `/terminal-setup` in iTerm2 enables clipboard access** so `/copy` can write to the system clipboard; it detects iTerm2 even from inside tmux, and **requires an iTerm2 restart**.

---

## 4. Notifications

Claude Code fires a notification event when it finishes a task or pauses for a permission prompt.

**By default a desktop notification is sent only in Ghostty, Kitty, and iTerm2.** Elsewhere:

```json
{ "preferredNotifChannel": "terminal_bell" }
```

**The desktop notification reaches your local machine over SSH**, so a remote session can still alert you. Ghostty and Kitty forward to the OS notification center automatically; **iTerm2 needs Settings → Profiles → Terminal → "Notification Center Alerts", then "Filter Alerts" → "Send escape sequence-generated alerts".**

For anything custom, a **Notification hook**:

```json
{
  "hooks": {
    "Notification": [
      { "hooks": [{ "type": "command", "command": "afplay /System/Library/Sounds/Glass.aiff" }] }
    ]
  }
}
```

**Hooks run alongside the built-in notification rather than replacing it**, which is how terminals that get no desktop notification — Warp, the VS Code integrated terminal — cover the gap.

Recall from the hooks reference that **hooks have no controlling terminal**, so a hook that needs to emit an escape sequence should return `terminalSequence` rather than writing to `/dev/tty`.

---

## 5. tmux

Two things break by default: **Shift+Enter submits instead of inserting a newline, and desktop notifications and the progress bar never reach the outer terminal.**

```bash
# ~/.tmux.conf
set -g allow-passthrough on
set -s extended-keys on
set -as terminal-features 'xterm*:extkeys'
```

Then `tmux source-file ~/.tmux.conf`.

**`allow-passthrough` lets notifications and progress updates through; the `extended-keys` lines let tmux distinguish Shift+Enter from Enter.**

---

## 6. Themes

`/theme`, or the picker in `/config`. **The auto option detects your terminal's light or dark background and follows OS appearance changes** whenever your terminal does.

### Custom themes

Requires v2.1.118+. `/theme` lists your custom themes and any contributed by installed plugins. **Select "New custom theme…" to create one interactively**, or press **`Ctrl+E`** on a highlighted custom theme to edit it.

Each is a JSON file in **`~/.claude/themes/`**. The filename without `.json` is the slug, stored as `custom:<slug>`.

| Field | Description |
|---|---|
| `name` | Display label. Defaults to the slug |
| `base` | `dark`, `light`, `dark-daltonized`, `light-daltonized`, `dark-ansi`, `light-ansi`. Defaults to `dark` |
| `overrides` | Token-to-color map. **Unlisted tokens fall through to the base** |

Colors accept `#rrggbb`, `#rgb`, `rgb(r,g,b)`, `ansi256(n)`, or `ansi:<name>` from the 16 standard names.

**Unknown tokens and invalid colors are ignored, so a typo cannot break rendering.**

```json
{
  "name": "Dracula",
  "base": "dark",
  "overrides": { "claude": "#bd93f9", "error": "#ff5555", "success": "#50fa7b" }
}
```

**Claude Code watches `~/.claude/themes/` and live-reloads**, so editor edits apply to a running session. **One exception: if the folder did not exist at startup, restart once after creating your first theme file.**

### Token groups

| Group | Representative tokens |
|---|---|
| **Text and accent** | `claude` (brand accent, spinner, assistant label), `text`, `inverseText`, `inactive`, `subtle`, `suggestion`, `permission`, `remember` |
| **Status** | `success`, `error`, `warning` (also the auto mode border), `merged` |
| **Input box and modes** | `promptBorder`, `planMode`, `autoAccept`, `bashBorder`, `ide`, `fastMode` |
| **Diffs** | `diffAdded`, `diffRemoved`, plus `Dimmed` and `Word` variants of each |
| **Fullscreen only** | `userMessageBackground`, `userMessageBackgroundHover`, `bashMessageBackgroundColor`, `memoryBackgroundColor`, `selectionBg` |
| **Usage meter and labels** | `rate_limit_fill`, `rate_limit_empty`, `briefLabelYou`, `briefLabelClaude` |

Two patterned families worth knowing:

- **Shimmer variants** supply the lighter color in the spinner's animated gradient: `claudeShimmer`, `warningShimmer`, `permissionShimmer`, `promptBorderShimmer`, `inactiveShimmer`, `fastModeShimmer`. **Override the shimmer alongside its base or the animation looks mismatched**
- **Subagent colors** follow `<color>_FOR_SUBAGENTS_ONLY` for the eight named colors. A subagent declaring `color: blue` is drawn with `blue_FOR_SUBAGENTS_ONLY`

The `ultrathink` and `ultraplan` keywords render with a seven-color rainbow gradient, tokens `rainbow_<color>` and `rainbow_<color>_shimmer`.

**The interactive editor in `/theme` shows the same tokens with a live preview**, plus a few single-purpose accents the reference omits.

---

## 7. Rendering mode

If the display flickers or the scroll position jumps, **fullscreen rendering** draws to a separate screen the terminal reserves for full-screen apps instead of appending to scrollback. That **keeps memory usage flat and adds mouse support** for scrolling and selection.

**The tradeoff**: you scroll with the mouse or PageUp **inside** Claude Code rather than with the terminal's native scrollback.

```bash
CLAUDE_CODE_NO_FLICKER=1 claude
```

Or `/tui fullscreen`, which **saves the preference and relaunches your conversation intact**, with future sessions starting in fullscreen.

**If flicker is the only problem** and your terminal supports synchronized output but is not auto-detected, such as Emacs `eat`, **`CLAUDE_CODE_FORCE_SYNC_OUTPUT=1` fixes it without changing renderers.** That is the lighter-touch option.

**In screen reader mode none of this applies**: Claude Code always renders as plain scrolling text except in attached background sessions, and `/tui fullscreen` prints an explanation instead of switching. `--ax-screen-reader` forces the classic renderer and takes precedence over `CLAUDE_AX_SCREEN_READER` and the `axScreenReader` setting.

---

## 8. Pasting

**Pastes over 800 characters or more than two lines collapse to a placeholder** such as `[Pasted text #1 +120 lines]`. **The full content is still sent** when you submit.

**The VS Code integrated terminal can drop characters from very large pastes before they reach Claude Code.** For entire files or long logs, write to a file and ask Claude to read it — which also keeps the transcript readable and lets Claude reference the path in later turns.

---

## 9. Vim mode and keybindings

Enable via `/config` → Editor mode, `/vim`, or `editorMode: "vim"`.

Supports a subset of NORMAL and VISUAL motions and operators: `hjkl`, `v`/`V`, and `d`/`c`/`y` with text objects.

Three behaviors that differ from standard Vim:

- **Enter still submits in INSERT mode.** Use `o`, `O`, or `Ctrl+J` for a newline
- **Vim motions are not remappable** through the keybindings file
- To map a two-key INSERT sequence like `jj` to Escape, use **`vimInsertModeRemaps`** in user settings

**Every Claude Code key binding is remappable** via `/keybindings`, which opens `~/.claude/keybindings.json`. **Settings live-reload**, so changes apply immediately. This is the layer for changing what Claude Code responds to; terminal configuration is the layer for what your terminal sends.

---

## 10. Status line and output styles

**Status line**: `/statusline` configures what appears at the bottom — model, working directory, git branch, or anything else. The command **can generate one for you based on your `.bashrc`/`.zshrc`**.

The status line command receives Claude Code's JSON on stdin, including a `session_name` field that carries the generated session title when no name is set. `statusLine.refreshInterval` is configurable from v2.1.97.

**Output styles** change how Claude responds — tone, form, and behavior — by modifying the system prompt. Set via `/config`. Two ship in the official marketplace: `explanatory-output-style` for educational insight into implementation choices, and `learning-output-style` for interactive skill building. The **Proactive** style is the recommended way to get more autonomous behavior **while keeping permission prompts**, as opposed to auto mode.

Three properties worth carrying from the other guides:

- **Output style text goes into the system prompt**, so it costs context every session, alongside anything from `--append-system-prompt`
- **It is therefore untouched by compaction**, being outside message history
- **Output styles never load into subagents**

**Spinner verbs** are customizable in `settings.json`, and checking that file into source control shares them with your team.

---

## 11. Where personalization lives

| Setting | File |
|---|---|
| Theme preference, editor mode, notification channel, spinner verbs | `~/.claude/settings.json` |
| Custom themes | `~/.claude/themes/*.json` |
| Keybindings | `~/.claude/keybindings.json` |
| Output styles | `.claude/output-styles/`, `~/.claude/output-styles/`, or a plugin |
| Terminal-side settings | Your terminal app's own configuration |

**`--safe-mode` disables custom themes, keybindings, status line commands, and output styles** along with everything else, which makes it the fastest way to tell whether a personalization is causing a problem.

---

## 12. Gotchas

1. **Claude Code does not control your terminal's color scheme.** `/theme` matches its own interface to it.
2. **`Ctrl+J` always works.** Reach for it before configuring Shift+Enter.
3. **Shift+Enter is unavailable in gnome-terminal and JetBrains IDEs** regardless of setup.
4. **Run `/terminal-setup` in the host terminal, not inside tmux.**
5. **`/terminal-setup` changes VS Code editor settings**, including turning GPU acceleration off.
6. **tmux needs its own configuration even when the outer terminal supports Shift+Enter.**
7. **Without `allow-passthrough`, tmux swallows notifications and the progress bar.**
8. **macOS Option shortcuts silently do nothing** until Option-as-Meta is enabled.
9. **iTerm2 needs a restart** after `/terminal-setup` enables clipboard access.
10. **Desktop notifications are default-on in only three terminals.** Elsewhere set `preferredNotifChannel` or use a hook.
11. **Notification hooks run alongside the built-in notification**, not instead of it.
12. **Custom themes need v2.1.118+**, and a restart only if `~/.claude/themes/` did not exist at startup.
13. **Invalid theme tokens and colors are silently ignored**, so a broken theme looks like an unchanged theme.
14. **Shimmer variants must be overridden alongside their base token.**
15. **Fullscreen mode replaces terminal scrollback** with in-app scrolling.
16. **Try `CLAUDE_CODE_FORCE_SYNC_OUTPUT=1` before switching renderers** if flicker is the only symptom.
17. **Screen reader mode overrides rendering choices entirely**, and `/tui fullscreen` refuses.
18. **Pastes over 800 characters collapse visually but send in full.**
19. **The VS Code integrated terminal can silently drop characters from very large pastes.**
20. **Enter still submits in Vim INSERT mode**, and Vim motions are not remappable.
21. **Output styles cost system-prompt context every session** and never reach subagents.
22. **`--safe-mode` disables all of this at once**, which is the diagnostic move.

---

## Reference links

- Configure your terminal: https://code.claude.com/docs/en/terminal-config
- Interactive mode and the Vim key table: https://code.claude.com/docs/en/interactive-mode
- Keybindings: https://code.claude.com/docs/en/keybindings
- Fullscreen rendering: https://code.claude.com/docs/en/fullscreen
- Status line: https://code.claude.com/docs/en/statusline
- Output styles: https://code.claude.com/docs/en/output-styles
- Accessibility and screen reader mode: https://code.claude.com/docs/en/accessibility
- Plugin themes: https://code.claude.com/docs/en/plugins-reference
- Hooks guide, Notification examples: https://code.claude.com/docs/en/hooks-guide
- Settings: https://code.claude.com/docs/en/settings
- Environment variables: https://code.claude.com/docs/en/env-vars
- Troubleshooting: https://code.claude.com/docs/en/troubleshooting
- Full docs index: https://code.claude.com/docs/llms.txt
