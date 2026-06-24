// question-renderer.js — turns a question object into an interactive HTML form.
//
// Control mapping (driven by the question's `type`, with an optional `render`
// hint per documents/technical_architecture.md §5.4):
//   mcq_single  → radio buttons   (or a <select> dropdown when render="dropdown")
//   mcq_multi   → checkboxes
//   true_false  → radio buttons (True / False)
//
// Grading is DETERMINISTIC — the answer is compared to the `correct` flags in
// the package; the LLM is never asked to grade (architecture §1, §7.1). Per-
// option rationale and the overall explanation are revealed only after the
// student submits.

let _uid = 0;
const nextUid = () => `q${++_uid}`;

export class QuestionPanel {
  /**
   * @param {HTMLElement} container  where the panel mounts.
   * @param {TutorPackage} pkg       the loaded package.
   * @param {object} [callbacks]
   * @param {(q, pkg, state) => void} [callbacks.onAskTutor]  "Ask the tutor" pressed.
   * @param {(q, result) => void}     [callbacks.onAnswered]  fired once per question on submit.
   * @param {(info) => void}          [callbacks.onQuestionChange]  fired on every render
   *        (navigation + answer): { question, index, total, answered, correct, selectedIds }.
   */
  constructor(container, pkg, callbacks = {}) {
    this.container = container;
    this.pkg = pkg;
    this.cb = callbacks;
    this.index = 0;
    // Per-question runtime state, keyed by question id. Survives navigation.
    this.state = new Map();
    this._render();
  }

  get question() { return this.pkg.questions[this.index]; }

  _stateFor(q) {
    if (!this.state.has(q.id)) this.state.set(q.id, { selected: new Set(), answered: false, correct: null, attempts: 0, hintsShown: 0 });
    return this.state.get(q.id);
  }

  go(delta) {
    const next = this.index + delta;
    if (next < 0 || next >= this.pkg.questionCount) return;
    this.index = next;
    this._render();
  }

  goTo(i) {
    if (i < 0 || i >= this.pkg.questionCount) return;
    this.index = i;
    this._render();
  }

  /**
   * Restore previously-saved answers (from a study session): mark those
   * questions answered with their selection + correctness, then re-render.
   * @param {Array<{question_id, selected_ids, correct}>} answers
   */
  applySaved(answers) {
    for (const a of answers || []) {
      if (!a || !a.question_id) continue;
      this.state.set(a.question_id, {
        selected: new Set(a.selected_ids || []),
        answered: true,
        correct: a.correct == null ? null : !!a.correct,
        attempts: a.attempts || 1,
        hintsShown: 0,
      });
    }
    this._render();
  }

  /**
   * Progress across the package: { total, answered, correct, attempts, points }.
   * `points` is retry-aware (a first-try correct = 1; correct after N tries =
   * 1/N), matching the server-side session score.
   */
  getProgress() {
    let answered = 0, correct = 0, attempts = 0, points = 0;
    for (const st of this.state.values()) {
      if (st.answered) {
        answered++;
        attempts += st.attempts || 1;
        if (st.correct) { correct++; points += 1 / (st.attempts || 1); }
      }
    }
    return { total: this.pkg.questionCount, answered, correct, attempts, points: Math.round(points * 100) / 100 };
  }

  // ---- adaptive-trio support (called by client tools) -------------------

  /**
   * Grade a stated answer WITHOUT UI interaction (for the `submit_answer` tool).
   * `refs` may be an option id, a letter (A/B/…), a 1-based number, option text,
   * or an array of those for multi-select. Returns the graded result or {error}.
   */
  submitAnswer(refs) {
    const q = this.question;
    const ids = this._resolveOptionRefs(q, refs);
    if (!ids.length) return { error: `Could not match "${Array.isArray(refs) ? refs.join(', ') : refs}" to an option of this question.` };
    const st = this._stateFor(q);
    st.selected = new Set(ids);
    st.answered = false;             // allow (re)grading via the deterministic grader
    this._grade();                   // marks answered, fires onAnswered (persist + mastery), re-renders
    return {
      question: { number: this.index + 1, total: this.pkg.questionCount, stem: q.stem },
      selected: ids, correct: st.correct, attempts: st.attempts,
      correctIds: (q.options || []).filter((o) => o.correct).map((o) => o.id),
    };
  }

