# Tutor

An interactive, adaptive learning frontend. It consumes a **canonical question
package** (see [`documents/technical_architecture.md`](documents/technical_architecture.md))
and runs a tutoring session: a dynamic question/document panel on the left and a
**Tutor chat** (an LLM clarifier on `agent_server`) on the right, split by a
draggable vertical divider.

> **Status:** Phase-2 frontend shell. The questions panel is driven by a
> hand-authored sample package (`packages/ai-901-core.json`); the authoring
> pipeline (Phase 1) and the adaptive runtime (Phase 2+) are not built yet.

## What's here

```
tutor/
├── documents/
│   └── technical_architecture.md     vision, package schema, roadmap
├── data/                            bind-mounted into the container (live)
│   └── packages/
│       └── ai-901-core.json          sample package (mock AI-901 MCQs)
├── frontend/
│   ├── index.html                    layout shell (top bar + split view)
│   ├── css/tutor.css
│   ├── js/
│   │   ├── app.js                    bootstrap: layout, package load, wiring
│   │   ├── splitter.js               draggable vertical splitter
│   │   ├── package-loader.js         fetch + validate a package, build indexes
│   │   ├── question-renderer.js      dynamic form + deterministic grading
│   │   ├── chat.js                   Tutor chat panel (agent_server SDK)
│   │   └── markdown.js               tiny safe Markdown renderer for replies
│   └── vendor/agent-server-client/   vendored JS SDK (chat streaming, parser)
├── Dockerfile                        nginx static image
├── nginx/default.conf                container nginx (serves at web root)
└── docker-compose.yml                tutor container (host :4930 → :80)
```

## Architecture (deployed)

- The frontend is a **pure static site** (ES6 modules, no build step), served by
  an nginx container on host port **4930**. The frontend is baked into the image;
  `data/` (question packages and runtime content) is **bind-mounted** at `/data`,
  so package edits need no rebuild.
- The **domain proxy** (`proxy_server`) serves it at **`/tutor/`** and exposes
  `agent_server` at **`/llm/`**. Both are the same origin, so the browser's chat
  calls (`/llm/v1/chat/completions`) need no CORS.
- The chat talks to the **`tutor`** agent preset on `agent_server` (a grounded
  clarifier: explains and nudges, never grades, never reveals unattempted
  answers). Grading is **deterministic** in the browser — the LLM is never asked
  to grade (architecture §1, §7.1).

```
browser ── /tutor/ ──► proxy_server ──► tutor container (nginx :4930, static)
       └─ /llm/v1 ───► proxy_server ──► agent_server :7701  (tutor agent)
```

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

## Run / develop

```bash
# build + (re)start the static container
cd ~/env/assets/tutor
docker compose up -d --build

# editing data/ (e.g. packages) needs NO rebuild — it's bind-mounted
# editing frontend/ files DOES need a rebuild (baked into the image):
docker compose up -d --build
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

`/tutor/` is currently **public**, like `/llm/`. To gate it behind Google OAuth
like `job2cool`, add the `auth_request /oauth2/auth;` lines from that block.

## Next steps

- Phase 1 authoring pipeline → replace the mock package with a real
  `ai-901-core.json`.
- Student-state store + adaptive selector (architecture §7–§8).
- Clarifier grounding + `web_search` fallback with citations (§7.2).
- Optional voice/avatar (the vendored SDK already supports fail-soft TTS/STT/
  avatar; wire `socket.io` + the proxy's `/tts`,`/stt`,`/avatar` paths).
