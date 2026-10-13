import { useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../api/api';
import { PageHeader, Badge, Spinner, ErrorBanner, EmptyState } from '../components/ui';
import useAsync from '../lib/useAsync';
import { useAuth } from '../context/AuthContext';
import { isStaff } from '../lib/roles';
import { dateTime, label } from '../lib/format';

const STATUSES = ['open', 'in_progress', 'waiting_customer', 'escalated', 'resolved', 'closed'];

export default function TicketsPage() {
  const { data, loading, error, reload } = useAsync(() => api.getTickets());
  const { user } = useAuth();
  const [saving, setSaving] = useState(null);
  const [actionError, setActionError] = useState(null);

  const setStatus = async (ticket, status) => {
    setSaving(ticket.ticket_id);
    setActionError(null);
    try {
      await api.updateTicket(ticket.ticket_id, { status });
      await reload();
    } catch (e) {
      setActionError(e.message);
    } finally {
      setSaving(null);
    }
  };

  return (
    <div>
      <PageHeader title="Support tickets" subtitle="Tickets opened by the agent or by staff, including human escalations." />
      <ErrorBanner error={error || actionError} onRetry={reload} />
      <div className="card card-flush">
        {loading && !data ? <Spinner /> : !data?.length ? <EmptyState icon="ticket" title="No tickets" /> : (
          <div className="table-container">
            <table>
              <thead><tr><th>Ticket</th><th>Subject</th><th>Category</th><th>Priority</th><th>Created</th><th>Source</th><th>Status</th></tr></thead>
              <tbody>
                {data.map((t) => (
                  <tr key={t.id}>
                    <td className="mono">{t.ticket_id}</td>
                    <td><div className="strong">{t.subject}</div><div className="muted small clamp-1">{t.description}</div></td>
                    <td>{label(t.category)}</td>
                    <td><Badge value={t.priority} /></td>
                    <td className="nowrap muted">{dateTime(t.created_at)}</td>
                    <td>{t.conversation_id ? <Link to={`/conversations/${t.conversation_id}`} className="link-sm">Conversation</Link> : <span className="muted">Manual</span>}</td>
                    <td>
                      <select className="form-input select-sm" value={t.status} disabled={saving === t.ticket_id || !isStaff(user)}
                        onChange={(e) => setStatus(t, e.target.value)} aria-label={`Status of ${t.ticket_id}`}>
                        {STATUSES.map((s) => <option key={s} value={s}>{label(s)}</option>)}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
