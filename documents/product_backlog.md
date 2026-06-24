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
- [x] **Dispute Review area**: held packages retained in `data/held/`, resolved
      by owner/admin (`TUTOR_ADMIN_EMAILS`) — select correct answer / edit / discard
      / re-run validator — then published into the Catalog (`/etl/review*`).
- [x] **Ingest progress hardening**: polling fallback + resume-on-reload + elapsed
      timer so long stages never look frozen.
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

## Epic: Assistant Roles (Instructor / Coach / Mentor)

Role-based assistant modes (see `documents/assistant_roles.md`). **Decisions:** a
role = a system prompt + tool set + UI, over ONE shared corpus; role **is** the
agent (the agent selector is the role picker); app stays "Tutor", the teaching
role is **Instructor**; default = Instructor; Coach grades **deterministically**
(LLM explains only); build the mastery substrate first, then all three roles; no
hybrid mode. Parked: Auto mode, sentiment, role customization, effectiveness
metrics.

### Phase 0 — adaptive substrate (foundation)
- [ ] **Per-concept mastery model** — SQLite `mastery(student, concept, attempts,
      correct, ability, …)`; ability (ELO/IRT-lite) updated from each graded
      answer (server maps question→`concept_ids` via the package); endpoints.
- [ ] **Adaptive trio** (client tools, MCP `execution:client`):
      `submit_answer` (grade a stated choice via the browser grader),
      `get_grounding` (current concept's passages + citations),
      `next_best_question` (weakest in-scope concept just above ability,
      respecting prerequisites).
- [ ] Wire `get_progress` + a new `get_mastery` to **persisted** session +
      mastery (not just in-memory panel state).

### Phase 1 — the three roles
- [ ] **Instructor / Coach / Mentor agent presets** in agent_server (prompt +
      params; all over the shared corpus + tools).
- [ ] **Role picker UI** — the agent selector lists the roles with icon/color,
      Instructor default; role indicator in the status bar.
- [ ] **Coach** wired to the trio + deterministic grading; **Mentor** wired to
      mastery (in-app progress report + next-step study plan).

### Phase 2 — Mentor reminders
- [ ] **Notifications + study reminders** backend (spaced-repetition from
      mastery) and wire the status-bar **bell** (currently a placeholder).

### Parked (not now)
- [ ] Auto mode (rules → model-classified intent), with override + "switched
      because…" note, and ambiguous-query fallback.
- [ ] Sentiment-aware tone / switch suggestions.
- [ ] User-customizable role behaviors (stricter Coach, etc.).
- [ ] Role effectiveness metrics (mastery gain, retry trends, engagement).

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
