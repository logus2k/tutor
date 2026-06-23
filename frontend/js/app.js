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

const LS = {
  agent: 'tutor.agent',
  thinking: 'tutor.thinking',
  showReasoning: 'tutor.showReasoning',
};
const DEFAULTS = {
  // agent_server is reached same-origin through the domain proxy's public /llm/
  // path; the SDK builds `${baseUrl}/v1/...` → `/llm/v1/...`. Fixed by deploy.
  baseUrl: '/llm',
  agent: 'tutor',
  // Document-relative (page served at /tutor/ → /tutor/data/...).
  packagesIndex: 'data/packages/index.json',
  packagesDir: 'data/packages/',
  documentsIndex: 'data/documents/index.json',
};

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
  const settings = {
    agent: localStorage.getItem(LS.agent) ?? DEFAULTS.agent,
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
    onHide: () => setChatVisible(false),
  });
  setAgentStatus(settings.agent);
  // Chat shown by default on desktop; hidden by default on mobile (questions
  // first). A saved preference always wins.
  const chatPref = localStorage.getItem('tutor.chatHidden');
  const mobile = window.matchMedia('(max-width: 760px)').matches;
  setChatVisible(chatPref == null ? !mobile : chatPref !== 'true');

  initSplitter($('split'), $('left'), $('splitter'), $('right'));
  wireRail();
  wireSettings(settings);

  await buildCatalog();
  buildDocuments();   // fire-and-forget; renders when ready

  // Document ingestion (upload → ETL → live progress → Catalog). Created once;
  // on publish it refreshes the Catalog and the Documents list.
  new IngestPanel($('ingest'), {
    onPublished: () => { buildCatalog(); buildDocuments(); },
  });

  // Study Sessions (per-student, server-persisted). Optional login.
  sessionsPanel = new SessionsPanel($('sessions-panel'), {
    onActivate: setActiveSession,
    activeId: () => (activeSession ? activeSession.id : null),
  });
  restoreActiveSession();   // re-activate last session (if still signed in)
  renderIdentity();         // top-right: Anonymous / signed-in user

  // Wall of Fame (per-package leaderboards). Refreshed on demand when shown.
  famePanel = new FamePanel($('fame-panel'), {
    packages: () => packageIndex,
    currentPackageId: () => currentPackageId,
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
    trigger.append(el('span', 'identity-user', me.name || me.email));
    if (me.picture) {
      const img = el('img', 'identity-pic');
      img.src = me.picture; img.alt = ''; img.referrerPolicy = 'no-referrer';
      img.onerror = () => img.replaceWith(el('span', 'identity-icon', '👤'));
      trigger.append(img);
    } else {
      trigger.append(el('span', 'identity-icon', '👤'));
    }
    if (me.name) menu.append(el('div', 'identity-menu-email', me.email));
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

// ---- Study Sessions ------------------------------------------------------

const LS_ACTIVE = 'tutor.activeSession';

function setActiveSession(session) {
  activeSession = session || null;
  if (activeSession) localStorage.setItem(LS_ACTIVE, activeSession.id);
  else localStorage.removeItem(LS_ACTIVE);
  setSessionStatus(activeSession ? activeSession.name : null);
  // Guide the flow: once a session is active, go pick a package to study.
  if (activeSession) showView('catalog');
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

/** Persist a graded answer to the active session (no-op when anonymous). */
function persistAnswer(packageId, q, result) {
  if (!activeSession) return;
  fetch(`etl/sessions/${activeSession.id}/answers`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ package_id: packageId, question_id: q.id, selected_ids: result.selected, correct: result.correct, attempts: result.attempts }),
  }).catch(() => { /* non-fatal */ });
}

// ---- activity rail / left-pane view switching --------------------------

const VIEWS = ['sessions', 'questions', 'catalog', 'documents', 'fame', 'settings'];

function showView(name) {
  document.querySelectorAll('.rail-btn[data-view]').forEach((b) => b.classList.toggle('active', b.dataset.view === name));
  for (const v of VIEWS) $(`view-${v}`).classList.toggle('hidden', v !== name);
}

