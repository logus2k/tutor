// sessions.js — Study Sessions: per-student, server-persisted workspaces.
//
// Login is OPTIONAL (mirrors the app's free-play model). Anonymous users see a
// sign-in prompt; signed-in students (Google via oauth2-proxy) get a list of
// their sessions, can create/rename/delete them, and pick an ACTIVE one. While
// a session is active, the package selections and per-question answers are
// persisted to it (see app.js), so progress survives reloads and restarts.
//
// Backend: /etl/me and /etl/sessions* (etl/sessions.py, SQLite). Identity comes
// from the proxy's X-Forwarded-Email header — never trusted from the client.

const API = 'etl';   // document-relative → /tutor/etl/...

export class SessionsPanel {
  /**
   * @param {HTMLElement} root  the Sessions view container.
   * @param {object} opts
   * @param {(session|null) => void} opts.onActivate  called when the active session changes.
   * @param {() => string|null}      opts.activeId    returns the currently-active session id.
   */
  constructor(root, { onActivate, activeId } = {}) {
    this.root = root;
    this.onActivate = onActivate || (() => {});
    this.activeId = activeId || (() => null);
    this.me = { authenticated: false, email: '' };
    this._build();
    this.refresh();
  }

  _build() {
    this.root.innerHTML = '';
    this.header = el('div', 'sessions-head');
    this.title = el('h2', 'leftview-title', 'Study Sessions');
    this.newBtn = el('button', 'tq-btn tq-btn-primary', '+ New session');
    this.newBtn.type = 'button';
    this.newBtn.addEventListener('click', () => this._create());
    this.header.append(this.title, this.newBtn);

    this.who = el('div', 'sessions-who');
    this.listEl = el('div', 'catalog');   // reuse card styling
    this.root.append(this.header, this.who, this.listEl);
  }

  async refresh() {
    try {
      this.me = await fetchJson(`${API}/me`);
    } catch { this.me = { authenticated: false, email: '' }; }

    if (!this.me.authenticated) {
      this.newBtn.style.display = 'none';
      this.who.innerHTML = '';
      const msg = el('p', 'muted', 'Sign in to save your progress across sessions. You can keep using Tutor without signing in — your answers just won’t be saved.');
      const btn = el('a', 'tq-btn tq-btn-primary', 'Sign in with Google');
      btn.href = `/oauth2/sign_in?rd=${encodeURIComponent(location.pathname + location.search)}`;
      this.who.append(msg, btn);
      this.listEl.innerHTML = '';
      return;
    }

    this.newBtn.style.display = '';
    this.who.textContent = `Signed in as ${this.me.email}`;
    await this._renderList();
  }

  async _renderList() {
    let sessions = [];
    try { sessions = (await fetchJson(`${API}/sessions`)).sessions || []; }
    catch (e) { this.listEl.innerHTML = `<p class="muted">Could not load sessions: ${escapeHtml(e.message)}</p>`; return; }

    this.listEl.innerHTML = '';
    if (!sessions.length) { this.listEl.innerHTML = '<p class="muted">No sessions yet. Create one to start tracking your progress.</p>'; return; }

    const active = this.activeId();
    for (const s of sessions) {
      const card = el('div', 'card session-card' + (s.id === active ? ' is-active' : ''));
      const open = el('button', 'session-open');
      open.type = 'button';
      open.append(
        el('div', 'card-title', s.name),
        el('div', 'card-meta', `${s.package_count} package${s.package_count === 1 ? '' : 's'} · ${s.answered} answered · ${s.correct} correct`),
      );
      open.addEventListener('click', () => this._activate(s));

      const actions = el('div', 'session-actions');
      const ren = iconBtn('✎', 'Rename'); ren.addEventListener('click', (e) => { e.stopPropagation(); this._rename(s); });
      const del = iconBtn('🗑', 'Delete'); del.addEventListener('click', (e) => { e.stopPropagation(); this._delete(s); });
      actions.append(ren, del);

      card.append(open, actions);
      this.listEl.appendChild(card);
    }
  }

  async _create() {
    const name = (prompt('Name this study session:', '') || '').trim();
    if (!name) return;
    try {
      const s = await fetchJson(`${API}/sessions`, { method: 'POST', body: { name } });
      await this._renderList();
      this._activate(s);
    } catch (e) { alert(`Could not create session: ${e.message}`); }
  }

  async _rename(s) {
    const name = (prompt('Rename session:', s.name) || '').trim();
    if (!name || name === s.name) return;
    try { await fetchJson(`${API}/sessions/${s.id}`, { method: 'PATCH', body: { name } }); await this._renderList(); }
    catch (e) { alert(`Could not rename: ${e.message}`); }
  }

  async _delete(s) {
    if (!confirm(`Delete session "${s.name}"? Its saved answers will be lost.`)) return;
    try {
      await fetchJson(`${API}/sessions/${s.id}`, { method: 'DELETE' });
      if (this.activeId() === s.id) this._activate(null);
      await this._renderList();
    } catch (e) { alert(`Could not delete: ${e.message}`); }
  }

  _activate(session) {
    this.onActivate(session);
    this._renderList();   // re-highlight
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

function iconBtn(glyph, title) {
  const b = el('button', 'session-icon', glyph);
  b.type = 'button'; b.title = title; b.setAttribute('aria-label', title);
  return b;
}

function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
