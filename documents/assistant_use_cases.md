# Assistant Use Cases → Build Plan

> **Status**: design — not built. Derived from
> [`assistant_auto_mode.md`](assistant_auto_mode.md) (the ensemble/bus architecture)
> and [`assistant_roles_current.md`](assistant_roles_current.md) (what exists today).
>
> Drafted: 2026-06-27.

---

## How to read this

Each use case is a named **workflow** — a self-contained proactive behavior the student
can switch **on/off** in the frontend. The catalog (Part 1) is the source; from it we
derive:

- **Part 2** — the **events to instrument** (publish onto the Agent Bus).
- **Part 3** — the **agent roles / system prompts** needed.
- **Part 4** — the **code to produce** per layer.
- **Part 5** — the **toggle UX**.

Every workflow obeys the global rules from `assistant_auto_mode.md`: silence is the
default; a cheap deterministic pre-gate (subscription + threshold + cooldown + **toggle
state**) decides whether a role even wakes; a role may still self-silence; in **Auto**
mode the surviving contributions are consolidated into one message by the synthesizer.

**Workflow record format** — each entry below lists: *Default* (on/off), *Scenario*,
*Trigger events*, *Roles & contributions*, *Workflow steps*, *Restraint*.

---

## Part 1 — Use case catalog (the workflows)

### A. Action-triggered

#### W1 — Answer feedback  ·  *default: ON*
- **Scenario**: The student answers a question. If wrong, they get a focused
  explanation of *why*, grounded in the source; if right (especially first try on a hard
  item), a brief acknowledgement.
- **Trigger**: `answer_submitted{correct, attempts, concept_ids, selected}`.
- **Roles**: Instructor (explain the misconception via `get_grounding`); Coach (short
  encouragement / "next?"). Wrong → Instructor-led; right → Coach-led.
- **Steps**: event → gate (toggle on?) → Instructor/Coach reaction passes →
  synthesizer (context-weighted; wrong leans Instructor, right leans Coach) → emit.
- **Restraint**: never reveal correctness *before* answering (already enforced); don't
  re-explain what was just explained; routine correct answers usually → silence.

#### W2 — Hint on request  ·  *default: ON*
- **Scenario**: The student clicks "hint" (or asks for one in chat). They get a
  scaffolded nudge that does **not** reveal the answer.
- **Trigger**: `hint_requested{qid}` (and chat intent).
- **Roles**: Instructor (scaffolded hint from grounding).
- **Steps**: event → Instructor reaction (hint, no reveal) → emit (single role, light
  synth).
- **Restraint**: never reveal the correct option; escalate hint specificity only on
  repeated requests.

### B. Time-triggered

#### W3 — Stuck nudge (present-stalled)  ·  *default: ON*
- **Scenario**: The student is clearly *present* (moving the mouse, scrolling) but has
  sat on the same question without answering for a while. The Tutor gently offers help.
- **Trigger**: `stalled{qid, seconds}` (presence-gated — see auto_mode §5.3).
- **Roles**: Instructor (offer to clarify / a first hint); Coach (nudge to commit an
  answer).
- **Steps**: event → gate (toggle + cooldown) → Instructor/Coach reaction → synth →
  emit a short offer.
- **Restraint**: re-arm with **backoff**, not on a fixed interval; suppressed by W11 if
  prior offers were ignored.

#### W4 — Pacing coaching  ·  *default: OFF*
- **Scenario**: The student is rushing — answering in a couple of seconds and getting
  them wrong (guessing). Coach suggests slowing down and reading fully.
- **Trigger**: `dwell{qid, seconds, vs_norm}` (very short) + `answer_submitted{correct:false}`.
- **Roles**: Coach.
- **Steps**: event → gate → Coach reaction → emit.
- **Restraint**: off by default (can feel preachy); strong cooldown when on.

#### W5 — Session wellbeing  ·  *default: OFF*
- **Scenario**: The student has been studying a long stretch, or has gone idle (tab
  visible, no input). The Tutor suggests a short break or checks in gently.
