# Tutor — Vision & Technical Architecture

**Status:** Draft v0.1 · **Last updated:** 2026-06-22

---

## 1. Vision

**Tutor** turns extracted document content into an **interactive, measurable,
self-adjusting learning experience**. The flow is:

1. A document (PDF, Word, etc.) is **extracted** into clean, structured content
   with provenance (which page/section each fact came from).
2. An **LLM authoring pipeline** reformats that content into a **canonical
   question package** — a portable, versioned bank of multiple-choice questions
   (certification-exam style), each classified by **concept**, **difficulty
   (1–5)**, and **cognitive level**, and each carrying its source grounding.
3. The **Tutor application** consumes a package and runs a learning loop:
   present a question → validate the answer → explain → **adapt** the next
   question's difficulty and topic to the student → and, on demand, run a
   **clarifier LLM** (grounded on the source, with **web search** as a fallback)
   to answer the student's own questions.

The result: each package is a self-contained unit that enables an adaptive
tutoring session whose outcomes are **measurable per concept** and whose
difficulty **auto-adjusts** to each student.

### Guiding principles

- **Separate static content from runtime behaviour.** The package is a static,
  versioned artifact. Student ability, difficulty selection, and clarification
  are *runtime* behaviours driven by the package's metadata, never baked in.
- **Provenance is a feature, not a by-product.** Every concept and question
  keeps a source reference, so the clarifier can cite and the content is
  auditable.
- **Deterministic where it can be, generative where it must be.** MCQ grading is
  deterministic (compare to key). The LLM is used for *authoring* and for
  *follow-up clarification* — never for grading.
- **LLM-friendly format.** Plain JSON, flat enough to generate and validate
  reliably, rich enough to drive adaptation.

---

## 2. Scope

### In scope (this design)
- The **canonical package format** (the "standard" interchange format).
- The **authoring pipeline**: extraction → normalization → question generation →
  validation → publish.
- The **conceptual design of the Tutor runtime** (MCQ loop, validation, adaptive
  selection, clarifier LLM + web search, measurability).

### Out of scope (later phases)
- The Tutor UI/UX implementation.
- Cross-LMS interoperability export (e.g. 1EdTech **QTI**) — possible later.
- Psychometric calibration (IRT) from real response data — possible later.
- Multi-language packages, media-rich items, spaced-repetition scheduling — noted
  as future extensions in §11.

---

## 3. System overview

```
                       ┌──────────────────────────────────────────────┐
                       │            AUTHORING PIPELINE                  │
                       │  (offline, produces a versioned package)       │
                       └──────────────────────────────────────────────┘

  Documents          Extraction            Normalize            Generate (LLM)         Validate
 (PDF/Word/…)  ─►  docling / vision  ─►  source registry  ─►  questions per concept ─► schema + LLM-judge
                   + provenance          + concepts             (MCQ, rationale,        + dedup + key check
                                         + grounding             difficulty, bloom)            │
                                                                                               ▼
                                                                                   ┌───────────────────────┐
                                                                                   │  CANONICAL PACKAGE     │
                                                                                   │  (JSON, versioned)     │
                                                                                   └───────────┬───────────┘
                                                                                               │
                       ┌───────────────────────────────────────────────────────────────────── ▼ ─────────┐
                       │                              TUTOR RUNTIME                                        │
                       │                                                                                   │
                       │   Adaptive selector ─► present MCQ ─► validate (deterministic) ─► feedback        │
                       │          ▲                                   │                                     │
                       │          │            update student state ◄─┘                                     │
                       │          │                                                                         │
                       │   Clarifier LLM  ◄── student asks a question ──►  grounded on package source       │
                       │          └────────────────────────────────────►  web_search fallback when needed  │
                       └───────────────────────────────────────────────────────────────────────────────────┘
```

### Reusing existing environment services
The Tutor runtime is designed to compose services that already run in this
environment rather than introduce new infrastructure:

| Need | Existing service | Role |
|---|---|---|
| Authoring & clarifier LLM | `agent_server` (:7701) | Named **agent presets** (system prompt + sampling) over the active model; vision-capable models (Gemma 4) for the extraction step too |
| Web search | `websearch_server` | The clarifier's `web_search` tool capability |
| Grounding / RAG | `noted-rag`, `kb-service` | Optional vector retrieval over source passages for the clarifier |
| Tool routing | `mcp-service` | Optional MCP surface for tools |
| Extraction | `docling` (in `noted-graph`) and the `page_transcriber` vision agent | Two complementary extractors (see §4) |

---

## 4. Extraction layer (input to the pipeline)

