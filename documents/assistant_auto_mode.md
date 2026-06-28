# Auto Mode — Event-Driven Assistant Ensemble (design)

> **Status**: design / vision — not yet built. Companion to
> [`assistant_roles_current.md`](assistant_roles_current.md) (what exists today) and
> [`assistant_roles.md`](assistant_roles.md) (original product guide).
>
> Drafted: 2026-06-27.

---

## 1. The vision in one paragraph

Each Assistant role (Instructor / Coach / Mentor) runs as a **subscriber on an Agent
Bus** (Redis Streams + a socket.io layer) and is notified of every *relevant* event
in a Tutor session — generally something the student did. All three receive the same
events, but each **interprets them in its own personality**. When the user selects
**Auto** mode, the contributions of whichever roles choose to react are **consolidated
by an LLM into a single, coherent answer**. The individual roles remain selectable as
single-voice modes; **Auto is the blend.**

The goal is *liveness*: the application feels like an attentive presence — a teacher, a
coach, and a mentor who speak at the right moments — not a bot that comments on
everything.

---

## 2. First principles

- **Silence is the default; intervention is earned.** Like a real mentor, a role
  often has nothing useful to add. Most events produce **no output at all**. The rare,
  well-timed remark is what creates the feeling of a live presence. Reacting to
  everything is the failure mode.
- **Ensemble synthesis, NOT arbitration.** Auto does not pick one role to "win" (no
  judge). An LLM **synthesizer/editor** merges the contributions of the roles that
  chose to speak into one voice.
- **Not every role on every turn.** 0, 1, 2, or all 3 roles may contribute on a given
  event. The synthesizer handles whatever survives (none → stay silent; one → light
  edit; several → weave).
- **Personality = prompt + trigger profile.** A role's character is defined as much by
  *when it chooses to speak* (its event subscriptions and sensitivity) as by *how* it
  speaks.
- **The LLM never grades.** Grading stays deterministic (browser grader +
  `submit_answer`); roles only explain, motivate, and guide.

---

## 3. The reaction funnel (two filters + synthesis)

```
 curated events ─▶ [ deterministic pre-gate ] ─▶ eligible roles
                                                      │  (each gets an LLM "react?" pass)
                                                      ▼
                                      roles that CHOOSE to speak  (0..3)
                                                      │
                                                      ▼
                                   [ LLM synthesizer / editor ]
                                                      │
                                                      ▼
                                    one consolidated utterance  (or nothing)
```

1. **Curated events** — not every DOM event reaches the bus. Noise (rapid
   select/deselect, scrolls) is debounced or dropped; only salient events are
   published (§5).
2. **Deterministic pre-gate** (cheap, no LLM) — per-role **subscription + threshold +
   cooldown**. Decides *which roles even get to think*. This is the primary **cost**
   control: most turns wake zero or one role. Cannot be skipped — three always-on LLM
   loops would saturate the single GPU.
3. **Per-role LLM reaction** — an eligible role runs a short pass that returns either a
   contribution **or `{speak:false}` ("nothing to add")**. This is the **quality**
   control: a role can be eligible yet still stay silent. Contributions are *raw
   material*, kept short (a few sentences); the synthesizer does the wordsmithing.
4. **LLM synthesizer** — takes the surviving contributions + the live context + the
   Tutor's own recent utterances, and produces **one** message — **context-weighted**,
   not equal-thirds (see §6). If nothing survived, the Tutor stays silent.

---

## 4. Per-role trigger profiles ("response triggering levels")

Sensitivity is where the personalities diverge. Draft profiles (all tunable):

| Role | Sensitivity | Wakes on (examples) | Cooldown / restraint |
|------|-------------|---------------------|----------------------|
| **Coach** | **High** (most reactive) | graded answer (right OR wrong), retry, streak | **Strongest** cooldown + anti-repetition — otherwise becomes "great job!" spam |
| **Instructor** | Medium | wrong answer, repeated misconception on a concept, conceptual confusion / explicit question, low-ability concept viewed | Medium; avoid re-explaining what was just explained |
| **Mentor** | **Low** (rare, weighty) | milestone (e.g. package % complete), mastery transition (newly mastered / slipping), disengagement / idle, session start/end, "what should I study next" intent | High threshold — when Mentor speaks, it should *mean* something |