- **Trigger**: `session_duration{minutes}`, `idle{seconds}`.
- **Roles**: Mentor.
- **Steps**: event → gate → Mentor reaction → emit.
- **Restraint**: rare; one break suggestion per long stretch.

#### W6 — Welcome back / re-orientation  ·  *default: ON*
- **Scenario**: The student refocuses the tab after a while, or returns after days. The
  Tutor gives a light "welcome back", and after a multi-day gap, a one-line recap +
  suggested starting point.
- **Trigger**: `returned{after}`, `return_after{days}`.
- **Roles**: Mentor.
- **Steps**: event → gate → Mentor reaction (uses `get_progress` / `get_mastery`) → emit.
- **Restraint**: short; not on every tab switch (threshold on `after`).

### C. Progress-triggered

#### W7 — Momentum & celebration  ·  *default: ON*
- **Scenario**: The student strings together correct answers, or nails a previously-hard
  concept. Coach acknowledges the streak to keep momentum.
- **Trigger**: `streak{count}`, `answer_submitted{correct:true}` (first-try, hard).
- **Roles**: Coach.
- **Steps**: event → gate → Coach reaction → emit.
- **Restraint**: **strongest** anti-repetition + cooldown of any workflow (otherwise
  "great job!" spam); vary phrasing using the bus history.

#### W8 — Progress check-ins  ·  *default: ON*
- **Scenario**: The student crosses a milestone (e.g. 50% of a package) or newly masters
  a concept. Mentor reflects on where they stand and what's next.
- **Trigger**: `milestone{kind, value}`, `mastery_updated{mastered_transition}`.
- **Roles**: Mentor.
- **Steps**: event → gate → Mentor reaction (`get_progress`/`get_mastery`) → suggest a
  next step (concept or `next_best_question`) → emit.
- **Restraint**: only on genuine transitions; not every mastery delta.

### D. Repetition-triggered

#### W9 — Re-teach on repeated struggle  ·  *default: ON*
- **Scenario**: The student keeps missing the same concept, or keeps picking the same
  wrong option. Instructor explains it a **different way** — analogy, smaller steps — not
  a repeat of the prior explanation.
- **Trigger**: `concept_struggle{concept_id, misses}`, `repeated_wrong_option{qid,
  option}`, `recurring_mistake{pattern}`.
- **Roles**: Instructor (re-teach); optionally Coach (suggest a targeted drill via
  `next_best_question`).
- **Steps**: event → gate → Instructor reaction (must differ from prior explanation —
  reads its own bus history) → synth → emit.
- **Restraint**: change approach each time; hand off to silence/Mentor if it persists.

#### W10 — Spaced-review prompts  ·  *default: ON (signed-in)*
- **Scenario**: A concept the student learned earlier is due for review. Mentor surfaces
  it (and can offer to jump there).
- **Trigger**: `review_due{concept_id, days_since}`.
- **Roles**: Mentor (offer review; `open_package` / `next_best_question`).
- **Steps**: scheduler emits event → gate → Mentor reaction → emit.
- **Restraint**: needs sign-in (mastery history); batch due items, don't fire per concept.

### E. Governor (always-on)

#### W11 — Anti-nag governor  ·  *default: ON (intensity slider, not a plain toggle)*
- **Scenario**: The Tutor has offered help on a topic several times and the student kept
  going without responding. The Tutor **backs off** — raises cooldowns, lowers
  sensitivity, switches approach, or goes silent on that topic.
- **Trigger**: `unanswered_offers{topic, count, kinds}` (auto_mode §5.4, inverted
  semantics).
- **Roles**: none speak — this **suppresses** other workflows via the gate.
- **Steps**: correlator detects ignored offers → emits event → gate raises that topic's
  thresholds / disables re-offer for a window.
- **Restraint**: this *is* the restraint mechanism; the "intensity" slider sets how
  proactive the whole ensemble is allowed to be.

> **Auto mode** is not a workflow — it is the *mode* in which the surviving
> contributions from any of W1–W10 are consolidated into one message. With a single role
> selected, only that role's contributions surface (others stay internal).

