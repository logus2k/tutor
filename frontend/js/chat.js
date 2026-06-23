// chat.js — the Tutor chat panel, a thin client of agent_server via the
// vendored agent-server-client SDK.
//
// Uses the REST streaming client (AgentRestClient) so we get per-request
// thinking on/off control (the Socket.IO path follows the agent preset). Chat
// history is kept client-side and the full messages[] array is sent each turn,
// so the stateless `tutor` agent still has conversational context.
//
// TTS and STT are OPTIONAL and FAIL SOFT (SDK design): if a service or the mic
// is unavailable the toggle reverts and chat keeps working. The tutor agent
// emits no <voice> channel, so we speak the answer text itself — flushed
// sentence-by-sentence as it streams so speech starts before the reply ends.

import { AgentRestClient, TtsClient, newClientId, StreamParser, thinkingKwargs }
  from '../vendor/agent-server-client/src/index.js';
import { SttMic } from './voice-stt.js';
import { renderMarkdown } from './markdown.js';
import { describeContext } from './context.js';

export class ChatPanel {
  /**
   * @param {HTMLElement} root  the chat panel container.
   * @param {object} opts
   * @param {string} opts.baseUrl  agent_server origin (e.g. "/llm").
   * @param {string} opts.agent    agent name to talk to (e.g. "tutor").
   * @param {function} [opts.io]   socket.io-client factory (for TTS/STT). If
   *                               absent, voice/mic toggles fail soft.
   * @param {boolean} [opts.thinking]        generate reasoning (Settings-driven).
   * @param {boolean} [opts.showReasoning]   display reasoning (Settings-driven).
   */
  constructor(root, { baseUrl = '', agent = 'tutor', io = null, thinking = true, showReasoning = false,
                      onStatus = null, context = null, mcpBase = '/mcp', mcpApp = 'tutor', clientTools = {} } = {}) {
    this.root = root;
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.agent = agent;
    this.io = io;
    this.onStatus = onStatus;   // (text, state) => void ; state: 'ready'|'busy'|'error'
    this.context = context;     // observable TutorContext (live package/question)
    this.thinking = thinking;          // generate <think> (per-request)
    this.showReasoning = showReasoning; // display reasoning panels
    this.clientId = newClientId();
    this.rest = new AgentRestClient({ baseUrl });
    this.history = [];          // [{ role, content }]
    this.stream = null;         // active stream controller
    this.streaming = false;
    this.tts = null;            // TtsClient when voice is enabled
    this.stt = null;            // SttClient when mic is enabled

    // Tools: MCP is the unified catalog. We advertise every tool's schema to the
    // model; client tools (execution=client) run in JS here, server tools run in
    // mcp-service via /invoke. Loaded fail-soft (no tools → plain chat).
    this.mcpBase = mcpBase.replace(/\/$/, '');
    this.mcpApp = mcpApp;
    this.clientTools = clientTools;     // { name: (args) => result }
    this.toolSpecs = [];                // OpenAI tools[] sent to the model
    this.serverToolNames = new Set();   // names executed via mcp-service
    this._toolsReady = this._loadTools();

    this._build();
    this._greet();
    if (this.context) {
      this._renderContext(this.context.snapshot);
      this.context.addEventListener('change', (e) => this._renderContext(e.detail));
    }
  }

  /** Load the tutor tool catalog from mcp-service (fail-soft). */
  async _loadTools() {
    try {
      const r = await fetch(`${this.mcpBase}/tools?app=${encodeURIComponent(this.mcpApp)}`, { headers: { Accept: 'application/json' } });
      if (!r.ok) return;
      const { tools } = await r.json();
      for (const t of tools || []) {
        if (t.enabled === false) continue;
        this.toolSpecs.push({
          type: 'function',
          function: { name: t.name, description: t.description || '', parameters: t.input_schema || { type: 'object', properties: {} } },
        });
        if ((t.config || {}).execution === 'server') this.serverToolNames.add(t.name);
      }
    } catch { /* mcp unavailable → chat works without tools */ }
  }

  // ---- settings (driven by the Settings view) ---------------------------
  setAgent(name) { if (name) this.agent = name; }
  setThinking(on) { this.thinking = !!on; }
  setShowReasoning(on) {
    this.showReasoning = !!on;
    this.root.querySelectorAll('.chat-think').forEach((n) => n.classList.toggle('hidden', !on));
  }

  // ---- DOM ---------------------------------------------------------------

