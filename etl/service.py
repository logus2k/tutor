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
import asyncio, json, os, re, shutil, subprocess, sys, threading, datetime, time, urllib.request
import socketio
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request

from etl import extract as extract_mod
from etl import catalog
from etl import sessions as sess
from etl import review
from etl import merge

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
JOB_CANCEL = set()   # jids the user asked to cancel (checked between docs)
JOB_PROCS = {}       # jid -> running orchestrator Popen (killed on cancel)

# Package reader (for mapping a graded answer's question → its concept_ids, so
# mastery can be updated server-side). Cached by file mtime.
_pkg_cache: dict = {}
def _load_package(package_id: str):
    path = os.path.join(PKG_DIR, (package_id or "") + ".json")
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None
    hit = _pkg_cache.get(package_id)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        pkg = json.load(open(path, encoding="utf-8"))
    except Exception:
        return None
    _pkg_cache[package_id] = (mt, pkg)
    return pkg

def _question_concepts(package_id: str, question_id: str) -> list:
    """[(concept_id, title), …] for a question in a package (empty if unknown)."""
    pkg = _load_package(package_id)
    if not pkg:
        return []
    q = next((x for x in pkg.get("questions", []) if x.get("id") == question_id), None)
    if not q:
        return []
    titles = {c.get("id"): c.get("title") for c in pkg.get("concepts", [])}
    return [(cid, titles.get(cid)) for cid in (q.get("concept_ids") or [])]