Extraction produces clean content **plus provenance**. Two complementary paths
exist (validated on the AI-901 cheat sheet):

- **docling** (layout pipeline, in `noted-graph`, GPU, on-disk models):
  strongest at **structure** — heading hierarchy, accurate wrapped-cell tables
  (`--table-mode accurate`), and code/formula recovery (`--enrich-code`). Emits
  Markdown + HTML + a structured JSON with element coordinates (provenance).
- **Vision transcription** (`page_transcriber` agent on Gemma 4 over
  `agent_server`): strongest at **readability** — preserves word spacing and
  correct table columns where docling's text extraction struggles on justified
  layouts; per-page, fast (~1.2 s/page on a 4090).

Either path yields, per document: Markdown body + per-element/per-section source
locators (page + section). The pipeline does not depend on which extractor was
used; it consumes the normalized output.

---

## 5. The canonical format (the "standard")

### 5.1 Relationship model — packages ⇄ documents are **many-to-many**

- One package MAY draw on **several source documents**.
- Several packages MAY target the **same document** (e.g. a long document split
  into multiple focused packages).

Therefore documents live in a **`sources[]` registry** and are referenced by id;
they are never embedded as a single owner of a package.

```
 sources[]  ◄──────── source_id ──────── concepts[].grounding[]  and  questions[].source_refs[]
     ▲                                                  ▲
     └──────────── package.source_ids[] ───────────────┘   (a package declares which sources it uses)
```

### 5.2 Three layers (only the first ships in the package)

| Layer | Lifetime | Where it lives |
|---|---|---|
| **Package** — sources, taxonomy, concepts, questions | Static, versioned | The `.json` artifact |
| **Student state** — per-concept ability, mastery, history | Per student, mutable | Tutor runtime store |
| **Tutor config** — clarifier model, web_search policy, adaptive params | Deployment | Tutor runtime config |

### 5.3 Package schema (v1.0, sketch)

```json
{
  "schema_version": "1.0",
  "id": "ai-901-core",
  "title": "Azure AI Fundamentals (AI-901)",
  "description": "Core question bank for the AI-901 certification.",
  "generated_by": "tutor-author vX.Y (model: ...)",
  "generated_at": "2026-06-22T00:00:00Z",

  "sources": [
    {
      "id": "src-ai901-cheatsheet",
      "title": "Microsoft Certified: Azure AI Fundamentals (AI-901) Master Cheat Sheet",
      "kind": "pdf",
      "extractor": "docling+enrich",
      "extracted_at": "2026-06-22T00:00:00Z",
      "checksum": "sha256:...",
      "uri": "materials/AI-901.pdf"
    }
  ],

  "source_ids": ["src-ai901-cheatsheet"],

  "taxonomy": {
    "domains": [
      { "id": "d1", "title": "Identify AI concepts and capabilities", "weight": 0.42 },
      { "id": "d2", "title": "Implement AI solutions using Microsoft Foundry", "weight": 0.58 }
    ]
  },

  "concepts": [
    {
      "id": "c-fairness",
      "domain": "d1",
      "objective": "1.1.1",
      "title": "Fairness in responsible AI",
      "summary": "An AI system should treat all people equitably, without bias.",
      "prerequisites": [],
      "grounding": [
        {
          "source_id": "src-ai901-cheatsheet",
          "locator": "p.2 §1.1.1",
          "text": "Fairness means that an AI system treats all people equitably...",
          "citation": "AI-901 Cheat Sheet, p.2"
        }
      ]
    }
  ],

  "questions": [
    {
      "id": "q-0001",
      "concept_ids": ["c-fairness"],
      "type": "mcq_single",          // mcq_single | mcq_multi | true_false
      "render": "radio",             // optional UI hint: radio | checkbox | dropdown (see §5.5)
      "difficulty": 2,
      "bloom": "understand",
      "stem": "An AI loan-approval model denies loans more often to one demographic group. Which responsible-AI principle is violated?",
      "options": [
        { "id": "a", "text": "Reliability and safety", "correct": false, "rationale": "That concerns consistent, safe behaviour, not equitable treatment across groups." },
        { "id": "b", "text": "Fairness", "correct": true,  "rationale": "Disparate outcomes across demographic groups is the defining fairness violation." },
        { "id": "c", "text": "Transparency", "correct": false, "rationale": "Transparency is about explainability/disclosure, not equitable outcomes." },
        { "id": "d", "text": "Privacy and security", "correct": false, "rationale": "That concerns protecting data, not outcome equity." }
      ],
      "explanation": "Fairness requires equitable treatment across groups; biased denial rates are the textbook fairness violation.",
      "hints": ["Which principle is specifically about equitable outcomes across people?"],
      "source_refs": [{ "source_id": "src-ai901-cheatsheet", "locator": "p.2 §1.1.1" }],
      "tags": ["responsible-ai", "fairness"]
    }
  ]
}
```