  _build() {
    this.root.innerHTML = '';

    const header = el('div', 'chat-header');
    this.statusWrap = el('div', 'chat-status');
    this.statusText = el('span', 'chat-status-text', 'Ready');
    this.led = el('span', 'chat-led');
    this.led.title = 'Ready';
    this.statusWrap.append(this.statusText, this.led);   // "Ready" then the LED
    header.append(el('div', 'chat-title', '🎓 Tutor'), this.statusWrap);

    // Awareness strip — shows what the assistant currently "sees" (live context).
    this.contextEl = el('div', 'chat-context');

    this.messagesEl = el('div', 'chat-messages');

    // Composer with the audio controls below, beside the input.
    const composer = el('div', 'chat-composer');

    this.voiceBtn = iconToggle('🔊', 'Speak replies (TTS)');
    this.voiceBtn.addEventListener('click', () => this._toggleVoice());
    this.micBtn = iconToggle('🎤', 'Dictate (STT)');
    this.micBtn.addEventListener('click', () => this._toggleMic());
    const audio = el('div', 'chat-audio-btns');
    audio.append(this.micBtn, this.voiceBtn);

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

    composer.append(audio, this.input, this.sendBtn);
    this.root.append(header, this.contextEl, this.messagesEl, composer);
  }

  /** Reflect the live context (package / question) in the awareness strip. */
  _renderContext(s) {
    if (!this.contextEl) return;
    let text;
    if (!s || !s.pkg) text = '🧭 No package — browsing';
    else if (!s.question) text = `📦 ${s.pkg.title}`;
    else {
      const tag = s.qState && s.qState.answered ? (s.qState.correct ? '✓ answered' : '✗ answered') : 'unanswered';
      text = `📦 ${s.pkg.title} · Q${s.qIndex + 1}/${s.qTotal} · ${tag}`;
    }
    this.contextEl.textContent = text;
    this.contextEl.title = 'The assistant is aware of this context';
  }

  _greet() {
    this._addBubble('assistant',
      "Hi! I'm your tutor. Ask me to explain any concept, or hit **🎓 Ask the tutor** on a question and I'll help you reason it through. I won't give away answers you haven't tried yet.");
  }

  // ---- voice (TTS) -------------------------------------------------------

  async _toggleVoice() {
    if (this.tts) { this.tts.disconnect(); this.tts = null; this.voiceBtn.classList.remove('on'); return; }
    if (!this.io) { this._softFail(this.voiceBtn, 'Voice needs socket.io — unavailable.'); return; }
    this.voiceBtn.classList.add('pending');
    this.tts = new TtsClient({ io: this.io, clientId: this.clientId, path: '/tts/socket.io' });
    this.tts.onSpeakingChange = (on) => this.voiceBtn.classList.toggle('speaking', on);
    const ok = await this.tts.connect();
    this.voiceBtn.classList.remove('pending');
    if (!ok) { this.tts = null; this._softFail(this.voiceBtn, 'Voice service unavailable — chat still works.'); return; }
    this.voiceBtn.classList.add('on');
  }

  // ---- mic (STT) ---------------------------------------------------------

  async _toggleMic() {
    if (this.stt) { this.stt.stop(); this.stt = null; this.micBtn.classList.remove('on', 'speaking'); return; }
    if (!this.io) { this._softFail(this.micBtn, 'Mic needs socket.io — unavailable.'); return; }
    this.micBtn.classList.add('pending');

    // Dictation: show live partials in the composer as a preview, and on a final
    // transcript SEND it as a chat turn (it becomes a user bubble + goes to the
    // tutor) — mirroring the cv-chat widget. The mic stays on to keep listening.
    this.stt = new SttMic({
      io: this.io,
      clientId: this.clientId,
      shouldSend: () => !(this.tts && this.voiceBtn.classList.contains('speaking')), // don't transcribe our own TTS
      onPartial: (text) => { this.input.value = text; this._autosize(); this.micBtn.classList.add('speaking'); },
      onFinal: (text) => {
        this.micBtn.classList.remove('speaking');
        const t = (text || '').trim();
        if (!t) return;
        this.input.value = t;
        this._autosize();
        if (!this.streaming) this.send();   // hands-free: dictate → send
      },
    });
    const ok = await this.stt.start();
    this.micBtn.classList.remove('pending');
    if (!ok) { this.stt = null; this._softFail(this.micBtn, 'Mic/STT unavailable — check permission; chat still works.'); return; }
    this.micBtn.classList.add('on');
  }

  _softFail(btn, msg) { btn.classList.remove('on'); this._toast(msg); }

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
    if (this.tts) this.tts.stop();            // barge-in: stop any prior speech
    this.input.value = '';
    this._autosize();