  _resolveOptionRefs(q, refs) {
    const list = Array.isArray(refs) ? refs : [refs];
    const opts = q.options || [];
    const out = new Set();
    for (const raw of list) {
      if (raw == null) continue;
      const s = String(raw).trim();
      const sl = s.toLowerCase();
      let opt = opts.find((o) => String(o.id).toLowerCase() === sl);                       // by id
      if (!opt && /^[a-z]$/i.test(s)) opt = opts[s.toUpperCase().charCodeAt(0) - 65];       // letter
      if (!opt && /^\d+$/.test(s)) opt = opts[parseInt(s, 10) - 1];                         // 1-based number
      if (!opt) opt = opts.find((o) => (o.text || '').toLowerCase() === sl);                // exact text
      if (!opt && sl.length >= 3) opt = opts.find((o) => (o.text || '').toLowerCase().includes(sl)); // contains
      if (opt) out.add(opt.id);
    }
    return [...out];
  }

  /** Grounding for the current question (for the `get_grounding` tool). */
  groundingForCurrent() {
    const q = this.question;
    const concepts = this.pkg.conceptsFor(q).map((c) => ({
      id: c.id, title: c.title,
      passages: (Array.isArray(c.grounding) ? c.grounding : []).map((g) => ({
        text: g.text || '', citation: g.citation || g.locator || '',
      })),
    }));
    return { question: { number: this.index + 1, stem: q.stem }, concepts, citations: this.pkg.citationsFor(q) };
  }

  /**
   * Index of the "next best" question (for `next_best_question`): an UNANSWERED
   * question on the weakest in-scope concept, at a difficulty just above that
   * concept's ability. `mastery` = server list [{concept_id, ability}]. Falls
   * back to the first unanswered; null when everything is answered.
   */
  pickNextBest(mastery = []) {
    const ability = {};
    for (const m of mastery) ability[m.concept_id] = m.ability;
    const isAnswered = (q) => { const st = this.state.get(q.id); return st && st.answered; };
    const pool = this.pkg.questions.map((q, i) => ({ q, i })).filter(({ q }) => !isAnswered(q));
    if (!pool.length) return null;
    const score = ({ q }) => {
      const cids = q.concept_ids || [];
      const ab = cids.length ? Math.min(...cids.map((c) => ability[c] ?? 0)) : 0;   // weakest concept
      const targetDiff = 1 + Math.round(ab * 4);                                    // 0..1 ability → 1..5
      return ab * 10 + Math.abs((q.difficulty || 1) - targetDiff);                  // weakest first, then closest difficulty
    };
    pool.sort((a, b) => score(a) - score(b));
    return pool[0].i;
  }

  // ---- rendering --------------------------------------------------------

  _render() {
    const q = this.question;
    const st = this._stateFor(q);
    this.container.innerHTML = '';

    this.container.appendChild(this._header(q));
    this.container.appendChild(this._stem(q));

    const form = el('form', 'tq-options');
    form.addEventListener('submit', (e) => e.preventDefault());
    this._buildControls(form, q, st);
    this.container.appendChild(form);
    this._form = form;

    this.container.appendChild(this._hints(q, st));
    this.container.appendChild(this._actions(q, st));

    this._feedback = el('div', 'tq-feedback');
    this.container.appendChild(this._feedback);
    if (st.answered) this._renderFeedback(q, st);

    this.container.appendChild(this._footer(q));

    // Publish the current question + state so the assistant stays aware.
    if (this.cb.onQuestionChange) {
      this.cb.onQuestionChange({
        question: q, index: this.index, total: this.pkg.questionCount,
        answered: st.answered, correct: st.correct, selectedIds: [...st.selected],
      });
    }
  }

