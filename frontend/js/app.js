// app.js — Tutor frontend bootstrap.
//
// Layout: a full-width topbar; below it an activity rail (Catalog, Documents,
// Settings) that switches the LEFT pane, with the tutor chat docked on the
// right at all times via a draggable splitter.
//
//   Catalog   → list packages → open one → its questions (with a back link)
//   Documents → list uploaded source documents
//   Settings  → active agent + reasoning options (drive the chat)

import { loadPackage, PackageError } from './package-loader.js';
import { QuestionPanel } from './question-renderer.js';
import { ChatPanel } from './chat.js';
import { initSplitter } from './splitter.js';
import { TutorContext } from './context.js';
import { IngestPanel } from './ingest.js';
import { SessionsPanel } from './sessions.js';
import { FamePanel } from './fame.js';
import { GroundingPanel } from './grounding.js';
import { ReviewPanel } from './review.js';

const LS = {
  agent: 'tutor.agent',
  thinking: 'tutor.thinking',
  showReasoning: 'tutor.showReasoning',
};
const DEFAULTS = {
  // agent_server is reached same-origin through the domain proxy's public /llm/
  // path; the SDK builds `${baseUrl}/v1/...` → `/llm/v1/...`. Fixed by deploy.
  baseUrl: '/llm',
  agent: 'instructor',
  // Catalog + package content go through the AUTHENTICATED API (privacy-filtered);
  // static /data/packages is blocked in nginx. Documents index stays static.
  packagesIndex: 'etl/catalog',
  documentsIndex: 'data/documents/index.json',
};

// Assistant roles (each is an agent_server preset). The agent selector IS the
// role picker; Instructor is the default.
const ROLES = [
  { id: 'instructor', label: 'Instructor', icon: '📚' },
  { id: 'coach', label: 'Coach', icon: '🎯' },
  { id: 'mentor', label: 'Mentor', icon: '🧭' },
];

const $ = (id) => document.getElementById(id);
const boolPref = (key, dflt) => { const v = localStorage.getItem(key); return v == null ? dflt : v === 'true'; };

let chat = null;
let questionPanel = null;   // current package's panel (null until one is opened)
let packageIndex = [];      // catalog entries (for open_package by id)
let activeSession = null;   // { id, name } when a study session is active (signed in)
let currentPackageId = null;
// Live session context (active package + current question). Nav publishes here;
// the chat reads it each turn so the assistant is always aware of the state.
const context = new TutorContext();

async function main() {
  const savedAgent = localStorage.getItem(LS.agent);
  const settings = {
    // Roles are the agents; fall back to Instructor if an old/unknown one is stored.
    agent: ROLES.some((r) => r.id === savedAgent) ? savedAgent : DEFAULTS.agent,
    thinking: boolPref(LS.thinking, true),
    showReasoning: boolPref(LS.showReasoning, false),
  };

  // Chat (right pane) — created once, always available. clientTools are the
  // browser-executed tools the model can call (the rest, e.g. web_search, run
  // server-side in mcp-service); their schemas all come from the MCP catalog.
  chat = new ChatPanel($('chat'), {
    baseUrl: DEFAULTS.baseUrl, agent: settings.agent, io: window.io || null,
    thinking: settings.thinking, showReasoning: settings.showReasoning,
    context, clientTools: buildClientTools(),
    onStatus: (text, state) => setChatStatus(text, state),   // → bottom status pill
  });
  setAgentStatus(settings.agent);
  setChatStatus('Ready', 'ready');

  // Grounding tab (right pane, beside the Assistant): shows the current question's
  // verbatim source chunks, revealed after answering. Reactive via shared context;
  // also driven explicitly from Review (showConcepts).
  groundingPanel = new GroundingPanel($('grounding'), context);
  wireRightTabs();

  // The Assistant panel starts COLLAPSED on every load; the user opens it from the rail.
  setChatVisible(false);

  initSplitter($('split'), $('left'), $('splitter'), $('right'));
  wireRail();
  wireSettings(settings);

  await buildCatalog();
  buildDocuments();   // fire-and-forget; renders when ready

  // Document ingestion (upload → ETL → live progress → Catalog). Created once;
  // on publish it refreshes the Catalog and the Documents list.
  new IngestPanel($('ingest'), {
    onUploaded: () => buildDocuments(),                 // show new files in "Uploaded" right away
    onPublished: () => { buildCatalog(); buildDocuments(); },
  });

  // Study Sessions (per-student, server-persisted). Optional login.
  sessionsPanel = new SessionsPanel($('sessions-panel'), {
    onActivate: setActiveSession,
    activeId: () => (activeSession ? activeSession.id : null),
    onOpenPackage: (id, title) => openPackage({ id, title }),   // study a package from a session
    onPackageShuffleChanged: onPackageShuffleChanged,           // re-apply shuffle live
  });
  restoreActiveSession();   // re-activate last session (if still signed in)
  renderIdentity();         // top-right: Anonymous / signed-in user
  wireNotifications();      // status-bar bell: study reminders + job notices
  wireRolePicker();         // status-bar role pill → role select popup

  // Wall of Fame (per-package leaderboards). Refreshed on demand when shown.
  famePanel = new FamePanel($('fame-panel'), {
    packages: () => packageIndex,
    currentPackageId: () => currentPackageId,
  });

  // Dispute Review (held packages → resolve → publish). Refreshed when shown.
  reviewPanel = new ReviewPanel($('review-panel'), {
    onPublished: () => { buildCatalog(); buildDocuments(); },
    // Selecting a dispute card shows its grounding chunks in the Grounding tab.
    onSelectDispute: (payload) => { if (groundingPanel) groundingPanel.showConcepts(payload); setGroundingActive(true); },
  });
}