Two cross-cutting liveness rules (apply to every role):

- **Anti-repetition.** A role sees its own recent utterances (they're events on the
  bus too — §7) and won't repeat the same beat.
- **Earned escalation.** Routine moment → silence or a one-liner; a genuine moment (a
  streak, a breakthrough on a previously-failed concept, a third miss on the same
  concept) → a richer, multi-role consolidated response.

---

## 5. Event taxonomy (draft — the contract)

The event schema is the foundation everything depends on; get it right first. Every
event is tagged with a **type**; roles subscribe by type (and by specifics within it).
There are **four event types**:

### 5.1 Action events — *what the student just did* (producer: browser)
Direct interactions. `question_viewed{qid, concept_ids, ability?}`,
`option_selected` / `option_deselected` (low-salience; debounced),
`answer_submitted{qid, correct, attempts, concept_ids, selected}`,
`question_navigated{from, to}`, `package_opened{package_id}`,
`session_activated{sid}`, `chat_message_sent{text, addressed_role?}`,
`hint_requested{qid}`.

### 5.2 Progress events — *learning-state changes* (producer: server, `etl/sessions.py`)
Derived from the mastery model. `mastery_updated{concept_id, ability, delta,
mastered_transition?}`, `milestone{kind, value}` (e.g. 25/50/100% of a package),
`streak{count}`.

### 5.3 Time events — *temporal / pacing signals* (producer: browser + a server tick/scheduler) — NEW
The application's sense of *tempo and rhythm*. Mostly **derived**, not raw clicks —
needs a lightweight temporal tracker + scheduler.

**Presence model — the three states must be distinguished** (a naive timer conflates
them). The discriminators are *tab focus*, *micro-activity*, and *progress*:

| State | Tab | Micro-activity (mousemove / scroll / click) | Progress (answer / navigate) |
|-------|-----|------|------|
| **away** | hidden / blurred | — | — |
| **idle** | visible | none for *T* | none |
| **present-stalled** | visible | **yes, recent** | **none for *T*** |

- **`stalled{qid, seconds}`** — *the key signal.* Tab is **focused**, the student is
  **demonstrably present** (cursor/scroll/click activity within a short window), yet has
  made **no progress** (no `answer_submitted`, no `question_navigated`) on the **same
  question** for *T* seconds. This is "reading / hesitating / stuck" — the best moment
  for a proactive nudge (Instructor: offer to clarify; Coach: nudge to commit). Crucial
  rule: **micro-activity proves presence but is NOT progress** — selecting/hovering
  options keeps them "present", only answering or navigating clears the stall timer.
- **`idle{seconds}`** — tab visible but **no input at all** for *T* (may have stepped
  away without switching tabs). Softer signal than `stalled`.
- **`away{since}` / `returned{after}`** — tab hidden/blurred, then refocused
  (`visibilitychange` / `window.blur`/`focus`). Don't nudge while away; `returned` can
  warrant a light "welcome back".
- `dwell{qid, seconds, vs_norm}` — total time on the current question vs. its norm;
  **very short = likely guessing** (slow down). (Long dwell is better expressed as
  `stalled`, which is presence-gated.)
- `session_duration{minutes}` — fatigue / suggest a break.
- `return_after{days}` — re-engagement after a multi-day absence (welcome back).
- `time_of_day` — light contextual framing (late-night study, etc.).

**Detection (browser)**: throttled `pointermove`/`mousemove`/`scroll`/`keydown`/`click`
reset a *last-presence* timer (presence, not progress); `answer_submitted` /
`question_navigated` reset the *progress* timer and clear any stall; `visibilitychange`
+ `window.blur`/`focus` drive away/returned. `stalled` fires when *present && !progress
&& same qid* crosses *T*; it re-arms (with backoff) if the stall continues, so it can
escalate rather than nag.

