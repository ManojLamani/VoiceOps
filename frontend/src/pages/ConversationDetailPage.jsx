import { useParams, Link } from 'react-router-dom';
import api from '../api/api';
import Icon from '../components/Icon';
import EscalationActions from '../components/EscalationActions';
import HandoffPanel from '../components/HandoffPanel';
import { Badge, Spinner, ErrorBanner } from '../components/ui';
import useAsync from '../lib/useAsync';
import useLiveConversation from '../lib/useLiveConversation';
import { label, dateTime, time, duration, ms, TOOL_LABELS } from '../lib/format';

function Field({ name, children }) {
  return <div className="field"><dt>{name}</dt><dd>{children || '—'}</dd></div>;
}

export default function ConversationDetailPage() {
  const { id } = useParams();
  const { data, loading, error, reload, refresh } = useAsync(() => api.getConversation(id), [id]);
  const live = !!data && !data.conversation.ended_at;
  useLiveConversation(id, live, refresh); // every live event (message, join, hand back, end) re-syncs the page

  if (loading) return <Spinner />;
  if (error) return <ErrorBanner error={error} onRetry={reload} />;
  const { conversation: c, messages, tool_executions: tools, escalation: esc, state } = data;
  const details = c.summary_details || {};
  const toolsByMessage = tools.reduce((acc, t) => { (acc[t.message_id] ||= []).push(t); return acc; }, {});

  return (
    <div className="detail-page">
      <Link to="/conversations" className="back-link"><Icon name="arrowLeft" size={14} /> Conversations</Link>
      <div className="page-header">
        <div>
          <h1>{c.customer_name ? `${c.customer_name} · ${c.customer_code}` : 'Unverified caller'}</h1>
          <p>{dateTime(c.started_at)} · {c.channel} · {c.message_count} messages · {duration(c.duration_seconds)}</p>
        </div>
        <div className="page-actions">
          {live && <span className="conn conn-open"><span className="conn-dot" />Live</span>}
          <Badge value={c.status} />{c.resolution && <Badge value={c.resolution} />}
        </div>
      </div>

      {esc && (
        <div className="banner banner-danger escalation-banner">
          <Icon name="alert" size={18} />
          <div>
            <strong>Escalated to a human agent: {label(esc.reason)}</strong>
            <div className="small">{esc.details}</div>
            <div className="small muted">{esc.customer_summary}{esc.ticket_id ? ` · Ticket ${esc.ticket_id}` : ''} · {label(esc.status)} · {dateTime(esc.created_at)}</div>
            <EscalationActions escalation={esc} onChange={reload} />
          </div>
        </div>
      )}

      {live && <HandoffPanel conversationId={c.id} humanAgent={state.human_agent} onChange={refresh} />}

      <div className="detail-grid-2">
        <div className="card">
          <h3 className="card-title">Summary</h3>
          {c.summary ? (
            <>
              <p className="summary-text">{c.summary}</p>
              <dl className="fields">
                <Field name="Issue">{details.issue}</Field>
                <Field name="Customer intent">{label(c.primary_intent)}</Field>
                <Field name="Resolution">{details.resolution}</Field>
                <Field name="Actions taken">{details.actions_taken?.length ? details.actions_taken.join('; ') : 'None'}</Field>
                <Field name="Tools used">{details.tools_used?.length ? details.tools_used.join(', ') : 'None'}</Field>
                <Field name="Recommended follow-up">{details.follow_up}</Field>
                <Field name="Sentiment"><Badge value={c.sentiment} /></Field>
              </dl>
            </>
          ) : (
            <p className="muted">The summary is generated when the conversation ends.
              {state.pending_action && <> Pending confirmation: {state.pending_action}</>}</p>
          )}
        </div>

        <div className="card">
          <h3 className="card-title">Tool executions ({tools.length})</h3>
          {tools.length === 0 ? <p className="muted small">No backend tools were called.</p> : (
            <ul className="tool-timeline">
              {tools.map((t) => (
                <li key={t.id}>
                  <span className={`tool-dot ${t.success ? 'ok' : 'fail'}`} />
                  <div className="tool-body">
                    <div className="tool-name">{TOOL_LABELS[t.tool_name] || t.tool_name} <span className="muted small">· {time(t.created_at)} · {ms(t.execution_time_ms)}</span></div>
                    <code className="code-line">{t.tool_name}({JSON.stringify(t.tool_args)})</code>
                    <details>
                      <summary className="small">{t.success ? (t.tool_result?.status || 'success') : (t.tool_result?.error_code || 'failed')}</summary>
                      <pre className="code-block">{JSON.stringify(t.tool_result, null, 2)}</pre>
                    </details>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="card">
        <h3 className="card-title">Transcript</h3>
        <ol className="transcript-log">
          {messages.map((m) => (m.role === 'system' ? (
            <li key={m.id} className="turn-system"><span>{m.content}</span><span className="muted">{time(m.created_at)}</span></li>
          ) : (
            <li key={m.id} className={`turn turn-${m.role}${m.author_name ? ' turn-human' : ''}`}>
              <div className="turn-avatar"><Icon name={m.role === 'user' || m.author_name ? 'user' : 'mic'} size={14} /></div>
              <div className="turn-body">
                <div className="turn-head">
                  <strong>{m.role === 'user' ? 'Customer' : m.author_name ? `${m.author_name} · Support agent` : 'AI agent'}</strong>
                  <span className="muted">{time(m.created_at)}</span>
                  {m.role === 'user' && m.intent && <span className="chip chip-muted">{label(m.intent)}{m.confidence ? ` · ${Math.round(m.confidence * 100)}%` : ''}</span>}
                  {m.processing_time_ms && <span className="muted small">{ms(m.processing_time_ms)}</span>}
                </div>
                <div className="turn-text">{m.content}</div>
                {(toolsByMessage[m.id]?.length > 0 || m.rag_sources?.length > 0) && (
                  <div className="bubble-meta">
                    {toolsByMessage[m.id]?.map((t) => (
                      <span key={t.id} className={`chip ${t.success ? 'chip-tool' : 'chip-fail'}`}><Icon name="tool" size={11} /> {t.tool_name}</span>
                    ))}
                    {m.rag_sources?.map((s) => (
                      <span key={s.chunk_id} className="chip chip-source" title={s.excerpt}><Icon name="file" size={11} /> {s.document_title} · {s.section} ({Math.round(s.score * 100)}%)</span>
                    ))}
                  </div>
                )}
              </div>
            </li>
          )))}
        </ol>
      </div>
    </div>
  );
}
