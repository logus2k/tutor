// ingest.js — document ingestion UI for the Documents view.
//
// Uploads a document to the ETL backend (POST etl/jobs), then tracks the job
// live over socket.io (path <base>etl/socket.io, room job:{jobId}) and renders
// the upload → extract → transform → load → Catalog journey. On job.published it
// calls onPublished() so the app can refresh the Catalog + Documents list.
//
// Paths are derived from the page location so it works whether the app is served
// at / (dev) or /tutor/ (behind the domain proxy).

const BASE = location.pathname.replace(/[^/]*$/, '');   // '/tutor/' or '/'
const ETL_JOBS = BASE + 'etl/jobs';
const ETL_SIO_PATH = BASE + 'etl/socket.io';

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
    this.counts = { concepts: 0, questions: 0 };
    this.render();
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
    this.job = el('div', 'ingest-job hidden');
    this.mount.append(form, this.job);
  }

  async submit(fileInput, titleInput, btn) {
    const f = fileInput.files[0];
    if (!f) return;
    btn.disabled = true; fileInput.disabled = true; titleInput.disabled = true;

    this.counts = { concepts: 0, questions: 0 };
    this.job.classList.remove('hidden');
    this.job.innerHTML = '';
    this.statusEl = el('div', 'ingest-status', `Uploading “${f.name}”…`);
    this.countsEl = el('div', 'ingest-counts', '');
    this.logEl = el('div', 'ingest-log');
    this.bannerEl = el('div', 'ingest-banner hidden');
    this.job.append(this.statusEl, this.countsEl, this.logEl, this.bannerEl);

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
      this.reset(fileInput, titleInput, btn);
      return;
    }
    this.statusEl.textContent = 'Queued…';
    this.track(jid, () => this.reset(fileInput, titleInput, btn));
  }

  reset(fileInput, titleInput, btn) {
    btn.disabled = false; fileInput.disabled = false; titleInput.disabled = false;
    fileInput.value = '';
  }

  track(jid, onDone) {
    if (!window.io) { this.finish('failed', 'socket.io client unavailable'); onDone(); return; }
    const socket = window.io(location.origin, {
      path: ETL_SIO_PATH, transports: ['websocket', 'polling'], forceNew: true,
    });
    this.socket = socket;
    socket.on('connect', () => socket.emit('join', { jobId: jid }));
    socket.on('connect_error', () => { this.finish('failed', 'could not connect to the ingestion service'); onDone(); });
    socket.onAny((event, data) => {
      if (event === 'job.snapshot') {
        for (const e of (data && data.events) || []) this.handle(e.event, e);
        return;
      }
      this.handle(event, data || {});
      if (event === 'job.published' || event === 'job.held' || event === 'job.failed') {
        socket.disconnect();
        onDone();
      }
    });
  }

  log(text) {
    this.logEl.append(el('div', 'ingest-log-line', text));
    while (this.logEl.childNodes.length > 8) this.logEl.removeChild(this.logEl.firstChild);
    this.logEl.scrollTop = this.logEl.scrollHeight;
  }

  setCounts() {
    this.countsEl.textContent =
      `concepts: ${this.counts.concepts} · questions: ${this.counts.questions}`;
  }

  handle(event, d) {
    switch (event) {
      case 'job.queued': this.statusEl.textContent = 'Queued…'; break;
      case 'stage.started':
        this.statusEl.textContent = ({ extract: 'Extracting (docling)…', segment: 'Segmenting…', transform: 'Generating questions…', load: 'Validating & publishing…' }[d.stage] || d.stage); break;
      case 'extract.progress': if (d.detail) this.statusEl.textContent = `Extracting: ${d.detail}`; break;
      case 'stage.done': this.log(`✓ ${d.stage}`); break;
      case 'concept.gated': this.counts.concepts++; this.setCounts(); this.log(`concept ${d.objective || ''} — ${d.title || ''}`); break;
      case 'question.judged': if (d.accepted) { this.counts.questions++; this.setCounts(); } break;
      case 'transform.progress':
        if (d.concepts_done != null) this.counts.concepts = d.concepts_done;
        if (d.questions_accepted != null) this.counts.questions = d.questions_accepted;
        this.setCounts(); break;
      case 'dedup.done': if (d.removed) this.log(`removed ${d.removed} duplicate question(s)`); break;
      case 'package.judged': this.log(`quality score ${d.score}${d.publishable ? '' : ' (below threshold)'}`); break;
      case 'job.published':
        this.finish('ok', `Published “${d.packageId}” — now in the Catalog (${(d.catalogEntry && d.catalogEntry.questions) ?? this.counts.questions} questions).`);
        this.onPublished(); break;
      case 'job.held':
        this.finish('warn', `Held for review — quality score ${d.score ?? ''} below threshold. Not published.`); break;
      case 'job.failed':
        this.finish('failed', `Failed${d.stage ? ` at ${d.stage}` : ''}: ${d.error || 'unknown error'}`); break;
      default: break;
    }
  }

  finish(kind, message) {
    this.statusEl.textContent = ({ ok: 'Done', warn: 'Needs review', failed: 'Failed' }[kind] || '');
    this.bannerEl.className = `ingest-banner ingest-${kind}`;
    this.bannerEl.textContent = message;
  }
}