  _header(q) {
    const head = el('div', 'tq-head');
    const counter = el('span', 'tq-counter', `Question ${this.index + 1} of ${this.pkg.questionCount}`);
    const meta = el('span', 'tq-meta');
    const concepts = this.pkg.conceptsFor(q).map((c) => c.title);
    meta.append(
      chip(`difficulty ${q.difficulty ?? '?'}/5`, 'tq-chip-diff'),
      chip(q.bloom || 'n/a', 'tq-chip-bloom'),
      chip(typeLabel(q), 'tq-chip-type'),
    );
    if (concepts.length) meta.append(chip(concepts[0], 'tq-chip-concept'));
    head.append(counter, meta);
    return head;
  }

  _stem(q) {
    return el('h2', 'tq-stem', q.stem || '(no question text)');
  }

  _buildControls(form, q, st) {
    const multi = q.type === 'mcq_multi';
    const useDropdown = q.render === 'dropdown' && !multi;
    const groupName = nextUid();

    if (useDropdown) {
      const select = el('select', 'tq-select');
      select.disabled = st.answered;
      const placeholder = el('option', null, '— choose an answer —');
      placeholder.value = '';
      placeholder.disabled = true;
      placeholder.selected = st.selected.size === 0;
      select.appendChild(placeholder);
      for (const opt of q.options || []) {
        const o = el('option', null, opt.text);
        o.value = opt.id;
        if (st.selected.has(opt.id)) o.selected = true;
        select.appendChild(o);
      }
      select.addEventListener('change', () => {
        if (st.answered) return;
        st.selected = new Set(select.value ? [select.value] : []);
        this._syncSubmitEnabled();
      });
      form.appendChild(select);
      return;
    }

    for (const opt of q.options || []) {
      const id = nextUid();
      const row = el('label', 'tq-option');
      row.htmlFor = id;
      const input = document.createElement('input');
      input.type = multi ? 'checkbox' : 'radio';
      input.name = groupName;
      input.id = id;
      input.value = opt.id;
      input.disabled = st.answered;
      input.checked = st.selected.has(opt.id);
      input.addEventListener('change', () => {
        if (st.answered) return;
        if (multi) {
          if (input.checked) st.selected.add(opt.id); else st.selected.delete(opt.id);
        } else {
          st.selected = new Set([opt.id]);
        }
        this._syncSubmitEnabled();
      });
      row.append(input, el('span', 'tq-option-text', opt.text));
      // After grading, colour the option and append its rationale.
      if (st.answered) this._decorateOption(row, opt);
      form.appendChild(row);
    }
  }

  _decorateOption(row, opt) {
    const st = this._stateFor(this.question);
    const chosen = st.selected.has(opt.id);
    if (opt.correct) row.classList.add('is-correct');
    else if (chosen) row.classList.add('is-wrong');
    if (chosen) row.classList.add('is-chosen');
    if (opt.rationale) {
      row.appendChild(el('span', 'tq-rationale', opt.rationale));
    }
  }

  _hints(q, st) {
    const wrap = el('div', 'tq-hints');
    const hints = q.hints || [];
    if (!hints.length) return wrap;
    const shown = el('div', 'tq-hints-shown');
    for (let i = 0; i < st.hintsShown; i++) {
      shown.appendChild(el('div', 'tq-hint', `💡 ${hints[i]}`));
    }
    wrap.appendChild(shown);
    if (st.hintsShown < hints.length && !st.answered) {
      const btn = el('button', 'tq-link', st.hintsShown ? 'Another hint' : 'Need a hint?');
      btn.type = 'button';
      btn.addEventListener('click', () => { st.hintsShown++; this._render(); });
      wrap.appendChild(btn);
    }
    return wrap;
  }

