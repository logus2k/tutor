// review.js — Dispute Review area.
//
// Held packages (non-publishable: key disputes) are resolved here by the
// uploader (owner) or an admin, then published into the Catalog. For each
// disputed question the reviewer sees the stored key + the answer-blind solved
// answer (both as hints) and resolves it by selecting the correct answer,
// fixing the enunciate, discarding the question, or re-running the validator.
//
// Backend: /etl/review* (etl/service.py + etl/review.py). See
// documents/technical_architecture.md §6.1.

const API = 'etl';   // document-relative → /tutor/etl/...

export class ReviewPanel {
  /**
   * @param {HTMLElement} root
   * @param {object} opts
   * @param {() => void} opts.onPublished  called after a package is published (refresh Catalog).
   */
  constructor(root, { onPublished } = {}) {
    this.root = root;
    this.onPublished = onPublished || (() => {});
    this.me = { authenticated: false, is_admin: false };
    this.current = null;   // open package id (detail view) or null (list)
  }

  /** (Re)load whatever view is active. Called when the rail item is opened. */
  async refresh() {
    try { this.me = await fetchJson(`${API}/me`); } catch { this.me = { authenticated: false }; }
    if (this.current) return this.openPackage(this.current);
    return this.renderList();
  }

  async renderList() {
    this.current = null;
    this.root.innerHTML = '';
    this.root.append(el('h2', 'leftview-title', '🛠 Review'));

    if (!this.me.authenticated) {
      this.root.append(el('p', 'muted', 'Sign in to review and publish your held packages.'));
      const a = el('a', 'tq-btn tq-btn-primary', 'Sign in with Google');
      a.href = `/oauth2/sign_in?rd=${encodeURIComponent(location.pathname + location.search)}`;
      this.root.append(a);
      return;
    }

    let data;
    try { data = await fetchJson(`${API}/review`); }
    catch (e) { this.root.append(el('p', 'muted', `Could not load: ${e.message}`)); return; }

    this.root.append(el('p', 'muted fame-note',
      data.is_admin ? 'Packages awaiting review (admin: all uploaders).'
                    : 'Your packages held for review. Resolve the flagged questions, then publish.'));

    const pkgs = data.packages || [];
    if (!pkgs.length) { this.root.append(el('p', 'muted', 'Nothing awaiting review. 🎉')); return; }

    const list = el('div', 'catalog');
    for (const p of pkgs) {
      const card = el('button', 'card');
      card.type = 'button';
      const who = p.mine ? 'you' : (p.owner_email || 'anonymous');
      card.append(
        el('div', 'card-title', p.title || p.id),
        el('div', 'card-meta', `${p.disputes} dispute${p.disputes === 1 ? '' : 's'} · ${p.questions} questions · score ${p.score ?? '?'} · ${who}`),
      );
      card.addEventListener('click', () => this.openPackage(p.id));
      list.appendChild(card);
    }
    this.root.append(list);
  }

  async openPackage(id) {
    this.current = id;
    this.root.innerHTML = '';
    let data;
    try { data = await fetchJson(`${API}/review/${encodeURIComponent(id)}`); }
    catch (e) { this.root.append(el('p', 'muted', `Could not load: ${e.message}`)); return; }

    const head = el('div', 'rv-head');
    const back = el('button', 'tq-link', '← Back to review list');
    back.type = 'button';
    back.addEventListener('click', () => this.renderList());
    head.append(back);
    this.root.append(head);

    this.root.append(el('h2', 'leftview-title', data.title || id));
    this.metaEl = el('p', 'muted', '');
    this.root.append(this.metaEl);

    this.actionsEl = el('div', 'rv-actions');
    this.publishBtn = el('button', 'tq-btn tq-btn-primary', 'Publish to Catalog');
    this.publishBtn.type = 'button';
    this.publishBtn.addEventListener('click', () => this.publish());
    const ren = el('button', 'tq-btn tq-btn-ghost', '✎ Rename');
    ren.type = 'button';
    ren.addEventListener('click', async () => {
      const t = (prompt('Package title:', data.title || id) || '').trim();
      if (!t || t === data.title) return;
      try { await fetchJson(`${API}/review/${encodeURIComponent(id)}/rename`, { method: 'POST', body: { title: t } }); this.openPackage(id); }
      catch (e) { alert(`Rename failed: ${e.message}`); }
    });
    const del = el('button', 'tq-btn tq-btn-ghost', '🗑 Discard package');
    del.type = 'button';
    del.addEventListener('click', () => this.discardPackage());
    this.actionsEl.append(this.publishBtn, ren, del);
    this.root.append(this.actionsEl);

    this.hasSource = data.has_source;
    this.listEl = el('div', 'rv-disputes');
    this.root.append(this.listEl);

    this.disputes = data.disputes || [];
    this.score = data.score; this.qTotal = data.questions_total;
    this.renderDisputes();
  }

  renderDisputes() {
    this.metaEl.textContent = `${this.disputes.length} unresolved dispute(s) · ${this.qTotal} questions · score ${this.score ?? '?'}`;
    this.publishBtn.disabled = this.disputes.length > 0;
    this.publishBtn.title = this.disputes.length ? 'Resolve all disputes first' : '';

    this.listEl.innerHTML = '';
    if (!this.disputes.length) {
      this.listEl.append(el('p', 'muted', 'All disputes resolved — you can publish.'));
      return;
    }
    for (const d of this.disputes) this.listEl.appendChild(this.disputeCard(d));
  }

