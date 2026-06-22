# Tutor — Tool Ideas

**Status:** Brainstorm · **Last updated:** 2026-06-23

Ten tool ideas for the Tutor assistant, grounded in the vision in
[technical_architecture.md](technical_architecture.md) — adaptive selection,
measurability, and provenance.

## Architecture recap (how tools work here)

- **MCP is the unified catalog.** Every tool's schema lives in `mcp-service`
  under `app=tutor`, each tagged `config.execution = "client" | "server"`.
- **The browser is the host and runs the loop** (`frontend/js/chat.js`): it
  advertises `tools[]` to the model, and on each `tool_call` it dispatches —
  **client** tools run in JS (against the `QuestionPanel` + the live context
  store), **server** tools are forwarded to `mcp-service` `/invoke`.
- `agent_server` only relays `tool_calls`; it does not execute tools.

Already built: `next_question`, `previous_question`, `goto_question`,
`open_package`, `get_current_state`, `get_progress` (client) and `web_search`
(server).

---

## The 10

### 1. `submit_answer` — *client*
The student says "I'll go with B" (or by voice); the tutor submits it, the
**deterministic grader** runs, and the tool returns correct/incorrect plus the
revealed rationale. Makes the whole loop hands-free and keeps grading
deterministic (the LLM never grades). Touches `QuestionPanel` state.

### 2. `reveal_explanation` — *client, code-gated*
Returns the current question's per-option rationale + `explanation` — **only if
already answered**. Moves the "never reveal unattempted answers" rule from a
prompt request into enforced code, so it cannot leak even under coaxing.

### 3. `next_best_question` (adaptive select) — *client*
The centerpiece of the vision ("the LLM decides the level"): pick the next
question targeting the student's **weakest in-scope concept** at a difficulty
just above their estimated ability, respecting `prerequisites` and `taxonomy`
weights. Returns the chosen question and navigates to it.

### 4. `get_grounding` — *client*
Returns the `grounding[]` passages + `citation` for the current question's
concept(s). Lets the tutor explain **from source and cite** rather than from
memory — the grounding-first clarifier (§7.2), with provenance.

### 5. `search_source_documents` — *server (RAG)*
Beyond `web_search`: retrieve passages from the package's **own uploaded
documents** via `noted-rag` / `kb-service`. With the ETL/Documents track
ingesting PDFs, this answers "where in the material does it say that?" with real
citations into the source doc.

### 6. `progress_report` / `mastery_map` — *client*
Compute **per-concept mastery** and **per-domain coverage weighted by
`taxonomy.weight`**, plus difficulty/Bloom spread (§7.3). Returns a summary the
tutor narrates (and could render as a small dashboard).

### 7. `start_exam` — *client*
Assemble a **timed mock exam** mirroring the real cert: sample questions
proportional to domain weights, spread across difficulty/Bloom, no feedback
until the end, then a scored report. Exam simulation straight from the taxonomy.

### 8. `flag_for_review` + `review_queue` — *client*
Flag a question or concept to revisit; persist a **spaced-review queue**
(localStorage now, server later). The tutor resurfaces weak items next session —
the seed of spaced repetition (§11).

### 9. `generate_practice_question` — *server (author agent)*
On demand, call an **author agent preset** on `agent_server` to draft a *new*
practice item for a concept, constrained to that concept's `grounding[]`
(mirrors the authoring pipeline §6). Flagged as draft/unvalidated.

### 10. `open_source` — *client*
Jump the **Documents pane** to the exact source page/section behind the current
question (`source_refs[].locator`). One click from "why is B right?" to the
cited passage — provenance as a feature, tying the Questions, Documents, and
chat panes together.

---

## Bonus / quick wins

- `hint` *(client)* — reveal the next progressive `hints[]` entry, tracked so it
  never over-reveals.
- `set_difficulty` *(client)* — "give me harder ones" shifts the selector's band.
- `summarize_session` *(client)* — recap covered concepts, weak spots, next steps.
- `translate_question` *(server)* — translate stem/options to another locale
  (the multi-language extension, §11).

---

## Suggested first build

A high-impact trio, all client-side (no new infra), that turns the tutor from
"explains" into "runs the adaptive learning loop":

1. **`next_best_question`** (#3) — the adaptive vision.
2. **`submit_answer`** (#1) — closes the conversational loop.
3. **`get_grounding`** (#4) — grounded, cited explanations.