### 5.4 Repetition events — *recurrence patterns* (producer: server-derived) — NEW
The application's **memory across attempts**. The richest source of pedagogy. Two
sub-kinds: recurrence in what the *student* does, and recurrence in what the *Assistant*
does.

**Student-side recurrence** (drives the ensemble to act):
- `concept_struggle{concept_id, misses}` — same concept missed *k* times → re-teach,
  don't repeat the same explanation.
- `answer_retry{qid, attempts}` — same question retried repeatedly.
- `repeated_wrong_option{qid, option}` — fixating on one distractor (a specific
  misconception).
- `recurring_mistake{pattern}` — the same *kind* of error across different questions.
- `review_due{concept_id, days_since}` — spaced-repetition: a previously-seen concept is
  due to resurface (time-derived, but it triggers a *repeat*, so it lives here).

**Assistant-side recurrence — self-monitoring** (drives the ensemble to *back off*):
- **`unanswered_offers{topic, count, kinds}`** — the Assistant has already offered help /
  nudged on this `topic` (concept_id or qid) **count** times **without any user
  response**, and the student kept going regardless. *Inverted semantics:* unlike every
  other event, this should make the ensemble **raise the cooldown / lower sensitivity /
  change tactic / go silent** on that topic — the explicit anti-nag governor, not a
  prompt to speak again.
  - *No response* = after an offer event, within a response window, **none** of:
    `answer_submitted` on the topic, `chat_message_sent` engaging it, acceptance of the
    offer (e.g. a hint affordance clicked), or constructive `question_navigated`.
  - Computable **because agent utterances are events on the bus** (§7): correlate each
    emitted offer with the absence of a following user action.
  - Feeds back into the pre-gate (§3) and the anti-repetition / earned-escalation rules
    (§4): repeated ignored offers should *de-escalate*, possibly switching from "offer
    again" to a different role/approach or to silence.

### 5.5 Type → role affinity (why Time and Repetition matter)
Action/Progress skew toward Coach; Time and Repetition are what feed Mentor and
Instructor — which is why they round out the ensemble:

| Type | Primary roles it feeds |
|------|------------------------|
| **Action** | Coach (immediate feedback), Instructor (corrections) |
| **Progress** | Mentor (strategy), Coach (celebrate streaks/milestones) |
| **Time** | Mentor (disengagement, breaks, welcome-back), Coach (pacing), Instructor (long dwell → hint) |
| **Repetition** | Instructor (re-teach the misconception differently), Coach (targeted drill), Mentor (schedule review) |

**Salience** — each event carries (or the pre-gate assigns) a salience so the gate and
thresholds reason uniformly. Raw UI churn is collapsed before publishing.

---

## 6. The synthesizer (LLM editor, not a judge)

- **Inputs**: the 0..3 role contributions, the live SESSION CONTEXT (package +
  current question + answered state), the triggering event, and the Tutor's recent
  utterances (for continuity / anti-repetition).
- **Job**: weave into **one coherent voice**, **context-weighted** by the moment — a
  wrong answer leans Coach (encourage + correct) with a touch of Instructor; a "what
  next?" leans Mentor. Equal-thirds every time would dilute all three into mush.
- **It is an editor, not a judge** — it *merges/emphasizes*, it does not *score and
  pick a winner*.
- **Failure mode to avoid**: concatenation — "Here's the explanation. Also, great job!
  Also, study X next." Prototype the synthesizer first and judge it by feel.
- **Cheaper fallback** (deferred): a single prompt that role-plays all three at once
  (1 LLM call). Loses the genuinely-independent interpretations; keep the independent
  passes unless latency forces the trade.

---

## 7. Bus & scheduling — REUSE existing services (not new infra)

Two services already exist on `logus2k_network` (sharing one `valkey-bus`); we build
**on** them. See `agent_bus/documents/` and `agent_scheduler/documents/`.

