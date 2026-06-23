# Tutor — ETL Architecture

**Status:** Draft v0.1 · **Last updated:** 2026-06-22
**Companion to:** [`technical_architecture.md`](technical_architecture.md) (defines the canonical package format this ETL produces)

---

## 1. Purpose

Automate the path from **"a user uploads one or more documents"** to **"one or
more validated, published Tutor packages"** — with no manual editing in the happy
path. This document specifies the **Extract → Transform → Load** pipeline, the
**LLM agent presets** it relies on, the **orchestration** that sequences them,
and the **operational concerns** (idempotency, retries, versioning, observability).

All LLM steps run as **`agent_server` agent presets** over whatever chat model is
active at the time. For this design we **assume Gemma 4 E4B** (`gemma-4`,
vision-capable, 64K context), but the pipeline is **model-agnostic** — it calls
agents by name, never a model id (per `agent_server`'s contract).

> Scope boundary: the ETL **produces and publishes** packages. The Tutor runtime
> (MCQ loop, clarifier, adaptation) is a separate client-side effort and is out
> of scope here except as the *consumer* of what we publish.

---

## 2. High-level flow

```
 upload (1..N docs + packaging directive)
        │
        ▼
 ┌──────────────┐   ┌───────────────────────────────────────────────┐   ┌──────────────┐
 │   EXTRACT     │   │                 TRANSFORM                      │   │     LOAD      │
 │              │   │                                                │   │              │
 │ ingest+hash  │──►│ segment → concepts → generate Qs → validate    │──►│ assemble →   │
 │ docling/visn │   │ (LLM agents: concept_extractor, question_author,│   │ version →    │
 │ + provenance │   │  question_judge)                               │   │ publish →    │
 └──────────────┘   └───────────────────────────────────────────────┘   │ index (RAG)  │
        │                          │                                      └──────────────┘
        ▼                          ▼                                             │
   raw + normalized          per-concept question sets                          ▼
   content (cached)          (accepted/rejected)                      package(s) + job report
```

Everything is orchestrated as a **resumable job** with a per-job **work
directory**; each stage writes its output so the job can resume from the last
good stage (the same pattern proven in the AI-901 extraction work — per-page
files, balanced assembly, etc.).

---

## 3. Job model & entry point

### 3.1 Trigger & surface
A package build is an **ETL job**. The **primary surface is Tutor's frontend**
(its administration area, client-side, already under development): a user
uploads one or more documents there, sets the packaging/generation directive,
watches progress, and sees the result appear as a new entry in **Tutor's
Catalog**. The frontend is a *client* of this ETL backend — the backend owns the
job, the frontend renders it.

Triggers (both drive the same job model):
- **API (consumed by the admin UI):** `POST /etl/jobs` (multipart: documents +
  JSON directive); progress is observable via §3.3 so the UI can show the
  upload → extract → transform → load → *Catalog* journey live.
- **CLI:** `tutor-etl build --docs a.pdf b.pdf --directive directive.json`
  (for batch/back-office use, no UI).

The end state of a successful job is a **published package that the Tutor Catalog
lists** (see §6 Load). The admin area is therefore the window over the whole
ETL — from upload to Catalog entry — while the Catalog itself is the
student-facing list the runtime reads.

### 3.2 Job request (contract)
```json
{
  "job_id": "auto-or-supplied",
  "documents": [
    { "uri": "uploads/ai-901.pdf", "kind": "pdf", "title": "AI-901 Cheat Sheet" }
  ],
  "packaging": {
    "strategy": "auto",            // auto | single | by_domain | fixed_n
    "max_questions_per_package": 400,
    "target_package_count": null   // used when strategy = fixed_n
  },
  "generation": {
    "questions_per_concept": 4,
    "difficulty_distribution": { "1": 0.15, "2": 0.25, "3": 0.30, "4": 0.20, "5": 0.10 },
    "types": ["mcq_single", "mcq_multi", "true_false"]   // author also emits `render` (see §5.2)
  },
  "extraction": { "extractor": "auto", "ocr": false },   // auto | docling | vision
  "review": { "extraction": "hold_for_review" }          // hold_for_review (out-of-band human) | auto
}
```

Question `type`s and the `render` hint follow the **UI contract** in
`technical_architecture.md` §5.5 (`mcq_single`→radio/`dropdown`,
`mcq_multi`→checkboxes, `true_false`→radio). The generator emits `render` so the
frontend control is unambiguous for every question.

### 3.3 Job response / status
```json
{
  "job_id": "...",
  "state": "succeeded",            // queued | extracting | transforming | loading | succeeded | failed | held
  "stages": [ { "name": "extract", "state": "succeeded", "metrics": {...} }, ... ],
  "packages": [ { "id": "ai-901-core", "uri": "packages/ai-901-core.json", "questions": 312 } ],
  "warnings": [ "concept c-foo: only 2/4 questions passed the judge" ],
  "report_uri": "jobs/<id>/report.json"
}
```

**Live progress for the admin UI (socket.io).** Real-time progress is pushed over
**socket.io** — the project-wide convention for live communication (same as
`agent_server`'s `Chat`/`RunStarted`/`ChatChunk` events). `GET /etl/jobs/{id}`
remains for polling/late-join reconciliation, but the admin area renders the live
**upload → extracting → transforming → loading → published in Catalog** journey
from the socket.io event stream defined in §3.5.

### 3.4 Job state machine
```
queued → extracting → [held: extraction review] → transforming → [package-quality judge] → loading → succeeded
                            (out-of-band human,                         │
                             opt-in per review.extraction)              └─ low score → held / warnings
        any stage → failed (with stage + reason; resumable from last good stage)
```

Two quality gates:
- **Human review of extraction (out-of-band, opt-in).** When
  `review.extraction = hold_for_review`, the job pauses at **held** right after
  EXTRACT with the extracted Markdown/JSON available for a human to inspect/correct
  outside the pipeline; an explicit *resume* advances to TRANSFORM. With `auto`
  it flows straight through (artifacts are still retained — §9, §13).
- **LLM-as-Judge on package quality (automated).** After TRANSFORM, a
  `package_judge` agent scores the assembled package as a whole (coverage,
  balance, redundancy, difficulty spread) before LOAD — see §5.5. This is in
  addition to the per-question `question_judge` (§5.3); both may run on the same
  active model.

### 3.5 Real-time progress events (socket.io)

The orchestrator emits a socket.io event at every meaningful transition, on a
per-job channel (`job:{jobId}`). The admin UI joins that channel on upload and
renders the whole journey live. Event names use a `noun.verb` scheme:

| Event | Payload | Meaning |
|---|---|---|
| `job.queued` | `{ jobId, documents }` | Upload accepted; job created |
| `stage.started` / `stage.done` | `{ jobId, stage }` (`extract`\|`transform`\|`load`) | Stage boundaries |
| `extract.progress` | `{ jobId, page, totalPages }` | Per-page extraction (vision path) |
| `concept.gated` | `{ jobId, objective, title, action }` | A chunk passed the gate |
| `concept.skipped` / `concept.merged` / `concept.cached` | `{ jobId, objective, … }` | Gate remediation / resume |
| `question.judged` | `{ jobId, qid, accepted }` | One question accepted/rejected |
| `question.validated` | `{ jobId, qid, status }` | Answer-blind solve: `agree`\|`dispute`\|`inconclusive` |
| `transform.progress` | `{ jobId, conceptsDone, questionsAccepted }` | Rolling counters |
| `package.judged` | `{ jobId, score, publishable, disputes }` | Package-quality verdict |
| `job.held` | `{ jobId, reason, score?, disputes? }` | Awaiting human review (extraction, low score, or key disputes) |
| `job.published` | `{ jobId, packageId, catalogEntry }` | **Package live in the Catalog** (UI's "done") |
| `job.failed` | `{ jobId, stage, error }` | Terminal failure |

The orchestrator is already instrumented with these (an `emit(event, …)` seam,
see `etl/orchestrator.py`); the service layer binds `emit` to a socket.io
broadcast on `job:{jobId}`. Because state is also persisted (job record + work
dir), a client that connects late calls `GET /etl/jobs/{id}` once to catch up,
then follows the live stream — no events are lost.

### 3.6 Implementation status

The pipeline and service are implemented and tested end-to-end on AI-901:

| File | Role |
|---|---|
| `etl/service.py` | FastAPI + socket.io service: `POST /etl/jobs`, `GET /etl/jobs[/{id}]`, `GET /etl/health`, socket.io at `/etl/socket.io` (join room `job:{jobId}`, `job.snapshot` on join) |
| `etl/extract.py` | docling extraction (in `noted-graph`) + markdown cleanup → md + DoclingDocument JSON |
| `etl/clean_markdown.py` | re-levels headings, strips furniture, escapes loose code |
| `etl/orchestrator.py` | segment → gate → author → judge → assemble → package-judge → validate → write (env-parameterized; emits `EVENT` lines bridged to socket.io) |
| `etl/catalog.py` | maintains `data/packages/index.json` (Catalog) and `data/documents/index.json` |
| `schema/package.schema.json` | the package contract (hard-validated; a missing/broken validator fails loudly — no silent fallback) |

Verified end-to-end (live socket.io lifecycle `job.queued → stage.* → concept.gated → question.judged → dedup.done → package.judged → job.published → catalog.updated`):
- **Re-build path** (`directive.sourceMd`/`sourceJson`) — re-package an existing extraction without re-extracting.
- **Upload path** (`files[]`) — PDF → docling → full pipeline → publish.

**Catalog hygiene:** only `published` packages enter `data/packages/index.json`; `held`/`failed` packages are kept out of the student-facing Catalog.

**Remaining for production:** containerize the service (it currently shells `docker exec noted-graph` for docling — fine on the host, needs a network/extraction-service call from inside a container); multi-document-per-package jobs; a review area for `held` packages; generic (non-numbered) document segmentation; bloom-balance tuning of the author.

---

## 4. EXTRACT

| Step | Detail |
|---|---|
| **Ingest** | Accept `pdf`, `docx`, `md`, etc. Persist the original under the job work dir. |
| **Content-address** | Compute `sha256`. This becomes the `sources[].checksum` and the **extraction cache key** — re-uploading the same bytes reuses cached extraction. |
| **Extract** | Run the chosen extractor (§4.1) inside `noted-graph` (docling) or via `agent_server` (vision). Output: clean **Markdown** + **structured JSON** + **per-section provenance** (`page`, `section`). |
| **Normalize** | Emit a `sources[]` registry entry and a **section tree** (heading hierarchy → ordered sections, each with text + locator). Strip page furniture (`SKILLCERTPRO`, page numbers, promo blocks) here so it never enters a chunk — the first stage of the **sufficiency funnel** (§5.0): structural low-content is removed deterministically, before any LLM call. |

### 4.1 Extractor selection policy (`extraction.extractor`)
- **`docling`** (default for structured docs): best heading hierarchy + accurate
  tables (`--table-mode accurate`) + code recovery (`--enrich-code`). Use when
  the document is layout-rich (tables, code).
- **`vision`** (`page_transcriber` on the active vision model): best word-spacing
  and table-column fidelity on justified/dense layouts. Use as an **alternative
  or fallback** when docling's text extraction degrades (e.g. lost inter-word
  spaces, scrambled narrow columns).
- **`auto`**: run docling; if QA heuristics flag degraded text (e.g. high rate of
  space-loss tokens or malformed tables), fall back to vision for affected pages.

> Both extractors were validated on AI-901; the Transform stage consumes the
> normalized section tree and does **not** care which extractor produced it.

---

## 5. TRANSFORM

The Transform stage is a **fan-out pipeline over concepts**. Each section/concept
flows independently through generation and validation, so a slow or rejected item
doesn't stall the rest.

### 5.0 Chunking strategy (a deterministic loop that *consults* the LLM gate)

Chunk **boundaries are computed by deterministic code** — but the code does not
accept a chunk blindly. It **consults the `concept_extractor` coherence gate** and
refines boundaries from the gate's verdict. So the *control flow* is deterministic
and reproducible, while the *judgment* "does this chunk make sense" is the LLM's.
An LLM never draws boundaries freely (that would break id stability); it only
*advises* the deterministic loop.

Algorithm, per document:
1. **Propose + pre-filter (code):** walk the `DoclingDocument` reading-order tree;
   cut on the heading/objective hierarchy; **never split a table**; cap each unit
   to a token budget; group tiny sibling sections up to a target size. **Then drop
   the obviously empty** — page furniture (`SKILLCERTPRO`, promo blocks, page
   numbers), table-of-contents/copyright, and any section **below a minimum
   content threshold** (word/token floor). This *structural* insufficiency is
   decided here, **before any LLM call** — junk never reaches the gate.
2. **Consult (LLM):** send each surviving chunk to `concept_extractor`, which
   judges **semantic** sufficiency and returns `{ usable, reason,
   suggested_action, … }` (the gate, §5.1). This is the *first* LLM touchpoint and
   only adjudicates the ambiguous "has text but is it question-worthy?" cases the
   pre-filter can't.
3. **Remediate (code, bounded):** act on `suggested_action` —
   `accept` → keep chunk + concept; `merge_next`/`merge_prev` → join the named
   neighbour (same parent, respect the token cap) and **re-consult**; `split` →
   sub-split on sub-headings/paragraphs and re-consult each part; `skip` → drop
   (no teachable content) and log. Cap remediation at a few rounds per region;
   anything still unresolved is routed to the **out-of-band human review** (§3.4).
4. **Stop** when every region is `accept`ed or `skip`ped.

**Reproducibility.** Gate verdicts run at low temperature and are **cached by
chunk content-hash**, so re-runs replay the same boundaries (and skip the LLM
calls) — keeping concept/question ids stable for the checksum cache (§9).

**Inputs.** The JSON/`DoclingDocument` computes **boundaries + provenance**
(locators); the **Markdown slice** of each chunk is what `concept_extractor` (and
later `question_author`) actually read. Raw `DoclingDocument` JSON is never sent
to a model. The no-split-table and token-budget rules of step 1 are hard
constraints.

> Chunking is therefore **not its own agent** — it is deterministic orchestration
> that *calls* `concept_extractor`. The only LLM judgment involved (chunk sense +
> concept extraction) lives in that one role.

### 5.1 Segment → concepts (with coherence gate)
- **Deterministic skeleton:** derive concept candidates from the heading/objective
  structure (e.g. `1.1.1 …` → one concept), preserving `domain` and `objective`.
- **Gate + enrichment (`concept_extractor` agent):** in one pass over the chunk it
  (a) **gates** the chunk — `usable`, `reason`, `suggested_action`
  (`accept | merge_next | merge_prev | split | skip`) — and (b) when usable,
  produces the concept `summary`, selects the best **grounding passages** (verbatim
  spans with `locator`), suggests `prerequisites`, and tags. The chunking loop
  (§5.0) drives boundary remediation from the gate verdict; only `accept`ed chunks
  yield concepts.
- **Output:** `concepts[]` with grounding, ready for question generation (plus a
  log of `skip`/merge/split decisions for the job report).

### 5.2 Generate questions (`question_author` agent)
- **Input per call:** one concept + its grounding passages + the generation
  config (`questions_per_concept`, `difficulty_distribution`, allowed `types`).
- **Output:** MCQs with `stem`, `options[]` (each with `correct` + `rationale`),
  `explanation`, `hints[]`, `difficulty` (1–5), `bloom`, `source_refs`, `tags`.
- **Grounding constraint:** the author must produce questions answerable *from
  the grounding* and cite `source_refs`; this is the primary hallucination guard.
- **Difficulty spread:** the author targets the configured distribution so the
  adaptive selector has range across 1–5.

### 5.3 Validate (`question_judge` agent + deterministic checks)
A question is **accepted** only if it passes, in order:
1. **Schema** — conforms to the package schema (types, required fields).
2. **Key integrity** — exactly one `correct` for `mcq_single`; ≥1 for
   `mcq_multi`; well-formed `true_false`.
3. **Factual correctness** — `question_judge` verifies the keyed answer and each
   distractor's `rationale` against the **full source document** (LLM-as-judge;
   default-reject on uncertainty). The judge receives the *entire* document as
   authoritative `source_text` (it fits the 64K window), not just the extractor's
   highlighted spans — many keys depend on a fact in a *different* section (e.g. a
   global task→service mapping table), which span-only grounding cannot verify.
4. **Distractor quality** — distractors are plausible and mutually exclusive; no
   "all/none of the above" unless intended; never *every* option correct.
5. **Difficulty/Bloom sanity** — label matches the item's actual demand.
6. **Polarity** — for negative stems (NOT/EXCEPT/LEAST) the correct answer is the
   odd-one-out; the judge rejects inverted keys and unanswerable "all-belong" stems.
7. **Answer-blind validation** (`answer_validator` agent) — see §5.3.1.

Rejected items are either **auto-repaired** (one bounded regeneration attempt
with the judge's feedback) or **dropped with a warning** recorded in the report.

#### 5.3.1 Answer-blind validation (second opinion that re-solves the question)

`question_judge` is a *verifier*: shown the key, a small model (E2B) tends to
**rubber-stamp** it — confirmed in testing, where it accepted a question that keyed
"find entities → Computer Vision" even with the full document (which says
*entities → Language/NLP*) in context. The fix is to change the framing from
*verify* to *solve*: the **`answer_validator`** agent receives the question with the
`correct` flags **stripped** plus the full document, and **independently derives the
answer** (quoting the deciding line). The same E2B model that rubber-stamped the
wrong key solves it correctly when asked this way.

- **Self-consistency** — sampled `ETL_VALIDATE_N` times (default 3, temp ~0.3);
  the majority answer is taken. No majority ⇒ treated as a dispute (model unsure).
- **Deterministic compare** — code compares the derived option-id set to the stored
  key. On mismatch the question is recorded in `quality.disputes` and the **whole
  package is held** (not published) for out-of-band human review — the validator is
  fallible too, so keys are **never auto-flipped**.
- This one mechanism catches both **semantic mis-keys** and **polarity inversions**.
- Toggle with `ETL_VALIDATE=0`; it is the same model/cost order as the judge, kept
  cheap because the (stable) document prefix is sent first and can be cache-reused.

> **Limitation (honest):** this raises recall, it does not guarantee correctness.
> Where E2B's knowledge is *systematically* wrong it will solve the same wrong answer
> as the author (correlated error). Human review of `held` packages remains the final
> net; a stronger judge model (E4B) would raise the ceiling at the cost of an
> active-model switch.

Before the LLM judge, a **deterministic sanitize guardrail** runs on every
authored question: it coerces `type`/`render` to the valid enums, reconciles them
with the actual `correct`-option count (e.g. a single-choice with two keys becomes
`mcq_multi`+checkbox), defaults an out-of-range `difficulty`/`bloom`, and **drops**
anything unsalvageable (no options, no correct answer). This guarantees the
assembled package is always schema-valid even when the model emits a stray field —
a real case caught in testing (a `bloom` value leaked into `type`). The package is
also validated against `schema/package.schema.json` at the end of Load.

### 5.4 Concurrency & context
- All LLM calls share **one active model** on `agent_server` → run with **bounded
  concurrency** (e.g. 2–4 in flight; llama.cpp queues the rest). The **author** works
  per-concept (focused chunk → targeted questions, stable concept ids), but the
  **judge and `answer_validator` are given the whole document** as ground truth — at
  ~44.5K tokens for AI-901 it fits the 64K window comfortably. (For a document that
  exceeds the budget, `ETL_JUDGE_SOURCE_CAP` truncates to a leading slice; very large
  corpora would need section-level retrieval — a future upgrade.)
- Agents run with `enable_thinking: false`, low `temperature` for the author,
  and a stricter judge preset.

### 5.5 Package-quality judge (LLM-as-Judge)

Distinct from the per-question `question_judge`, a **`package_judge`** agent
evaluates the *assembled package as a whole* before publish:
- **Coverage** — every domain/objective has questions; no orphaned concepts.
- **Balance** — difficulty spread matches the requested distribution; Bloom
  levels are represented; per-domain counts roughly track `taxonomy.weight`.
- **Redundancy** — no clusters of near-duplicate questions across concepts.
- **Internal consistency** — each question's `render`/`type` is valid and its
  keyed answer is well-formed.

It emits a **quality score + findings** (each finding tagged
`info`/`warn`/`error`). **Publishability is decided deterministically in code,
not by the model's own boolean** — testing showed the model returned
`publishable=false` even with only `warn` findings, violating its own rubric. The
orchestrator publishes when there are **no `error`-severity findings AND score ≥
threshold** (default 60); otherwise the job goes to **held** with the score +
findings attached as advisory `quality` metadata on the package, so the
out-of-band reviewer can publish anyway, regenerate, or discard. The score/findings
are advisory; the gate is deterministic — same philosophy as deterministic
grading. Runs on the active model for now; can move to a stronger judge model
later without changing the contract.

---

## 6. LOAD

| Step | Detail |
|---|---|
| **Assemble** | Compose accepted concepts + questions into one or more packages per the **packaging policy** (§7). Fill `taxonomy` (domains + weights), `sources[]`, `source_ids[]`, `generated_by`, `generated_at`. |
| **Version** | Package id + **semver**; filename carries version; embed a content `checksum`. Re-runs that change content bump the version; identical content is a no-op. |
| **Publish** | **Filesystem store for now:** write `packages/<id>@<version>.json` and update `packages/index.json` — the registry that backs **Tutor's Catalog** (the frontend fetches packages straight from this directory). On success the package becomes a visible Catalog entry (the UI's "done" state). A DB/object store can replace the file store later without changing the package contract. |
| **Index for clarifier (later)** | **Deferred.** v1 ships the grounding passages *inline* in the package, which is enough for the runtime clarifier. A later phase will also push grounding/full source to **`noted-rag` / `kb-service`** for richer retrieval — but RAG is explicitly a follow-on, not part of the first ETL. |
| **Report** | Emit `jobs/<id>/report.json`: per-stage metrics, accept/reject counts per concept, warnings, resulting package ids. |

If the `package_judge` score (§5.5) is below threshold, the job stops at **held**
before Publish with the findings + a preview artifact; an explicit approve call
then performs Publish. (Human content review happens earlier and out-of-band,
after EXTRACT — §3.4.)

---

## 7. Packaging policy (many-to-many)

Packages and documents are **many-to-many** (per the architecture doc). The
packaging directive resolves how concepts map to packages:

- **`single`** — all concepts from all input documents into one package.
  `sources[]` lists every document; `source_ids[]` references all.
- **`by_domain`** — one package per top-level domain (good for large subjects).
- **`fixed_n`** — split into `target_package_count` packages along domain/section
  boundaries, balancing question counts.
- **`auto`** (default) — single package, but **auto-split** when estimated
  questions exceed `max_questions_per_package`; split along domain boundaries and
  record which `source_ids` each resulting package actually uses.

Cross-document handling:
- **Multiple docs → one package:** concepts from all docs are merged; overlapping
  concepts (same objective/topic) are **deduplicated**, and their grounding is
  unioned across sources.
- **One long doc → multiple packages:** the doc appears in every resulting
  package's `sources[]`; each package's `source_ids` still points at it.

---

## 8. Agent presets (the LLM contracts)

All created/maintained via `agent_server`'s admin API (hot-reload, no restart),
mirroring the existing `page_transcriber`.

| Agent | Role | Key params | Output |
|---|---|---|---|
| `page_transcriber` *(exists)* | Vision extraction (fallback/alternative) | vision, temp 0, fences code | Markdown per page |
| `concept_extractor` | **Coherence gate + extraction**: judge chunk sense, then (if usable) concept summary + grounding selection + prerequisites | temp 0.1, thinking off | `{usable, reason, suggested_action, title, summary, grounding[], prerequisites[], tags[]}` |
| `question_author` | Concept + grounding → N MCQs across difficulty 1–5 | temp 0.3, thinking off, larger `max_tokens` | `questions[]` (schema-shaped) |
| `question_judge` | Verify correctness, distractors, labels vs grounding (per question) | temp 0.0, thinking off, strict | `{verdict, issues[], fixes?}` |
| `package_judge` | LLM-as-Judge on the **whole package**: coverage, balance, redundancy, consistency (§5.5) | temp 0.0, thinking off, strict | `{score, findings[]}` |
| `package_curator` *(optional)* | Package title/description + taxonomy weights | temp 0.2 | `{title, description, taxonomy}` |

Each preset's system prompt encodes its rules and **must emit only JSON** (the
authoring/judge agents), so the orchestrator parses deterministically. (The
existing infra already validates agent JSON strictly at create time.)

