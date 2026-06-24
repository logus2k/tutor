// ingest.js — document ingestion UI for the Documents view.
//
// Uploads a document to the ETL backend (POST etl/jobs), then tracks the job
// live and renders the upload → extract → transform → load → Catalog journey.
// On job.published it calls onPublished() so the app can refresh the Catalog +
// Documents list.
//
// Progress tracking is BELT-AND-SUSPENDERS:
//   • socket.io (path <base>etl/socket.io, room job:{jobId}) for live events, and
//   • a polling fallback (GET etl/jobs/{jobId} every few seconds) that re-reads
//     the authoritative event log — so progress advances even if the socket is
//     blocked/slow, which long stages (docling) made look frozen before.
// The active job id is persisted so a page reload RESUMES tracking. Rendering is
// idempotent — derived from the full event list each time — so snapshot + live
// + poll can't double-count.
//
// Paths are derived from the page location so it works whether the app is served
// at / (dev) or /tutor/ (behind the domain proxy).

const BASE = location.pathname.replace(/[^/]*$/, '');   // '/tutor/' or '/'
const ETL_JOBS = BASE + 'etl/jobs';
const ETL_SIO_PATH = BASE + 'etl/socket.io';
const LS_ACTIVE_JOB = 'tutor.activeJob';
const POLL_MS = 2500;

const STAGE_LABEL = { extract: 'Extracting (docling)…', segment: 'Segmenting…', transform: 'Generating questions…', load: 'Validating & publishing…' };
const TERMINAL = new Set(['published', 'held', 'failed', 'done']);

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

export class IngestPanel {
  constructor(mount, { onPublished } = {}) {
    this.mount = mount;
    this.onPublished = onPublished || (() => {});
    this.socket = null;
    this.poll = null;
    this.ticker = null;
    this.events = [];
    this.lastStatus = null;
    this.statusSince = Date.now();
    this.published = false;     // guard so onPublished fires once
    this.render();
    this.resumeActive();        // pick up an in-flight job after a reload
  }

  render() {
    this.mount.innerHTML = '';
    const form = el('form', 'ingest-form');

    const file = el('input');
    file.type = 'file';
    file.accept = '.pdf,.docx,.md,.markdown';
    file.required = true;
    file.className = 'ingest-file';

    const title = el('input');
    title.type = 'text';
    title.placeholder = 'Package title (optional)';
    title.className = 'ingest-title';

    const btn = el('button', 'ingest-btn', 'Ingest document');
    btn.type = 'submit';

    form.append(
      el('label', 'ingest-label', 'Add a source document'),
      file, title, btn,
    );
    form.addEventListener('submit', (e) => { e.preventDefault(); this.submit(file, title, btn); });

    this.form = form;
    this.fileInput = file; this.titleInput = title; this.btn = btn;
    this.job = el('div', 'ingest-job hidden');
    this.mount.append(form, this.job);
  }

  buildJobPanel(headline) {
    this.events = [];
    this.published = false;
    this.terminalShown = false;
    this.lastStatus = null;
    this.statusSince = Date.now();
    this.job.classList.remove('hidden');
    this.job.innerHTML = '';
    this.statusEl = el('div', 'ingest-status is-working');
    this.statusLabel = el('span', 'ingest-status-label', headline);
    this.statusElapsed = el('span', 'ingest-status-elapsed', '');
    this.statusEl.append(this.statusLabel, this.statusElapsed);
    this.countsEl = el('div', 'ingest-counts', '');
    this.logEl = el('div', 'ingest-log');
    this.bannerEl = el('div', 'ingest-banner hidden');
    this.job.append(this.statusEl, this.countsEl, this.logEl, this.bannerEl);
  }

  async submit(fileInput, titleInput, btn) {
    const f = fileInput.files[0];
    if (!f) return;
    btn.disabled = true; fileInput.disabled = true; titleInput.disabled = true;

    this.buildJobPanel(`Uploading “${f.name}”…`);

    const fd = new FormData();
    fd.append('files', f, f.name);
    fd.append('directive', JSON.stringify({ title: titleInput.value.trim() || f.name }));

    let jid;
    try {
      const resp = await fetch(ETL_JOBS, { method: 'POST', body: fd });
      if (!resp.ok) throw new Error(`upload failed (${resp.status})`);
      jid = (await resp.json()).jobId;
    } catch (e) {
      this.finish('failed', `Upload failed: ${e.message}`);
      this.reset();
      return;
    }
    localStorage.setItem(LS_ACTIVE_JOB, jid);
    this.setStatus('Queued…');
    this.track(jid);
  }

  /** After a reload, re-attach to a job that was still running. */
  async resumeActive() {
    const jid = localStorage.getItem(LS_ACTIVE_JOB);
    if (!jid) return;
    let job;
    try { job = await (await fetch(`${ETL_JOBS}/${encodeURIComponent(jid)}`, { headers: { Accept: 'application/json' } })).json(); }
    catch { localStorage.removeItem(LS_ACTIVE_JOB); return; }
    if (!job || !job.jobId) { localStorage.removeItem(LS_ACTIVE_JOB); return; }
    if (TERMINAL.has(job.state)) { localStorage.removeItem(LS_ACTIVE_JOB); return; }   // already done; nothing live to show
    this.btn.disabled = true;
    this.buildJobPanel('Reconnecting…');
    this.applyJob(job);
    this.track(jid);
  }

