import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/api';
import { PageHeader, Badge, Spinner, ErrorBanner, EmptyState } from '../components/ui';
import useAsync from '../lib/useAsync';
import { label, dateTime, duration } from '../lib/format';

const FILTERS = [['', 'All'], ['active', 'Active'], ['completed', 'Completed'], ['escalated', 'Escalated'], ['abandoned', 'Abandoned']];

export default function ConversationsPage() {
  const [status, setStatus] = useState('');
  const navigate = useNavigate();
  const { data, loading, error, reload } = useAsync(() => api.getConversations({ limit: 200, status }), [status]);

  return (
    <div>
      <PageHeader title="Conversations" subtitle={data ? `${data.total} conversations` : 'Transcripts, outcomes and summaries'} />
      <div className="filter-row" role="tablist" aria-label="Filter by status">
        {FILTERS.map(([value, text]) => (
          <button key={value} role="tab" aria-selected={status === value}
            className={`seg ${status === value ? 'seg-active' : ''}`} onClick={() => setStatus(value)}>{text}</button>
        ))}
      </div>
      <ErrorBanner error={error} onRetry={reload} />
      <div className="card card-flush">
        {loading ? <Spinner /> : !data?.conversations.length ? (
          <EmptyState icon="chat" title="No conversations">Conversations from the voice agent appear here.</EmptyState>
        ) : (
          <div className="table-container">
            <table>
              <thead><tr><th>Started</th><th>Customer</th><th>Intent</th><th>Status</th><th>Outcome</th><th>Sentiment</th><th>Msgs</th><th>Duration</th><th>Summary</th></tr></thead>
              <tbody>
                {data.conversations.map((c) => (
                  <tr key={c.id} className="clickable" onClick={() => navigate(`/conversations/${c.id}`)}>
                    <td className="nowrap muted">{dateTime(c.started_at)}</td>
                    <td className="nowrap">{c.customer_name || <span className="muted">Unverified</span>}</td>
                    <td className="nowrap">{label(c.primary_intent) || '—'}</td>
                    <td><Badge value={c.status} /></td>
                    <td><Badge value={c.resolution} /></td>
                    <td><Badge value={c.sentiment} /></td>
                    <td>{c.message_count}</td>
                    <td className="nowrap">{duration(c.duration_seconds)}</td>
                    <td className="summary-cell">{c.summary || '—'}</td>
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