function wireRail() {
  document.querySelectorAll('.rail-btn[data-view]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const v = btn.dataset.view;
      showView(v);
      // The Wall of Fame reflects live scores — reload it each time it's opened.
      if (v === 'fame' && famePanel) famePanel.refresh();
    });
  });
  // Robot button: toggle the chat pane (not a view).
  $('rail-chat').addEventListener('click', () => setChatVisible($('split').classList.contains('chat-collapsed')));
}

/** Show/hide the chat (right) pane; collapsing gives the left pane full width. */
function setChatVisible(visible) {
  $('split').classList.toggle('chat-collapsed', !visible);
  $('rail-chat').classList.toggle('active', visible);
  localStorage.setItem('tutor.chatHidden', String(!visible));
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
  if (!packages.length) { listEl.innerHTML = '<p class="muted">No packages yet.</p>'; return; }

  listEl.innerHTML = '';
  listEl.append(el('h2', 'leftview-title', 'Catalog'));
  for (const p of packages) {
    const card = el('button', 'card');
    card.type = 'button';
    card.append(
      el('div', 'card-title', p.title || p.id),
      el('div', 'card-desc', p.description || ''),
      el('div', 'card-meta', `${p.questions != null ? p.questions + ' questions · ' : ''}${p.id}`),
    );
    card.addEventListener('click', () => openPackage(p));
    listEl.appendChild(card);
  }
}

async function openPackage(entry) {
  const mount = $('questions');
  mount.innerHTML = '<p class="muted">Loading…</p>';
  showView('questions');

  try {
    const pkg = await loadPackage(DEFAULTS.packagesDir + entry.file);
    $('pkg-title').textContent = pkg.title;
    $('pkg-sub').textContent = `${pkg.questionCount} questions · ${pkg.id}`;
    document.title = `Tutor — ${pkg.title}`;
    setPackageStatus(pkg.title);
    context.setPackage(pkg);
    currentPackageId = pkg.id;
    mount.innerHTML = '';
    questionPanel = new QuestionPanel(mount, pkg, {
      onAskTutor: (q, p, state) => chat.prefill(buildPrompt(q, p, state)),
      // Keep the assistant aware of the question the student is on + its state.
      onQuestionChange: (info) => context.setQuestion(
        info.question,
        { answered: info.answered, correct: info.correct, selectedIds: info.selectedIds },
        info.index, info.total,
      ),
      // Persist each graded answer to the active study session (if signed in).
      onAnswered: (q, result) => persistAnswer(pkg.id, q, result),
    });

    // Restore saved answers for this package within the active session.
    if (activeSession) {
      try {
        await fetch(`etl/sessions/${activeSession.id}/packages`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ package_id: pkg.id }),
        });
        const { answers } = await fetchJson(`etl/sessions/${activeSession.id}/answers?package_id=${encodeURIComponent(pkg.id)}`);
        if (answers && answers.length) questionPanel.applySaved(answers);
      } catch { /* non-fatal — free play continues */ }
    }
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

  agentSel.addEventListener('change', () => {
    localStorage.setItem(LS.agent, agentSel.value);
    chat.setAgent(agentSel.value);
    setAgentStatus(agentSel.value);
  });
  thinkCb.addEventListener('change', () => {
    localStorage.setItem(LS.thinking, String(thinkCb.checked));
    chat.setThinking(thinkCb.checked);
  });
  showCb.addEventListener('change', () => {
    localStorage.setItem(LS.showReasoning, String(showCb.checked));
    chat.setShowReasoning(showCb.checked);
  });
}

// ---- status bar ---------------------------------------------------------

function setAgentStatus(name) { $('sb-agent').textContent = `🤖 ${name}`; }
function setPackageStatus(title) { $('sb-package').textContent = title ? `📦 ${title}` : '📦 No package'; }
function setSessionStatus(name) { const el = $('sb-session'); if (el) el.textContent = name ? `🎯 ${name}` : ''; }

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