### 5.4 Field reference (essentials)

**Package**
- `schema_version` — semver of this format; the Tutor refuses unknown majors.
- `id`, `title`, `description` — package identity.
- `generated_by`, `generated_at` — authoring provenance.
- `sources[]` — document registry (see below).
- `source_ids[]` — which sources this package draws on (subset of `sources[]`).
- `taxonomy.domains[]` — `{ id, title, weight }`; weights drive exam-like topic
  proportioning and per-domain mastery reporting.

**Source** (`sources[]`)
- `id` (referenced everywhere), `title`, `kind` (`pdf|docx|md|…`), `extractor`,
  `extracted_at`, `checksum`, `uri`.

**Concept** (`concepts[]`) — the unit of *measurement* and *adaptation*
- `id`, `domain`, `objective` (e.g. `1.1.1`), `title`, `summary`.
- `prerequisites[]` — concept ids; enables a learning-order graph.
- `grounding[]` — `{ source_id, locator, text, citation }`: passages the
  clarifier LLM is grounded on and cites.

**Question** (`questions[]`)
- `id`, `concept_ids[]` — every question maps to ≥1 concept.
- `type` — `mcq_single | mcq_multi | true_false`. Sets selection cardinality and
  the **default** input control (see §5.5).
- `render` *(optional)* — explicit presentation hint: `radio | checkbox |
  dropdown`. If omitted, the control defaults from `type`. The only non-default
  in v1 is `dropdown`, which renders a single-choice question as a `<select>`.
- `difficulty` — **ordinal 1–5** (1 = foundational … 5 = advanced).
- `bloom` — cognitive level: `recall | understand | apply | analyze`.
- `stem` — the question text.
- `options[]` — `{ id, text, correct, rationale }`; per-option rationale enables
  precise feedback and offline answer-key validation.
- `explanation` — overall teaching explanation shown after answering.
- `hints[]` — optional progressive hints.
- `source_refs[]` — `{ source_id, locator }` for grounding/citation.
- `tags[]` — free-form keywords for search/filtering.

### 5.5 Question UI contract (presentation per `type`)

The package must make the **required UI control unambiguous for every question**.
`type` sets the default control; `render` can override it. This is the exact
contract the Tutor frontend's `question-renderer` implements.

| `type` | Selection | Default control | `render` override | Grading |
|---|---|---|---|---|
| `mcq_single` | exactly one | radio buttons | `dropdown` → `<select>` | chosen id == the one `correct` id |
| `mcq_multi` | one or more | checkboxes | — | chosen id-set == set of `correct` ids |
| `true_false` | exactly one | two radio buttons (True / False) | — | chosen id == the one `correct` id |

Grading is **deterministic**: the chosen option-id set must equal the set of
options flagged `correct: true`. Per-option `rationale` and the question
`explanation` are revealed only after submission.

---

## 6. Authoring pipeline

| Stage | Input | Output | Notes |
|---|---|---|---|
| **1. Extract** | document | Markdown + structured JSON + provenance | docling and/or vision (§4) |
| **2. Normalize** | extractor output | `sources[]` entry + candidate `concepts[]` with grounding | Concepts derived from headings/objectives; grounding = the section's passages |
| **3. Generate** | concept + grounding | `questions[]` for that concept | LLM agent preset; produces stem, options, per-option rationale, explanation, difficulty (1–5), bloom; constrained to the grounding to avoid hallucination |
| **4. Validate** | questions | accepted / rejected + fixes | (a) **schema** validation; (b) **answer-key check** (exactly one correct for `mcq_single`, ≥1 for `mcq_multi`); (c) **LLM-as-judge** for factual correctness vs grounding and distractor plausibility; (d) **dedup**; (e) difficulty sanity |
| **5. Publish** | accepted questions | canonical package `.json` | Versioned; checksummed; ready for the Tutor |

The generator and judge run as **`agent_server` presets** (one author agent, one
judge agent), mirroring the `page_transcriber` pattern already in use.

---

## 7. Tutor runtime

### 7.1 Learning loop
1. **Select** next question (adaptive — §8).
2. **Present** the MCQ.
3. **Validate** deterministically against the key; show per-option rationale +
   `explanation`.
