#!/usr/bin/env python3
"""Tutor ETL service — upload API + socket.io progress + Catalog publish.

Endpoints (FastAPI):
  POST /etl/jobs           multipart: files[] (+ form 'directive' JSON) OR a
                           directive with sourceMd/sourceJson to re-build from an
                           existing extraction. Returns {jobId}.
  GET  /etl/jobs/{id}      job record (state + event log) for polling / late-join
  GET  /etl/jobs           list jobs
  GET  /etl/health
socket.io (path /etl/socket.io): emit 'join' {jobId} to receive that job's events
live (the §3.5 contract); a 'job.snapshot' is sent on join for catch-up.

Run:  .venv_tutor/bin/uvicorn etl.service:asgi --host 0.0.0.0 --port 8099  (cwd = tutor/)
"""
import asyncio, json, os, re, subprocess, sys, threading, datetime, time, urllib.request
import socketio
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request

from etl import extract as extract_mod
from etl import catalog
from etl import sessions as sess

ROOT = catalog.ROOT
PKG_DIR, DOC_DIR = catalog.PKG_DIR, catalog.DOC_DIR
JOBS_DIR = os.path.join(ROOT, "data", "jobs")
PYTHON = sys.executable   # same interpreter running the service (venv on host, system python in-container)
for d in (PKG_DIR, DOC_DIR, JOBS_DIR):
    os.makedirs(d, exist_ok=True)

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
app = FastAPI(title="tutor-etl")
asgi = socketio.ASGIApp(sio, other_asgi_app=app, socketio_path="/etl/socket.io")

JOBS = {}
LOOP = None

def _slug(s): return re.sub(r"[^a-z0-9]+", "-", os.path.splitext(s)[0].lower()).strip("-") or "package"
def _now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def _job_file(jid): return os.path.join(JOBS_DIR, jid + ".json")
def _save(job): json.dump(job, open(_job_file(job["jobId"]), "w", encoding="utf-8"), indent=2, ensure_ascii=False)

@app.on_event("startup")
async def _capture_loop():
    global LOOP
    LOOP = asyncio.get_event_loop()

# ---- socket.io ----
@sio.on("join")
async def join(sid, data):
    jid = (data or {}).get("jobId")
    if not jid:
        return
    await sio.enter_room(sid, f"job:{jid}")
    if jid in JOBS:
        await sio.emit("job.snapshot", JOBS[jid], room=sid)

def emit(jid, event, payload):
    """Record an event on the job and broadcast it to the job's socket.io room."""
    data = {"jobId": jid, "event": event, **payload}
    job = JOBS.get(jid)
    if job is not None:
        job["events"].append(data)
        if event in ("job.published", "job.held", "job.failed"):
            job["state"] = event.split(".")[1]
        if event == "job.published":
            job["packageId"] = payload.get("packageId", job.get("packageId"))
        _save(job)
    if LOOP:
        asyncio.run_coroutine_threadsafe(sio.emit(event, data, room=f"job:{jid}"), LOOP)

