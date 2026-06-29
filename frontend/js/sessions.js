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
  constructor(root, { onActivate, activeId, onOpenPackage, onPackageShuffleChanged } = {}) {
    this.root = root;
    this.onActivate = onActivate || (() => {});
    this.activeId = activeId || (() => null);
    this.onOpenPackage = onOpenPackage || (() => {});
    this.onPackageShuffleChanged = onPackageShuffleChanged || (() => {});
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
    this.who.textContent = '';   // identity is shown in the top-right widget
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
        el('div', 'card-meta', `${s.package_count} package${s.package_count === 1 ? '' : 's'} · ${s.answered} answered · ${s.correct} correct · ${s.score ?? 0} pts`),
      );
      open.addEventListener('click', () => this._activate(s));

      const actions = el('div', 'session-actions');
      const pk = iconBtn('📂', 'Packages in this session'); pk.addEventListener('click', (e) => { e.stopPropagation(); this._togglePackages(s, card); });
      const ren = iconBtn('✎', 'Rename'); ren.addEventListener('click', (e) => { e.stopPropagation(); this._rename(s); });
      const del = iconBtn('🗑', 'Delete'); del.addEventListener('click', (e) => { e.stopPropagation(); this._delete(s); });
      actions.append(pk, ren, del);

      card.append(open, actions);
      this.listEl.appendChild(card);
      // The active session auto-expands its package list (clear feedback on select).
      if (s.id === active) this._togglePackages(s, card);
    }
  }

  /** Two independent shuffle toggles for ONE package within a session
   *  (none / one / both, default both). Persists immediately to that
   *  (session, package) and re-applies live if the package is open. */
  _shuffleRow(s, pkg) {
    const row = el('div', 'session-shuffle');
    row.append(el('span', 'session-shuffle-lbl', '🔀 Shuffle'));
    const mk = (key, label) => {
      const lab = el('label', 'session-shuffle-opt');
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = !!pkg[key];
      cb.addEventListener('click', (e) => e.stopPropagation());
      cb.addEventListener('change', async () => {
        const prev = !!pkg[key];
        pkg[key] = cb.checked;
        try {
          await fetchJson(`${API}/sessions/${s.id}/packages/${encodeURIComponent(pkg.package_id)}`,
            { method: 'PATCH', body: { [key]: cb.checked } });
          this.onPackageShuffleChanged(s.id, pkg.package_id);
        } catch (e) {
          pkg[key] = prev; cb.checked = prev;
          alert(`Could not update shuffle: ${e.message}`);
        }
      });
      lab.append(cb, el('span', null, label));
      return lab;
    };
    row.append(mk('shuffle_questions', 'Questions'), mk('shuffle_options', 'Options'));
    return row;
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

  /** Expand a session card to list its packages, each removable from the session
   *  ONLY (the package stays in the Catalog — this deletes just the membership). */
  async _togglePackages(s, card) {
    const existing = card.querySelector('.session-pkgs');
    if (existing) { existing.remove(); return; }
    const box = el('div', 'session-pkgs'); box.append(el('div', 'muted', 'Loading…'));
    card.append(box);
    try {
      const pkgs = (await fetchJson(`${API}/sessions/${s.id}/packages`)).packages || [];
      let titleById = {};
      try { titleById = Object.fromEntries(((await fetchJson(`${API}/catalog`)).packages || []).map((p) => [p.id, p.title])); } catch { /* anon */ }
      box.innerHTML = '';
      if (!pkgs.length) { box.append(el('div', 'muted', 'No packages in this session.')); return; }
      for (const pkg of pkgs) {
        const pid = pkg.package_id;
        const row = el('div', 'session-pkg-row');
        const top = el('div', 'session-pkg-top');
        const known = titleById[pid] != null;
        const name = el('button', 'session-pkg-name' + (known ? '' : ' is-orphan'),
          known ? titleById[pid] : `${pid} (no longer in Catalog)`);
        name.type = 'button';
        if (known) {
          name.title = 'Open this package’s questions';
          name.addEventListener('click', (ev) => { ev.stopPropagation(); this.onActivate(s); this.onOpenPackage(pid, titleById[pid]); });
        } else { name.disabled = true; }   // orphan: not openable
        top.append(name);
        const rm = iconBtn('✕', 'Remove from this session (keeps it in the Catalog)');
        rm.addEventListener('click', async (ev) => {
          ev.stopPropagation();
          try {
            const r = await fetch(`${API}/sessions/${s.id}/packages/${encodeURIComponent(pid)}`, { method: 'DELETE' });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            row.remove();
          } catch (e) { alert(`Could not remove: ${e.message}`); }
        });
        top.append(rm);
        row.append(top);
        if (known) row.append(this._shuffleRow(s, pkg));   // per-package shuffle toggles
        box.append(row);
      }
    } catch (e) { box.innerHTML = ''; box.append(el('div', 'muted', 'Could not load packages.')); }
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
