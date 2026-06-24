// grounding.js — the "Grounding" tab (right pane, next to the Assistant).
//
// Shows the VERBATIM source chunk(s) the CURRENT question was written from,
// resolved as question.concept_ids → package concepts → concept.grounding[].
// ANSWER-SAFE (architecture §7.2): the source is revealed only AFTER the student
// answers — before that it could hand them the answer. The panel is reactive: it
// subscribes to the shared TutorContext and re-renders on every navigation/answer.

import { renderMarkdown } from './markdown.js';

export class GroundingPanel {
  /**
   * @param {HTMLElement} mount   the #grounding container.
   * @param {EventTarget} context the shared TutorContext (emits 'change').
   */
  constructor(mount, context) {
    this.mount = mount;
    this.context = context;
    context.addEventListener('change', (e) => this.render(e.detail));
    this.render(context.snapshot);
  }

  render(s) {
    const m = this.mount;
    m.innerHTML = '';
    // No title here — the right-pane tab already reads "📄 Grounding".
    const body = el('div', 'gr-body');
    m.appendChild(body);

    if (!s || !s.pkg) {
      return body.appendChild(el('p', 'gr-empty', 'Open a package and a question to see the source it was written from.'));
    }
    const q = s.question;
    if (!q) {
      return body.appendChild(el('p', 'gr-empty', 'Select a question to see its source passage.'));
    }
    if (!(s.qState && s.qState.answered)) {
      return body.appendChild(el('p', 'gr-locked',
        '🔒 Answer this question to reveal the source passage(s) it was written from.'));
    }

    const pkg = s.pkg;
    const concepts = typeof pkg.conceptsFor === 'function' ? pkg.conceptsFor(q) : [];
    const citations = typeof pkg.citationsFor === 'function' ? pkg.citationsFor(q) : [];

    if (!concepts.length) {
      body.appendChild(el('p', 'gr-empty', 'No grounding is recorded for this question.'));
    }
    for (const c of concepts) {
      const card = el('div', 'gr-concept');
      card.appendChild(el('div', 'gr-concept-title', c.title || c.id || 'Concept'));
      const spans = Array.isArray(c.grounding) ? c.grounding : [];
      if (!spans.length) {
        card.appendChild(el('p', 'gr-empty', '(no verbatim span recorded for this concept)'));
      }
      for (const g of spans) {
        const chunk = el('div', 'gr-chunk');
        const cite = g.citation || g.locator || '';
        if (cite) chunk.appendChild(el('div', 'gr-cite', cite));
        const body = el('div', 'gr-text');
        body.innerHTML = renderMarkdown(reflowLists(g.text || ''));   // renderMarkdown escapes first (XSS-safe), then renders tables/lists
        chunk.appendChild(body);
        card.appendChild(chunk);
      }
      body.appendChild(card);
    }
    if (citations.length) {
      const foot = el('div', 'gr-citations');
      foot.appendChild(el('span', 'gr-citations-label', 'Source: '));
      foot.appendChild(document.createTextNode(citations.join('; ')));
      body.appendChild(foot);
    }
  }
}

// The extractor sometimes flattens a list captured as a grounding span into one
// line; re-break it so renderMarkdown shows real <ol>/<ul> items.
//  - Numbered: "1. a. 2. b." → break before an "N. " that follows a sentence
//    period (so prose like "$5. 2 items" — no dot after the 2 — is left alone).
//  - Bullet: " - 'x' → A - 'y' → B" → break before a " - " whose item starts with
//    a quote (the cheat-sheet's keyword lists), so plain "term - definition"
//    dashes are NOT split.
function reflowLists(text) {
  let t = text || '';
  t = t.replace(/\.\s+(\d+\.\s)/g, '.\n$1');     // numbered run-ons
  t = t.replace(/\s+-\s+(?=['"])/g, '\n- ');      // quoted-item bullet run-ons
  return t;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}
