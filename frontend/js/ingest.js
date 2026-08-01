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
const ETL_CATALOG = BASE + 'etl/catalog';
const ETL_ME = BASE + 'etl/me';
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
  constructor(mount, { onPublished, onUploaded, getIngestSettings } = {}) {
    this.mount = mount;
    this.onPublished = onPublished || (() => {});
    this.onUploaded = onUploaded || (() => {});
    this.getIngestSettings = getIngestSettings || (() => ({}));
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

    // Target: "New package…" (default, first) OR an existing package you own → MERGE.
    // This replaces the old free-text title that silently overwrote same-named packages.
    const pkgSel = el('select', 'ingest-pkg');
    pkgSel.appendChild(new Option('➕ New package…', ''));
    const title = el('input');
    title.type = 'text';
    title.placeholder = 'New package name (optional)';
    title.className = 'ingest-title';
    const hint = el('div', 'ingest-hint');
    const syncMode = () => {
      const isNew = pkgSel.value === '';
      title.style.display = isNew ? '' : 'none';
      hint.textContent = isNew ? '' : 'These document(s) will be added to this package and merged, then de-duplicated.';
    };
    pkgSel.addEventListener('change', syncMode);

    const btn = el('button', 'ingest-btn', 'Ingest document');
    btn.type = 'submit';

    form.append(
      el('label', 'ingest-label', 'Add source document(s) — select several to combine'),
      file,
      el('label', 'ingest-sublabel', 'Add to'),
      pkgSel, title, hint, btn,
    );
    form.addEventListener('submit', (e) => { e.preventDefault(); this.submit(); });

    this.form = form;
    this.fileInput = file; this.pkgSel = pkgSel; this.titleInput = title; this.btn = btn;
    this.job = el('div', 'ingest-job hidden');
    this.mount.append(form, this.job);
    syncMode();
    this._loadPackages();
  }

  /** Populate the dropdown with the packages this user can add to (owner/admin). */
  async _loadPackages() {
    try {
      const [me, cat] = await Promise.all([
        fetch(ETL_ME, { headers: { Accept: 'application/json' } }).then((r) => r.json()).catch(() => ({})),
        fetch(ETL_CATALOG, { headers: { Accept: 'application/json' } }).then((r) => r.json()).catch(() => ({ packages: [] })),
      ]);
      const email = (me.email || '').toLowerCase(), admin = !!me.is_admin;
      const mine = (cat.packages || []).filter((p) => admin || ((p.owner || '').toLowerCase() === email && email));
      for (const p of mine) this.pkgSel.appendChild(new Option(p.title || p.id, p.id));
    } catch { /* leave just "New package…" */ }
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

  async submit() {
    const files = [...this.fileInput.files];
    if (!files.length) return;
    const targetId = this.pkgSel.value;            // '' → new package; else an existing id → MERGE
    const isNew = targetId === '';
    this.btn.disabled = true; this.fileInput.disabled = true;
    this.pkgSel.disabled = true; this.titleInput.disabled = true;

    const targetTitle = isNew ? '' : this.pkgSel.options[this.pkgSel.selectedIndex].text;
    this.buildJobPanel(isNew
      ? (files.length === 1 ? `Uploading “${files[0].name}”…` : `Uploading ${files.length} documents → new package…`)
      : `Adding ${files.length} document(s) to “${targetTitle}”…`);

    const cfg = this.getIngestSettings() || {};
    const fd = new FormData();
    for (const f of files) fd.append('files', f, f.name);
    const directive = { questionsPerConcept: cfg.questionsPerConcept, granularity: cfg.granularity };
    if (isNew) { directive.title = this.titleInput.value.trim() || undefined; directive.planOnly = cfg.planOnly; }
    fd.append('directive', JSON.stringify(directive));

    const url = isNew ? ETL_JOBS : `${BASE}etl/review/${encodeURIComponent(targetId)}/documents`;
    let jid;
    try {
      const resp = await fetch(url, { method: 'POST', body: fd });
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
    if (this.pkgSel) { this.pkgSel.disabled = false; }
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
    // Documents are the top-level unit, but we ALSO track progress WITHIN the current
    // document (concepts found → questions authored) so the bar + counts advance live.
    let fileTotal = 0, fileIdx = 0, filesDone = 0, curFile = '';
    let completedConcepts = 0, completedQ = 0, disputes = 0;
    let curConcepts = 0, curQuestions = 0;                 // current document (reset each doc)
    let inAuthor = false, transformStarted = false, authorTotal = 0, authorDone = 0;
    let sub = 'Queued…';
    let terminal = null, published = null, plan = null;
    const logs = [];
    for (const e of this.events) {
      switch (e.event) {
        case 'job.queued': sub = 'Queued…'; break;
        case 'extract.file':
          fileTotal = e.total || fileTotal; fileIdx = e.index || fileIdx; curFile = e.title || curFile;
          sub = 'extracting…';
          inAuthor = false; transformStarted = false; authorTotal = 0; authorDone = 0; curConcepts = 0; curQuestions = 0;
          break;
        case 'extract.file.done': filesDone = Math.max(filesDone, (e.index || 1) - 1); break;
        case 'extract.progress': if (e.detail) sub = e.detail; break;
        case 'stage.started':
          if (e.stage === 'author') { inAuthor = true; authorTotal = e.total || 0; authorDone = 0; sub = 'writing questions…'; }
          else if (e.stage === 'transform') { transformStarted = true; inAuthor = false; sub = 'finding concepts…'; }
          else sub = ({ chunk: 'segmenting…', segment: 'segmenting…', load: 'validating…' }[e.stage] || e.stage);
          break;
        case 'transform.progress':
          curConcepts = e.concepts_done ?? curConcepts; curQuestions = e.questions_accepted ?? curQuestions; break;
        case 'concept.authored': if (inAuthor) authorDone++; break;
        case 'doc.done':
          filesDone = Math.max(filesDone, e.index || 0);
          completedConcepts = e.concepts ?? completedConcepts; completedQ = e.questions ?? completedQ; disputes = e.disputes ?? disputes;
          curConcepts = 0; curQuestions = 0; inAuthor = false; transformStarted = false;
          logs.push(`✓ ${e.index}/${e.total}: ${e.title || ''} — ${e.questions} Q so far`.trim());
          break;
        case 'job.planned':
          plan = { documents: e.documents || [], concepts: e.concepts || 0, questions: e.questions || 0,
                   qpc: e.qpc, granularity: e.granularity };
          break;
        case 'dedup.done': if (e.removed) logs.push(`removed ${e.removed} duplicate(s)`); break;
        case 'dedup.semantic': if (e.removed) logs.push(`dedup: removed ${e.removed}, kept ${e.remaining}`); break;
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
        case 'job.published': published = e; terminal = { kind: 'ok', message: `Published “${e.packageId}”.` }; break;
        case 'job.held': terminal = { kind: 'warn', message: `Held for review.` }; break;
        default: break;
      }
    }

    const concepts = completedConcepts + curConcepts;       // live cumulative
    const questions = completedQ + curQuestions;
    let status, pct = null;
    if (!terminal && !plan) {
      status = fileTotal ? `Document ${fileIdx}/${fileTotal}${curFile ? `: ${curFile}` : ''} — ${sub}` : sub;
      if (fileTotal) {
        // Fraction WITHIN the current document: authoring is the long tail.
        const docFrac = inAuthor ? (authorTotal ? 0.15 + 0.85 * Math.min(1, authorDone / authorTotal) : 0.5)
                                 : (transformStarted ? 0.1 : 0);
        pct = Math.min(0.99, (filesDone + docFrac) / fileTotal);
      }
    }
    return { status, fileTotal, fileIdx, filesDone, concepts, questions, disputes, pct, logs, terminal, published, plan };
  }

  repaint() {
    if (!this.statusEl) return;
    const s = this.derive();

    if (s.terminal) {
      this.finish(s.terminal.kind, s.terminal.message);
      if (s.published && !this.published) { this.published = true; this.onPublished(); }
      this.stopTracking();
      localStorage.removeItem(LS_ACTIVE_JOB);
    } else if (s.plan) {
      this.renderPlan(s.plan);
      this.stopTracking();
      localStorage.removeItem(LS_ACTIVE_JOB);   // don't reattach a finished plan on reload
      return;
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

    // Rebuild the FULL log (idempotent); the panel scrolls (see .ingest-log CSS).
    this.logEl.innerHTML = '';
    for (const line of s.logs) this.logEl.append(el('div', 'ingest-log-line', line));
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

  /** Forecast ready (plan-only run): show the projected question count + an Author button. */
  renderPlan(plan) {
    this.terminalShown = true;
    this.statusEl.classList.remove('is-working');
    this.statusLabel.textContent = 'Forecast ready';
    this.statusElapsed.textContent = '';
    if (this.cancelBtn) this.cancelBtn.style.display = 'none';
    if (this.barWrap) this.barWrap.style.display = 'none';
    this.countsEl.textContent = `≈ ${plan.questions} questions · ${plan.concepts} concepts · ${plan.qpc}/concept · ${plan.granularity} granularity`;
    this.bannerEl.className = 'ingest-banner ingest-warn';
    this.bannerEl.innerHTML = '';
    this.bannerEl.append(el('div', null,
      `Forecast: about ${plan.questions} questions from ${plan.concepts} concepts. Adjust “Questions per concept” or “Concept granularity” in Settings and re-upload to re-forecast, or author now.`));
    const go = el('button', 'ingest-btn', `Author now (~${plan.questions} questions)`);
    go.type = 'button';
    go.addEventListener('click', () => this.authorPlanned(go));
    this.bannerEl.append(go);
  }

  /** Confirm a forecast → author the planned job on the server (reuses uploaded files). */
  async authorPlanned(btn) {
    if (!this.jid) return;
    if (btn) { btn.disabled = true; btn.textContent = 'Starting…'; }
    const cfg = this.getIngestSettings() || {};
    try {
      const r = await fetch(`${ETL_JOBS}/${encodeURIComponent(this.jid)}/author`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ questionsPerConcept: cfg.questionsPerConcept, granularity: cfg.granularity }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
    } catch (e) {
      alert(`Could not start authoring: ${e.message}`);
      if (btn) { btn.disabled = false; btn.textContent = 'Author now'; }
      return;
    }
    this.buildJobPanel('Authoring…');
    localStorage.setItem(LS_ACTIVE_JOB, this.jid);
    this.track(this.jid);
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