**`agent_bus`** — Valkey Streams **choreography** bus (gateway Socket.IO on `:6815`).
Autonomous **actors** subscribe to event *types* and emit downstream consequences; there
is **no central orchestrator**. The model we must adopt:
- Routing is per **initiator stream** `stream:<initiator_id>`; concurrent **workflows**
  are told apart by `cid`, steps by monotonic `sid` (`INCR sid:<cid>`). A triggering
  Tutor event opens a workflow (`cid`); the whole reaction chain (role contributions →
  consolidation → utterance) carries that `cid`.
- **One consumer group per actor *type*** (`cg:instructor`, `cg:coach`, `cg:mentor`,
  `cg:synthesizer`, …) — each actor independently sees every event. *(This is
  agent_bus's actual mechanism; it supersedes the earlier "one group per role" sketch —
  same effect.)*
- Delivery is **at-least-once**; handlers dedupe on `(cid, sid)`. AOF persistence,
  crash-recovery reaper (`XAUTOCLAIM`), DLQ, and an optional runaway backstop are already
  provided. No auto-cap; outlier governance is a future **Monitor** actor.
- **The LLM brain is `agent_server`**, called by actors over **REST A1** behind the actor
  seam (agent_bus architecture §8). The **two Socket.IO layers** — gateway (browser ↔
  bus) vs agent_server `Chat` (actor ↔ brain) — are already a first-class distinction
  there.
- **Agent utterances are events too**: role contributions and the consolidated message
  are published back onto the stream, giving roles mutual awareness (anti-repetition,
  deference) and feeding the `unanswered_offers` correlation (§5.4).

**`agent_scheduler`** — APScheduler + Valkey job store (admin API/UI on `:6816`). A pure
trigger actor: interval/cron/date jobs emit an `EventEnvelope` (`schedule.fired` by
default, or a custom `event_type` to a chosen target stream) when they fire. This is our
source of **server-side periodic events** (Time aggregates, `review_due`,
`unanswered_offers` correlation ticks) — no custom cron to build.

The earlier "Redis Streams + a new socket.io layer" is replaced by these two; the
envelope, the `cid`/`sid` model, and the gateway contract are fixed by agent_bus.

---

## 8. Cost & latency (single GPU, one active model)

- Constraint: **one global active chat model** (llama.cpp) — concurrent role passes
  **serialize**.
- Auto's worst case = (roles that fired) + 1 synthesis pass. Because of the pre-gate +
  self-restraint, the common case is **0–1 role + maybe a synth**, not always 4 passes.
- Mitigations: short role contributions (raw material, low `max_tokens`); fire only
  relevant roles; debounce events; reserve multi-role consolidation for escalated
  moments.

---

## 9. Relationship to current app

- The role picker gains a 4th option **Auto** (the ⚡/🤖 entry already sketched in the
  vision doc). Manual roles = single voice (current behavior); Auto = consolidated
  ensemble.
- Auto **subsumes the previously-parked "Auto mode"** idea: roles no longer *switch* —
  they coexist and self-select via subscriptions + thresholds, then consolidate.
- **Manual chat is retained.** The proactive loop *augments* chat; a user can still
  address a specific role directly (`addressed_role` on `chat_message_sent`).
- **Anonymous degradation.** Mastery/notification-driven triggers (Mentor especially)
  need sign-in; define the reduced-liveness experience for anonymous users.

---

## 10. Open decisions

1. **Synthesis weighting** — context-weighted (lean toward the role that fits the
   moment) [recommended] vs. balanced three-way.
2. **Auto scope** — proactive event loop only, manual chat only, or both.
3. **Default mode** — keep Instructor as default, or make Auto the default once it
   feels good.
4. **Threshold tuning** — static per-role thresholds first; adapt later from
   engagement signals (parked ML).
5. **Voice/TTS** — if consolidated output is spoken, it's already one voice (good); but
   confirm turn-taking with the user's own speech.
6. **Where the gate + synthesizer live** — `agent_server` orchestration vs. a new
   small bus-worker service.
```