**Status:** the five Transform/Load presets — `concept_extractor`,
`question_author`, `question_judge`, `package_judge`, `package_curator` — are
**created on `agent_server`** (admin API; prompts persisted under
`agent_server/data/prompts/`, configs under `data/agents/`), alongside the
existing `page_transcriber`. They are callable now by name via
`POST /v1/chat/completions`. Chunking is **not** an agent — it is the deterministic
loop in §5.0 that consults `concept_extractor`.

---

## 9. Idempotency, caching, versioning

- **Document checksum** keys the extraction cache → re-extracting identical bytes
  is skipped.
- **Deterministic ids:** concept id = `<objective-slug>` (or stable hash of the
  section); question id = stable hash of `(concept_id, stem, options)` → re-runs
  are diffable, not duplicative.
- **Resumable stages:** each stage writes to the work dir; a failed job resumes
  from the last good stage.
- **Package semver:** content-equal rebuild = no-op; content change = version
  bump; the registry keeps history.

---

## 10. Error handling & observability

- **Retries:** transient `agent_server` / extractor errors retried with backoff
  (the AI-901 transcription pattern).
- **Bounded repair:** one judge-guided regeneration per rejected question, then
  drop-with-warning — the job never hangs on one bad item.
- **Per-stage metrics:** counts + timings into the job report (concepts found,
  questions generated/accepted/rejected/repaired, dedup drops, per-domain
  coverage).