  disputeCard(d) {
    const q = d.question || {};
    const multi = q.type === 'mcq_multi';
    const card = el('div', 'rv-card');
    card.append(el('div', 'rv-qid', `${d.qid} · ${q.type || ''}`));

    // Editable stem.
    const stem = el('textarea', 'rv-stem');
    stem.value = q.stem || '';
    stem.rows = 2;
    card.append(el('label', 'rv-label', 'Question'), stem);

    // Options: select the correct one(s) + edit text.
    const opts = el('div', 'rv-options');
    const inputs = [];
    for (const o of q.options || []) {
      const row = el('label', 'rv-option');
      const sel = document.createElement('input');
      sel.type = multi ? 'checkbox' : 'radio';
      sel.name = `rv-${d.qid}`;
      sel.checked = !!o.correct;
      const txt = document.createElement('input');
      txt.type = 'text'; txt.className = 'rv-opt-text'; txt.value = o.text || '';
      // Mark which option the stored key / solved answer point to.
      const tags = el('span', 'rv-opt-tags');
      if ((d.stored || []).includes(o.id)) tags.append(el('span', 'rv-tag rv-tag-stored', 'key'));
      if ((d.derived || []).includes(o.id)) tags.append(el('span', 'rv-tag rv-tag-solved', 'solved'));
      row.append(sel, txt, tags);
      opts.append(row);
      inputs.push({ id: o.id, sel, txt });
    }
    card.append(el('label', 'rv-label', 'Options (check the correct answer)'), opts);

    // Hint: stored vs solved + evidence.
    const hint = el('div', 'rv-hint');
    hint.append(el('span', 'rv-hint-line', `Stored key: ${(d.stored || []).join(', ') || '—'}  ·  Answer-blind solved: ${(d.derived || []).join(', ') || '—'}`));
    if (d.reason) hint.append(el('span', 'rv-hint-line muted', d.reason));
    if (d.evidence) hint.append(el('span', 'rv-evidence', `“${d.evidence}”`));
    card.append(hint);

    // Actions.
    const bar = el('div', 'rv-card-actions');
    const save = el('button', 'tq-btn tq-btn-primary', '✓ Save resolution');
    save.type = 'button';
    save.addEventListener('click', () => this.resolve(d.qid, {
      correct_ids: inputs.filter((i) => i.sel.checked).map((i) => i.id),
      stem: stem.value,
      options: inputs.map((i) => ({ id: i.id, text: i.txt.value })),
    }, save));

    const discard = el('button', 'tq-btn tq-btn-ghost', '🗑 Discard question');
    discard.type = 'button';
    discard.addEventListener('click', () => this.resolve(d.qid, { discard: true }, discard));

    bar.append(save, discard);

    if (this.hasSource) {
      const recheck = el('button', 'tq-btn tq-btn-ghost', '↻ Re-run check');
      recheck.type = 'button';
      recheck.addEventListener('click', () => this.revalidate(d.qid, hint, recheck));
      bar.append(recheck);
    }
    card.append(bar);
    return card;
  }

  async resolve(qid, body, btn) {
    if (btn) { btn.disabled = true; }
    try {
      await fetchJson(`${API}/review/${encodeURIComponent(this.current)}/resolve`,
        { method: 'POST', body: { qid, ...body } });
      this.disputes = this.disputes.filter((d) => d.qid !== qid);
      this.renderDisputes();
    } catch (e) {
      alert(`Could not save: ${e.message}`);
      if (btn) btn.disabled = false;
    }
  }

  async revalidate(qid, hintEl, btn) {
    const old = btn.textContent;
    btn.disabled = true; btn.textContent = 'Checking…';
    try {
      const r = await fetchJson(`${API}/review/${encodeURIComponent(this.current)}/revalidate`,
        { method: 'POST', body: { qid } });
      if (r.status === 'agree') {
        this.disputes = this.disputes.filter((d) => d.qid !== qid);
        this.renderDisputes();
      } else {
        hintEl.append(el('span', 'rv-hint-line', `Re-check: ${r.status} — solved ${(r.derived || []).join(', ') || '—'} vs key ${(r.stored || []).join(', ') || '—'}`));
        btn.disabled = false; btn.textContent = old;
      }
    } catch (e) {
      alert(`Re-check failed: ${e.message}`);
      btn.disabled = false; btn.textContent = old;
    }
  }

  async publish() {
    if (this.disputes.length) return;
    this.publishBtn.disabled = true;
    try {
      const r = await fetchJson(`${API}/review/${encodeURIComponent(this.current)}/publish`, { method: 'POST' });
      this.onPublished();
      this.current = null;
      this.root.innerHTML = '';
      this.root.append(el('h2', 'leftview-title', '🛠 Review'));
      this.root.append(el('p', 'muted', `Published “${(r.entry && r.entry.title) || this.current}” — it's now in the Catalog.`));
      const back = el('button', 'tq-btn tq-btn-primary', 'Back to review list');
      back.type = 'button';
      back.addEventListener('click', () => this.renderList());
      this.root.append(back);
    } catch (e) {
      alert(`Publish failed: ${e.message}`);
      this.publishBtn.disabled = false;
    }
  }

  async discardPackage() {
    if (!confirm('Discard this whole held package? This cannot be undone.')) return;
    try {
      await fetchJson(`${API}/review/${encodeURIComponent(this.current)}`, { method: 'DELETE' });
      this.renderList();
    } catch (e) { alert(`Could not discard: ${e.message}`); }
  }
}

// ---- helpers ------------------------------------------------------------

async function fetchJson(url, { method = 'GET', body } = {}) {
  const opts = { method, headers: { Accept: 'application/json' } };
  if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${method} ${url} → ${r.status}`);
  return r.json();
}

function el(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text != null) n.textContent = text;
  return n;
}