/** Top-right identity widget: "Anonymous" + Sign in, or the user + Sign out. */
async function renderIdentity() {
  const box = $('identity');
  let me = { authenticated: false, email: '' };
  try { me = await fetchJson('etl/me'); } catch { /* anonymous */ }
  const rd = encodeURIComponent(location.pathname + location.search);
  box.innerHTML = '';

  // Trigger: name on the LEFT, avatar on the RIGHT. Click toggles the menu.
  const trigger = el('button', 'identity-trigger');
  trigger.type = 'button';
  const menu = el('div', 'identity-menu hidden');

  if (me.authenticated) {
    // Just the avatar in the corner (no name beside it).
    if (me.picture) {
      const img = el('img', 'identity-pic');
      img.src = me.picture; img.alt = ''; img.referrerPolicy = 'no-referrer';
      img.onerror = () => img.replaceWith(el('span', 'identity-icon', '👤'));
      trigger.append(img);
    } else {
      trigger.append(el('span', 'identity-icon', '👤'));
    }
    // The dropdown shows the name (falling back to the email if unknown).
    menu.append(el('div', 'identity-menu-email', me.name || me.email));
    const out = el('a', 'identity-menu-item', 'Sign out');
    out.href = `/oauth2/sign_out?rd=${rd}`;
    menu.append(out);
  } else {
    trigger.append(el('span', 'identity-anon', 'Anonymous'), el('span', 'identity-icon', '👤'));
    const sign = el('a', 'identity-menu-item', 'Sign in');
    sign.href = `/oauth2/sign_in?rd=${rd}`;
    menu.append(sign);
  }

  trigger.addEventListener('click', (e) => { e.stopPropagation(); menu.classList.toggle('hidden'); });
  document.addEventListener('click', () => menu.classList.add('hidden'));
  box.append(trigger, menu);
}

let sessionsPanel = null;
let famePanel = null;
let reviewPanel = null;
let groundingPanel = null;

// ---- Study Sessions ------------------------------------------------------

const LS_ACTIVE = 'tutor.activeSession';

function setActiveSession(session) {
  activeSession = session || null;
  if (activeSession) localStorage.setItem(LS_ACTIVE, activeSession.id);
  else localStorage.removeItem(LS_ACTIVE);
  setSessionStatus(activeSession ? activeSession.name : null);
  // Selecting a session just activates it (stays on the Sessions view; the card
  // re-highlights). The user navigates to the Catalog themselves when ready.
}

