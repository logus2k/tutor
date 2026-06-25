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
  constructor(mount, { onPublished, onUploaded } = {}) {
    this.mount = mount;
    this.onPublished = onPublished || (() => {});
    this.onUploaded = onUploaded || (() => {});
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
    file.multiple = true;          // several documents → one combined package
    file.required = true;
    file.className = 'ingest-file';

    const title = el('input');
    title.type = 'text';
    title.placeholder = 'Package title (optional)';
    title.className = 'ingest-title';

    const btn = el('button', 'ingest-btn', 'Ingest document');
    btn.type = 'submit';

    form.append(
      el('label', 'ingest-label', 'Add source document(s) — select several to combine into one package'),
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
    this.cancelBtn = el('button', 'ingest-cancel', '✕ Cancel');
    this.cancelBtn.type = 'button';
    this.cancelBtn.addEventListener('click', () => this.cancel());
    this.statusEl.append(this.statusLabel, this.statusElapsed, this.cancelBtn);
    // Real progress bar (extract: files done/total; transform: concepts done/found).
    this.barWrap = el('div', 'ingest-bar');
    this.barFill = el('div', 'ingest-bar-fill');
    this.barWrap.append(this.barFill);
    this.countsEl = el('div', 'ingest-counts', '');
    this.logEl = el('div', 'ingest-log');
    this.bannerEl = el('div', 'ingest-banner hidden');
    this.job.append(this.statusEl, this.barWrap, this.countsEl, this.logEl, this.bannerEl);
  }

  async submit(fileInput, titleInput, btn) {
    const files = [...fileInput.files];
    if (!files.length) return;
    btn.disabled = true; fileInput.disabled = true; titleInput.disabled = true;

    this.buildJobPanel(files.length === 1 ? `Uploading “${files[0].name}”…`
                                          : `Uploading ${files.length} documents → one package…`);

    const fd = new FormData();
    for (const f of files) fd.append('files', f, f.name);   // all files → one job → one package
    fd.append('directive', JSON.stringify({ title: titleInput.value.trim() || undefined }));

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
    this.onUploaded();             // files are registered already → refresh the Uploaded list
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
    this.jid = jid;
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
    // Per-document model: documents are the top-level unit. Cumulative counts
    // come from doc.done (merge totals); the bar tracks documents done / total.
    let fileTotal = 0, fileIdx = 0, filesDone = 0, curFile = '';
    let concepts = 0, questions = 0, disputes = 0;
    let sub = 'Queued…';
    let terminal = null, published = null;
    const logs = [];
    for (const e of this.events) {
      switch (e.event) {
        case 'job.queued': sub = 'Queued…'; break;
        case 'extract.file': fileTotal = e.total || fileTotal; fileIdx = e.index || fileIdx; curFile = e.title || curFile; sub = 'extracting (docling)…'; break;
        case 'extract.file.done': filesDone = Math.max(filesDone, (e.index || 1) - 1); break;
        case 'extract.progress': if (!fileTotal && e.detail) sub = `docling: ${e.detail}`; break;
        case 'stage.started': sub = ({ segment: 'segmenting…', transform: 'generating questions…', load: 'validating…' }[e.stage] || e.stage); break;
        case 'doc.done':
          filesDone = Math.max(filesDone, e.index || 0);
          concepts = e.concepts ?? concepts; questions = e.questions ?? questions; disputes = e.disputes ?? disputes;
          logs.push(`✓ ${e.index}/${e.total}: ${e.title || ''} — ${e.questions} Q so far`.trim());
          break;
        case 'dedup.done': if (e.removed) logs.push(`removed ${e.removed} duplicate(s)`); break;
        case 'job.review_ready':
          terminal = { kind: (e.disputes ? 'warn' : 'ok'),
            message: (e.stoppedAt ? `Stopped at “${e.stoppedAt}”. ` : '') +
              `Saved to Review: ${e.questions} questions from ${e.sources} document(s)` +
              (e.disputes ? `, ${e.disputes} to resolve. ` : '. ') +
              `Open the Review tab to resolve and publish.` };
          break;
        case 'job.failed':
          terminal = { kind: 'failed',
            message: `Stopped at “${e.document || '?'}”${e.stage ? ` during ${e.stage}` : ''}: ${e.error || 'unknown error'}. ` +
              `Documents processed before it are kept — fix that file and add it to the package from Review.` };
          break;
        case 'job.cancelled': terminal = { kind: 'cancelled', message: 'Import cancelled — its draft and uploaded files were removed.' }; break;
        // legacy single-package flow (kept for compatibility)
        case 'job.published': published = e; terminal = { kind: 'ok', message: `Published “${e.packageId}”.` }; break;
        case 'job.held': terminal = { kind: 'warn', message: `Held for review.` }; break;
        default: break;
      }
    }

    let status, pct = null;
    if (!terminal) {
      status = fileTotal ? `Document ${fileIdx}/${fileTotal}${curFile ? `: ${curFile}` : ''} — ${sub}` : sub;
      if (fileTotal) pct = filesDone / fileTotal;
    }
    return { status, fileTotal, fileIdx, filesDone, concepts, questions, disputes, pct, logs, terminal, published };
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

    // Progress bar: determinate when we have a fraction, else an indeterminate sweep.
    if (this.barWrap) {
      if (s.terminal) {
        this.barWrap.classList.remove('indeterminate');
        this.barFill.style.width = '100%';
      } else if (s.pct != null) {
        this.barWrap.classList.remove('indeterminate');
        this.barFill.style.width = `${Math.round(Math.max(0, Math.min(1, s.pct)) * 100)}%`;
      } else {
        this.barWrap.classList.add('indeterminate');   // unknown total → animated sweep
        this.barFill.style.width = '100%';
      }
    }

    const bits = [];
    if (s.fileTotal) bits.push(`documents: ${s.filesDone}/${s.fileTotal}`);
    if (s.concepts) bits.push(`concepts: ${s.concepts}`);
    if (s.questions) bits.push(`questions: ${s.questions}`);
    if (s.disputes) bits.push(`to review: ${s.disputes}`);
    this.countsEl.textContent = bits.join(' · ');

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
    this.statusLabel.textContent = ({ ok: 'Done', warn: 'Needs review', failed: 'Failed', cancelled: 'Cancelled' }[kind] || '');
    this.statusElapsed.textContent = '';
    if (this.cancelBtn) this.cancelBtn.style.display = 'none';
    if (this.barWrap) this.barWrap.style.display = 'none';
    this.bannerEl.className = `ingest-banner ingest-${kind}`;
    this.bannerEl.textContent = message;
    this.reset();
  }

  /** Cancel the running import and clean up its mess (server stops + removes draft/files). */
  async cancel() {
    if (!this.jid) return;
    if (!confirm('Cancel this import and remove what it created so far?')) return;
    this.cancelBtn.disabled = true; this.cancelBtn.textContent = 'Cancelling…';
    try {
      const r = await fetch(`${ETL_JOBS}/${encodeURIComponent(this.jid)}/cancel`, { method: 'POST' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
    } catch (e) {
      alert(`Cancel failed: ${e.message}`);
      this.cancelBtn.disabled = false; this.cancelBtn.textContent = '✕ Cancel';
      return;
    }
    this.stopTracking();
    localStorage.removeItem(LS_ACTIVE_JOB);
    this.finish('cancelled', 'Import cancelled — its draft and uploaded files were removed.');
    this.onUploaded();   // refresh the Documents list (files removed)
  }
}
