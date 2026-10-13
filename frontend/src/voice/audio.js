/**
 * Microphone capture with voice-activity detection (VAD) and 16 kHz WAV encoding.
 *
 * - AudioWorklet streams mic samples; RMS energy against an adaptive noise floor detects speech.
 * - A short pre-roll buffer keeps the start of each utterance.
 * - An utterance ends after `silenceMs` of silence (hands-free) or when stop() is called (push-to-talk).
 * - While the agent is speaking, a higher threshold detects barge-in (interruptions).
 */

const WORKLET = `
class Tap extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(2048); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) {
      for (let i = 0; i < ch.length; i++) {
        this.buf[this.n++] = ch[i];
        if (this.n === this.buf.length) { this.port.postMessage(this.buf.slice(0)); this.n = 0; }
      }
    }
    return true;
  }
}
registerProcessor('voiceops-tap', Tap);
`;

const TARGET_RATE = 16000;

export function encodeWav(samples, sampleRate) {
  const ratio = sampleRate / TARGET_RATE;
  const length = Math.floor(samples.length / ratio);
  const pcm = new Int16Array(length);
  for (let i = 0; i < length; i++) {
    // average the source window (simple low-pass) while decimating
    const start = Math.floor(i * ratio);
    const end = Math.min(samples.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += samples[j];
    const v = Math.max(-1, Math.min(1, sum / Math.max(1, end - start)));
    pcm[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  const buffer = new ArrayBuffer(44 + pcm.length * 2);
  const view = new DataView(buffer);
  const write = (offset, text) => [...text].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  write(0, 'RIFF'); view.setUint32(4, 36 + pcm.length * 2, true); write(8, 'WAVE');
  write(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, TARGET_RATE, true); view.setUint32(28, TARGET_RATE * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  write(36, 'data'); view.setUint32(40, pcm.length * 2, true);
  new Int16Array(buffer, 44).set(pcm);
  return new Blob([buffer], { type: 'audio/wav' });
}

export function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

export class VoiceRecorder {
  constructor({ onLevel, onSpeechStart, onUtterance, onBargeIn, silenceMs = 1200, maxMs = 15000, sensitivity = 1 } = {}) {
    Object.assign(this, { onLevel, onSpeechStart, onUtterance, onBargeIn, silenceMs, maxMs, sensitivity });
    this.mode = 'off';            // off | listening | monitoring (agent speaking) | manual (push-to-talk)
    this.noise = 0.004;
    this.chunks = [];
    this.preroll = [];
    this.speaking = false;
    this.loudChunks = 0;
    this.silentMs = 0;
    this.speechMs = 0;
  }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    this.ctx = new AudioContext();
    const url = URL.createObjectURL(new Blob([WORKLET], { type: 'application/javascript' }));
    await this.ctx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    this.source = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, 'voiceops-tap');
    this.node.port.onmessage = (e) => this._chunk(e.data);
    this.source.connect(this.node);
    this.chunkMs = (2048 / this.ctx.sampleRate) * 1000;
  }

  setMode(mode) {
    if (mode === this.mode) return;
    this.mode = mode;
    this._reset();
    if (mode === 'manual') { this.speaking = true; this.onSpeechStart?.(); }
  }

  /** Push-to-talk: finish the current manual recording. */
  finishManual() {
    if (this.mode === 'manual' && this.chunks.length) this._emitUtterance();
    this.mode = 'off';
    this._reset();
  }

  _reset() {
    this.chunks = []; this.speaking = false; this.loudChunks = 0; this.silentMs = 0; this.speechMs = 0;
  }

  _chunk(samples) {
    let sum = 0;
    for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
    const rms = Math.sqrt(sum / samples.length);
    this.onLevel?.(Math.min(1, rms * 12));

    if (this.mode === 'manual') { this.chunks.push(samples); return; }
    if (this.mode === 'off') return;

    const threshold = Math.max(0.012, this.noise * 3.2) / this.sensitivity;
    const loud = rms > (this.mode === 'monitoring' ? threshold * 2.4 : threshold);

    if (this.mode === 'monitoring') {
      // agent is talking: only a sustained loud voice counts as barge-in
      this.loudChunks = loud ? this.loudChunks + 1 : 0;
      if (this.loudChunks * this.chunkMs >= 300) {
        this.onBargeIn?.();
        this.mode = 'listening';
        this.speaking = true;
        this.chunks = [...this.preroll, samples];
        this.onSpeechStart?.();
      }
      this._keepPreroll(samples);
      return;
    }

    if (!this.speaking) {
      if (!loud) this.noise = this.noise * 0.95 + rms * 0.05;
      this.loudChunks = loud ? this.loudChunks + 1 : 0;
      this._keepPreroll(samples);
      if (this.loudChunks >= 2) {
        this.speaking = true;
        this.chunks = [...this.preroll];
        this.silentMs = 0;
        this.speechMs = 0;
        this.onSpeechStart?.();
      }
      return;
    }

    this.chunks.push(samples);
    this.speechMs += this.chunkMs;
    this.silentMs = loud ? 0 : this.silentMs + this.chunkMs;
    if (this.silentMs >= this.silenceMs || this.speechMs >= this.maxMs) this._emitUtterance();
  }

  _keepPreroll(samples) {
    this.preroll.push(samples);
    const keep = Math.ceil(350 / (this.chunkMs || 43));
    if (this.preroll.length > keep) this.preroll.shift();
  }

  _emitUtterance() {
    const total = this.chunks.reduce((n, c) => n + c.length, 0);
    const durationMs = (total / this.ctx.sampleRate) * 1000;
    const merged = new Float32Array(total);
    let offset = 0;
    for (const c of this.chunks) { merged.set(c, offset); offset += c.length; }
    this._reset();
    this.preroll = [];
    if (durationMs >= 400) this.onUtterance?.(encodeWav(merged, this.ctx.sampleRate), durationMs);
  }

  async stop() {
    this.mode = 'off';
    if (this.node?.port) this.node.port.onmessage = null;
    this.source?.disconnect();
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    if (this.ctx && this.ctx.state !== 'closed') await this.ctx.close();
  }
}