- **Logs:** structured, correlated by `job_id` + stage; LLM call traces optional.
- **Quality gates:** a job can fail/hold if acceptance rate or per-domain coverage
  falls below configurable thresholds (prevents publishing a thin package). This
  includes a **document-level guard** — if too few chunks survive the sufficiency
  funnel (§5.0) to form a meaningful package (e.g. the upload was mostly furniture
  or off-topic), the job is **held/failed** rather than publishing a hollow
  package.

---

## 11. Integration points (existing services)

| Concern | Service | Use |
|---|---|---|
| Extraction (layout) | `docling` in `noted-graph` (GPU, on-disk models `/data/models/docling/models`) | Markdown/JSON + provenance |
| Extraction (vision) & all authoring LLM calls | `agent_server` (:7701) | Agent presets over the active model (Gemma 4 E4B assumed) |
| Grounding index for the clarifier | `noted-rag` / `kb-service` | Embed/serve source passages |
| Tool routing (optional) | `mcp-service` | If agents need tools during authoring |

> Web search (`websearch_server`) is **not** used in ETL — it belongs to the
> *runtime* clarifier. ETL stays grounded in the uploaded sources only.

---

## 12. Configuration (tunables)

- `questions_per_concept`, `difficulty_distribution`, allowed `types`.
- `max_questions_per_package`, packaging `strategy`.
- `extractor` policy + OCR toggle.
- Judge strictness + acceptance/coverage thresholds.
- Concurrency cap for LLM calls.
- `review.extraction` (out-of-band human review after extract: hold vs auto).
- `package_judge` quality threshold (below it → job held).

