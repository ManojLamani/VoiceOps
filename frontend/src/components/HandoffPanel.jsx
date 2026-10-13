import { useState } from 'react';
import api from '../api/api';
import Icon from './Icon';
import { useAuth } from '../context/AuthContext';
import { isStaff } from '../lib/roles';

/** Take over an active conversation from the AI agent, talk to the customer, and hand back (staff only). */
export default function HandoffPanel({ conversationId, humanAgent, onChange }) {
  const { user } = useAuth();
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  if (!isStaff(user)) return null;

  const mine = humanAgent?.user_id === user.id;
  const run = async (action) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChange?.();
      return true;
    } catch (err) {
      setError(err.message);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const send = async (e) => {
    e.preventDefault();
    if (await run(() => api.sendHandoffMessage(conversationId, text.trim()))) setText('');
  };

  return (
    <div className="card handoff-panel">
      <div className="handoff-head">
        <div>
          <h3 className="card-title">Live conversation</h3>
          <p className="muted small">
            {!humanAgent && 'Ava is handling this call. Join to take over: Ava stops answering until you hand back.'}
            {mine && 'You are talking to the customer. Your replies are spoken to them.'}
            {humanAgent && !mine && `${humanAgent.name} is handling this conversation.`}
          </p>
        </div>
        <div className="page-actions">
          {!humanAgent && (
            <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => run(() => api.joinConversation(conversationId))}>
              <Icon name="phone" size={14} /> Join conversation
            </button>
          )}
          {humanAgent && (mine || user.role === 'admin') && (
            <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => run(() => api.leaveConversation(conversationId))}>
              Hand back to Ava
            </button>
          )}
        </div>
      </div>
      {mine && (
        <form className="composer" onSubmit={send}>
          <Icon name="keyboard" size={16} className="muted" />
          <input
            className="composer-input" value={text} onChange={(e) => setText(e.target.value)} maxLength={2000}
            placeholder="Reply to the customer…" aria-label="Reply to the customer" disabled={busy}
          />
          <button className="btn-icon btn-send" type="submit" disabled={!text.trim() || busy} aria-label="Send">
            <Icon name="send" size={16} />
          </button>
        </form>
      )}
      {error && <div className="small text-danger" role="alert">{error}</div>}
    </div>
  );
}
