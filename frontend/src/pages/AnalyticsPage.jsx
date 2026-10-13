import { useState } from 'react';
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from 'recharts';
import api from '../api/api';
import { PageHeader, StatCard, Spinner, ErrorBanner, EmptyState } from '../components/ui';
import useAsync from '../lib/useAsync';
import { label, ms } from '../lib/format';
import { SERIES, SERIES_FOR, axis, grid, tooltip, barCursor } from '../lib/chart';

async function load(days) {
  const [overview, series, intents, tools, latency, reasons, sentiment] = await Promise.all([
    api.getAnalyticsOverview(), api.getConversationsOverTime(days), api.getIntentDistribution(), api.getToolUsage(),
    api.getLatency(60), api.getEscalationReasons(), api.getSentiment(),
  ]);
  return { overview, series, intents, tools, latency, reasons, sentiment };
}

function ChartCard({ title, children, empty }) {
  return (
    <div className="card">
      <h3 className="card-title chart-title">{title}</h3>
      {empty ? <p className="muted small chart-empty">No data yet.</p> : children}
    </div>
  );
}

function TableView({ rows, columns }) {
  return (
    <details className="table-view">
      <summary className="small muted">Show data table</summary>
      <table>
        <thead><tr>{columns.map(([key, name]) => <th key={key}>{name}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{columns.map(([key]) => <td key={key}>{r[key]}</td>)}</tr>)}</tbody>
      </table>
    </details>
  );
}