async function restoreActiveSession() {
  const id = localStorage.getItem(LS_ACTIVE);
  if (!id) return;
  try {
    const s = await fetchJson(`etl/sessions/${id}`);   // 404/401 if gone or signed out
    activeSession = { id: s.id, name: s.name };
    setSessionStatus(s.name);
  } catch { localStorage.removeItem(LS_ACTIVE); }
}

/** A package's shuffle toggles changed (per session+package); if it's the
 *  active session's currently-open package, reload it so the new order applies. */
function onPackageShuffleChanged(sid, pkgId) {
  if (activeSession && activeSession.id === sid && currentPackageId === pkgId) {
    openPackage({ id: pkgId });
  }
}

// ---- per-package shuffle (deterministic, seeded by session + package) ----
// Stable across reloads so progress/order stay consistent; answers persist by
// option id, so reordering never affects grading or restore. Already-answered
// questions are kept FIRST (original order); only the rest are shuffled.
function _seededRandom(str) {
  let h = 1779033703 ^ str.length;
  for (let i = 0; i < str.length; i++) {
    h = Math.imul(h ^ str.charCodeAt(i), 3432918353);
    h = (h << 13) | (h >>> 19);
  }
  return () => {
    h = Math.imul(h ^ (h >>> 16), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    return ((h ^= h >>> 16) >>> 0) / 4294967296;
  };
}

function _shuffleInPlace(arr, rnd) {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(rnd() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

/** Reorder a loaded package's questions and/or each question's options.
 *  `answeredIds` (a Set) are pinned first, in original order; the rest shuffle. */
function shufflePackage(pkg, { questions, options, seed, answeredIds }) {
  if (questions) {
    const answered = [], rest = [];
    for (const q of pkg.questions) {
      (answeredIds && answeredIds.has(q.id) ? answered : rest).push(q);
    }
    _shuffleInPlace(rest, _seededRandom(`${seed}:q`));
    pkg.questions = [...answered, ...rest];
  }
  if (options) {
    for (const q of pkg.questions) {
      if (Array.isArray(q.options) && q.options.length > 1) {
        _shuffleInPlace(q.options, _seededRandom(`${seed}:o:${q.id}`));
      }
    }
  }
}

/** Persist a graded answer to the active session (no-op when anonymous). */
function persistAnswer(packageId, q, result) {
  if (!activeSession) return;
  fetch(`etl/sessions/${activeSession.id}/answers`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ package_id: packageId, question_id: q.id, selected_ids: result.selected, correct: result.correct, attempts: result.attempts }),
  }).catch(() => { /* non-fatal */ });
}

// ---- activity rail / left-pane view switching --------------------------

const VIEWS = ['sessions', 'questions', 'catalog', 'documents', 'fame', 'review', 'settings'];

function showView(name) {
  document.querySelectorAll('.rail-btn[data-view]').forEach((b) => b.classList.toggle('active', b.dataset.view === name));
  for (const v of VIEWS) $(`view-${v}`).classList.toggle('hidden', v !== name);
}

function wireRail() {
  // Mobile: the rail is a slide-in drawer toggled by the topbar hamburger.
  const toggle = $('rail-toggle');
  const backdrop = $('rail-backdrop');
  const setRailOpen = (open) => {
    document.body.classList.toggle('rail-open', open);
    if (toggle) toggle.setAttribute('aria-expanded', String(open));
  };
  if (toggle) toggle.addEventListener('click', (e) => { e.stopPropagation(); setRailOpen(!document.body.classList.contains('rail-open')); });
  if (backdrop) backdrop.addEventListener('click', () => setRailOpen(false));

  document.querySelectorAll('.rail-btn[data-view]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const v = btn.dataset.view;
      showView(v);
      setRailOpen(false);   // selecting a section closes the drawer (mobile)
      // The Wall of Fame reflects live scores — reload it each time it's opened.
      if (v === 'fame' && famePanel) famePanel.refresh();
      if (v === 'review' && reviewPanel) reviewPanel.refresh();
      if (v === 'documents') buildDocuments();   // always show the latest uploads
    });
  });
  // Robot button: toggle the chat pane (not a view).
  $('rail-chat').addEventListener('click', () => { setChatVisible($('split').classList.contains('chat-collapsed')); setRailOpen(false); });
}

