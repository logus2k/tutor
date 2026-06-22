// voice-stt.js — microphone → stt_server → transcript, mirroring the deployed
// cv-chat widget's working protocol (the SDK's stt.js used the wrong wire
// payload — {client_id, audio} instead of {clientId, audioData} — and a
// deprecated ScriptProcessorNode, so it never produced transcripts).
//
// Wire contract (confirmed against the live widget + voice integration guide):
//   connect  io(origin, { path: '/stt/socket.io', ... })
//   send     socket.emit('audio_data', { clientId, audioData: <Int16 ArrayBuffer> })
//   receive  'transcription' (final) / 'transcription_partial' (interim) → { text }
//
// Audio: AudioWorklet captures mono Float32 → AudioResampler to 16 kHz PCM16 →
// packetised to ~100 ms → emitted while listening.

const STT_PATH = '/stt/socket.io';
const PACKET_SECONDS = 0.1;        // ~100 ms packets
const TARGET_RATE = 16000;         // stt_server expects 16 kHz

/** mono Float32 (e.g. 48 kHz) → Int16 PCM (16 kHz), carrying the remainder. */
class AudioResampler {
  constructor(inRate, outRate) {
    this._ratio = inRate / outRate;
    this._carry = new Float32Array(0);
  }
  pushFloat32(chunk) {
    const input = new Float32Array(this._carry.length + chunk.length);
    input.set(this._carry, 0);
    input.set(chunk, this._carry.length);
    const outLen = Math.floor(input.length / this._ratio);
    if (outLen === 0) { this._carry = input; return null; }
    const out = new Int16Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const idx = i * this._ratio;
      const i0 = Math.floor(idx);
      const i1 = Math.min(i0 + 1, input.length - 1);
      const frac = idx - i0;
      let s = input[i0] * (1 - frac) + input[i1] * frac;
      s = Math.max(-1, Math.min(1, s));
      out[i] = (s < 0 ? s * 0x8000 : s * 0x7fff) | 0;
    }
    this._carry = input.subarray(Math.floor(outLen * this._ratio));
    return out;
  }
}

export class SttMic {
  /**
   * @param {object} opts
   * @param {function} opts.io        socket.io-client factory (required).
   * @param {string}   opts.clientId  stable client id.
   * @param {string}   [opts.url]     origin (default same-origin '').
   * @param {(t:string)=>void} [opts.onFinal]    final transcript handler.
   * @param {(t:string)=>void} [opts.onPartial]  interim transcript handler.
   * @param {()=>boolean}      [opts.shouldSend] gate (e.g. false while our TTS speaks).
   */
  constructor({ io, clientId, url = '', onFinal, onPartial, shouldSend } = {}) {
    if (typeof io !== 'function') throw new Error('SttMic needs an `io` factory');
    this._io = io;
    this.clientId = clientId;
    this.url = url;
    this.onFinal = onFinal;
    this.onPartial = onPartial;
    this.shouldSend = shouldSend || (() => true);
    this.listening = false;
    this.socket = null;
    this._stream = null; this._ctx = null; this._src = null; this._node = null; this._resampler = null;
  }

  /** Connect + open mic. Resolves true if listening, false if unavailable. */
  async start() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return false;
    try {
      // 1. Socket first — if STT is down, bail before prompting for the mic.
      const ok = await new Promise((resolve) => {
        this.socket = this._io(this.url, {
          path: STT_PATH, transports: ['websocket', 'polling'], forceNew: true,
        });
        this.socket.once('connect', () => resolve(true));
        this.socket.once('connect_error', () => resolve(false));
      });
      if (!ok) { this._cleanup(); return false; }
      this.socket.on('transcription', (d) => { const t = textOf(d); if (t && this.onFinal) this.onFinal(t); });
      this.socket.on('transcription_partial', (d) => { const t = textOf(d); if (t && this.onPartial) this.onPartial(t); });

      // 2. Microphone + audio graph.
      this._stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      this._ctx = new (window.AudioContext || window.webkitAudioContext)();
      await this._ctx.audioWorklet.addModule(new URL('./recorder-worklet.js', import.meta.url));
      this._src = this._ctx.createMediaStreamSource(this._stream);
      this._node = new AudioWorkletNode(this._ctx, 'recorder-worklet');
      this._resampler = new AudioResampler(this._ctx.sampleRate, TARGET_RATE);

      const perPacket = Math.round(this._ctx.sampleRate * PACKET_SECONDS);
      let pending = [];
      let pendingLen = 0;
      this._node.port.onmessage = (ev) => {
        const chunk = ev.data;
        if (!chunk || !chunk.length) return;
        pending.push(chunk);
        pendingLen += chunk.length;
        if (pendingLen < perPacket) return;
        const merged = new Float32Array(pendingLen);
        let o = 0;
        for (const part of pending) { merged.set(part, o); o += part.length; }
        pending = []; pendingLen = 0;

        const pcm = this._resampler.pushFloat32(merged);
        if (!pcm || !pcm.length) return;
        if (!this.listening || !this.shouldSend()) return;     // drop while gated
        if (!(this.socket && this.socket.connected)) return;
        this.socket.emit('audio_data', { clientId: this.clientId, audioData: pcm.buffer });
      };

      this._src.connect(this._node);
      this._node.connect(this._ctx.destination);   // worklet output is silent
      this.listening = true;
      return true;
    } catch (e) {
      this._cleanup();
      return false;
    }
  }

  setListening(on) { this.listening = !!on; }

  stop() { this._cleanup(); }

  _cleanup() {
    this.listening = false;
    try { if (this._node) this._node.disconnect(); } catch { /* ignore */ }
    try { if (this._src) this._src.disconnect(); } catch { /* ignore */ }
    try { if (this._ctx) this._ctx.close(); } catch { /* ignore */ }
    try { if (this._stream) this._stream.getTracks().forEach((t) => t.stop()); } catch { /* ignore */ }
    try { if (this.socket) this.socket.disconnect(); } catch { /* ignore */ }
    this.socket = null; this._node = null; this._src = null; this._ctx = null; this._stream = null; this._resampler = null;
  }
}

function textOf(d) {
  return ((d && (d.text || d.transcript)) || (typeof d === 'string' ? d : '') || '').trim();
}