4. **Update** student state (concept ability, mastery, history).
5. **Clarify on demand** — the student can ask anything; the clarifier LLM
   answers, grounded on the relevant `concepts[].grounding` (and the current
   question), citing sources; if the grounding is insufficient it calls
   **`web_search`** and cites what it finds.
6. Loop until a session/mastery goal is met.

### 7.2 Clarifier LLM
- **Grounding-first:** retrieve the concept's `grounding[]` passages (optionally
  via `noted-rag`) and answer from them, citing `citation`.
- **Web fallback:** when the question exceeds the package's content, call
  `websearch_server`'s `web_search`, then synthesize with citations.
- **Boundaries:** the clarifier explains and complements; it never reveals
  unattempted answer keys and never grades.

### 7.3 Measurability
Because every question carries `concept_ids`, `difficulty`, and `bloom`, the
runtime can report: per-concept mastery, per-domain coverage (weighted by
`taxonomy.weight`), difficulty progression, and time-to-mastery — all derived
data, none stored in the package.

---

## 8. Adaptive learning model

- **Ability estimate** per student per concept (start simple: an ELO/IRT-lite
  rating updated from correct/incorrect outcomes; upgradeable to full IRT once
  real response data exists).
- **Difficulty selection ("the LLM decides the level"):** the selector targets a
  difficulty band matched to the student's current ability for the weakest
  in-scope concept (slightly above ability to maximise learning). Questions are
  **pre-classified 1–5**, so selection is a lookup, not generation.
- **Topic selection:** prioritise low-mastery concepts, respecting
  `prerequisites` (don't advance to a concept whose prerequisites aren't met)
  and `taxonomy` weights (spend effort proportional to exam weight).
- **Mastery:** a concept is "mastered" when the ability estimate clears a
  threshold across a spread of difficulties/Bloom levels.

---

## 9. Difficulty & cognitive taxonomy

- **Difficulty:** ordinal **1–5** assigned by the generator and confirmed by the
  judge. 1 = recall of a single fact; 5 = multi-step application/analysis or
  fine distinctions between close concepts.
- **Cognitive level (`bloom`):** `recall | understand | apply | analyze` —
  orthogonal to difficulty; lets the runtime ensure coverage across cognition,
  not just facts.

---

## 10. Locked design decisions

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Interchange format | **Custom JSON** (not QTI for now) | LLM-friendly, fast to iterate; QTI export possible later |
| 2 | Difficulty scale | **Ordinal 1–5** | Simple to assign and adapt on; IRT calibration later |
| 3 | Package ⇄ document | **Many-to-many** via `sources[]` + `source_ids[]` | A package may span documents; a long document may yield several packages |
| 4 | First build scope | Schema + generator + validator + **one real sample package from AI-901**; Tutor runtime is a later phase | De-risk the format with a concrete artifact before building the app |
| — | Grading | **Deterministic**, LLM only for authoring + clarification | Correctness and trust |
| — | Rationales/explanations | **Pre-generated** at authoring time | Quality + instant feedback + offline validation |

---

## 11. Open questions & future extensions

- **QTI / 1EdTech export** for LMS interoperability.
- **IRT calibration** of difficulty from real student responses.
- **Multi-language** packages (parallel `stem`/`options` per locale).
- **Media-rich items** (images/diagrams in stems — our extraction already
  captures figures).
- **Spaced-repetition** scheduling metadata for long-term retention.
- **Question types** beyond MCQ (ordering, matching, short-answer with LLM
  grading rubric).
- **Package composition** (merging/splitting packages; shared concept ids across
  packages drawing on the same source).

---

## 12. Roadmap

1. **Phase 1 — Format & authoring (next):**
   formalize the JSON Schema; build the `author` + `judge` agent presets; run the
   pipeline on the AI-901 extraction; publish `ai-901-core` as the reference
   package; ship a small validator/CLI.
2. **Phase 2 — Tutor runtime core:** package loader, deterministic MCQ loop,
   student-state store, basic adaptive selector.
3. **Phase 3 — Clarifier:** grounded clarifier LLM + `web_search` fallback with
   citations.
4. **Phase 4 — Adaptation & analytics:** ability/mastery model, per-concept and
   per-domain measurement, dashboards.
5. **Phase 5 — Extensions:** from §11, prioritized by need.

---

## 13. Repository layout (proposed)

```
~/env/assets/tutor/
├── documents/
│   └── technical_architecture.md      ← this document
├── schema/
│   └── package.schema.json            ← formal JSON Schema (Phase 1)
├── packages/
│   └── ai-901-core.json               ← reference package (Phase 1)
└── (author/, validator/, runtime/ …   ← later phases)
```
