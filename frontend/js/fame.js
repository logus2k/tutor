// fame.js — Wall of Fame: per-package leaderboards of the best study sessions.
//
// Each row is one *session's* run at a package, ranked by a retry-aware score
// (a first-try correct answer scores 1; correct after N tries scores 1/N — so
// the leaderboard rewards getting it right with fewer attempts). Because the
// same student can study a package in several sessions, they can appear more
// than once — each session is its own entry.
//
// Two scopes:
//   • Mine     (default) — the signed-in student's own sessions (login needed).
//   • Everyone           — best sessions across all students (others masked).
//
// Backend: GET /etl/fame?package_id=&scope=mine|all (etl/service.py).

const API = 'etl';   // document-relative → /tutor/etl/...

export class FamePanel {
  /**
   * @param {HTMLElement} root
   * @param {object} opts
   * @param {() => Array}        opts.packages         catalog entries [{id,title,...}].
   * @param {() => string|null}  opts.currentPackageId the open package (default selection).
   */
  constructor(root, { packages, currentPackageId } = {}) {
    this.root = root;
    this.packages = packages || (() => []);
    this.currentPackageId = currentPackageId || (() => null);
    this.scope = 'mine';
    this.pkgId = null;
    this.me = { authenticated: false };
    this._build();
  }

  _build() {
    this.root.innerHTML = '';
    this.root.append(el('h2', 'leftview-title', '🏆 Wall of Fame'));

    const controls = el('div', 'fame-controls');
    this.pkgSel = el('select', 'fame-select');
    this.pkgSel.addEventListener('change', () => { this.pkgId = this.pkgSel.value; this._load(); });

    this.scopeWrap = el('div', 'fame-scope');
    this.mineBtn = el('button', 'fame-tab', 'Mine');
    this.allBtn = el('button', 'fame-tab', 'Everyone');
    this.mineBtn.type = this.allBtn.type = 'button';
    this.mineBtn.addEventListener('click', () => this._setScope('mine'));
    this.allBtn.addEventListener('click', () => this._setScope('all'));
    this.scopeWrap.append(this.mineBtn, this.allBtn);

    controls.append(this.pkgSel, this.scopeWrap);
    this.root.append(controls);

    this.note = el('p', 'muted fame-note');
    this.listEl = el('div', 'fame-list');
    this.root.append(this.note, this.listEl);
  }

  /** Refresh identity + package list, then load the board. Call when shown. */
  async refresh() {
    try { this.me = await fetchJson(`${API}/me`); }
    catch { this.me = { authenticated: false }; }
    if (!this.me.authenticated && this.scope === 'mine') this.scope = 'all';

    const pkgs = this.packages() || [];
    const prev = this.pkgId;
    this.pkgSel.innerHTML = '';
    if (!pkgs.length) {
      this.pkgSel.append(opt('', 'No packages'));
      this.note.textContent = '';
      this.listEl.innerHTML = '<p class="muted">No packages in the catalog yet.</p>';
      return;
    }
    for (const p of pkgs) this.pkgSel.append(opt(p.id, p.title || p.id));
    // Default to the currently-open package, else the prior choice, else first.
    this.pkgId = [this.currentPackageId(), prev, pkgs[0].id].find((id) => id && pkgs.some((p) => p.id === id));
    this.pkgSel.value = this.pkgId;
    this._syncScopeTabs();
    await this._load();
  }

  _setScope(scope) {
    if (scope === 'mine' && !this.me.authenticated) return;   // disabled
    this.scope = scope;
    this._syncScopeTabs();
    this._load();
  }

  _syncScopeTabs() {
    this.mineBtn.classList.toggle('is-active', this.scope === 'mine');
    this.allBtn.classList.toggle('is-active', this.scope === 'all');
    this.mineBtn.disabled = !this.me.authenticated;
    this.mineBtn.title = this.me.authenticated ? '' : 'Sign in to see your own sessions';
  }

  async _load() {
    if (!this.pkgId) return;
    this.listEl.innerHTML = '<p class="muted">Loading…</p>';
    let data;
    try {
      data = await fetchJson(`${API}/fame?package_id=${encodeURIComponent(this.pkgId)}&scope=${this.scope}`);
    } catch (e) {
      this.listEl.innerHTML = `<p class="muted">Could not load the leaderboard (${escapeHtml(e.message)}).</p>`;
      return;
    }
    this.note.textContent = this.scope === 'mine'
      ? 'Your best runs at this package — one row per study session.'
      : 'Best runs across all students. Score rewards correct answers with fewer retries.';

    const entries = data.entries || [];
    this.listEl.innerHTML = '';
    if (!entries.length) {
      this.listEl.innerHTML = this.scope === 'mine' && !this.me.authenticated
        ? '<p class="muted">Sign in to track and rank your own sessions.</p>'
        : '<p class="muted">No graded sessions yet for this package. Be the first!</p>';
      return;
    }

    for (const e of entries) {
      const row = el('div', 'fame-row' + (e.is_me ? ' is-me' : ''));
      row.append(
        el('span', 'fame-rank', medal(e.rank)),
        rowMain(e),
        el('span', 'fame-score', String(e.score)),
      );
      this.listEl.appendChild(row);
    }
  }
}

// ---- helpers ------------------------------------------------------------

function rowMain(e) {
  const main = el('div', 'fame-main');
  main.append(el('div', 'fame-who', e.who));
  const acc = `${e.correct}/${e.answered} correct`;
  const tries = e.attempts > e.answered ? ` · ${e.attempts} attempts` : '';
  main.append(el('div', 'fame-sub', `${e.session_name} · ${acc}${tries}`));
  return main;
}

function medal(rank) {
  return { 1: '🥇', 2: '🥈', 3: '🥉' }[rank] || `#${rank}`;
}

async function fetchJson(url) {
  const r = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

function el(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text != null) n.textContent = text;
  return n;
}

function opt(value, text) {
  const o = document.createElement('option');
  o.value = value; o.textContent = text;
  return o;
}

function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