# ---- job runner (background thread: blocking docker + subprocess) ----
def run_job(jid, pdf_path, md, js, pkg_id, title, src_uri, max_concepts, qpc):
    try:
        emit(jid, "job.queued", {"documents": [os.path.basename(src_uri)]})
        if md and js:                                   # re-build from existing extraction
            emit(jid, "stage.started", {"stage": "extract"})
            emit(jid, "extract.progress", {"detail": "using provided extraction"})
            emit(jid, "stage.done", {"stage": "extract"})
        else:                                           # full docling extraction
            emit(jid, "stage.started", {"stage": "extract"})
            workdir = f"/tmp/etl_jobs/{jid}/extract"
            md, js = extract_mod.extract(pdf_path, workdir, emit=lambda e, **k: emit(jid, e, k))
            emit(jid, "stage.done", {"stage": "extract"})

        out = os.path.join(PKG_DIR, pkg_id + ".json")
        env = {**os.environ, "ETL_MD": md, "ETL_DOC": js, "ETL_OUT": out, "ETL_PKG_ID": pkg_id,
               "ETL_JOB_ID": jid, "ETL_MAX_CONCEPTS": str(max_concepts), "ETL_QPC": str(qpc),
               "ETL_SRC_TITLE": title, "ETL_SRC_URI": src_uri}
        proc = subprocess.Popen([PYTHON, "etl/orchestrator.py"], cwd=ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith("EVENT "):
                try:
                    p = json.loads(line[6:]); ev = p.pop("event", None); p.pop("jobId", None)
                    if ev:
                        emit(jid, ev, p)
                except Exception:
                    pass
        proc.wait()
        state = JOBS[jid].get("state")
        if state == "published":
            catalog.rebuild_package_index()
            emit(jid, "catalog.updated", {"packageId": pkg_id})
        else:
            # held/failed packages must NOT enter the student-facing Catalog.
            try:
                os.remove(out)
            except OSError:
                pass
            catalog.rebuild_package_index()
            emit(jid, "catalog.skipped", {"packageId": pkg_id, "state": state})
            if state not in ("held", "failed"):
                JOBS[jid]["state"] = "done"; _save(JOBS[jid])
    except Exception as e:
        emit(jid, "job.failed", {"stage": "job", "error": str(e)})

# ---- REST ----
@app.post("/etl/jobs")
async def create_job(files: list[UploadFile] = File(default=[]), directive: str = Form(default="{}")):
    d = json.loads(directive or "{}")
    jid = "job-" + datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    src_md = d.get("sourceMd"); src_json = d.get("sourceJson")
    pdf_path = None; src_uri = d.get("sourceUri", "")

    if files:                                            # real upload
        f = files[0]
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", f.filename or "upload.pdf")
        pdf_path = os.path.join(DOC_DIR, safe)
        with open(pdf_path, "wb") as out:
            out.write(await f.read())
        src_uri = f"documents/{safe}"
        catalog.upsert_document({"id": _slug(safe), "title": d.get("title") or safe,
                                 "file": safe, "kind": "pdf", "uploaded_at": _now()})
        pkg_id = d.get("packageId") or _slug(safe)
        title = d.get("title") or safe
    elif src_md and src_json:                            # re-build from extraction
        pkg_id = d.get("packageId") or "package"
        title = d.get("title") or pkg_id
        src_uri = src_uri or "materials/source"
    else:
        raise HTTPException(400, "provide files[] or directive.sourceMd+sourceJson")

    job = {"jobId": jid, "state": "queued", "packageId": pkg_id, "title": title,
           "documents": [os.path.basename(src_uri)], "events": [], "created_at": _now()}
    JOBS[jid] = job; _save(job)
    threading.Thread(target=run_job, daemon=True, args=(
        jid, pdf_path, src_md, src_json, pkg_id, title, src_uri,
        int(d.get("maxConcepts", 999)), int(d.get("questionsPerConcept", 5)))).start()
    return {"jobId": jid, "state": "queued", "packageId": pkg_id}

@app.get("/etl/jobs/{jid}")
def get_job(jid: str):
    if jid in JOBS:
        return JOBS[jid]
    if os.path.exists(_job_file(jid)):
        return json.load(open(_job_file(jid), encoding="utf-8"))
    raise HTTPException(404, "unknown job")

@app.get("/etl/jobs")
def list_jobs():
    return {"jobs": [{k: v for k, v in j.items() if k != "events"} for j in JOBS.values()]}

@app.get("/etl/health")
def health():
    return {"status": "ok"}

# ---- Study Sessions (per-student, SQLite) -----------------------------------
# Identity comes from oauth2-proxy's X-Forwarded-Email (forwarded by the domain
# proxy after Google login). Login is OPTIONAL: with no email the app still
# works anonymously; the session endpoints require a signed-in student.

def _email(request: Request) -> str:
    return (request.headers.get("X-Forwarded-Email") or "").strip().lower()

# Google omits name/picture from the ID token; they live at the UserInfo
# endpoint, reachable with the access token forwarded as X-Access-Token
# (oauth2-proxy --pass-access-token). Cached briefly per token (~hourly rotation).
_GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
_userinfo_cache: dict = {}   # access_token -> (expires_at, claims)

def _google_userinfo(access_token: str) -> dict:
    if not access_token:
        return {}
    now = time.time()
    hit = _userinfo_cache.get(access_token)
    if hit and hit[0] > now:
        return hit[1]
    info = {}
    try:
        req = urllib.request.Request(_GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            info = json.load(r)
    except Exception:  # noqa: BLE001  (profile is best-effort; never break /me)
        info = {}
    if len(_userinfo_cache) > 256:
        _userinfo_cache.clear()
    _userinfo_cache[access_token] = (now + 600, info)
    return info

def _require_email(request: Request) -> str:
    e = _email(request)
    if not e:
        raise HTTPException(401, "login required")
    sess.upsert_student(e)
    return e

@app.get("/etl/me")
async def me(request: Request):
    e = _email(request)
    token = (request.headers.get("X-Access-Token") or "").strip()
    info = await asyncio.to_thread(_google_userinfo, token) if (e and token) else {}
    return {
        "email": e,
        "authenticated": bool(e),
        "name": (info.get("name") or "").strip() or None,
        "picture": (info.get("picture") or "").strip() or None,
    }

@app.get("/etl/sessions")
def sessions_list(request: Request):
    return {"sessions": sess.list_sessions(_require_email(request))}

@app.post("/etl/sessions")
async def sessions_create(request: Request):
    e = _require_email(request)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    return sess.create_session(e, name[:120])

@app.get("/etl/sessions/{sid}")
def sessions_get(sid: str, request: Request):
    s = sess.get_session(_require_email(request), sid)
    if not s:
        raise HTTPException(404, "session not found")
    return s

@app.patch("/etl/sessions/{sid}")
async def sessions_rename(sid: str, request: Request):
    e = _require_email(request)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    if not sess.rename_session(e, sid, name[:120]):
        raise HTTPException(404, "session not found")
    return {"ok": True, "name": name[:120]}

@app.delete("/etl/sessions/{sid}")
def sessions_delete(sid: str, request: Request):
    if not sess.delete_session(_require_email(request), sid):
        raise HTTPException(404, "session not found")
    return {"deleted": True}

@app.post("/etl/sessions/{sid}/packages")
async def sessions_add_package(sid: str, request: Request):
    e = _require_email(request)
    body = await request.json()
    pkg = (body.get("package_id") or "").strip()
    if not pkg:
        raise HTTPException(400, "package_id required")
    if not sess.add_package(e, sid, pkg):
        raise HTTPException(404, "session not found")
    return {"ok": True}

@app.get("/etl/sessions/{sid}/answers")
def sessions_get_answers(sid: str, request: Request, package_id: str | None = None):
    e = _require_email(request)
    return {"answers": sess.get_answers(e, sid, package_id)}

@app.put("/etl/sessions/{sid}/answers")
async def sessions_save_answer(sid: str, request: Request):
    e = _require_email(request)
    body = await request.json()
    pkg = (body.get("package_id") or "").strip()
    qid = (body.get("question_id") or "").strip()
    if not pkg or not qid:
        raise HTTPException(400, "package_id and question_id required")
    if not sess.save_answer(e, sid, pkg, qid, body.get("selected_ids") or [], body.get("correct")):
        raise HTTPException(404, "session not found")
    return {"ok": True}