/** Show/hide the chat (right) pane; collapsing gives the left pane full width. */
function setChatVisible(visible) {
  $('split').classList.toggle('chat-collapsed', !visible);
  $('rail-chat').classList.toggle('active', visible);
  localStorage.setItem('tutor.chatHidden', String(!visible));
}

// Right-pane tabs. The Assistant tab is always present; the Grounding tab is
// activated/deactivated from the toggle in the Questions footer (or closed via
// the tab-bar ✕). `groundingOn` is the source of truth.
let groundingOn = false;
let activeTab = 'chat';

function setActiveTab(tab) {
  if (tab === 'grounding' && !groundingOn) tab = 'chat';
  activeTab = tab;
  document.querySelectorAll('.rtab[data-rtab]').forEach((b) => {
    const on = b.dataset.rtab === tab;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', String(on));
  });
  $('chat').classList.toggle('hidden', tab !== 'chat');
  $('grounding').classList.toggle('hidden', tab !== 'grounding');
}

/** Show/hide the Grounding tab (toggle in Questions, or close ✕ on the tab bar). */
function setGroundingActive(on) {
  groundingOn = on;
  const gtab = document.querySelector('.rtab[data-rtab="grounding"]');
  if (gtab) gtab.classList.toggle('hidden', !on);
  if (on) { setChatVisible(true); setActiveTab('grounding'); }
  else if (activeTab === 'grounding') setActiveTab('chat');
  if (questionPanel) questionPanel.setGroundingState(on);   // keep the footer toggle in sync
}

function wireRightTabs() {
  document.querySelectorAll('.rtab[data-rtab]').forEach((btn) =>
    btn.addEventListener('click', () => setActiveTab(btn.dataset.rtab)));
  // Close ✕ (tab-bar level): closes the ACTIVE tab only.
  $('rtab-close').addEventListener('click', () => {
    if (activeTab === 'grounding') setGroundingActive(false);   // Grounding → back to Assistant
    else setChatVisible(false);                                  // Assistant → collapse the pane
  });
}

// ---- Catalog (packages) -------------------------------------------------

async function buildCatalog() {
  const listEl = $('catalog-list');
  let manifest;
  try {
    manifest = await fetchJson(DEFAULTS.packagesIndex);
  } catch (e) {
    listEl.innerHTML = errBox('Could not load the package catalog.', e);
    return;
  }
  const packages = manifest.packages || [];
  packageIndex = packages;

  let me = { is_admin: false, authenticated: false };
  try { me = await fetchJson('etl/me'); } catch { /* anon */ }

  listEl.innerHTML = '';
  listEl.append(el('h2', 'leftview-title', 'Catalog'));
  if (!packages.length) { listEl.append(el('p', 'muted', 'No packages yet.')); return; }

  for (const p of packages) {
    const card = el('div', 'card session-card');
    // Catalog cards are display + management only (visibility, sessions, rename).
    // Studying happens by opening a package from inside a Session, not here.
    const open = el('div', 'session-open');
    open.style.cursor = 'default';
    open.append(
      el('div', 'card-title', p.title || p.id),
      el('div', 'card-desc', p.description || ''),
      el('div', 'card-meta', `${p.questions != null ? p.questions + ' questions · ' : ''}${p.id}`),
    );
    card.append(open);

    const actions = el('div', 'session-actions');
    // Visibility — owner/admin can toggle private/shared; others see a static badge.
    if (p.owned) {
      const vis = el('button', 'session-icon', visLabel(p.visibility));
      vis.type = 'button'; vis.title = 'Toggle private / shared';
      vis.addEventListener('click', (e) => { e.stopPropagation(); togglePackageVisibility(p, vis); });
      actions.append(vis);
    } else {
      const badge = el('span', 'session-icon is-static', '🌐'); badge.title = 'Shared';
      actions.append(badge);
    }
    // Session membership — pick which of YOUR sessions include this package (signed in).
    if (me.authenticated) {
      const sb = el('button', 'session-icon', '📂');
      sb.type = 'button'; sb.title = 'Add to / remove from your study sessions';
      sb.addEventListener('click', (e) => { e.stopPropagation(); openSessionChooser(p, sb); });
      actions.append(sb);
    }
    if (me.is_admin || p.owned) {
      const ren = el('button', 'session-icon', '✎');
      ren.type = 'button'; ren.title = 'Rename';
      ren.addEventListener('click', (e) => { e.stopPropagation(); renamePackage(p); });
      actions.append(ren);
    }
    card.append(actions);
    listEl.appendChild(card);
  }
}

