import { useCallback, useEffect, useRef, useState } from 'react';
import api from '../api/api';
import { VoiceRecorder, blobToBase64 } from './audio';
import { TOOL_LABELS } from '../lib/format';

export const STATE_LABELS = {
  idle: 'Ready to start',
  connecting: 'Connecting',
  ready: 'Mic paused',
  listening: 'Listening',
  user_speaking: 'Hearing you',
  transcribing: 'Transcribing',
  thinking: 'Understanding',
  searching: 'Searching knowledge',
  checking: 'Running action',
  generating: 'Thinking',
  speaking: 'Speaking',
  completed: 'Conversation completed',
  error: 'Something went wrong',
};

export const BUSY_STATES = new Set(['transcribing', 'thinking', 'searching', 'checking', 'generating']);

const PREFS_KEY = 'voiceops_voice_prefs';
export const DEFAULT_PREFS = { handsFree: true, bargeIn: true, sensitivity: 1, browserVoice: '' };

export function loadPrefs() {
  try { return { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') }; } catch { return DEFAULT_PREFS; }
}
export function savePrefs(prefs) {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch { /* ignore */ }
}

const SpeechRecognitionImpl = typeof window !== 'undefined' && (window.SpeechRecognition || window.webkitSpeechRecognition);
const emptySession = { conversationId: null, customer: null, activeOrder: null, intent: null, confidence: null,
  pending: null, escalated: false, sources: [], tools: [], timings: null, provider: null };

export default function useVoiceAgent() {
  const [state, setStateRaw] = useState('idle');
  const [activity, setActivity] = useState('');
  const [messages, setMessages] = useState([]);
  const [session, setSession] = useState(emptySession);
  const [events, setEvents] = useState([]);
  const [level, setLevel] = useState(0);
  const [connection, setConnection] = useState('closed');
  const [capabilities, setCapabilities] = useState(null);
  const [micEnabled, setMicEnabled] = useState(true);
  const [micError, setMicError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [summary, setSummary] = useState(null);
  const [prefs, setPrefs] = useState(loadPrefs);

  const r = useRef({ ws: null, recorder: null, audio: null, conv: null, ended: false, attempts: 0, speaking: false,
    awaitingAudio: false, state: 'idle', micEnabled: true, prefs, browserStt: false, recognition: null, endResolve: null });
  useEffect(() => { r.current.prefs = prefs; r.current.micEnabled = micEnabled; }, [prefs, micEnabled]);
  const selfRefs = useRef({});

  const setState = useCallback((s) => { r.current.state = s; setStateRaw(s); }, []);
  const log = useCallback((type, detail = '') => {
    setEvents((prev) => [...prev.slice(-59), { type, detail, at: new Date().toLocaleTimeString() }]);
  }, []);
  const flash = useCallback((text, kind = 'info') => {
    setNotice({ text, kind, id: Date.now() });
  }, []);

  // ── Listening control ──

  const startBrowserRecognition = useCallback(() => {
    if (!SpeechRecognitionImpl || r.current.recognition) return;
    const rec = new SpeechRecognitionImpl();
    rec.lang = 'en-US';
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    rec.onresult = (e) => {
      const text = e.results[0][0].transcript;
      r.current.recognition = null;
      if (text.trim()) selfRefs.current.sendText?.(text, 'browser-stt');
    };
    rec.onerror = () => { r.current.recognition = null; };
    rec.onend = () => {
      r.current.recognition = null;
      if (r.current.state === 'listening') setTimeout(() => selfRefs.current.startRecognition?.(), 250);
    };
    r.current.recognition = rec;
    rec.start();
  }, []);

  const goListening = useCallback(() => {
    const cur = r.current;
    if (cur.ended || !cur.conv) return;
    if (!cur.micEnabled || !cur.recorder) { setState('ready'); setActivity(''); return; }
    if (!cur.prefs.handsFree) { cur.recorder.setMode('off'); setState('ready'); setActivity('Press the mic to talk'); return; }
    if (cur.browserStt) { cur.recorder.setMode('off'); setState('listening'); startBrowserRecognition(); return; }
    cur.recorder.setMode('listening');
    setState('listening');
    setActivity('');
  }, [setState, startBrowserRecognition]);

  const stopAudio = useCallback(() => {
    const cur = r.current;
    if (cur.audio) { cur.audio.onended = null; cur.audio.pause(); cur.audio = null; }
    if (window.speechSynthesis?.speaking) window.speechSynthesis.cancel();
    cur.speaking = false;
  }, []);

  const afterSpeech = useCallback(() => {
    r.current.speaking = false;
    r.current.awaitingAudio = false;
    goListening();
  }, [goListening]);

  const beginSpeaking = useCallback(() => {
    const cur = r.current;
    cur.speaking = true;
    setState('speaking');
    setActivity('');
    if (cur.recorder && cur.micEnabled && cur.prefs.handsFree && cur.prefs.bargeIn && !cur.browserStt) {
      cur.recorder.setMode('monitoring');
    } else {
      cur.recorder?.setMode('off');
    }
  }, [setState]);

  const playAudio = useCallback((b64, mime) => {
    stopAudio();
    const audio = new Audio(`data:${mime || 'audio/mpeg'};base64,${b64}`);
    r.current.audio = audio;
    audio.onended = () => { if (r.current.audio === audio) { r.current.audio = null; afterSpeech(); } };
    audio.onerror = () => { r.current.audio = null; afterSpeech(); };
    beginSpeaking();
    audio.play().catch(() => { r.current.audio = null; flash('Audio playback was blocked by the browser.', 'warning'); afterSpeech(); });
  }, [afterSpeech, beginSpeaking, flash, stopAudio]);

  const speakWithBrowser = useCallback((text) => {
    if (!window.speechSynthesis) { afterSpeech(); return; }
    stopAudio();
    const utterance = new SpeechSynthesisUtterance(text);
    const voice = window.speechSynthesis.getVoices().find((v) => v.name === r.current.prefs.browserVoice);
    if (voice) utterance.voice = voice;
    utterance.onend = afterSpeech;
    utterance.onerror = afterSpeech;
    beginSpeaking();
    window.speechSynthesis.speak(utterance);
  }, [afterSpeech, beginSpeaking, stopAudio]);

  // ── Applying agent results (shared by WebSocket and REST fallback) ──

  const applyResult = useCallback((data) => {
    setMessages((prev) => [...prev, {
      id: data.message_id, role: 'assistant', content: data.content, tools: data.tool_calls || [],
      sources: data.rag_sources || [], at: new Date(), intent: data.intent,
    }]);
    setSession((s) => ({
      ...s,
      customer: data.customer || s.customer,
      activeOrder: data.active_order_id || s.activeOrder,
      intent: data.intent, confidence: data.confidence,
      pending: data.pending_action,
      escalated: s.escalated || data.escalated,
      sources: data.rag_sources?.length ? data.rag_sources : s.sources,
      tools: [...s.tools, ...(data.tool_calls || []).map((t) => ({ ...t, at: new Date() }))],
      provider: data.provider,
    }));
    if (data.degraded) flash('The AI model was unavailable, so the backup agent answered this turn.', 'warning');
  }, [flash]);

  const handleEvent = useCallback((msg) => {
    const { type, data = {} } = msg;
    const cur = r.current;
    if (!['PONG', 'SPEAKING'].includes(type)) log(type, type === 'TOOL_CALL_STARTED' ? data.tool : (data.intent || data.text || ''));

    switch (type) {
      case 'READY':
        cur.attempts = 0;
        if (cur.restMode) { cur.restMode = false; flash('Live connection restored.'); }
        setConnection('open');
        setCapabilities(data);
        cur.browserStt = data.stt === 'browser' && !!SpeechRecognitionImpl;
        if (!BUSY_STATES.has(cur.state) && !cur.speaking) goListening();
        break;
      case 'AUTH_FAILED':
        flash('Your session expired. Please sign in again.', 'danger');
        cur.ended = true;
        break;
      case 'TRANSCRIBING':
        setState('transcribing');
        break;
      case 'TRANSCRIPT':
        setMessages((prev) => [...prev, { id: `u-${Date.now()}`, role: 'user', content: data.text, source: data.source, at: new Date() }]);
        setState('thinking');
        break;
      case 'NO_SPEECH':
        flash("I didn't catch that. Please try again.");
        goListening();
        break;
      case 'STT_FAILED':
        if (data.fallback === 'browser' && SpeechRecognitionImpl) {
          cur.browserStt = true;
          flash('Server speech recognition is unavailable; using the browser instead.', 'warning');
        } else {
          flash(data.error || 'Speech recognition failed. Please try again.', 'warning');
        }
        goListening();
        break;
      case 'INTENT_DETECTED':
        setSession((s) => ({ ...s, intent: data.intent, confidence: data.confidence }));
        setState('thinking');
        break;
      case 'RETRIEVING_KNOWLEDGE':
        setState('searching');
        break;
      case 'KNOWLEDGE_RETRIEVED':
        if (data.sources?.length) setSession((s) => ({ ...s, sources: data.sources }));
        break;
      case 'TOOL_CALL_STARTED':
        setState('checking');
        setActivity(TOOL_LABELS[data.tool] || data.tool);
        break;
      case 'TOOL_CALL_COMPLETED':
        break;
      case 'CONFIRMATION_REQUIRED':
        setSession((s) => ({ ...s, pending: { tool: data.tool, summary: data.summary } }));
        break;
      case 'GENERATING_RESPONSE':
        setState('generating');
        setActivity('');
        break;
      case 'ESCALATED':
        setSession((s) => ({ ...s, escalated: true }));
        break;
      case 'AGENT_RESPONSE':
        applyResult(data);
        cur.awaitingAudio = true;
        break;
      case 'SPEAKING':
        playAudio(data.audio, data.mime);
        break;
      case 'TTS_UNAVAILABLE':
        speakWithBrowser(data.text);
        break;
      case 'COMPLETED':
        setSession((s) => ({ ...s, timings: data.timings }));
        if (!cur.speaking && !cur.audio && !window.speechSynthesis?.speaking) afterSpeech();
        break;
      case 'ERROR':
        flash(data.error || 'Something went wrong.', 'danger');
        if (data.fatal) { cur.ended = true; setState('error'); } else goListening();
        break;
      case 'CONVERSATION_ENDED':
        cur.endResolve?.(data);
        break;
      default:
        break;
    }
  }, [afterSpeech, applyResult, flash, goListening, log, playAudio, setState, speakWithBrowser]);

  // ── Transport ──

  const connect = useCallback((conversationId) => {
    const cur = r.current;
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${protocol}://${window.location.host}/api/voice/ws/${conversationId}`);
    cur.ws = ws;
    if (!cur.restMode) setConnection(cur.attempts ? 'reconnecting' : 'connecting');
    ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token: api.getToken() }));
    ws.onmessage = (e) => { try { handleEvent(JSON.parse(e.data)); } catch { /* ignore malformed */ } };
    ws.onclose = () => {
      if (cur.ws !== ws) return;
      cur.ws = null;
      if (cur.ended) { setConnection('closed'); return; }
      if (cur.attempts < 5) {
        const delay = Math.min(8000, 1000 * 2 ** cur.attempts);
        cur.attempts += 1;
        setConnection('reconnecting');
        setTimeout(() => { if (!cur.ended && cur.conv === conversationId) selfRefs.current.connect?.(conversationId); }, delay);
      } else {
        // Safe fallback: turns go over REST while the live socket keeps being probed in the background.
        if (!cur.restMode) {
          cur.restMode = true;
          setConnection('rest');
          flash('Live connection lost. Continuing over standard requests while reconnecting.', 'warning');
          if (!BUSY_STATES.has(cur.state)) goListening();
        }
        setTimeout(() => { if (!cur.ended && cur.conv === conversationId) selfRefs.current.connect?.(conversationId); }, 10000);
      }
    };
  }, [flash, goListening, handleEvent]);

  const restTurn = useCallback(async (payload) => {
    const cur = r.current;
    setState('thinking');
    try {
      const res = await api.voiceRespond(cur.conv, payload);
      if (res.status === 'no_speech') { flash("I didn't catch that. Please try again."); goListening(); return; }
      setMessages((prev) => [...prev, { id: `u-${Date.now()}`, role: 'user', content: res.transcript, at: new Date() }]);
      applyResult(res);
      setSession((s) => ({ ...s, timings: res.timings }));
      if (res.audio_base64) playAudio(res.audio_base64, res.audio_mime);
      else speakWithBrowser(res.content);
    } catch (e) {
      flash(e.message, 'danger');
      goListening();
    }
  }, [applyResult, flash, goListening, playAudio, setState, speakWithBrowser]);

  const sendText = useCallback((text, source = 'typed') => {
    const cur = r.current;
    const content = text.trim();
    if (!content || !cur.conv || cur.ended) return;
    stopAudio();
    cur.recorder?.setMode('off');
    if (cur.ws?.readyState === WebSocket.OPEN) {
      setState('thinking');
      cur.ws.send(JSON.stringify({ type: 'text', content, source }));
    } else {
      restTurn({ text: content });
    }
  }, [restTurn, setState, stopAudio]);
  useEffect(() => {
    selfRefs.current = { connect, startRecognition: startBrowserRecognition, sendText };
  }, [connect, startBrowserRecognition, sendText]);

  const sendAudio = useCallback(async (blob) => {
    const cur = r.current;
    cur.recorder?.setMode('off');
    setState('transcribing');
    if (cur.ws?.readyState === WebSocket.OPEN) {
      cur.ws.send(JSON.stringify({ type: 'audio', data: await blobToBase64(blob) }));
    } else {
      restTurn({ audio: blob });
    }
  }, [restTurn, setState]);

  // ── Public controls ──

  const start = useCallback(async (customerId) => {
    const cur = r.current;
    setSummary(null); setMessages([]); setEvents([]); setNotice(null); setMicError(null);
    setSession(emptySession);
    setState('connecting');
    let conv;
    try {
      conv = await api.createConversation(customerId || null, 'voice');
    } catch (e) {
      flash(e.message, 'danger');
      setState('idle');
      return;
    }
    Object.assign(cur, { conv: conv.id, ended: false, attempts: 0, browserStt: false, restMode: false });
    setSession({ ...emptySession, conversationId: conv.id,
      customer: conv.customer_code ? { customer_id: conv.customer_code, name: conv.customer_name } : null });
    log('SESSION_STARTED', conv.customer_code || 'unidentified caller');

    try {
      const recorder = new VoiceRecorder({
        onLevel: setLevel,
        onSpeechStart: () => { if (r.current.state !== 'speaking') setState('user_speaking'); log('USER_SPEAKING'); },
        onBargeIn: () => { stopAudio(); log('INTERRUPTED'); setState('user_speaking'); },
        onUtterance: (blob) => sendAudio(blob),
        sensitivity: cur.prefs.sensitivity,
      });
      await recorder.start();
      cur.recorder = recorder;
    } catch (e) {
      setMicError(e.name === 'NotAllowedError'
        ? 'Microphone permission was denied. Allow microphone access in your browser, or type below.'
        : 'No microphone is available. You can type your messages instead.');
      cur.recorder = null;
    }
    connect(conv.id);
  }, [connect, flash, log, sendAudio, setState, stopAudio]);

  const end = useCallback(async () => {
    const cur = r.current;
    if (!cur.conv || cur.ended) return;
    stopAudio();
    cur.recorder?.setMode('off');
    cur.recognition?.abort?.();
    setState('thinking');
    setActivity('Wrapping up');
    let result = null;
    if (cur.ws?.readyState === WebSocket.OPEN) {
      result = await new Promise((resolve) => {
        cur.endResolve = resolve;
        cur.ws.send(JSON.stringify({ type: 'end' }));
        setTimeout(() => resolve(null), 20000);
      });
    }
    try {
      if (!result) result = await api.endConversation(cur.conv);
    } catch (e) {
      flash(e.message, 'danger');
    }
    cur.ended = true;
    cur.ws?.close();
    await cur.recorder?.stop();
    cur.recorder = null;
    setLevel(0);
    setSummary(result);
    setConnection('closed');
    setActivity('');
    setState('completed');
  }, [flash, setState, stopAudio]);

  const toggleMic = useCallback(() => {
    const cur = r.current;
    if (!cur.recorder || cur.ended) return;
    if (!cur.prefs.handsFree) {
      // push-to-talk
      if (cur.recorder.mode === 'manual') cur.recorder.finishManual();
      else { stopAudio(); cur.recorder.setMode('manual'); setState('user_speaking'); }
      return;
    }
    const next = !cur.micEnabled;
    setMicEnabled(next);
    cur.micEnabled = next;
    if (!next) { cur.recorder.setMode('off'); if (!BUSY_STATES.has(cur.state) && !cur.speaking) setState('ready'); }
    else if (!BUSY_STATES.has(cur.state) && !cur.speaking) goListening();
  }, [goListening, setState, stopAudio]);

  const interrupt = useCallback(() => { stopAudio(); afterSpeech(); }, [afterSpeech, stopAudio]);

  const updatePrefs = useCallback((patch) => {
    setPrefs((p) => { const next = { ...p, ...patch }; savePrefs(next); return next; });
    if (patch.sensitivity && r.current.recorder) r.current.recorder.sensitivity = patch.sensitivity;
  }, []);

  useEffect(() => () => {
    const cur = r.current;
    cur.ended = true;
    stopAudio();
    cur.ws?.close();
    cur.recorder?.stop();
  }, [stopAudio]);

  return {
    state, label: STATE_LABELS[state], activity, messages, session, events, level, connection, capabilities,
    micEnabled, micError, notice, summary, prefs, active: !!session.conversationId && state !== 'completed',
    start, end, sendText, toggleMic, interrupt, updatePrefs, dismissNotice: () => setNotice(null),
  };
}
