// app.js — Tutor frontend bootstrap.
//
// Wires the split layout (questions left, tutor chat right) with a draggable
// vertical splitter, loads the canonical package, and connects the question
// panel's "Ask the tutor" action to the chat.

import { loadPackage, PackageError } from './package-loader.js';
import { QuestionPanel } from './question-renderer.js';
import { ChatPanel } from './chat.js';
import { initSplitter } from './splitter.js';

// ---- config (overridable via the settings bar; persisted in localStorage) --
const LS = {
  agent: 'tutor.agent',
};
const DEFAULTS = {
  // agent_server is reached same-origin through the domain proxy's public /llm/
  // path; the SDK builds `${baseUrl}/v1/...` → `/llm/v1/...`. Fixed by the
  // deployment, so it is not user-configurable.
  baseUrl: '/llm',
  agent: 'tutor',
  // Document-relative (the page is served at /tutor/, so this resolves to
  // /tutor/data/...). fetch() resolves against the document base, not this
  // module's URL — a leading ../ would wrongly escape to the site root.
  packageUrl: 'data/packages/ai-901-core.json',
};

const $ = (id) => document.getElementById(id);

function getConfig() {
  return {
    baseUrl: DEFAULTS.baseUrl,
    agent: localStorage.getItem(LS.agent) ?? DEFAULTS.agent,
  };
}

async function main() {
  const cfg = getConfig();

  // Settings bar (agent name only).
  $('cfg-agent').value = cfg.agent;
  $('cfg-save').addEventListener('click', () => {
    localStorage.setItem(LS.agent, $('cfg-agent').value.trim());
    location.reload();
  });

  initSplitter($('split'), $('left'), $('splitter'), $('right'));

  // Chat panel (independent of the package; always available).
  const chat = new ChatPanel($('chat'), { baseUrl: cfg.baseUrl, agent: cfg.agent });

  // Load the package and mount the question panel.
  try {
    const pkg = await loadPackage(DEFAULTS.packageUrl);
    $('pkg-title').textContent = pkg.title;
    $('pkg-sub').textContent = `${pkg.questionCount} questions · ${pkg.id}`;
    document.title = `Tutor — ${pkg.title}`;

    new QuestionPanel($('questions'), pkg, {
      onAskTutor: (q, p, state) => chat.prefill(buildPrompt(q, p, state)),
    });
  } catch (e) {
    const msg = e instanceof PackageError ? e.message : `Unexpected error: ${e.message}`;
    $('questions').innerHTML = `<div class="tq-error"><b>Could not load the question package.</b><br>${escapeHtml(msg)}</div>`;
  }
}

/**
 * Build the message that pre-fills the chat when the student asks for help.
 * Includes the stem and option *texts* only — never the correct flags or
 * rationales — so context never leaks the answer to an unattempted question.
 */
function buildPrompt(q, pkg, state) {
  const opts = (q.options || []).map((o, i) => `  ${String.fromCharCode(65 + i)}. ${o.text}`).join('\n');
  const concept = pkg.conceptsFor(q)[0];
  const lines = [
    `I'm working on this question:`,
    ``,
    `"${q.stem}"`,
    ``,
    `Options:`,
    opts,
  ];
  if (concept) lines.push(``, `(Topic: ${concept.title})`);
  lines.push('');
  if (state.answered) {
    lines.push(state.correct
      ? `I answered it correctly. Can you reinforce why, and what I should take away?`
      : `I got it wrong. Can you explain the concept so I understand my mistake?`);
  } else {
    lines.push(`I haven't answered yet — please help me understand the concept without telling me the answer.`);
  }
  return lines.join('\n');
}

function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

main();