const visLabel = (v) => (v === 'private' ? '🔒' : '🌐');

/** Toggle a package private/shared (owner/admin). */
async function togglePackageVisibility(p, btn) {
  const next = p.visibility === 'private' ? 'shared' : 'private';
  btn.disabled = true;
  try {
    const r = await fetch(`etl/packages/${encodeURIComponent(p.id)}/visibility`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ visibility: next }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    p.visibility = next; btn.textContent = visLabel(next);
  } catch (e) { alert(`Could not change visibility: ${e.message}`); }
  finally { btn.disabled = false; }
}

function closePkgMenu() { document.querySelectorAll('.pkg-menu').forEach((m) => m.remove()); }

/** Popup to add/remove a package from the user's study sessions (editable anytime). */
async function openSessionChooser(p, anchor) {
  closePkgMenu();
  const menu = el('div', 'pkg-menu');
  menu.append(el('div', 'pkg-menu-head', `Sessions with “${p.title || p.id}”`));
  const body = el('div', 'pkg-menu-body'); body.append(el('div', 'muted', 'Loading…'));
  menu.append(body);
  document.body.appendChild(menu);
  const r = anchor.getBoundingClientRect();
  menu.style.left = `${Math.max(8, r.right - 240)}px`;
  menu.style.top = `${r.bottom + 6}px`;
  const onDoc = (ev) => { if (!menu.contains(ev.target)) { closePkgMenu(); document.removeEventListener('click', onDoc); } };
  setTimeout(() => document.addEventListener('click', onDoc), 0);
  try {
    const sessions = (await fetchJson('etl/sessions')).sessions || [];
    const inSet = new Set((await fetchJson(`etl/packages/${encodeURIComponent(p.id)}/sessions`)).sessions || []);
    body.innerHTML = '';
    if (!sessions.length) { body.append(el('div', 'muted', 'No sessions yet — create one in the Sessions tab.')); return; }
    for (const s of sessions) {
      const row = el('label', 'pkg-menu-row');
      const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = inSet.has(s.id);
      cb.addEventListener('change', async () => {
        cb.disabled = true;
        try {
          if (cb.checked) {
            await fetch(`etl/sessions/${encodeURIComponent(s.id)}/packages`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ package_id: p.id }) });
          } else {
            await fetch(`etl/sessions/${encodeURIComponent(s.id)}/packages/${encodeURIComponent(p.id)}`, { method: 'DELETE' });
          }
        } catch (e) { cb.checked = !cb.checked; alert(`Failed: ${e.message}`); }
        finally { cb.disabled = false; }
      });
      row.append(cb, el('span', 'pkg-menu-name', s.name || s.id));
      body.append(row);
    }
  } catch (e) { body.innerHTML = ''; body.append(el('div', 'muted', 'Could not load sessions.')); }
}

/** Rename a published package's catalog title (admin/owner; id unchanged). */
async function renamePackage(p) {
  const t = (prompt('Package title:', p.title || p.id) || '').trim();
  if (!t || t === p.title) return;
  try {
    const r = await fetch(`etl/packages/${encodeURIComponent(p.id)}/rename`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: t }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    buildCatalog();
  } catch (e) { alert(`Rename failed: ${e.message}`); }
}

