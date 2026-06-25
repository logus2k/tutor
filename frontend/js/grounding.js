// grounding.js — the "Grounding" tab (right pane, next to the Assistant).
//
// Shows the VERBATIM source chunk(s) a question was written from, resolved as
// question.concept_ids → package concepts → concept.grounding[].
//
// Two drivers:
//  • Questions flow — subscribes to the shared TutorContext and renders the
//    CURRENT question's grounding, ANSWER-SAFE (revealed only after answering,
//    architecture §7.2).
//  • Review flow — `showConcepts(payload)` renders an explicit, already-resolved
//    grounding payload for the dispute card the reviewer selected (no gate).

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

  // ---- Questions flow (reactive, answer-safe) ---------------------------
  render(s) {
    const m = this.mount;
    if (!s || !s.pkg) return this._empty('Open a package and a question to see the source it was written from.');
    const q = s.question;
    if (!q) return this._empty('Select a question to see its source passage.');
    if (!(s.qState && s.qState.answered)) {
      m.innerHTML = '';
      return m.appendChild(el('p', 'gr-locked', '🔒 Answer this question to reveal the source passage(s) it was written from.'));
    }
    const pkg = s.pkg;
    const concepts = (typeof pkg.conceptsFor === 'function' ? pkg.conceptsFor(q) : []).map((c) => ({
      title: c.title || c.id, objective: c.objective,
      passages: (Array.isArray(c.grounding) ? c.grounding : []).map((g) => ({ text: g.text || '', citation: g.citation || g.locator || '' })),
    }));
    const citations = typeof pkg.citationsFor === 'function' ? pkg.citationsFor(q) : [];
    this._paint({ concepts, citations });
  }

  // ---- Review flow (explicit payload) -----------------------------------
  /** @param {{question?:{stem?:string}, concepts:Array, citations?:Array}} payload */
  showConcepts(payload = {}) {
    const stem = payload.question && payload.question.stem;
    this._paint({
      heading: stem ? `Reviewing: ${stem}` : 'Grounding',
      concepts: payload.concepts || [],
      citations: payload.citations || [],
    });
  }

  // ---- shared renderer --------------------------------------------------
  _empty(msg) { this.mount.innerHTML = ''; this.mount.appendChild(el('p', 'gr-empty', msg)); }

  _paint({ heading, concepts, citations }) {
    const m = this.mount;
    m.innerHTML = '';
    if (heading) m.appendChild(el('div', 'gr-head', heading));
    const body = el('div', 'gr-body');
    m.appendChild(body);

    if (!concepts.length) body.appendChild(el('p', 'gr-empty', 'No grounding is recorded for this question.'));
    for (const c of concepts) {
      const card = el('div', 'gr-concept');
      card.appendChild(el('div', 'gr-concept-title', c.objective ? `${c.objective} · ${c.title}` : (c.title || 'Concept')));
      const passages = c.passages || [];
      if (!passages.length) card.appendChild(el('p', 'gr-empty', '(no verbatim span recorded for this concept)'));
      for (const g of passages) {
        const chunk = el('div', 'gr-chunk');
        if (g.citation) chunk.appendChild(el('div', 'gr-cite', g.citation));
        const tb = el('div', 'gr-text');
        tb.innerHTML = renderMarkdown(reflowLists(g.text || ''));   // escapes first (XSS-safe), then renders tables/lists
        chunk.appendChild(tb);
        card.appendChild(chunk);
      }
      body.appendChild(card);
    }
    if (citations && citations.length) {
      const foot = el('div', 'gr-citations');
      foot.appendChild(el('span', 'gr-citations-label', 'Source: '));
      foot.appendChild(document.createTextNode(citations.join('; ')));
      body.appendChild(foot);
    }
  }
}

// The extractor sometimes flattens a list captured as a grounding span into one
// line; re-break it so renderMarkdown shows real <ol>/<ul> items.
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