def _slug(s): return re.sub(r"[^a-z0-9]+", "-", os.path.splitext(s)[0].lower()).strip("-") or "package"
def _pretty_title(fn):
    """A human title from a filename when the uploader didn't give one:
    'Microsoft-AI-Transformation-Leader-AB-731.pdf' → 'Microsoft AI Transformation Leader AB 731'."""
    base = os.path.splitext(os.path.basename(fn or ""))[0]
    base = re.sub(r"[-_]+", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    return base or "Untitled package"
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
# Per-DOCUMENT ingestion: each document is extracted → authored (orchestrator
# run once for that document) → MERGED into the package draft, persisted after
# every document so finished docs are committed and visible. FAIL-STOP: if a
# document fails, the run stops (a failed doc may be essential) but the docs
# already merged are kept — fix it and add it to the package. The draft lives in
# data/held/ (the Review area); the user resolves disputes and publishes.

# Terminal/catalog events from a per-doc orchestrator run must NOT flip the job
# state — the job is only done after ALL docs + finalize.
_PART_SKIP = {"job.published", "job.held", "job.failed", "catalog.updated", "catalog.skipped"}

def _author_part(jid, md, js, doc, out, max_concepts, qpc, idx):
    """Run the orchestrator for ONE document → a standalone 'part' package at
    `out`. Returns (ok, error). Per-doc terminal events are filtered out."""
    env = {**os.environ, "ETL_MD": md, "ETL_DOC": js or "", "ETL_OUT": out,
           "ETL_PKG_ID": f"part{idx}", "ETL_JOB_ID": jid,
           "ETL_MAX_CONCEPTS": str(max_concepts), "ETL_QPC": str(qpc),
           "ETL_SRC_TITLE": doc["title"],
           "ETL_SOURCES": json.dumps([{"id": doc["id"], "title": doc["title"], "uri": doc["uri"]}]),
           "ETL_SRC_URI": doc["uri"], "ETL_SRC_ID": doc["id"]}
    proc = subprocess.Popen([PYTHON, "etl/orchestrator.py"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    JOB_PROCS[jid] = proc            # so a cancel can kill it immediately
    err = None
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if not line.startswith("EVENT "):
                continue
            try:
                p = json.loads(line[6:]); ev = p.pop("event", None); p.pop("jobId", None)
            except Exception:
                continue
            if not ev:
                continue
            if ev == "job.failed":
                err = p.get("error", "authoring failed")
            if ev not in _PART_SKIP:
                emit(jid, ev, p)        # re-emit useful progress (concept.gated, transform.progress, …)
        proc.wait()
    finally:
        JOB_PROCS.pop(jid, None)
    if jid in JOB_CANCEL:
        return (False, "cancelled")
    if proc.returncode != 0 and not err:
        err = f"orchestrator exited {proc.returncode}"
    return (err is None and os.path.exists(out)), err

def run_job(jid, docs, md, js, pkg_id, title, max_concepts, qpc, owner=None,
            base_pkg=None, start_index=0):
    try:
        emit(jid, "job.queued", {"documents": [d["title"] for d in docs] or [title]})
        workroot = f"/tmp/etl_jobs/{jid}"
        total = len(docs)
        draft = base_pkg                    # existing package when ADDING; else None
        src_parts = []
        for n, doc in enumerate(docs):
            if jid in JOB_CANCEL:
                emit(jid, "job.cancelled", {"packageId": pkg_id}); return
            idx = start_index + n
            emit(jid, "extract.file", {"index": n + 1, "total": total, "title": doc["title"]})
            # ---- extract this document (FAIL-STOP) ----
            try:
                if doc.get("path"):
                    md_i, js_i = extract_mod.extract(doc["path"], os.path.join(workroot, "x", str(idx)),
                                                     emit=lambda e, **k: emit(jid, e, k))
                else:                       # rebuild-from-extraction (synthetic single doc)
                    md_i, js_i = md, js
            except Exception as ex:
                emit(jid, "job.failed", {"stage": "extract", "document": doc["title"], "error": str(ex)[:400]})
                _finalize_draft(jid, draft, pkg_id, title, owner, src_parts, stopped_at=doc["title"])
                return
            emit(jid, "extract.file.done", {"index": n + 1, "total": total, "title": doc["title"]})
            if jid in JOB_CANCEL:
                emit(jid, "job.cancelled", {"packageId": pkg_id}); return
            # ---- author this document into a part package (FAIL-STOP) ----
            part_out = os.path.join(workroot, f"part-{idx}.json")
            ok, err = _author_part(jid, md_i, js_i, doc, part_out, max_concepts, qpc, idx)
            if jid in JOB_CANCEL:
                emit(jid, "job.cancelled", {"packageId": pkg_id}); return
            if not ok:
                emit(jid, "job.failed", {"stage": "author", "document": doc["title"], "error": (err or "no package")[:400]})
                _finalize_draft(jid, draft, pkg_id, title, owner, src_parts, stopped_at=doc["title"])
                return
            # ---- merge into the draft, persist (committed/visible per doc) ----
            part = json.load(open(part_out, encoding="utf-8"))
            draft = merge.merge_part(draft, part, idx, pkg_id, title)
            try: src_parts.append(open(md_i, encoding="utf-8").read())
            except Exception: pass
            review.save_held(draft, owner, None)        # persist after each document
            c = merge.counts(draft)
            emit(jid, "doc.done", {"index": n + 1, "total": total, "title": doc["title"], **c})

        if jid in JOB_CANCEL:
            emit(jid, "job.cancelled", {"packageId": pkg_id}); return
        if draft is None:
            emit(jid, "job.failed", {"stage": "job", "error": "no documents produced content"})
            return
        _finalize_draft(jid, draft, pkg_id, title, owner, src_parts)
    except Exception as e:
        emit(jid, "job.failed", {"stage": "job", "error": str(e)[:400]})

def _finalize_draft(jid, draft, pkg_id, title, owner, src_parts, stopped_at=None):
    """Persist the draft to held (Review) with the combined source, notify, emit ready."""
    if draft is None:
        return
    try:
        # Quality pass on the FULL merged bank: LLM semantic dedup (keeps the best of
        # each near-duplicate group) + interleave by concept so similar Qs aren't adjacent.
        try:
            from etl import orchestrator as _orch
            draft["questions"] = _orch.dedup_and_order(
                draft.get("questions", []), emit=lambda e, **k: emit(jid, e, k))
            _surv = {q["id"] for q in draft["questions"]}
            _q = draft.get("quality") or {}
            _q["disputes"] = [d for d in (_q.get("disputes") or []) if d.get("qid") in _surv]
            draft["quality"] = _q
        except Exception as ex:  # noqa: BLE001
            emit(jid, "warn", {"message": f"dedup/order skipped ({ex})"})
        review.save_held(draft, owner, "\n\n".join(src_parts) if src_parts else None)
        catalog.rebuild_package_index()
        c = merge.counts(draft)
        emit(jid, "job.review_ready", {"packageId": pkg_id, "stoppedAt": stopped_at, **c})
        if owner:
            msg = (f"Stopped at “{stopped_at}”. " if stopped_at else "") + \
                  f"{c['questions']} questions from {c['sources']} document(s); {c['disputes']} to review."
            sess.add_notification(owner, "review", f"Ready for review: {title}", msg,
                                  dedup_key=f"review:{pkg_id}:{c['sources']}")
    except Exception as e:  # noqa: BLE001
        emit(jid, "review.save_failed", {"packageId": pkg_id, "error": str(e)[:300]})

# ---- REST ----
@app.post("/etl/jobs")
async def create_job(request: Request, files: list[UploadFile] = File(default=[]), directive: str = Form(default="{}")):
    owner = _email(request)   # verified uploader (blank when anonymous) → held-package ownership
    d = json.loads(directive or "{}")
    jid = "job-" + datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    src_md = d.get("sourceMd"); src_json = d.get("sourceJson")

    docs = []
    if files:                                            # one OR MORE uploads → a single package
        for f in files:
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", f.filename or "upload.pdf")
            path = os.path.join(DOC_DIR, safe)
            with open(path, "wb") as out:
                out.write(await f.read())
            t = _pretty_title(f.filename or safe)
            catalog.upsert_document({"id": _slug(safe), "title": t, "file": safe,
                                     "kind": "pdf", "uploaded_at": _now()})
            docs.append({"path": path, "title": t, "uri": f"documents/{safe}", "id": f"src-{_slug(safe)}"})
        if d.get("title"):
            title = d["title"]
        elif len(docs) == 1:
            title = docs[0]["title"]
        else:
            title = f"{docs[0]['title']} + {len(docs) - 1} more"
        pkg_id = d.get("packageId") or _slug(title)
        documents = [dd["title"] for dd in docs]
    elif src_md and src_json:                            # re-build from extraction (one synthetic doc)
        pkg_id = d.get("packageId") or "package"
        title = d.get("title") or pkg_id
        docs = [{"path": None, "title": title, "uri": "materials/source", "id": f"src-{pkg_id}"}]
        documents = [title]
    else:
        raise HTTPException(400, "provide files[] or directive.sourceMd+sourceJson")

    job = {"jobId": jid, "state": "queued", "packageId": pkg_id, "title": title,
           "documents": documents, "events": [], "created_at": _now(),
           "owner": owner, "mode": "new",
           "files": [os.path.basename(dd["uri"]) for dd in docs if dd.get("path")]}
    JOBS[jid] = job; _save(job)
    threading.Thread(target=run_job, daemon=True, args=(
        jid, docs, src_md, src_json, pkg_id, title,
        int(d.get("maxConcepts", 999)), int(d.get("questionsPerConcept", 5)), owner)).start()
    return {"jobId": jid, "state": "queued", "packageId": pkg_id}

@app.post("/etl/jobs/{jid}/cancel")
def cancel_job(jid: str, request: Request):
    """Cancel a running import and clean up its mess: stop the worker, then remove
    the half-built draft (new imports) and the files this job uploaded."""
    e = _require_email(request)
    job = JOBS.get(jid)
    if job is None and os.path.exists(_job_file(jid)):
        job = json.load(open(_job_file(jid), encoding="utf-8"))
    if not job:
        raise HTTPException(404, "unknown job")
    owner = (job.get("owner") or "").strip().lower()
    if not (_is_admin(e) or (owner and owner == e)):
        raise HTTPException(403, "not allowed")

    JOB_CANCEL.add(jid)
    p = JOB_PROCS.get(jid)
    if p:
        try: p.kill()
        except Exception: pass

    pkgid = job.get("packageId")
    # New import → drop the half-built draft (and any package file). Add-to-existing
    # → leave the original package intact (only the just-uploaded docs are removed).
    if job.get("mode") == "new" and pkgid:
        review.delete_held(pkgid)
        try: os.remove(os.path.join(PKG_DIR, pkgid + ".json"))
        except OSError: pass
    # Remove the files this job uploaded + their document-index entries.
    files = set(job.get("files") or [])
    if files:
        for fn in files:
            try: os.remove(os.path.join(DOC_DIR, fn))
            except OSError: pass
        idxp = os.path.join(DOC_DIR, "index.json")
        try:
            idx = json.load(open(idxp, encoding="utf-8"))
            idx["documents"] = [x for x in idx.get("documents", []) if x.get("file") not in files]
            json.dump(idx, open(idxp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        except Exception:
            pass
    catalog.rebuild_package_index()
    shutil.rmtree(f"/tmp/etl_jobs/{jid}", ignore_errors=True)
    job["state"] = "cancelled"; JOBS[jid] = job; _save(job)
    emit(jid, "job.cancelled", {"packageId": pkgid})
    return {"ok": True, "cancelled": jid}

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

# Dispute-review admins (env allowlist). Admins can review/publish ANY held
# package, including those uploaded anonymously (no owner).
ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("TUTOR_ADMIN_EMAILS", "").split(",") if e.strip()}

def _is_admin(email: str) -> bool:
    return bool(email) and email.lower() in ADMIN_EMAILS

def _can_review(email: str, package: dict) -> bool:
    """A held package is reviewable by its uploader (owner) or any admin."""
    if not email:
        return False
    owner = (package.get("owner_email") or "").strip().lower()
    return _is_admin(email) or (bool(owner) and owner == email.lower())

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
    name = (info.get("name") or "").strip() or None
    if e:
        # Persist the display name so the Wall of Fame can show real handles.
        await asyncio.to_thread(sess.upsert_student, e, name)
    return {
        "email": e,
        "authenticated": bool(e),
        "name": name,
        "picture": (info.get("picture") or "").strip() or None,
        "is_admin": _is_admin(e),
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
    out = {"answers": sess.get_answers(e, sid, package_id)}
    if package_id:   # also return this package's per-session shuffle options (None if not a member)
        out["options"] = sess.get_package_options(e, sid, package_id)
    return out

@app.put("/etl/sessions/{sid}/answers")
async def sessions_save_answer(sid: str, request: Request):
    e = _require_email(request)
    body = await request.json()
    pkg = (body.get("package_id") or "").strip()
    qid = (body.get("question_id") or "").strip()
    if not pkg or not qid:
        raise HTTPException(400, "package_id and question_id required")
    correct = body.get("correct")
    attempts = int(body.get("attempts") or 1)
    if not sess.save_answer(e, sid, pkg, qid, body.get("selected_ids") or [], correct, attempts):
        raise HTTPException(404, "session not found")
    # Update per-concept mastery from this outcome (server maps question→concepts).
    if correct is not None:
        for cid, title in _question_concepts(pkg, qid):
            sess.update_mastery(e, cid, title, bool(correct), attempts)
    return {"ok": True}

@app.get("/etl/mastery")
def mastery(request: Request, package_id: str | None = None):
    e = _require_email(request)
    cids = None
    if package_id:
        pkg = _load_package(package_id)
        if pkg:
            cids = {c.get("id") for c in pkg.get("concepts", [])}
    return {"mastery": sess.get_mastery(e, cids)}

# ---- Notifications + study reminders ----------------------------------------
# Study reminders are spaced-repetition nudges derived from per-concept mastery
# (generated on read, deduped per concept per day). Job events (held/published)
# also notify the package owner.

def _generate_study_reminders(email: str) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    today = now.date().isoformat()
    for m in sess.get_mastery(email):
        try:
            when = datetime.datetime.fromisoformat(m.get("updated_at"))
        except Exception:
            continue
        age_days = (now - when).total_seconds() / 86400.0
        title = m.get("title") or m.get("concept_id")
        if m.get("mastered") and age_days >= 7:
            sess.add_notification(email, "reminder", f"Time to review: {title}",
                                  "You mastered this a while ago — a quick review keeps it fresh.",
                                  dedup_key=f"review:{m['concept_id']}:{today}")
        elif (not m.get("mastered")) and age_days >= 1 and (m.get("attempts") or 0) >= 1:
            sess.add_notification(email, "reminder", f"Keep practicing: {title}",
                                  "You're still building this concept — try a few more questions.",
                                  dedup_key=f"practice:{m['concept_id']}:{today}")

@app.get("/etl/notifications")
def notifications_list(request: Request):
    e = _require_email(request)
    try:
        _generate_study_reminders(e)
    except Exception:  # noqa: BLE001  (reminders are best-effort)
        pass
    return {"notifications": sess.list_notifications(e), "unread": sess.unread_count(e)}

@app.post("/etl/notifications/read")
async def notifications_read(request: Request):
    e = _require_email(request)
    body = await request.json()
    sess.mark_read(e, body.get("id"))
    return {"ok": True, "unread": sess.unread_count(e)}

# ---- Wall of Fame (per-package leaderboard) ---------------------------------
# scope=mine (default) → the signed-in student's own sessions (login required).
# scope=all            → best sessions across all students (other handles masked).

@app.get("/etl/fame")
def fame(request: Request, package_id: str, scope: str = "mine"):
    pkg = (package_id or "").strip()
    if not pkg:
        raise HTTPException(400, "package_id required")
    e = _email(request)
    if scope == "mine":
        if not e:
            raise HTTPException(401, "login required")
        sess.upsert_student(e)
        entries = sess.leaderboard(pkg, email=e, mine=True)
    else:
        scope = "all"
        entries = sess.leaderboard(pkg, email=(e or None), mine=False)
    return {"scope": scope, "package_id": pkg, "entries": entries}

# ---- Dispute Review (held packages) -----------------------------------------
# Held packages (non-publishable: key disputes / sub-threshold) live in
# data/held/ and are resolved here by the uploader (owner) or an admin, then
# published into the Catalog. See documents/technical_architecture.md §6.1.

def _load_reviewable(request: Request, pkg_id: str):
    """Return (email, package) if the caller may review it, else raise."""
    e = _require_email(request)
    pkg = review.get_held(pkg_id)
    if not pkg:
        raise HTTPException(404, "held package not found")
    if not _can_review(e, pkg):
        raise HTTPException(403, "not allowed")
    return e, pkg

@app.get("/etl/review")
def review_list(request: Request):
    e = _require_email(request)
    admin = _is_admin(e)
    items = []
    for it in review.list_held():
        owner = (it.get("owner_email") or "").strip().lower()
        mine = bool(owner) and owner == e
        if admin or mine:
            items.append({**it, "mine": mine})
    return {"packages": items, "is_admin": admin}

@app.get("/etl/review/{pkg_id}")
def review_get(pkg_id: str, request: Request):
    _e, pkg = _load_reviewable(request, pkg_id)
    qmap = {q.get("id"): q for q in pkg.get("questions", [])}
    cmap = {c.get("id"): c for c in pkg.get("concepts", [])}
    srcmap = {s.get("id"): s for s in pkg.get("sources", [])}
    fallback_src = (pkg.get("sources") or [{}])[0]

    disputes = []
    for d in review.disputes_of(pkg):
        q = qmap.get(d.get("qid")) or {}
        refs = q.get("source_refs", []) or []
        src = srcmap.get(refs[0].get("source_id")) if refs else fallback_src
        locators = [r.get("locator") for r in refs if r.get("locator")]
        # Resolve the concepts' verbatim grounding chunks for this question.
        grounding = []
        pages = set()
        for cid in (q.get("concept_ids") or []):
            c = cmap.get(cid)
            if not c:
                continue
            passages = [{"text": g.get("text", ""), "citation": g.get("citation") or g.get("locator") or ""}
                        for g in (c.get("grounding") or [])]
            for p in passages:
                pages.update(re.findall(r"p\.?\s*(\d+)", p["citation"]))
            grounding.append({"title": c.get("title"), "objective": c.get("objective"), "passages": passages})
        for loc in locators:
            pages.update(re.findall(r"p\.?\s*(\d+)", loc))
        disputes.append({**d, "question": q,
                         "source": {"title": (src or {}).get("title"), "uri": (src or {}).get("uri")},
                         "locators": locators,
                         "pages": sorted(pages, key=lambda x: int(x)),
                         "grounding": grounding})
    return {
        "id": pkg.get("id"), "title": pkg.get("title"),
        "owner_email": pkg.get("owner_email"),
        "score": (pkg.get("quality") or {}).get("score"),
        "questions_total": len(pkg.get("questions", [])),
        "has_source": bool(review.get_src(pkg_id)),
        "disputes": disputes,
    }

@app.post("/etl/review/{pkg_id}/resolve")
async def review_resolve(pkg_id: str, request: Request):
    _e, pkg = _load_reviewable(request, pkg_id)
    body = await request.json()
    qid = (body.get("qid") or "").strip()
    if not qid:
        raise HTTPException(400, "qid required")

    if body.get("discard"):
        # Always clears the dispute — works even for an orphan whose question was
        # already removed (e.g. by dedup), which is exactly what needs cleaning up.
        pkg["questions"] = [x for x in pkg.get("questions", []) if x.get("id") != qid]
        review.clear_dispute(pkg, qid)
        review.write_held(pkg)
        remaining = len(review.disputes_of(pkg))
        return {"ok": True, "disputes_remaining": remaining, "publishable": remaining == 0}

    q = review.find_question(pkg, qid)
    if not q:
        raise HTTPException(404, "question not found")
    if body.get("stem") is not None:
        q["stem"] = str(body["stem"])
    if body.get("explanation") is not None:
        q["explanation"] = str(body["explanation"])
    for upd in (body.get("options") or []):
        opt = next((o for o in q.get("options", []) if o.get("id") == upd.get("id")), None)
        if opt and upd.get("text") is not None:
            opt["text"] = str(upd["text"])
    if body.get("correct_ids") is not None:
        want = {str(x) for x in body["correct_ids"]}
        for o in q.get("options", []):
            o["correct"] = o.get("id") in want
        if not any(o.get("correct") for o in q.get("options", [])):
            raise HTTPException(400, "at least one option must be correct")
    review.clear_dispute(pkg, qid)
    review.write_held(pkg)
    remaining = len(review.disputes_of(pkg))
    return {"ok": True, "disputes_remaining": remaining, "publishable": remaining == 0}

@app.post("/etl/review/{pkg_id}/discard-flagged")
async def review_discard_flagged(pkg_id: str, request: Request):
    """Discard EVERY currently-flagged (disputed) question and clear all disputes,
    leaving the rest of the package publishable. Keeps the package non-empty."""
    _e, pkg = _load_reviewable(request, pkg_id)
    flagged = {d.get("qid") for d in review.disputes_of(pkg) if d.get("qid")}
    before = len(pkg.get("questions", []))
    kept = [x for x in pkg.get("questions", []) if x.get("id") not in flagged]
    if not kept:
        raise HTTPException(409, "discarding the flagged questions would leave the package empty")
    pkg["questions"] = kept
    pkg.setdefault("quality", {})["disputes"] = []
    review.write_held(pkg)
    return {"ok": True, "removed": before - len(kept),
            "remaining_questions": len(kept), "disputes_remaining": 0, "publishable": True}

@app.post("/etl/review/{pkg_id}/revalidate")
async def review_revalidate(pkg_id: str, request: Request):
    _e, pkg = _load_reviewable(request, pkg_id)
    body = await request.json()
    qid = (body.get("qid") or "").strip()
    q = review.find_question(pkg, qid)
    if not q:
        raise HTTPException(404, "question not found")
    src = review.get_src(pkg_id)
    if not src:
        raise HTTPException(409, "source text unavailable — cannot re-validate")
    from etl import orchestrator   # lazy: keeps service startup instant
    va = await asyncio.to_thread(orchestrator.validate_answer, q, src)
    review.clear_dispute(pkg, qid)
    if va.get("status") == "dispute":
        review.disputes_of(pkg).append({
            "qid": qid, "stem": (q.get("stem") or "")[:120],
            "stored": va.get("stored"), "derived": va.get("derived"),
            "reason": va.get("reason", ""), "evidence": (va.get("quote") or "")[:200],
        })
    review.write_held(pkg)
    return {"status": va.get("status"), "stored": va.get("stored"), "derived": va.get("derived"),
            "evidence": va.get("quote", ""), "disputes_remaining": len(review.disputes_of(pkg))}

@app.post("/etl/review/{pkg_id}/publish")
def review_publish(pkg_id: str, request: Request):
    _e, pkg = _load_reviewable(request, pkg_id)
    open_disputes = len(review.disputes_of(pkg))
    if open_disputes:
        raise HTTPException(409, f"{open_disputes} unresolved dispute(s) remain")
    entry = review.publish_held(pkg_id)
    if not entry:
        raise HTTPException(404, "held package not found")
    return {"ok": True, "entry": entry}

@app.delete("/etl/review/{pkg_id}")
def review_delete(pkg_id: str, request: Request):
    _load_reviewable(request, pkg_id)
    review.delete_held(pkg_id)
    return {"deleted": True}

@app.post("/etl/review/{pkg_id}/documents")
async def review_add_documents(pkg_id: str, request: Request, files: list[UploadFile] = File(default=[])):
    """Add one or more documents to an EXISTING package (held draft or published).
    Each new doc is processed per-document and merged in; the result lands in the
    held draft (Review) for the owner/admin to resolve + (re)publish."""
    e = _require_email(request)
    safe = review._safe_id(pkg_id)
    pkg = review.get_held(safe)
    from_published = False
    if pkg is None:
        ppath = os.path.join(PKG_DIR, safe + ".json") if safe else ""
        if safe and os.path.exists(ppath):
            pkg = json.load(open(ppath, encoding="utf-8")); from_published = True
        else:
            raise HTTPException(404, "package not found")
    if not _can_review(e, pkg):
        raise HTTPException(403, "not allowed")
    if not files:
        raise HTTPException(400, "no files")

    docs = []
    for f in files:
        sname = re.sub(r"[^A-Za-z0-9._-]+", "_", f.filename or "upload.pdf")
        path = os.path.join(DOC_DIR, sname)
        with open(path, "wb") as out:
            out.write(await f.read())
        t = _pretty_title(f.filename or sname)
        catalog.upsert_document({"id": _slug(sname), "title": t, "file": sname,
                                 "kind": "pdf", "uploaded_at": _now()})
        docs.append({"path": path, "title": t, "uri": f"documents/{sname}", "id": f"src-{_slug(sname)}"})

    # If it was published, pull it out of the Catalog while we extend it (it
    # becomes a held draft until the user re-publishes).
    if from_published:
        try: os.remove(os.path.join(PKG_DIR, safe + ".json"))
        except OSError: pass
        catalog.rebuild_package_index()

    title = pkg.get("title") or safe
    start_index = len(pkg.get("sources", []))
    jid = "job-" + datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    job = {"jobId": jid, "state": "queued", "packageId": safe, "title": title,
           "documents": [d["title"] for d in docs], "events": [], "created_at": _now(),
           "owner": e, "mode": "add",
           "files": [os.path.basename(dd["uri"]) for dd in docs if dd.get("path")]}
    JOBS[jid] = job; _save(job)
    threading.Thread(target=run_job, daemon=True, args=(
        jid, docs, None, None, safe, title, 999, 5, e, pkg, start_index)).start()
    return {"jobId": jid, "state": "queued", "packageId": safe}

@app.post("/etl/review/{pkg_id}/rename")
async def review_rename(pkg_id: str, request: Request):
    _e, pkg = _load_reviewable(request, pkg_id)
    body = await request.json()
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title required")
    pkg["title"] = title[:200]
    review.write_held(pkg)
    return {"ok": True, "title": pkg["title"]}

@app.post("/etl/packages/{pkg_id}/rename")
async def package_rename(pkg_id: str, request: Request):
    """Rename a PUBLISHED package's title (admin, or the package owner). The id
    is unchanged — only the catalog display title."""
    e = _require_email(request)
    safe = review._safe_id(pkg_id)
    path = os.path.join(PKG_DIR, safe + ".json") if safe else ""
    if not safe or not os.path.exists(path):
        raise HTTPException(404, "package not found")
    try:
        pkg = json.load(open(path, encoding="utf-8"))
    except Exception:
        raise HTTPException(500, "cannot read package")
    owner = (pkg.get("owner_email") or "").strip().lower()
    if not (_is_admin(e) or (owner and owner == e)):
        raise HTTPException(403, "not allowed")
    body = await request.json()
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title required")
    pkg["title"] = title[:200]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pkg, f, indent=2, ensure_ascii=False)
    _pkg_cache.pop(safe, None)
    catalog.rebuild_package_index()
    return {"ok": True, "title": pkg["title"]}


# ---- package visibility (private/shared) + viewer-filtered catalog -----------
def _visible_to(pkg_owner, visibility, viewer: str) -> bool:
    """Visible if shared (or legacy/no-visibility) OR the viewer owns it OR is admin."""
    if (visibility or "shared").strip().lower() != "private":
        return True
    owner = (pkg_owner or "").strip().lower()
    return bool(viewer) and (viewer == owner or _is_admin(viewer))


@app.get("/etl/catalog")
def catalog_list(request: Request):
    """The Catalog the viewer may see: shared/legacy packages for everyone, private
    packages only for their owner (or an admin)."""
    e = _email(request)
    try:
        entries = json.load(open(os.path.join(PKG_DIR, "index.json"), encoding="utf-8")).get("packages", [])
    except Exception:
        entries = []
    out = []
    for p in entries:
        if not _visible_to(p.get("owner"), p.get("visibility"), e):
            continue
        owner = (p.get("owner") or "").strip().lower()
        out.append({**p, "owned": bool(e) and (e == owner or _is_admin(e))})
    return {"packages": out}


@app.get("/etl/packages/{pkg_id}/content")
def package_content(pkg_id: str, request: Request):
    """Serve a package's JSON only if the viewer may see it (privacy is enforced here
    because /data/packages is no longer served statically)."""
    e = _email(request)
    safe = review._safe_id(pkg_id)
    path = os.path.join(PKG_DIR, safe + ".json") if safe else ""
    if not safe or not os.path.exists(path):
        raise HTTPException(404, "package not found")
    try:
        pkg = json.load(open(path, encoding="utf-8"))
    except Exception:
        raise HTTPException(500, "cannot read package")
    if not _visible_to(pkg.get("owner_email"), pkg.get("visibility"), e):
        raise HTTPException(403, "this package is private")
    return pkg


@app.post("/etl/packages/{pkg_id}/visibility")
async def package_visibility(pkg_id: str, request: Request):
    """Set a published package private/shared (owner or admin only)."""
    e = _require_email(request)
    safe = review._safe_id(pkg_id)
    path = os.path.join(PKG_DIR, safe + ".json") if safe else ""
    if not safe or not os.path.exists(path):
        raise HTTPException(404, "package not found")
    pkg = json.load(open(path, encoding="utf-8"))
    owner = (pkg.get("owner_email") or "").strip().lower()
    if not (_is_admin(e) or (owner and owner == e)):
        raise HTTPException(403, "not allowed")
    vis = ((await request.json()).get("visibility") or "").strip().lower()
    if vis not in ("private", "shared"):
        raise HTTPException(400, "visibility must be 'private' or 'shared'")
    pkg["visibility"] = vis
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pkg, f, indent=2, ensure_ascii=False)
    _pkg_cache.pop(safe, None)
    catalog.rebuild_package_index()
    return {"ok": True, "visibility": vis}


# ---- session membership management ------------------------------------------
@app.get("/etl/sessions/{sid}/packages")
def sessions_list_packages(sid: str, request: Request):
    e = _require_email(request)
    if sess.get_session(e, sid) is None:
        raise HTTPException(404, "session not found")
    return {"packages": sess.list_session_packages(e, sid)}

@app.patch("/etl/sessions/{sid}/packages/{pkg_id}")
async def sessions_set_package_options(sid: str, pkg_id: str, request: Request):
    """Set per (session, package) shuffle options."""
    e = _require_email(request)
    body = await request.json()
    opts = {}
    if "shuffle_questions" in body:
        opts["shuffle_questions"] = bool(body.get("shuffle_questions"))
    if "shuffle_options" in body:
        opts["shuffle_options"] = bool(body.get("shuffle_options"))
    if not opts:
        raise HTTPException(400, "nothing to update")
    if not sess.set_package_options(e, sid, pkg_id, **opts):
        raise HTTPException(404, "session not found")
    return {"ok": True, **opts}


@app.delete("/etl/sessions/{sid}/packages/{pkg_id}")
def sessions_remove_package(sid: str, pkg_id: str, request: Request):
    e = _require_email(request)
    if not sess.remove_package(e, sid, pkg_id):
        raise HTTPException(404, "session not found")
    return {"ok": True}


@app.get("/etl/packages/{pkg_id}/sessions")
def package_sessions(pkg_id: str, request: Request):
    """Which of the viewer's sessions currently contain this package (for the chooser)."""
    e = _email(request)
    if not e:
        return {"sessions": []}
    return {"sessions": sess.sessions_with_package(e, pkg_id)}