async function openPackage(entry) {
  const mount = $('questions');
  mount.innerHTML = '<p class="muted">Loading…</p>';
  showView('questions');

  try {
    const pkg = await loadPackage(`etl/packages/${encodeURIComponent(entry.id)}/content`);

    // Fetch saved answers + this package's per-session shuffle options BEFORE rendering,
    // so answered questions can be pinned first and the right shuffle applied.
    let saved = [];
    let pkgOpts = null;
    if (activeSession) {
      try {
        const r = await fetchJson(`etl/sessions/${activeSession.id}/answers?package_id=${encodeURIComponent(pkg.id)}`);
        saved = r.answers || [];
        pkgOpts = r.options || null;   // null when the package isn't a member of the session
      } catch { /* non-fatal — free play continues */ }
    }
    if (pkgOpts && (pkgOpts.shuffle_questions || pkgOpts.shuffle_options)) {
      shufflePackage(pkg, {
        questions: !!pkgOpts.shuffle_questions,
        options: !!pkgOpts.shuffle_options,
        seed: `${activeSession.id}:${pkg.id}`,
        answeredIds: new Set(saved.map((a) => a.question_id)),
      });
    }
    $('pkg-title').textContent = pkg.title;
    document.title = `Tutor — ${pkg.title}`;
    setPackageStatus(pkg.title);
    context.setPackage(pkg);
    currentPackageId = pkg.id;
    mount.innerHTML = '';
    questionPanel = new QuestionPanel(mount, pkg, {
      onAskTutor: (q, p, state) => {
        setChatVisible(true);            // reveal the Assistant pane (it may be collapsed)
        setActiveTab('chat');
        chat.ask(buildPrompt(q, p, state));   // submit straight to the chat (don't just prefill)
      },
      // Keep the assistant aware of the question the student is on + its state.
      onQuestionChange: (info) => context.setQuestion(
        info.question,
        { answered: info.answered, correct: info.correct, selectedIds: info.selectedIds },
        info.index, info.total,
      ),
      // Persist each graded answer to the active study session (if signed in).
      onAnswered: (q, result) => persistAnswer(pkg.id, q, result),
      // Grounding tab toggle (between the nav buttons).
      isGroundingOn: () => groundingOn,
      onToggleGrounding: () => setGroundingActive(!groundingOn),
    });

    // Restore saved answers (already fetched above, before shuffling).
    if (saved.length) questionPanel.applySaved(saved);
  } catch (e) {
    const msg = e instanceof PackageError ? e.message : `Unexpected error: ${e.message}`;
    mount.innerHTML = errBox('Could not load this package.', { message: msg });
  }
}

// ---- client-side tools (executed in the browser; schemas live in MCP) ----

/** A compact, answer-safe snapshot of live state for tool results. */
function stateResult() {
  const s = context.snapshot;
  if (!s.pkg) return { package: null, message: 'No package is open.' };
  const out = { package: { id: s.pkg.id, title: s.pkg.title } };
  if (s.question) {
    out.question = {
      number: s.qIndex + 1, total: s.qTotal,
      type: s.question.type, stem: s.question.stem,
      answered: !!(s.qState && s.qState.answered),
    };
    if (s.qState && s.qState.answered) out.question.correct = !!s.qState.correct;
  }
  return out;
}

function buildClientTools() {
  const needPackage = () => (questionPanel ? null : { error: 'No package is open. Ask the student to open one from the Catalog (or use open_package).' });
  return {
    next_question: () => needPackage() || (questionPanel.go(1), showView('questions'), stateResult()),
    previous_question: () => needPackage() || (questionPanel.go(-1), showView('questions'), stateResult()),
    goto_question: ({ number }) => needPackage() || (questionPanel.goTo((parseInt(number, 10) || 1) - 1), showView('questions'), stateResult()),
    get_current_state: () => stateResult(),
    get_progress: () => (questionPanel ? questionPanel.getProgress() : { error: 'No package is open.' }),
    // --- adaptive trio (Coach/Mentor) ---
    submit_answer: ({ answer, answers } = {}) => needPackage() || (showView('questions'), questionPanel.submitAnswer(answers ?? answer)),
    get_grounding: () => needPackage() || questionPanel.groundingForCurrent(),
    get_mastery: async () => {
      if (!currentPackageId) return { error: 'No package is open.' };
      try { return { mastery: (await fetchJson(`etl/mastery?package_id=${encodeURIComponent(currentPackageId)}`)).mastery }; }
      catch (e) { return { error: 'Mastery requires sign-in.', detail: String(e.message) }; }
    },
    next_best_question: async () => {
      if (!questionPanel) return needPackage();
      let mastery = [];
      try { mastery = (await fetchJson(`etl/mastery?package_id=${encodeURIComponent(currentPackageId)}`)).mastery || []; } catch { /* anonymous → fallback */ }
      const idx = questionPanel.pickNextBest(mastery);
      if (idx == null) return { message: 'All questions in this package are answered.', ...stateResult() };
      questionPanel.goTo(idx); showView('questions');
      return stateResult();
    },
    open_package: async ({ package_id }) => {
      const entry = packageIndex.find((p) => p.id === package_id);
      if (!entry) return { error: `No package with id '${package_id}'. Available: ${packageIndex.map((p) => p.id).join(', ') || '(none)'}` };
      await openPackage(entry);
      return stateResult();
    },
  };
}

