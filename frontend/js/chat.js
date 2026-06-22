// chat.js — the Tutor chat panel, a thin client of agent_server via the
// vendored agent-server-client SDK.
//
// Uses the REST streaming client (AgentRestClient) so we get per-request
// thinking on/off control (the Socket.IO path follows the agent preset). Chat
// history is kept client-side and the full messages[] array is sent each turn,
// so the stateless `tutor` agent still has conversational context.

import { AgentRestClient } from '../vendor/agent-server-client/src/index.js';
import { renderMarkdown } from './markdown.js';

export class ChatPanel {
  /**
   * @param {HTMLElement} root  the chat panel container.
   * @param {object} opts
   * @param {string} opts.baseUrl  agent_server origin (e.g. http://localhost:7701).
   * @param {string} opts.agent    agent name to talk to (e.g. "tutor").
   */
  constructor(root, { baseUrl = '', agent = 'tutor' } = {}) {
    this.root = root;
    this.agent = agent;
    this.rest = new AgentRestClient({ baseUrl });
    this.history = [];          // [{ role, content }]
    this.stream = null;         // active stream controller
    this.streaming = false;
    this._build();
    this._greet();
  }

  // ---- DOM ---------------------------------------------------------------

  _build() {
    this.root.innerHTML = '';

    const header = el('div', 'chat-header');
    header.append(
      el('div', 'chat-title', '🎓 Tutor'),
      this._toggles(),
    );

    this.messagesEl = el('div', 'chat-messages');

    const composer = el('div', 'chat-composer');
    this.input = document.createElement('textarea');
    this.input.className = 'chat-input';
    this.input.rows = 1;
    this.input.placeholder = 'Ask the tutor anything about this material…';
    this.input.addEventListener('input', () => this._autosize());
    this.input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.send(); }
    });

    this.sendBtn = el('button', 'chat-send', 'Send');
    this.sendBtn.addEventListener('click', () => (this.streaming ? this.stop() : this.send()));

    composer.append(this.input, this.sendBtn);
    this.root.append(header, this.messagesEl, composer);
  }

  _toggles() {
    const wrap = el('div', 'chat-toggles');
    this.thinkGen = checkbox('Thinking', true);
    this.thinkShow = checkbox('Show reasoning', false);
    this.thinkShow.input.addEventListener('change', () => {
      this.root.querySelectorAll('.chat-think').forEach((n) => n.classList.toggle('hidden', !this.thinkShow.input.checked));
    });
    wrap.append(this.thinkGen.label, this.thinkShow.label);
    return wrap;
  }

  _greet() {
    this._addBubble('assistant',
      "Hi! I'm your tutor. Ask me to explain any concept, or hit **🎓 Ask the tutor** on a question and I'll help you reason it through. I won't give away answers you haven't tried yet.");
  }

  // ---- public API --------------------------------------------------------

  /** Prefill the composer (e.g. from "Ask the tutor" on a question) and focus it. */
  prefill(text) {
    this.input.value = text;
    this._autosize();
    this.input.focus();
    this.input.setSelectionRange(text.length, text.length);
  }

  send() {
    const text = this.input.value.trim();
    if (!text || this.streaming) return;
    this.input.value = '';
    this._autosize();

    this._addBubble('user', text, { markdown: false });
    this.history.push({ role: 'user', content: text });

    const bubble = this._addBubble('assistant', '');
    const think = el('div', 'chat-think' + (this.thinkShow.input.checked ? '' : ' hidden'));
    bubble.body.before(think);

    let answer = '';
    this._setStreaming(true);
    this.stream = this.rest.chatStream(
      this.agent,
      this.history,
      { thinking: this.thinkGen.input.checked },
      {
        onThinking: (t) => { think.textContent += t; this._scroll(); },
        onAnswer: (t) => { answer += t; bubble.body.innerHTML = renderMarkdown(answer); this._scroll(); },
        onError: (e) => { bubble.body.innerHTML = renderMarkdown(`⚠️ *Error talking to the tutor:* ${e.message}`); this._setStreaming(false); },
        onDone: () => {
          if (!answer) bubble.body.textContent = '(no response)';
          this.history.push({ role: 'assistant', content: answer });
          if (!think.textContent.trim()) think.remove();
          this._setStreaming(false);
        },
      },
    );
  }

  stop() {
    if (this.stream) this.stream.abort();
    this._setStreaming(false);
  }

  // ---- internals ---------------------------------------------------------

  _addBubble(role, text, { markdown = true } = {}) {
    const row = el('div', `chat-row chat-${role}`);
    const body = el('div', 'chat-bubble');
    if (text) {
      if (markdown && role === 'assistant') body.innerHTML = renderMarkdown(text);
      else body.textContent = text;
    }
    row.appendChild(body);
    this.messagesEl.appendChild(row);
    this._scroll();
    return { row, body };
  }

  _setStreaming(on) {
    this.streaming = on;
    this.sendBtn.textContent = on ? 'Stop' : 'Send';
    this.sendBtn.classList.toggle('is-stop', on);
  }

  _autosize() {
    this.input.style.height = 'auto';
    this.input.style.height = Math.min(this.input.scrollHeight, 160) + 'px';
  }

  _scroll() { this.messagesEl.scrollTop = this.messagesEl.scrollHeight; }
}

// ---- helpers ------------------------------------------------------------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function checkbox(labelText, checked) {
  const label = el('label', 'chat-toggle');
  const input = document.createElement('input');
  input.type = 'checkbox';
  input.checked = checked;
  label.append(input, document.createTextNode(' ' + labelText));
  return { label, input };
}