---

## Part 2 — Derived: events to instrument

| Event | Type | Producer | Key payload | Consumed by |
|-------|------|----------|-------------|-------------|
| `answer_submitted` | Action | browser | correct, attempts, concept_ids, selected, qid | W1, W4, W7 |
| `hint_requested` | Action | browser | qid | W2 |
| `option_selected` / `_deselected` | Action | browser (debounced) | qid, option | presence/stall input |
| `question_viewed` | Action | browser | qid, concept_ids, ability? | context, stall reset |
| `question_navigated` | Action | browser | from, to | progress signal (clears stall) |
| `chat_message_sent` | Action | browser | text, addressed_role? | manual chat, W2 |
| `mastery_updated` | Progress | server `etl/sessions.py` | concept_id, ability, delta, mastered_transition | W8 |
| `milestone` | Progress | server | kind, value | W8 |
| `streak` | Progress | server | count | W7 |
| `stalled` | Time | browser presence tracker | qid, seconds | W3 |
| `idle` | Time | browser presence tracker | seconds | W5 |
| `away` / `returned` | Time | browser (visibility) | since / after | W6 |
| `dwell` | Time | browser | qid, seconds, vs_norm | W4 |
| `session_duration` | Time | browser/server tick | minutes | W5 |
| `return_after` | Time | server (last-seen) | days | W6 |
| `time_of_day` | Time | server tick | hour bucket | framing only |
| `concept_struggle` | Repetition | server | concept_id, misses | W9 |
| `repeated_wrong_option` | Repetition | server | qid, option | W9 |
| `recurring_mistake` | Repetition | server | pattern | W9 |
| `review_due` | Repetition | server scheduler | concept_id, days_since | W10 |
| `unanswered_offers` | Repetition (self) | server correlator | topic, count, kinds | W11 (gate) |
| *(agent)* `offer_emitted` / `utterance` | meta | bus | role, topic, text, workflow_id | mutual awareness, W11 correlation |

**Instrumentation buckets**: (a) **browser-direct** (Action + raw presence/visibility),
(b) **server-derived from the mastery/answers path** (Progress + student Repetition),
(c) **scheduler/tick-derived** (Time aggregates + `review_due` + `unanswered_offers`
correlation). Agent utterances are themselves published so the bus is self-observing.

---

## Part 3 — Derived: agent roles & system prompts

- **Three role *reaction* prompts** — Instructor / Coach / Mentor. The current prompts
  assume a *chat turn*; we need a **reaction variant** (or an added section) for the
  event loop: input = `{event, session_context, my_recent_utterances}`, output =
  **structured** `{speak: bool, priority, contribution, topic}`. The role must be able to
  return `speak:false`. Contributions are short (raw material). *Modify* the existing
  three prompts (add a "Reaction mode" section) rather than fork them.
- **Synthesizer prompt** *(new)* — input = the surviving contributions + context +
  recent Tutor utterances; output = one **context-weighted** message (editor, not judge).
  Must handle 0 (silent), 1 (light edit), many (weave). See auto_mode §6.
- **No prompt for the gate or the governor** — those are deterministic code (Part 4).
- Per-role **params**: low `max_tokens` for reaction passes (short), higher for the
  synthesizer; `temperature` modest; reuse the agent_server hot-reload admin API.

Net new/changed prompt files (in `agent_server/data/prompts/`): modify
`instructor`/`coach`/`mentor` (+ reaction section); add `auto_synthesizer`. Matching
`.agent.json` presets for the reaction params + the synthesizer.

---

## Part 4 — Derived: code to produce

Organized by layer. Each workflow's "active" state is just a **toggle row** the gate
checks — adding a workflow later = data + a prompt tweak, not new plumbing.

### 4.1 Agent Bus (new)
- Redis Streams wiring: a `tutor.events` stream; **one consumer group per role** + one
  for the synthesizer/governor; publish helper + typed event envelope
  `{id, type, name, ts, student, session, payload}`.