// ---- Documents ----------------------------------------------------------

async function buildDocuments() {
  const listEl = $('documents-list');
  let manifest;
  try {
    manifest = await fetchJson(DEFAULTS.documentsIndex);
  } catch (e) {
    listEl.innerHTML = errBox('Could not load documents.', e);
    return;
  }
  const docs = manifest.documents || [];
  if (!docs.length) { listEl.innerHTML = '<p class="muted">No documents uploaded yet.</p>'; return; }

  listEl.innerHTML = '';
  for (const d of docs) {
    const card = el('div', 'card card-doc');
    card.append(
      el('div', 'card-title', `📄 ${d.title || d.id}`),
      el('div', 'card-meta', [d.kind, d.extractor, d.uri].filter(Boolean).join(' · ')),
    );
    listEl.appendChild(card);
  }
}

// ---- Settings -----------------------------------------------------------

function wireSettings(settings) {
  const agentSel = $('set-agent');
  const thinkCb = $('set-thinking');
  const showCb = $('set-show');

  // Reflect current values.
  if ([...agentSel.options].some((o) => o.value === settings.agent)) agentSel.value = settings.agent;
  thinkCb.checked = settings.thinking;
  showCb.checked = settings.showReasoning;

  agentSel.addEventListener('change', () => applyRole(agentSel.value));
  thinkCb.addEventListener('change', () => {
    localStorage.setItem(LS.thinking, String(thinkCb.checked));
    chat.setThinking(thinkCb.checked);
  });
  showCb.addEventListener('change', () => {
    localStorage.setItem(LS.showReasoning, String(showCb.checked));
    chat.setShowReasoning(showCb.checked);
  });
}

// ---- notifications (status-bar bell) ------------------------------------

let notifMenu = null;

function wireNotifications() {
  const bell = $('sb-notifications');
  if (!bell) return;
  bell.appendChild(el('span', 'sb-badge hidden'));
  notifMenu = el('div', 'notif-menu hidden');
  document.querySelector('.statusbar').appendChild(notifMenu);
  bell.addEventListener('click', (e) => {
    e.stopPropagation();
    if (notifMenu.classList.contains('hidden')) openNotifications();
    else notifMenu.classList.add('hidden');
  });
  document.addEventListener('click', () => notifMenu && notifMenu.classList.add('hidden'));
  refreshNotifBadge();
  setInterval(refreshNotifBadge, 60000);
}

function setNotifBadge(n) {
  const b = $('sb-notifications') && $('sb-notifications').querySelector('.sb-badge');
  if (!b) return;
  b.textContent = n > 9 ? '9+' : String(n);
  b.classList.toggle('hidden', !n);
}

async function refreshNotifBadge() {
  try { setNotifBadge((await fetchJson('etl/notifications')).unread || 0); }
  catch { setNotifBadge(0); }   // anonymous / offline
}

