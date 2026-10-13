import { useState } from 'react';
import api from '../api/api';
import { useAuth } from '../context/AuthContext';
import { isStaff } from '../lib/roles';

/** Assign / resolve / reopen an escalation (staff only). Calls onChange with the updated escalation. */
export default function EscalationActions({ escalation, onChange }) {
  const { user } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  if (!isStaff(user)) return null;

  const set = async (status, e) => {
    e?.stopPropagation();
    setBusy(true);
    setError(null);
    try {
      onChange?.(await api.updateEscalation(escalation.id, status));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="esc-actions" onClick={(e) => e.stopPropagation()}>
      {escalation.status === 'pending' && (
        <button className="btn btn-secondary btn-sm" disabled={busy} onClick={(e) => set('assigned', e)}>Assign to me</button>
      )}
      {escalation.status !== 'resolved' ? (
        <button className="btn btn-primary btn-sm" disabled={busy} onClick={(e) => set('resolved', e)}>Resolve</button>
      ) : (
        <button className="btn btn-ghost btn-sm" disabled={busy} onClick={(e) => set('pending', e)}>Reopen</button>
      )}
      {error && <span className="small text-danger" role="alert">{error}</span>}
    </div>
  );
}