All are job-level (request) overrides on top of system defaults.

---

## 13. Resolved decisions

| # | Topic | Decision |
|---|---|---|
| 1 | **Package store / Catalog backing** | **Filesystem now** — `packages/<id>@<ver>.json` + `packages/index.json`, which the frontend fetches directly to build the Catalog. **RAG (`noted-rag`/`kb-service`) is a later phase**; v1 ships grounding inline in the package. A DB/object store can replace the file store later without changing the package contract. |
| 2 | **Human review + quality** | **Human reviews after EXTRACT, out-of-band** (opt-in `review.extraction = hold_for_review`; job holds with extracted artifacts for inspection/correction, then resumes). **Plus an automated `package_judge` LLM-as-Judge** on the assembled package (§5.5), on the same active model for now. |
| 3 | **Upload / trigger surface** | Users upload document(s) in **Tutor's frontend** (its admin area), which calls the ETL API and renders live progress to a new **Catalog** entry. CLI remains for batch/back-office. |
| 4 | **Question types & UI metadata** | v1 supports **all three**: `mcq_single` (radio, or `dropdown` via `render`), `mcq_multi` (checkboxes), `true_false`. Each question's JSON carries `type` (+ optional `render`) so the **required UI control is unambiguous** — the contract in `technical_architecture.md` §5.5. |
| 5 | **Source retention** | **Keep** original uploads + extracted artifacts long-term — enables out-of-band review, deterministic re-builds (checksum cache), and future RAG indexing. |

### Still to refine (non-blocking)
- **Progress transport** — **resolved: socket.io** (project-wide convention; event
  contract in §3.5), with `GET /etl/jobs/{id}` for late-join reconciliation.
- **Question-volume targets** per concept/package (defaults in §3.2 are
  placeholders to tune against the first real runs).
- **`package_judge` threshold** that flips a job to *held* — calibrate on the
  AI-901 dogfood run.

---

## 14. Phase-1 build checklist (ETL)

1. Define agent presets: `concept_extractor`, `question_author`, `question_judge`,
   `package_judge` (+ optional `package_curator`).
2. Implement the orchestrator (job model, work dir, resumable stages) — extract →
   transform (fan-out per concept) → validate → load.
3. Wire extraction (docling primary, vision fallback) with the checksum cache.
4. Implement packaging policy (auto/single/by_domain/fixed_n) incl. many-to-many.
5. Publish to the packages store + `index.json`; emit the job report.
6. **Dogfood on AI-901** → produce `ai-901-core` and validate against the schema
   from `technical_architecture.md`.
```