async function openNotifications() {
  notifMenu.innerHTML = '';
  notifMenu.classList.remove('hidden');
  notifMenu.append(el('div', 'notif-head', 'Notifications'));
  let data;
  try { data = await fetchJson('etl/notifications'); }
  catch { notifMenu.append(el('div', 'notif-empty', 'Sign in to get study reminders and updates.')); return; }
  const items = data.notifications || [];
  if (!items.length) { notifMenu.append(el('div', 'notif-empty', 'Nothing yet — keep studying! 📚')); }
  for (const n of items) {
    const row = el('div', 'notif-item' + (n.read ? '' : ' unread'));
    row.append(el('div', 'notif-title', n.title));
    if (n.body) row.append(el('div', 'notif-body', n.body));
    notifMenu.append(row);
  }
  // Opening marks everything read.
  fetch('etl/notifications/read', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
    .then(() => setNotifBadge(0)).catch(() => {});
}

// ---- status bar ---------------------------------------------------------

function setAgentStatus(name) {
  const r = ROLES.find((x) => x.id === name);
  const el = $('sb-agent');
  el.textContent = r ? `${r.icon} ${r.label}` : `🤖 ${name}`;
  ROLES.forEach((x) => el.classList.toggle(`sb-role-${x.id}`, x.id === name));
  // The right-pane chat tab shows the SELECTED assistant's name (not generic "Assistant").
  const tab = document.querySelector('.rtab[data-rtab="chat"]');
  if (tab) tab.textContent = r ? r.label : 'Assistant';
}

/** Switch the active role (agent). Keeps the pill, the chat, and Settings in sync. */
function applyRole(id) {
  if (!ROLES.some((r) => r.id === id)) return;
  localStorage.setItem(LS.agent, id);
  if (chat) chat.setAgent(id);
  setAgentStatus(id);
  const sel = $('set-agent'); if (sel && sel.value !== id) sel.value = id;
}

/** Make the bottom-left role pill open a small role-select popup. */
let roleMenu = null;
function wireRolePicker() {
  const pill = $('sb-agent');
  if (!pill) return;
  roleMenu = el('div', 'role-menu hidden');
  for (const r of ROLES) {
    const item = el('button', 'role-item');
    item.type = 'button';
    item.dataset.role = r.id;
    item.append(el('span', 'role-item-icon', r.icon), el('span', 'role-item-label', r.label));
    item.addEventListener('click', (e) => { e.stopPropagation(); applyRole(r.id); roleMenu.classList.add('hidden'); });
    roleMenu.append(item);
  }
  document.querySelector('.statusbar').appendChild(roleMenu);
  pill.addEventListener('click', (e) => {
    e.stopPropagation();
    const cur = localStorage.getItem(LS.agent) || DEFAULTS.agent;
    roleMenu.querySelectorAll('.role-item').forEach((b) => b.classList.toggle('is-active', b.dataset.role === cur));
    roleMenu.classList.toggle('hidden');
  });
  document.addEventListener('click', () => roleMenu && roleMenu.classList.add('hidden'));
}
function setPackageStatus(title) { $('sb-package').textContent = title ? `📦 ${title}` : '📦 No package'; }
function setSessionStatus(name) { const el = $('sb-session'); if (el) el.textContent = name ? `🎯 ${name}` : '🎯 No session'; }
/** Assistant connection status → the colored pill in the bottom-right corner. */
function setChatStatus(text, state = 'ready') {
  const pill = $('sb-status'); if (!pill) return;
  pill.classList.remove('busy', 'error');
  if (state === 'busy' || state === 'error') pill.classList.add(state);
  const label = $('sb-status-text'); if (label) label.textContent = text;
  const led = $('sb-status-led'); if (led) led.title = text;
}

// ---- helpers ------------------------------------------------------------

async function fetchJson(url) {
  const resp = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!resp.ok) throw new Error(`fetch ${url} → ${resp.status}`);
  return resp.json();
}

/**
 * Pre-fill the composer when the student clicks "Ask the tutor". The question
 * details are already injected as live context each turn (see context.js), so
 * this is just a short, natural opener the student can edit.
 */
function buildPrompt(q, pkg, state) {
  if (state.answered) {
    return state.correct
      ? 'Can you reinforce why my answer to this question is right, and what I should take away?'
      : 'I got this question wrong — can you explain the concept so I understand my mistake?';
  }
  return 'Can you help me understand the current question and reason toward the answer, without telling me the answer?';
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function errBox(title, e) {
  return `<div class="tq-error"><b>${escapeHtml(title)}</b><br>${escapeHtml((e && e.message) || '')}</div>`;
}

function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

main();
