// context.js — the session's live "current context", as an observable store.
//
// The tutor assistant must be aware of the active package and the question the
// student is currently on (or that nothing is selected) AT ALL TIMES. Nav code
// publishes changes here; the chat reads a snapshot when composing each turn
// (prepending a system message) and subscribes to reflect awareness in the UI.
// Future agent_server TOOLS can query the same snapshot to act on live state.
//
// It is an EventTarget: `context.addEventListener('change', e => … e.detail)`.

export class TutorContext extends EventTarget {
  constructor() {
    super();
    this._pkg = null;        // TutorPackage | null
    this._question = null;   // question object | null
    this._qState = null;     // { answered, correct, selectedIds } | null
    this._qIndex = -1;
    this._qTotal = 0;
  }

  /** Set (or clear, with null) the active package. Resets the current question. */
  setPackage(pkg) {
    this._pkg = pkg || null;
    this._question = null;
    this._qState = null;
    this._qIndex = -1;
    this._qTotal = pkg ? pkg.questionCount : 0;
    this._emit();
  }

  clearPackage() { this.setPackage(null); }

  /** Set the question the student is currently viewing and its answer state. */
  setQuestion(question, state, index, total) {
    this._question = question || null;
    this._qState = state || null;
    if (index != null) this._qIndex = index;
    if (total != null) this._qTotal = total;
    this._emit();
  }

  get snapshot() {
    return {
      pkg: this._pkg,
      question: this._question,
      qState: this._qState,
      qIndex: this._qIndex,
      qTotal: this._qTotal,
    };
  }

  _emit() { this.dispatchEvent(new CustomEvent('change', { detail: this.snapshot })); }
}

/**
 * Render the live context as a system message for the assistant. ANSWER-SAFE:
 * the correct option is disclosed only AFTER the student has answered — never
 * for a question they have not yet attempted (architecture §7.2).
 */
export function describeContext(s) {
  const lines = ['[SESSION CONTEXT — this reflects what the student is looking at right now and updates as they navigate. Use it to ground your help.]'];

  if (!s || !s.pkg) {
    lines.push('No package is currently selected — the student is browsing the app (catalog / documents / settings). There is no active question.');
    return lines.join('\n');
  }

  const pkg = s.pkg;
  lines.push(`Active package: "${pkg.title}"${pkg.description ? ` — ${pkg.description}` : ''} (id: ${pkg.id}, ${pkg.questionCount} questions).`);

  if (!s.question) {
    lines.push('The student is not currently viewing a specific question.');
    return lines.join('\n');
  }

  const q = s.question;
  const letter = (i) => String.fromCharCode(65 + i);
  const opts = (q.options || []).map((o, i) => `    ${letter(i)}. ${o.text}`).join('\n');
  lines.push(`Current question (${s.qIndex + 1} of ${s.qTotal}, type ${q.type || 'mcq'}, difficulty ${q.difficulty ?? '?'}/5):`);
  lines.push(`  Stem: "${q.stem}"`);
  if (opts) lines.push(`  Options:\n${opts}`);
  const concept = typeof pkg.conceptsFor === 'function' ? pkg.conceptsFor(q)[0] : null;
  if (concept) lines.push(`  Topic: ${concept.title}`);

  const st = s.qState;
  if (st && st.answered) {
    const sel = new Set(st.selectedIds || []);
    const correct = (q.options || []).map((o, i) => (o.correct ? letter(i) : null)).filter(Boolean).join(', ');
    const chosen = (q.options || []).map((o, i) => (sel.has(o.id) ? letter(i) : null)).filter(Boolean).join(', ') || '(none)';
    lines.push(`  The student HAS ANSWERED: they chose ${chosen}; the correct answer is ${correct}; their answer was ${st.correct ? 'CORRECT' : 'INCORRECT'}. You may now explain why each option is right or wrong.`);
  } else {
    lines.push('  The student has NOT answered yet. Do NOT reveal which option is correct — guide them to reason it out.');
  }
  return lines.join('\n');
}
