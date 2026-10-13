import { useEffect, useState } from 'react';
import api from '../api/api';
import { useAuth } from '../context/AuthContext';
import { PageHeader, Spinner, ErrorBanner, Badge } from '../components/ui';
import useAsync from '../lib/useAsync';
import { loadPrefs, savePrefs } from '../voice/useVoiceAgent';

function Row({ name, value, ok }) {
  return (
    <div className="setting-row">
      <span>{name}</span>
      <span className="setting-value">{value}{ok !== undefined && <Badge kind={ok ? 'success' : 'warning'}>{ok ? 'Server' : 'Browser fallback'}</Badge>}</span>
    </div>
  );
}

export default function SettingsPage() {
  const { user } = useAuth();
  const { data: status, loading, error, reload } = useAsync(() => api.getSystemStatus());
  const [prefs, setPrefs] = useState(loadPrefs);
  const [voices, setVoices] = useState([]);

  useEffect(() => {
    const update = () => setVoices(window.speechSynthesis?.getVoices().filter((v) => v.lang.startsWith('en')) || []);
    update();
    window.speechSynthesis?.addEventListener?.('voiceschanged', update);
    return () => window.speechSynthesis?.removeEventListener?.('voiceschanged', update);
  }, []);

  const update = (patch) => setPrefs((p) => { const next = { ...p, ...patch }; savePrefs(next); return next; });

  return (
    <div className="settings">
      <PageHeader title="Settings" subtitle="Your profile, voice preferences, and the AI services this deployment uses." />

      <div className="card">
        <h3 className="card-title">Profile</h3>
        <div className="setting-row"><span>Name</span><span className="setting-value">{user.full_name}</span></div>
        <div className="setting-row"><span>Email</span><span className="setting-value">{user.email}</span></div>
        <div className="setting-row"><span>Role</span><span className="setting-value"><Badge kind="info">{user.role}</Badge></span></div>
      </div>

      <div className="card">
        <h3 className="card-title">Voice preferences</h3>
        <p className="muted small">Saved in this browser and applied to new voice sessions.</p>
        <label className="toggle-row">
          <span><strong>Hands-free conversation</strong><br /><span className="muted small">Detect the end of your speech automatically. Turn off for push-to-talk.</span></span>
          <input type="checkbox" checked={prefs.handsFree} onChange={(e) => update({ handsFree: e.target.checked })} />
        </label>
        <label className="toggle-row">
          <span><strong>Allow interruptions</strong><br /><span className="muted small">Start talking while the agent speaks to cut it off (barge-in).</span></span>
          <input type="checkbox" checked={prefs.bargeIn} onChange={(e) => update({ bargeIn: e.target.checked })} />
        </label>
        <label className="toggle-row">
          <span><strong>Microphone sensitivity</strong><br /><span className="muted small">Raise it if quiet speech isn't detected; lower it in noisy rooms.</span></span>
          <input type="range" min="0.6" max="1.8" step="0.1" value={prefs.sensitivity}
            onChange={(e) => update({ sensitivity: Number(e.target.value) })} aria-label="Microphone sensitivity" />
        </label>
        <label className="toggle-row">
          <span><strong>Browser fallback voice</strong><br /><span className="muted small">Used only when server speech synthesis is unavailable.</span></span>
          <select className="form-input select-sm" value={prefs.browserVoice} onChange={(e) => update({ browserVoice: e.target.value })}>
            <option value="">System default</option>
            {voices.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
          </select>
        </label>
      </div>

      <div className="card">
        <div className="card-header"><h3 className="card-title">AI services</h3><button className="btn btn-ghost btn-sm" onClick={reload}>Refresh</button></div>
        <ErrorBanner error={error} />
        {loading ? <Spinner /> : status && (
          <>
            <Row name="Language model" value={status.llm.provider} />
            <p className="muted small setting-note">{status.llm.description}</p>
            <Row name="Speech-to-text" value={status.stt.provider} ok={status.stt.server} />
            <Row name="Text-to-speech" value={status.tts.provider} ok={status.tts.server} />
            <Row name="Knowledge index" value={`${status.rag.documents} docs · ${status.rag.chunks} chunks · ${status.rag.index} · threshold ${status.rag.min_score}`} />
            <Row name="Embedding model" value={status.rag.embedding_model} />
            <Row name="Database" value={<>{status.database.dialect} <Badge kind={status.database.up ? 'success' : 'danger'}>{status.database.up ? 'Connected' : 'Down'}</Badge></>} />
            <p className="muted small setting-note">Providers are configured with environment variables on the server (see .env.example).</p>
          </>
        )}
      </div>
    </div>
  );
}
