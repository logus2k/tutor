# Tutor

An interactive, adaptive learning frontend. It consumes a **canonical question
package** (see [`documents/technical_architecture.md`](documents/technical_architecture.md))
and runs a tutoring session: a dynamic question/document panel on the left and a
**Tutor chat** (an LLM clarifier on `agent_server`) on the right, split by a
draggable vertical divider.

> **Status:** Frontend **and ETL backend** working. The questions panel renders
> any canonical package; the **authoring pipeline (Phase 1)** is built and runs
> in this same container — upload a document → docling extraction → LLM question
> generation/validation → a published package in the Catalog, with live progress
> over socket.io. `data/packages/ai-901-core.json` is now a pipeline-generated
> AI-901 package. The adaptive runtime (student state + difficulty selection,
> §7–§8) is the next phase.

## What's here

```
tutor/
├── documents/
│   ├── technical_architecture.md     vision, package schema, roadmap
│   └── etl_architecture.md           the ETL pipeline + socket.io contract
├── schema/
│   └── package.schema.json           the canonical package contract (validated)
├── etl/                              ETL backend (runs in the same container)
│   ├── service.py                    FastAPI + socket.io (POST /etl/jobs, …)
│   ├── orchestrator.py               segment → gate → author → judge → publish
│   ├── extract.py                    docling extraction (+ markdown cleanup)
│   ├── clean_markdown.py             re-level headings, strip page furniture
│   ├── catalog.py                    packages/index.json + documents/index.json
│   ├── requirements.txt              backend + docling deps
│   └── start.sh                      launches uvicorn + nginx
├── data/                            bind-mounted into the container (read-WRITE)
│   ├── packages/{index.json, *.json} the Catalog + published packages
│   ├── documents/index.json          uploaded source documents
│   └── jobs/                         per-job records (state + event log)
├── frontend/
│   ├── index.html                    layout shell (top bar + split view)
│   ├── css/tutor.css
│   ├── js/
│   │   ├── app.js                    bootstrap: layout, package load, wiring
│   │   ├── ingest.js                 document upload + live ETL progress
│   │   ├── question-renderer.js      dynamic form + deterministic grading
│   │   ├── chat.js                   Tutor chat panel (agent_server SDK)
│   │   └── …                         splitter, package-loader, context, voice
│   └── vendor/                       agent-server-client SDK + socket.io.min.js
├── Dockerfile                        nginx + uvicorn (ETL) + docling, one image
├── nginx/default.conf                serves the frontend, proxies /etl/ → uvicorn
└── docker-compose.yml                tutor container (host :4930 → :80)
```

## Architecture (deployed)

- The container runs **three things in one image**: nginx (serves the static ES6
  frontend), the **ETL backend** (FastAPI + socket.io on an internal uvicorn), and
  **docling** (document extraction). nginx serves the frontend at the web root and
  **reverse-proxies `/etl/`** (REST + socket.io) to uvicorn. Host port **4930**.
- `data/` is **bind-mounted read-write** at `/app/data` (the backend publishes
  packages and updates the Catalog/Documents indexes); docling models are
  bind-mounted read-only at `/data/models` (shared with `noted-graph`).
- The **domain proxy** (`proxy_server`) serves it at **`/tutor/`** and exposes
  `agent_server` at **`/llm/`** — same origin, so chat (`/llm/v1/...`) and the ETL
  backend (`/tutor/etl/...`) need no CORS. The in-container backend reaches
  `agent_server` at `host.docker.internal:7701`.
- The chat talks to the **`tutor`** agent preset on `agent_server` (a grounded
  clarifier: explains and nudges, never grades, never reveals unattempted
  answers). Grading is **deterministic** in the browser — the LLM is never asked
  to grade (architecture §1, §7.1).

```
browser ── /tutor/ ──────► proxy_server ──► tutor container :4930
       │                                     ├─ nginx (static frontend)
       │                                     └─ /etl/ → uvicorn (ETL) → docling
       └─ /llm/v1 ───────► proxy_server ──► agent_server :7701  (tutor + ETL agents)
```

## ETL backend — document ingestion

The **Documents** view has an upload control (`js/ingest.js`). Uploading a
document `POST`s it to `etl/jobs`; the backend runs the authoring pipeline and
streams progress over **socket.io** (path `<base>etl/socket.io`, room
`job:{jobId}`), which the UI renders as the **upload → extract → transform →
load → Catalog** journey. On publish the Catalog and Documents list refresh.

- **Pipeline** (`etl/orchestrator.py`, agents on `agent_server`): deterministic
  segmentation → `concept_extractor` (coherence gate + grounding) →
  `question_author` (MCQs, difficulty 1–5) → `question_judge` (per-question) →
  assemble → `package_judge` → **schema-validate** → publish. Grading stays
  deterministic; LLMs author and judge only. See
  [`documents/etl_architecture.md`](documents/etl_architecture.md).
