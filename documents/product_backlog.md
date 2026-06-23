# Tutor — Product Backlog

**Status:** Living document · **Last updated:** 2026-06-23

What's planned next, roughly in priority order. Companion docs:
[technical_architecture.md](technical_architecture.md) (vision/schema/roadmap),
[tutor_tool_ideas.md](tutor_tool_ideas.md) (tool catalog), and the README
(deployed architecture).

## Done (for reference)

- [x] Frontend shell: rail (Sessions/Questions/Catalog/Documents/Chat/Settings),
      split view + draggable splitter, status bar, settings, mobile layout.
- [x] Dynamic question renderer (radio/checkbox/dropdown/true-false) with
      **deterministic** grading + per-option rationale.
- [x] Tutor chat (REST streaming, thinking toggle, Markdown) + collapsible pane.
- [x] Voice: TTS (sentence-streamed) and STT (dictate → auto-send).
- [x] Live **context awareness** — answer-safe system message each turn.
- [x] **Tools**: client (navigate/state/progress) + server `web_search`, via the
      MCP unified catalog; browser-run tool loop.
- [x] **ETL authoring pipeline** (upload → docling → author → judge → publish).
- [x] **Study Sessions**: per-student SQLite, optional Google login, answer
      save/restore, identity widget + avatar.
- [x] **Question retries + retry-aware scoring** (score = Σ correct?1/attempts:0).
- [x] **Wall of Fame**: per-package leaderboards (scope Mine/Everyone), one row
      per session, retry-aware ranking.
- [x] Layered CPU image (fast rebuilds, instant startup).

---

## Now / next

### 1. Adaptive runtime (architecture §7–8) — the core differentiator
- [ ] **`submit_answer`** tool (client) — grade the student's stated choice via
      the deterministic grader; closes the conversational loop.
- [ ] **`get_grounding`** tool (client) — return the concept's grounding passages
      + citations so the tutor explains from source and cites.
- [ ] **`next_best_question`** tool (client) — adaptive selection: weakest
      in-scope concept at a difficulty just above ability, respecting
      `prerequisites` and `taxonomy` weights.
- [ ] **Student ability / mastery model** — per-concept ability estimate
      (ELO/IRT-lite) updated from outcomes; mastery threshold.

> Recommended first trio: `submit_answer` + `get_grounding` + `next_best_question`
> (all client-side, no new infra). See [tutor_tool_ideas.md](tutor_tool_ideas.md).

### 2. Sessions ↔ progress integration
- [ ] Wire **`get_progress` and the tutor tools to the active session** so the
      assistant reports *persisted* progress (not just in-memory panel state).
- [ ] Explicit **"add package to session"** affordance in the Catalog.
- [ ] **Per-concept / per-domain mastery view** (weighted by `taxonomy.weight`,
      §7.3) — a dashboard the tutor can also narrate.

### 3. Loose ends from current UI
- [ ] **Notifications** — the status-bar bell is a placeholder; design + wire a
      panel (e.g. ETL job completion, session reminders).
- [ ] **Avatar video** — optional talking-head via the SDK `AvatarClient` +
      the proxy `/avatar` path (TTS/STT already wired).

---

## Later (architecture §11 extensions)

- [ ] More question types (ordering, matching, short-answer w/ rubric).
- [ ] Spaced-repetition scheduling (review queue across sessions).
- [ ] Multi-language packages (parallel stem/options per locale).
- [ ] Media-rich items (figures in stems — extraction already captures them).
- [ ] QTI / 1EdTech export for LMS interoperability.
- [ ] IRT calibration of difficulty from real response data.

---

## Known constraints / notes

- docling runs **CPU-only** in-image; large-document extraction is slow (GPU is
  an upgrade — see README).
- Editing `proxy_server/nginx.conf` requires `docker restart proxy_server`.
- The signed-in auth path can't be tested headlessly (needs a real Google
  cookie) — validate session features by signing in via the browser.
