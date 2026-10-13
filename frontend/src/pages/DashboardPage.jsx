import { Link, useNavigate } from 'react-router-dom';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import api from '../api/api';
import Icon from '../components/Icon';
import EscalationActions from '../components/EscalationActions';
import { PageHeader, StatCard, Badge, Spinner, ErrorBanner, EmptyState } from '../components/ui';
import useAsync from '../lib/useAsync';
import { label, dateTime, duration, ms } from '../lib/format';
import { SERIES_FOR, axis, grid, tooltip, barCursor } from '../lib/chart';

async function loadDashboard() {
  const [overview, series, intents, recent, escalations] = await Promise.all([
    api.getAnalyticsOverview(), api.getConversationsOverTime(14), api.getIntentDistribution(),
    api.getConversations({ limit: 6 }), api.getEscalations(),
  ]);
  return { overview, series, intents, recent: recent.conversations, escalations };
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const { data, loading, error, reload } = useAsync(loadDashboard);

  if (loading && !data) return <Spinner />;
  if (error) return <ErrorBanner error={error} onRetry={reload} />;
  const { overview: o, series, intents, recent, escalations } = data;
  const openEscalations = escalations.filter((e) => e.status !== 'resolved');
  const maxIntent = Math.max(1, ...intents.map((i) => i.count));

  return (
    <div>
      <PageHeader title="Dashboard" subtitle="Live metrics computed from stored conversations, messages and tool executions."
        actions={<>
          <button className="btn btn-secondary btn-sm" onClick={reload}><Icon name="refresh" size={14} /> Refresh</button>
          <Link className="btn btn-primary btn-sm" to="/voice"><Icon name="mic" size={14} /> Start voice session</Link>
        </>} />

      <div className="stats-grid">
        <StatCard label="Total conversations" value={o.total_conversations} icon="chat"
          hint={`${o.active_conversations} active · ${o.total_messages} messages`} />
        <StatCard label="Resolution rate" value={o.resolution_rate} suffix="%" icon="check"
          hint={`${o.resolved_conversations} resolved · ${o.unresolved_conversations} unresolved`} />
        <StatCard label="Escalated" value={o.escalated_conversations} icon="alert" hint={`${o.escalation_rate}% of finished conversations`} />
        <StatCard label="Avg response time" value={ms(o.avg_response_time_ms)} icon="clock" hint="Agent turn, end to end" />
        <StatCard label="Avg duration" value={duration(o.avg_duration_seconds)} icon="phone" hint="Ended conversations" />
        <StatCard label="Tool success rate" value={o.tool_success_rate} suffix="%" icon="tool" hint={`${o.total_tool_calls} tool calls`} />
      </div>

      {o.total_conversations === 0 && (
        <div className="card empty-hero">
          <EmptyState icon="mic" title="No conversations yet">
            Every metric here comes from real agent conversations. Start a voice session to create the first one.
          </EmptyState>
        </div>
      )}

      <div className="grid-2">
        <div className="card">
          <div className="card-header"><h3 className="card-title">Conversations, last 14 days</h3></div>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={series.map((d) => ({ ...d, other: d.conversations - d.resolved - d.escalated }))} margin={{ top: 8, right: 12, left: -16, bottom: 0 }} barCategoryGap="20%">
              <CartesianGrid {...grid} />
              <XAxis dataKey="date" {...axis} tickFormatter={(d) => d.slice(5)} minTickGap={16} />
              <YAxis {...axis} allowDecimals={false} axisLine={false} />
              <Tooltip {...tooltip} cursor={barCursor} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar isAnimationActive={false} dataKey="resolved" name="Resolved" stackId="c" stroke="#12151c" strokeWidth={2} fill={SERIES_FOR.resolved} maxBarSize={22} />
              <Bar isAnimationActive={false} dataKey="escalated" name="Escalated" stackId="c" stroke="#12151c" strokeWidth={2} fill={SERIES_FOR.escalated} maxBarSize={22} />
              <Bar isAnimationActive={false} dataKey="other" name="Unresolved / active" stackId="c" stroke="#12151c" strokeWidth={2} fill={SERIES_FOR.conversations} radius={[4, 4, 0, 0]} maxBarSize={22} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <div className="card-header"><h3 className="card-title">Top customer issues</h3><Link to="/analytics" className="link-sm">Analytics</Link></div>
          {intents.length === 0 ? <p className="muted small">No intents recorded yet.</p> : (
            <ul className="bar-list">
              {intents.slice(0, 7).map((i) => (
                <li key={i.intent} title={`${i.count} conversations (${i.percentage}%)`}>
                  <span className="bar-label">{label(i.intent)}</span>
                  <span className="bar-track"><span className="bar-fill" style={{ width: `${(i.count / maxIntent) * 100}%` }} /></span>
                  <span className="bar-value">{i.count}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card-header"><h3 className="card-title">Recent conversations</h3><Link to="/conversations" className="link-sm">View all</Link></div>
          {recent.length === 0 ? <p className="muted small">Nothing yet.</p> : (
            <div className="table-container">
              <table>
                <thead><tr><th>Customer</th><th>Intent</th><th>Outcome</th><th>Started</th></tr></thead>
                <tbody>
                  {recent.map((c) => (
                    <tr key={c.id} className="clickable" onClick={() => navigate(`/conversations/${c.id}`)}>
                      <td>{c.customer_name || <span className="muted">Unverified</span>}</td>
                      <td>{label(c.primary_intent) || '—'}</td>
                      <td><Badge value={c.status === 'active' ? 'active' : c.resolution || c.status} /></td>
                      <td className="muted">{dateTime(c.started_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="card">
          <div className="card-header"><h3 className="card-title">Escalation queue</h3><Badge kind={openEscalations.length ? 'danger' : 'neutral'}>{openEscalations.length} open</Badge></div>
          {escalations.length === 0 ? <p className="muted small">No escalations. Conversations handed to humans appear here.</p> : (
            <ul className="queue">
              {escalations.slice(0, 6).map((e) => (
                <li key={e.id} onClick={() => navigate(`/conversations/${e.conversation_id}`)} className="clickable">
                  <div>
                    <div className="queue-title">{e.customer_name || 'Unverified caller'} <span className="muted">· {label(e.reason)}</span></div>
                    <div className="muted small clamp-1">{e.details || e.conversation_summary}</div>
                  </div>
                  <div className="queue-meta">
                    <Badge value={e.status} />
                    {e.ticket_id && <span className="muted small">{e.ticket_id}</span>}
                    <EscalationActions escalation={e} onChange={reload} />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
