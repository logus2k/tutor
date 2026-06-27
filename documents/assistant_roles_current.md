# Assistant Roles — Current State (as built)

> **Purpose**: A factual snapshot of how the three Assistant roles work *today*, to
> anchor discussion of the envisioned UX. The product/design vision lives in
> [`assistant_roles.md`](assistant_roles.md); this file describes what is actually
> implemented and deployed.
>
> Last reviewed: 2026-06-27.

---

## 1. The core model

- **A role *is* an agent.** Each role is an `agent_server` preset; there is no
  separate "role engine". The existing **agent selector is the role picker** — no
  second control.
- **The app stays "Tutor"**; the three roles are **Instructor / Coach / Mentor**.
  **Default role: Instructor.**
- **One shared corpus, different framing.** All three roles ground on the *same*
  content — the active package's questions/grounding plus `web_search`. There are
  **no per-role knowledge bases**.
- **Roles differ by *system prompt only*.** Today all three are handed the
  **identical tool set** (see §4) and the **identical UI** (same chat surface, same
  panels, same affordances). The distinction is purely prompt-driven: focus, tone,
  and the workflow each is told to run.

---

## 2. The three roles (prompt-level behavior)

| Role | Focus / tone | Workflow it is instructed to run |
|------|--------------|----------------------------------|
| **Instructor** (default) | Explain & clarify; patient, pedagogical | Lean on `get_grounding`; give a short explanation plus a guiding question rather than a wall of text |
| **Coach** | Drill & feedback; encouraging, action-oriented | Student attempts → `submit_answer` grades → explain the specific mistake via `get_grounding` → `next_best_question` to keep momentum |
| **Mentor** | Strategy & motivation; supportive, holistic | `get_mastery` + `get_progress` to find gaps → recommend a concrete next step + a short study plan + encouragement |

### Rules baked into ALL three prompts
- **Never reveal the correct option before the student has answered** — nudge
  instead. After they answer, the role may explain why each option is right/wrong.
- **Never grade or assign pass/fail.** Grading is always deterministic (the browser
  `QuestionPanel` grader and the `submit_answer` tool). The LLM only explains.
- Each turn the role receives a live **SESSION CONTEXT** (active package + the
  question being viewed + whether it's answered) and is told to ground in it, use
  Markdown, and stay concise.

---

## 3. Where each role lives

- **Presets** (`agent_server/data/agents/`): `instructor.agent.json`,
  `coach.agent.json`, `mentor.agent.json`.
- **Prompts** (`agent_server/data/prompts/`): `instructor_system_prompt.txt`,
  `coach_system_prompt.txt`, `mentor_system_prompt.txt`.
- Created/updated via the agent_server **hot-reload admin API** (no restart):
  `POST/PUT http://localhost:7701/admin/api/agents` with
  `{name, system_prompt, params_override, memory_policy}`.
- **Frontend**: `frontend/index.html` `#set-agent` lists the three roles;
  `frontend/js/app.js` `ROLES[]` (icon + label), `DEFAULTS.agent='instructor'`
  (stale saved agents normalized to instructor). Status-bar pill `#sb-agent` is
  role-colored (`.sb-role-{instructor|coach|mentor}`); the chat tab shows the
  selected role's name.

---

## 4. Shared tool set (same for every role)

All roles may call the full catalog; they are told to call a tool only when it
genuinely helps:

- **Navigation** — `next_question` / `previous_question` / `goto_question(number)`,
  `open_package(package_id)`, `get_current_state`, `get_progress`.
- **Adaptive trio + mastery** (client-run in the browser) —
  - `submit_answer(answer)` — deterministic grade of an answer the student *states*
    (letter / number / id / text).
  - `get_grounding` — verbatim source passages + citations for the current question.
  - `next_best_question` — jump to the best next unanswered question for this student.
  - `get_mastery` — per-concept ability (0..1), attempts, mastery (needs sign-in).
- **External** — `web_search(query)` — only for info outside the package; cite usage.

---

## 5. Shared substrate already built (Phases 0–2)

- **Mastery model** — SQLite `mastery(student_email, concept_id, title, attempts,
  correct, ability, updated_at)`. ELO/IRT-lite: `ability += k·(target−ability)`,
  k=0.40 first-try else 0.22; `mastered = ability≥0.8 & attempts≥2`. Updated
  server-side in the `PUT /etl/sessions/{sid}/answers` handler (maps question →
  `concept_ids`). `GET /etl/mastery?package_id=` returns per-concept ability.
- **Adaptive trio + `get_mastery`** registered as **client tools** in MCP
  (`impl:client_ui`, `config.execution:client`); handlers in `app.js`
  `buildClientTools()`, logic in `question-renderer.js`. Grading stays
  deterministic — the LLM never grades.
- **Notifications / study reminders** — SQLite `notifications(...)` with dedup.
  `GET /etl/notifications` generates spaced-repetition reminders from mastery
  (not-mastered + >1d → practice; mastered + >7d → review); `POST
  /etl/notifications/read`. Status-bar **bell** (`#sb-notifications`) shows an
  unread badge and a popup menu; polled every 60s.

---

## 6. Not built / parked

- **Auto mode** — automatic role selection (rule-based → context-aware → ML) and the
  ambiguous-query handling around it.
- **Sentiment analysis** (frustration → Mentor, etc.).
- **User customization** of role behavior (e.g. a stricter Coach).
- **Effectiveness metrics** / A-B testing of roles.
- **Hybrid mode** (e.g. Tutor + Coach combined).

---

## 7. Key gap to keep in mind for UX design

Today the three roles are differentiated by **system prompt only** — they share the
same tools, the same chat surface, and the same on-screen affordances. Any envisioned
UX where a role should *look* or *behave* structurally differently (different tools
exposed, different panels, proactive vs. reactive, different default actions, distinct
entry points) is **net-new** beyond what currently exists.