  reset() {
    if (this.btn) { this.btn.disabled = false; }
    if (this.fileInput) { this.fileInput.disabled = false; this.fileInput.value = ''; }
    if (this.titleInput) { this.titleInput.disabled = false; }
  }

  track(jid) {
    this.stopTracking();
    // 1) Live socket (best-effort).
    if (window.io) {
      const socket = window.io(location.origin, { path: ETL_SIO_PATH, transports: ['websocket', 'polling'], forceNew: true });
      this.socket = socket;
      socket.on('connect', () => socket.emit('join', { jobId: jid }));
      socket.onAny((event, data) => {
        if (event === 'job.snapshot') { this.events = (data && data.events) || []; }
        else { this.events.push({ event, ...(data || {}) }); }
        this.repaint();
      });
    }
    // 2) Polling fallback — authoritative job record, so progress never stalls
    //    even if the socket is blocked. Stops itself on a terminal state.
    const tick = async () => {
      try {
        const job = await (await fetch(`${ETL_JOBS}/${encodeURIComponent(jid)}`, { headers: { Accept: 'application/json' } })).json();
        if (job && job.events) this.applyJob(job);
      } catch { /* transient; keep polling */ }
    };
    this.poll = setInterval(tick, POLL_MS);
    tick();
    // 3) Elapsed ticker so long stages visibly advance.
    this.ticker = setInterval(() => this.paintElapsed(), 1000);
  }

  stopTracking() {
    if (this.socket) { try { this.socket.disconnect(); } catch { /* */ } this.socket = null; }
    if (this.poll) { clearInterval(this.poll); this.poll = null; }
    if (this.ticker) { clearInterval(this.ticker); this.ticker = null; }
  }

  /** Adopt the server's authoritative event log, then render. */
  applyJob(job) {
    this.events = job.events || [];
    this.repaint();
  }

  // ---- idempotent rendering (derive everything from the event list) ----

  derive() {
    let status = 'Queued…';
    let concepts = 0, questions = 0;
    let terminal = null;
    let published = null;
    const logs = [];
    for (const e of this.events) {
      switch (e.event) {
        case 'job.queued': status = 'Queued…'; break;
        case 'stage.started': status = STAGE_LABEL[e.stage] || e.stage; break;
        case 'extract.progress': if (e.detail) status = `Extracting: ${e.detail}`; break;
        case 'stage.done': logs.push(`✓ ${e.stage}`); break;
        case 'concept.gated': concepts++; logs.push(`concept ${e.objective || ''} — ${e.title || ''}`.trim()); break;
        case 'question.judged': if (e.accepted) questions++; break;
        case 'transform.progress':
          if (e.concepts_done != null) concepts = e.concepts_done;
          if (e.questions_accepted != null) questions = e.questions_accepted;
          break;
        case 'dedup.done': if (e.removed) logs.push(`removed ${e.removed} duplicate question(s)`); break;
        case 'package.judged': logs.push(`quality score ${e.score}${e.publishable ? '' : ' (below threshold)'}`); break;
        case 'job.published':
          published = e;
          terminal = { kind: 'ok', message: `Published “${e.packageId}” — now in the Catalog (${(e.catalogEntry && e.catalogEntry.questions) ?? questions} questions).` };
          break;
        case 'job.held': terminal = { kind: 'warn', message: `Held for review — quality score ${e.score ?? ''} below threshold. Not published.` }; break;
        case 'job.failed': terminal = { kind: 'failed', message: `Failed${e.stage ? ` at ${e.stage}` : ''}: ${e.error || 'unknown error'}` }; break;
        default: break;
      }
    }
    return { status, concepts, questions, logs, terminal, published };
  }

  repaint() {
    if (!this.statusEl) return;
    const s = this.derive();

    if (s.terminal) {
      this.finish(s.terminal.kind, s.terminal.message);
      if (s.published && !this.published) { this.published = true; this.onPublished(); }
      this.stopTracking();
      localStorage.removeItem(LS_ACTIVE_JOB);
    } else {
      this.setStatus(s.status);
    }

    this.countsEl.textContent = (s.concepts || s.questions)
      ? `concepts: ${s.concepts} · questions: ${s.questions}` : '';

    // Rebuild the log (last 8 lines) — cheap and keeps it idempotent.
    this.logEl.innerHTML = '';
    for (const line of s.logs.slice(-8)) this.logEl.append(el('div', 'ingest-log-line', line));
    this.logEl.scrollTop = this.logEl.scrollHeight;
  }

  setStatus(text) {
    if (text !== this.lastStatus) { this.lastStatus = text; this.statusSince = Date.now(); }
    this.statusLabel.textContent = text;
    this.statusEl.classList.add('is-working');
    this.paintElapsed();
  }

  paintElapsed() {
    if (!this.statusElapsed || this.terminalShown) return;
    const secs = Math.max(0, Math.round((Date.now() - this.statusSince) / 1000));
    this.statusElapsed.textContent = secs >= 1 ? ` · ${secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`}` : '';
  }

  finish(kind, message) {
    this.terminalShown = true;
    this.statusEl.classList.remove('is-working');
    this.statusLabel.textContent = ({ ok: 'Done', warn: 'Needs review', failed: 'Failed' }[kind] || '');
    this.statusElapsed.textContent = '';
    this.bannerEl.className = `ingest-banner ingest-${kind}`;
    this.bannerEl.textContent = message;
    this.reset();
  }
}