  _actions(q, st) {
    const bar = el('div', 'tq-actions');

    const submit = el('button', 'tq-btn tq-btn-primary', st.answered ? 'Answered' : 'Submit answer');
    submit.type = 'button';
    submit.disabled = st.answered || st.selected.size === 0;
    submit.addEventListener('click', () => this._grade());
    this._submitBtn = submit;

    const ask = el('button', 'tq-btn tq-btn-ghost', '🎓 Ask the tutor');
    ask.type = 'button';
    ask.addEventListener('click', () => {
      if (this.cb.onAskTutor) {
        this.cb.onAskTutor(q, this.pkg, { selected: [...st.selected], answered: st.answered, correct: st.correct });
      }
    });

    bar.append(submit);
    // Once answered, allow another attempt — re-enables the controls. The next
    // submit counts as a fresh attempt (tracked for scoring); selections stay so
    // the student can adjust rather than start over.
    if (st.answered) {
      const retry = el('button', 'tq-btn tq-btn-ghost', '↻ Try again');
      retry.type = 'button';
      retry.addEventListener('click', () => { st.answered = false; st.correct = null; this._render(); });
      bar.append(retry);
    }
    bar.append(ask);
    return bar;
  }

  _footer(q) {
    const foot = el('div', 'tq-foot');
    const prev = el('button', 'tq-btn tq-btn-nav', '← Previous');
    prev.type = 'button';
    prev.disabled = this.index === 0;
    prev.addEventListener('click', () => this.go(-1));

    const next = el('button', 'tq-btn tq-btn-nav', 'Next →');
    next.type = 'button';
    next.disabled = this.index >= this.pkg.questionCount - 1;
    next.addEventListener('click', () => this.go(1));

    // Grounding toggle (between the nav buttons) — replaces the old "Source:"
    // text; the source is shown in the status bar and at the foot of the
    // Grounding tab itself.
    const on = this.cb.isGroundingOn ? this.cb.isGroundingOn() : false;
    const gtoggle = el('button', 'tq-toggle' + (on ? ' is-on' : ''), '📄 Grounding');
    gtoggle.type = 'button';
    gtoggle.setAttribute('aria-pressed', String(on));
    gtoggle.title = 'Show the source this question was written from';
    gtoggle.addEventListener('click', () => { if (this.cb.onToggleGrounding) this.cb.onToggleGrounding(); });
    this._groundingBtn = gtoggle;

    foot.append(prev, gtoggle, next);
    return foot;
  }

  /** Reflect external Grounding-tab state (toggled elsewhere) on the footer button. */
  setGroundingState(on) {
    if (this._groundingBtn) {
      this._groundingBtn.classList.toggle('is-on', on);
      this._groundingBtn.setAttribute('aria-pressed', String(on));
    }
  }

  _syncSubmitEnabled() {
    if (this._submitBtn) this._submitBtn.disabled = this._stateFor(this.question).selected.size === 0;
  }

  // ---- grading (deterministic) -----------------------------------------

  _grade() {
    const q = this.question;
    const st = this._stateFor(q);
    if (st.answered || st.selected.size === 0) return;

    const correctIds = new Set((q.options || []).filter((o) => o.correct).map((o) => o.id));
    const chosen = st.selected;
    // Correct iff the chosen set equals the correct set (handles single & multi).
    const correct = correctIds.size === chosen.size && [...correctIds].every((id) => chosen.has(id));

    st.attempts = (st.attempts || 0) + 1;
    st.answered = true;
    st.correct = correct;
    this._render();

    if (this.cb.onAnswered) {
      this.cb.onAnswered(q, { correct, selected: [...chosen], correctIds: [...correctIds], attempts: st.attempts });
    }
  }

  _renderFeedback(q, st) {
    this._feedback.innerHTML = '';
    const label = (st.correct ? '✓ Correct' : '✗ Not quite')
      + (st.attempts > 1 ? `  ·  attempt ${st.attempts}` : '');
    const banner = el('div', `tq-banner ${st.correct ? 'is-correct' : 'is-wrong'}`, label);
    this._feedback.appendChild(banner);
    if (q.explanation) {
      this._feedback.appendChild(el('p', 'tq-explanation', q.explanation));
    }
  }
}

// ---- small DOM helpers --------------------------------------------------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function chip(text, cls) {
  return el('span', `tq-chip ${cls || ''}`.trim(), text);
}

function typeLabel(q) {
  return { mcq_single: 'single choice', mcq_multi: 'multiple choice', true_false: 'true / false' }[q.type] || q.type || 'question';
}