- socket.io bridge: browser ⇄ server for (a) inbound browser events, (b) outbound
  consolidated utterances. Reuse the existing socket.io layer (STT/TTS stack).

### 4.2 Frontend (`frontend/js/`)
- **Event emitters**: hook existing points in `app.js` / `question-renderer.js`
  (answer/select/navigate/open/chat/hint) to publish Action events.
- **Presence tracker** (new, e.g. `presence.js`): throttled pointer/scroll/key listeners
  + `visibilitychange`/`focus`/`blur`; maintains the *presence* vs *progress* timers and
  emits `stalled` / `idle` / `away` / `returned` / `dwell` (auto_mode §5.3 detection).
- **Inbound renderer**: receive consolidated utterances over socket.io and render them
  as proactive chat messages (distinct styling for proactive vs. user-invoked).
- **Workflow settings panel** (new): per-workflow on/off switches (W1–W10) + the W11
  intensity slider + a master "proactive assistance" switch + Auto/role mode; persists
  to the backend; the gate reads it. Surfaced via a rail/settings entry.

### 4.3 Backend (`etl/` + a new worker)
- **Producers (server-side)**: extend the `PUT /etl/sessions/{sid}/answers` path
  (already computes mastery) to publish `mastery_updated`/`milestone`/`streak` and the
  student-Repetition events (`concept_struggle`, `repeated_wrong_option`,
  `recurring_mistake`).
- **Tick/scheduler** (new): periodic job for `session_duration`, `return_after`,
  `time_of_day`, `review_due` (spaced-repetition from mastery), and the
  `unanswered_offers` correlation.
- **Pre-gate** (new, deterministic): per (student, workflow) check **toggle on +
  threshold + cooldown + governor state**; decides which roles wake. The single most
  important cost control.
- **Role reaction workers** (new): for each woken role, call its reaction prompt
  (agent_server) → structured `{speak,…}`; publish contributions as `offer_emitted`.
- **Synthesizer worker** (new): collect contributions for an event window → call
  `auto_synthesizer` (Auto) or pass through (single-role mode) → publish the consolidated
  utterance.
- **Offer/response correlator** (new): join `offer_emitted` against subsequent user
  events to produce `unanswered_offers` (feeds W11).

### 4.4 Persistence (SQLite `data/tutor.db`)
- `workflow_settings(student_email, workflow_id, enabled, params)` — the toggles + W11
  intensity. The gate's source of truth.
- `offer_log(student_email, topic, workflow_id, role, ts, responded_at)` — drives W11.
- `cooldown_state(student_email, workflow_id|topic, last_fired, backoff)` — gate state.
  (Reuse the existing per-call sqlite3 pattern from `etl/sessions.py`.)

### 4.5 Cost/latency guards (auto_mode §8)
- Single active GPU model ⇒ gate keeps woken roles to 0–1 in the common case; short
  reaction `max_tokens`; debounce browser events; only escalate to multi-role synthesis
  on "earned" moments.

---

## Part 5 — Toggle UX

- A **"Proactive assistance" settings panel**: a master switch, then a list of workflows
  (W1–W10) grouped by family (Feedback / Timing / Progress / Repetition), each a labelled
  on/off with a one-line description; W11 as an **intensity slider** ("how forward should
  the Tutor be?"). Auto vs. single-role lives with the role picker.
- **Per-student, persisted** server-side (`workflow_settings`); the gate enforces them —
  a disabled workflow never wakes a role, so it costs nothing.
- **Sensible defaults**: ON = W1, W2, W3, W6, W7, W8, W9, W10; OFF = W4, W5; W11 = medium.
- **Discoverability**: when a proactive message appears, a small affordance to "less of
  this" / "turn off" maps directly to the workflow toggle.

---

## Open questions for this layer

1. Do toggles live per-student globally, or per-session/per-package?
2. Should the synthesizer run in single-role mode at all, or only in Auto?
3. Anonymous users: which workflows degrade vs. disable (most Repetition/Progress need
   sign-in)?
4. Where do the workers run — inside `agent_server`, inside the tutor container, or a new
   small service?