export default function AnalyticsPage() {
  const [days, setDays] = useState(30);
  const { data, loading, error, reload } = useAsync(() => load(days), [days]);

  if (loading && !data) return <Spinner />;
  if (error) return <ErrorBanner error={error} onRetry={reload} />;
  const { overview: o, series, intents, tools, latency, reasons, sentiment } = data;
  const latencyRows = latency.map((l, i) => ({ turn: i + 1, latency: Math.round(l.latency_ms) }));
  const intentRows = intents.map((i) => ({ ...i, name: label(i.intent) }));
  const sentimentTotal = sentiment.reduce((n, s) => n + s.count, 0);

  return (
    <div>
      <PageHeader title="Analytics" subtitle="Every number is computed from the PostgreSQL conversation records."
        actions={(
          <div className="filter-row compact" role="tablist" aria-label="Time range">
            {[7, 30, 90].map((d) => (
              <button key={d} role="tab" aria-selected={days === d} className={`seg ${days === d ? 'seg-active' : ''}`} onClick={() => setDays(d)}>{d}d</button>
            ))}
          </div>
        )} />

      <div className="stats-grid">
        <StatCard label="Resolution rate" value={o.resolution_rate} suffix="%" hint={`${o.resolved_conversations} of ${o.resolved_conversations + o.unresolved_conversations + o.escalated_conversations} finished`} />
        <StatCard label="Escalation rate" value={o.escalation_rate} suffix="%" hint={`${o.escalated_conversations} escalated`} />
        <StatCard label="Avg response" value={ms(o.avg_response_time_ms)} hint="Per agent turn" />
        <StatCard label="Tool success" value={o.tool_success_rate} suffix="%" hint={`${o.total_tool_calls} calls`} />
      </div>

      {o.total_conversations === 0 && <div className="card"><EmptyState icon="chart" title="No conversation data yet">Charts fill in as the voice agent handles conversations.</EmptyState></div>}

      <div className="grid-2">
        <ChartCard title={`Conversations over time (${days} days)`} empty={!series.some((d) => d.conversations)}>
          <ResponsiveContainer width="100%" height={250}>
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
        </ChartCard>

        <ChartCard title="Customer intents" empty={!intents.length}>
          <ResponsiveContainer width="100%" height={Math.max(160, intentRows.length * 30)}>
            <BarChart data={intentRows} layout="vertical" margin={{ top: 0, right: 24, left: 8, bottom: 0 }} barCategoryGap={6}>
              <CartesianGrid {...grid} horizontal={false} vertical />
              <XAxis type="number" {...axis} allowDecimals={false} />
              <YAxis type="category" dataKey="name" {...axis} width={130} axisLine={false} />
              <Tooltip {...tooltip} cursor={barCursor} formatter={(v, n, p) => [`${v} (${p.payload.percentage}%)`, 'Conversations']} />
              <Bar isAnimationActive={false} dataKey="count" fill={SERIES[0]} radius={[0, 4, 4, 0]} maxBarSize={18} />
            </BarChart>
          </ResponsiveContainer>
          <TableView rows={intentRows} columns={[['name', 'Intent'], ['count', 'Conversations'], ['percentage', '%']]} />
        </ChartCard>
      </div>

      <div className="grid-2">
        <ChartCard title="Agent response latency (recent turns)" empty={!latencyRows.length}>
          <ResponsiveContainer width="100%" height={230}>
            <LineChart data={latencyRows} margin={{ top: 8, right: 12, left: -4, bottom: 0 }}>
              <CartesianGrid {...grid} />
              <XAxis dataKey="turn" {...axis} />
              <YAxis {...axis} axisLine={false} tickFormatter={(v) => ms(v)} />
              <Tooltip {...tooltip} formatter={(v) => [ms(v), 'Latency']} labelFormatter={(l) => `Turn ${l}`} />
              <Line isAnimationActive={false} dataKey="latency" stroke={SERIES[0]} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Tool usage" empty={!tools.length}>
          <ResponsiveContainer width="100%" height={Math.max(160, tools.length * 32)}>
            <BarChart data={tools} layout="vertical" margin={{ top: 0, right: 24, left: 8, bottom: 0 }} barCategoryGap={6}>
              <CartesianGrid {...grid} horizontal={false} vertical />
              <XAxis type="number" {...axis} allowDecimals={false} />
              <YAxis type="category" dataKey="tool_name" {...axis} width={150} axisLine={false} />
              <Tooltip {...tooltip} cursor={barCursor} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar isAnimationActive={false} dataKey="success_count" name="Succeeded" stackId="a" stroke="#12151c" strokeWidth={2} fill={SERIES[0]} maxBarSize={18} />
              <Bar isAnimationActive={false} dataKey="failure_count" name="Rejected / failed" stackId="a" stroke="#12151c" strokeWidth={2} fill={SERIES[1]} radius={[0, 4, 4, 0]} maxBarSize={18} />
            </BarChart>
          </ResponsiveContainer>
          <TableView rows={tools.map((t) => ({ ...t, avg: ms(t.avg_execution_time_ms) }))}
            columns={[['tool_name', 'Tool'], ['total_calls', 'Calls'], ['success_count', 'Succeeded'], ['failure_count', 'Failed'], ['avg', 'Avg time']]} />
        </ChartCard>
      </div>

      <div className="grid-2">
        <div className="card">
          <h3 className="card-title chart-title">Escalation reasons</h3>
          {!reasons.length ? <p className="muted small">No escalations yet.</p> : (
            <ul className="bar-list">
              {reasons.map((r) => (
                <li key={r.reason}><span className="bar-label">{label(r.reason)}</span>
                  <span className="bar-track"><span className="bar-fill" style={{ width: `${(r.count / Math.max(...reasons.map((x) => x.count))) * 100}%` }} /></span>
                  <span className="bar-value">{r.count}</span></li>
              ))}
            </ul>
          )}
        </div>
        <div className="card">
          <h3 className="card-title chart-title">Customer sentiment</h3>
          {!sentimentTotal ? <p className="muted small">Sentiment is recorded when conversations end.</p> : (
            <ul className="bar-list">
              {sentiment.map((s) => (
                <li key={s.sentiment}><span className="bar-label">{label(s.sentiment)}</span>
                  <span className="bar-track"><span className="bar-fill" style={{ width: `${(s.count / sentimentTotal) * 100}%` }} /></span>
                  <span className="bar-value">{Math.round((s.count / sentimentTotal) * 100)}%</span></li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