    this._addBubble('user', text, { markdown: false });
    this.history.push({ role: 'user', content: text });

    const bubble = this._addBubble('assistant', '');
    const think = el('div', 'chat-think' + (this.showReasoning ? '' : ' hidden'));
    bubble.body.before(think);

    this._setStreaming(true);
    this._status('Tutor is responding…', 'busy');

    (async () => {
      try {
        await this._toolsReady;
        // Prepend a fresh, answer-safe system message with the live context, so
        // the stateless agent knows the current package/question each turn.
        const messages = [];
        if (this.context) {
          const ctx = describeContext(this.context.snapshot);
          if (ctx) messages.push({ role: 'system', content: ctx });
        }
        messages.push(...this.history);

        const answer = await this._run(messages, { bubble, think });
        if (!answer && !bubble.body.textContent) bubble.body.textContent = '(no response)';
        this.history.push({ role: 'assistant', content: answer || '' });
        if (!think.textContent.trim()) think.remove();
        this._setStreaming(false);
        this._status('Ready', 'ready');
      } catch (e) {
        if (e.name === 'AbortError') { this._setStreaming(false); this._status('Ready', 'ready'); return; }
        bubble.body.innerHTML = renderMarkdown(`⚠️ *Error talking to the tutor:* ${e.message}`);
        this._setStreaming(false);
        this._status('Error talking to the tutor', 'error');
      }
    })();
  }

  /**
   * Run one user turn, looping over tool calls until the model gives a final
   * answer. Each round streams; if it ends in tool_calls we execute them
   * (client-side JS or mcp-service) and feed results back, then continue.
   */
  async _run(messages, ui) {
    const MAX_ROUNDS = 6;
    let answer = '';
    this._spoken = 0;
    for (let round = 0; round < MAX_ROUNDS; round++) {
      const handlers = {
        onThinking: (t) => { ui.think.textContent += t; this._scroll(); },
        onAnswer: (t) => {
          answer += t;
          ui.bubble.body.innerHTML = renderMarkdown(answer);
          this._scroll();
          if (this.tts) this._speakFlush(answer, false);
        },
      };
      const { content, toolCalls } = await this._streamOnce(messages, handlers);

      if (!toolCalls.length) {                // final answer
        if (this.tts) this._speakFlush(answer, true);
        return answer;
      }
      // Record the assistant's tool-call turn, execute each, feed results back.
      messages.push({ role: 'assistant', content: content || '', tool_calls: toolCalls });
      for (const tc of toolCalls) {
        this._toolNote(tc.function.name, tc.function.arguments);
        let result;
        try { result = await this._dispatchTool(tc.function.name, tc.function.arguments); }
        catch (e) { result = { error: String(e && e.message || e) }; }
        messages.push({
          role: 'tool', tool_call_id: tc.id, name: tc.function.name,
          content: typeof result === 'string' ? result : JSON.stringify(result),
        });
      }
    }
    if (this.tts) this._speakFlush(answer, true);
    if (!answer) ui.bubble.body.textContent = '(stopped after several tool steps)';
    return answer;
  }

  /** One streaming completion: feeds answer/thinking channels and accumulates tool_calls. */
  async _streamOnce(messages, handlers) {
    const ctrl = new AbortController();
    this.stream = { abort: () => ctrl.abort() };

    const body = { model: this.agent, messages, stream: true };
    if (this.toolSpecs.length) { body.tools = this.toolSpecs; body.tool_choice = 'auto'; }
    const ctk = thinkingKwargs(this.thinking);
    if (Object.keys(ctk).length) body.chat_template_kwargs = ctk;

    const resp = await fetch(`${this.baseUrl}/v1/chat/completions`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: ctrl.signal,
    });
    if (!resp.ok || !resp.body) throw new Error(`agent_server ${resp.status}`);

    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    const parser = new StreamParser();
    let buf = '';
    let content = '';
    const toolCalls = [];
    const dispatch = (ev) => {
      if (ev.kind === 'answer') { content += ev.text; if (handlers.onAnswer) handlers.onAnswer(ev.text); }
      else if (ev.kind === 'thinking' && handlers.onThinking) handlers.onThinking(ev.text, ev.final);
    };

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        const t = line.trim();
        if (!t.startsWith('data:')) continue;
        const d = t.slice(5).trim();
        if (d === '[DONE]') { buf = ''; break; }
        let obj; try { obj = JSON.parse(d); } catch { continue; }
        const delta = (obj.choices && obj.choices[0] && obj.choices[0].delta) || {};
        let piece = delta.content;
        if (delta.reasoning_content && !piece) piece = `<think>${delta.reasoning_content}</think>`;
        if (piece) for (const ev of parser.feed(piece)) dispatch(ev);
        if (Array.isArray(delta.tool_calls)) {
          for (const tcd of delta.tool_calls) {
            const i = tcd.index ?? 0;
            if (!toolCalls[i]) toolCalls[i] = { id: tcd.id || '', type: 'function', function: { name: '', arguments: '' } };
            if (tcd.id) toolCalls[i].id = tcd.id;
            if (tcd.function) {
              if (tcd.function.name) toolCalls[i].function.name += tcd.function.name;
              if (tcd.function.arguments) toolCalls[i].function.arguments += tcd.function.arguments;
            }
          }
        }
      }
    }
    for (const ev of parser.flush()) dispatch(ev);
    return { content, toolCalls: toolCalls.filter(Boolean) };
  }

  /** Execute a tool: client-side JS handler, or forward to mcp-service /invoke. */
  async _dispatchTool(name, argsJson) {
    let args = {};
    try { args = argsJson ? JSON.parse(argsJson) : {}; } catch { /* bad args → {} */ }

    if (this.clientTools && this.clientTools[name]) {
      const out = await this.clientTools[name](args);
      return out == null ? { ok: true } : out;
    }
    if (this.serverToolNames.has(name)) {
      const r = await fetch(`${this.mcpBase}/tools/${encodeURIComponent(name)}/invoke?app=${encodeURIComponent(this.mcpApp)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ args }),
      });
      if (!r.ok) return { error: `tool ${name} failed (${r.status})` };
      const data = await r.json();
      return data.result ?? data;
    }
    return { error: `unknown tool: ${name}` };
  }

  _toolNote(name, argsJson) {
    const args = argsJson && argsJson !== '{}' ? ` ${argsJson}` : '';
    const row = el('div', 'chat-toolnote', `🛠 ${name}${args}`);
    this.messagesEl.appendChild(row);
    this._scroll();
  }

  stop() {
    if (this.stream) this.stream.abort();
    if (this.tts) this.tts.stop();
    this._setStreaming(false);
  }

  // ---- internals ---------------------------------------------------------

  /** Speak whole sentences as they complete; flush the remainder when final. */
  _speakFlush(fullAnswer, final) {
    const pending = fullAnswer.slice(this._spoken);
    if (final) {
      const rest = stripForSpeech(pending);
      if (rest) this.tts.speak(rest);
      this._spoken = fullAnswer.length;
      return;
    }
    // Speak up to the last sentence boundary present in the pending text.
    const m = pending.match(/^[\s\S]*[.!?。！？](?=\s)/);
    if (m) {
      const chunk = stripForSpeech(m[0]);
      if (chunk) this.tts.speak(chunk);
      this._spoken += m[0].length;
    }
  }

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

  _status(text, state = 'ready') {
    if (this.statusText) this.statusText.textContent = text;
    if (this.statusWrap) {
      this.statusWrap.classList.remove('busy', 'error');
      if (state === 'busy' || state === 'error') this.statusWrap.classList.add(state);
    }
    if (this.led) {
      this.led.classList.remove('busy', 'error');
      if (state === 'busy' || state === 'error') this.led.classList.add(state);
      this.led.title = text;
    }
    if (this.onStatus) this.onStatus(text, state);
  }

  _toast(msg) {
    const t = el('div', 'chat-toast', msg);
    this.root.appendChild(t);
    setTimeout(() => t.classList.add('show'), 10);
    setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 300); }, 3200);
  }
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

function iconToggle(glyph, title) {
  const b = el('button', 'chat-icon', glyph);
  b.type = 'button';
  b.title = title;
  b.setAttribute('aria-label', title);
  return b;
}

/** Strip Markdown punctuation so TTS doesn't read "asterisk asterisk" etc. */
function stripForSpeech(s) {
  return s
    .replace(/```[\s\S]*?```/g, ' ')          // code fences
    .replace(/`([^`]+)`/g, '$1')              // inline code
    .replace(/\*\*([^*]+)\*\*/g, '$1')        // bold
    .replace(/\*([^*]+)\*/g, '$1')            // italics
    .replace(/^#{1,6}\s+/gm, '')              // headings
    .replace(/^\s*[-*]\s+/gm, '')             // bullet markers
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')  // links → text
    .replace(/\s+/g, ' ')
    .trim();
}