- **Catalog hygiene:** only `published` packages enter `data/packages/index.json`;
  `held` (valid but low-score) and `failed` packages stay out.
- **REST:** `POST /etl/jobs` (multipart `files[]` + `directive`, or
  `directive.sourceMd`/`sourceJson` to re-package an existing extraction),
  `GET /etl/jobs[/{id}]`, `GET /etl/health`.
- **docling runs in-image on CPU.** Extraction of a large document is therefore
  slow (minutes). GPU is an upgrade: install CUDA torch and add an nvidia device
  reservation to `docker-compose.yml` (mirror `noted-graph`).

## The questions panel

`question-renderer.js` builds the control from each question's `type` (with an
optional `render` hint, architecture §5.4):

| `type`       | control            |
|--------------|--------------------|
| `mcq_single` | radio buttons (or a `<select>` dropdown when `"render": "dropdown"`) |
| `mcq_multi`  | checkboxes         |
| `true_false` | True / False radio |

On submit it grades against the `correct` flags, colours the options, and shows
per-option rationale + the overall explanation. **🎓 Ask the tutor** pre-fills the
chat with the question's stem and option *texts* (never the correctness) so the
clarifier has context without leaking the answer.

## Voice (TTS + STT)

The composer has 🔊 (speak replies) and 🎤 (dictate) toggles below the chat,
beside the input:

- **TTS** — uses the vendored SDK's `TtsClient`. Replies are spoken
  sentence-by-sentence as they stream (Markdown is stripped first). The tutor
  agent has no `<voice>` channel, so the answer text itself is spoken. Sending a
  new turn barges in on prior speech.
- **STT** — [`js/voice-stt.js`](frontend/js/voice-stt.js) + the
  [`recorder-worklet.js`](frontend/js/recorder-worklet.js) AudioWorklet. Mic
  audio is resampled to 16 kHz PCM16 and streamed as
  `audio_data {clientId, audioData}`; `transcription`/`transcription_partial`
  come back and are appended to the composer (live partials, then committed).
  This mirrors the deployed cv-chat widget — the SDK's reference `stt.js` used
  the wrong wire payload (`{client_id, audio}`) and a deprecated
  ScriptProcessorNode, so it never transcribed.

Both talk **same-origin** through the proxy's existing socket.io paths
(`/tts/socket.io` → `tts_server` :7700, `/stt/socket.io` → `stt_server` :2700)
and need no extra config. They **fail soft**: if a service or the mic is
unavailable the toggle reverts with a notice and chat keeps working. socket.io
is vendored at `frontend/vendor/socket.io.min.js`.

## Run / develop

```bash
# build + (re)start the container (first build pulls docling/torch — slow)
cd ~/env/assets/tutor
docker compose up -d --build

# editing data/ (packages, indexes) needs NO rebuild — it's bind-mounted rw.
# editing frontend/ OR etl/ code DOES need a rebuild (baked into the image):
docker compose up -d --build

# quick backend check:
curl -s http://localhost:4930/etl/health        # {"status":"ok"}
```

Open **https://logus2k.com/tutor/**.

The top-bar fields let you point at a different `agent_server` base path
(default `/llm`) or agent name (default `tutor`) at runtime; they persist in
`localStorage`.

### The `tutor` agent

Created on `agent_server` via its admin API (no restart). To inspect or update:

```bash
curl -s http://localhost:7701/v1/agents/tutor | python3 -m json.tool   # resolved preset
# update the system prompt / sampling:
curl -s -X PUT http://localhost:7701/admin/api/agents/tutor \
  -H 'Content-Type: application/json' -d '{"name":"tutor","system_prompt":"...","memory_policy":"none"}'
```

See `~/env/assets/agent_server/documents/how_to.md` for the agent model.

## Proxy entry

Added to `~/env/assets/proxy_server/nginx.conf` (a `location ^~ /tutor/` block
proxying to `host.docker.internal:4930`). Apply changes with:

```bash
docker exec proxy_server nginx -t && docker exec proxy_server nginx -s reload
```

The same `/tutor/` block also carries the ETL backend (`/tutor/etl/...`, REST +
socket.io). For **live ingestion progress** the block must pass WebSocket upgrade
headers (`proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection
"upgrade";`); without them socket.io falls back to long-polling (still works).

`/tutor/` is currently **public**, like `/llm/`. To gate it behind Google OAuth
like `job2cool`, add the `auth_request /oauth2/auth;` lines from that block.

## Next steps

- **ETL:** a review area for `held` packages; multi-document-per-package jobs;
  GPU docling; generic (non-numbered) document segmentation.
- Student-state store + adaptive selector (architecture §7–§8).
- Clarifier grounding + `web_search` fallback with citations (§7.2).
- Optional **avatar** video (the SDK's `AvatarClient` + the proxy's `/avatar`
  path) — TTS/STT are already wired (see above).
