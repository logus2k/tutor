// recorder-worklet.js — AudioWorklet processor for STT capture.
// Pulls raw mono Float32 frames off the audio render thread and posts them to
// the main thread, where they are resampled to 16 kHz PCM16 and streamed to
// stt_server. Vendored from the deployed cv-chat / noted recorder worklet
// (replaces the SDK's deprecated ScriptProcessorNode path).
class RecorderWorklet extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (input && input[0] && input[0].length) {
      this.port.postMessage(input[0].slice());
    }
    return true;
  }
}
registerProcessor('recorder-worklet', RecorderWorklet);
