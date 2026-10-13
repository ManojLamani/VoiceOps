import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../api/api';
import Icon from '../components/Icon';
import { Badge } from '../components/ui';
import useVoiceAgent, { BUSY_STATES } from '../voice/useVoiceAgent';
import { label, ms, TOOL_LABELS } from '../lib/format';

const SUGGESTIONS = [
  'What is the status of order ORD001?',
  'I want to cancel my order',
  'How long do refunds take?',
  'I need to speak to a person',
];

function Orb({ state, level }) {
  const scale = state === 'user_speaking' || state === 'listening' ? 1 + level * 0.35 : 1;
  return (
    <div className={`orb orb-${state}`} aria-hidden="true">
      <div className="orb-ring" style={{ transform: `scale(${scale})` }} />
      <div className="orb-core">
        <Icon name={state === 'speaking' ? 'volume' : BUSY_STATES.has(state) ? 'zap' : 'mic'} size={30} />
      </div>
    </div>
  );
}

function Transcript({ messages, state }) {
  const end = useRef(null);
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [messages, state]);
  return (
    <div className="transcript" aria-live="polite">
      {messages.map((m) => (m.role === 'system' ? (
        <div key={m.id} className="bubble-system" role="status">{m.content}</div>
      ) : (
        <div key={m.id} className={`bubble bubble-${m.role}${m.author ? ' bubble-human' : ''}`}>
          <div className="bubble-head">
            <span>{m.role === 'user' ? 'Customer' : m.author ? `${m.author} · Support agent` : 'Ava · AI agent'}</span>
            <span>{m.at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
            {m.source === 'typed' && <span className="chip chip-muted">typed</span>}
          </div>
          <div className="bubble-text">{m.content}</div>
          {(m.tools?.length > 0 || m.sources?.length > 0) && (
            <div className="bubble-meta">
              {m.tools?.map((t, i) => (
                <span key={`t${i}`} className={`chip ${t.success ? 'chip-tool' : 'chip-fail'}`}>
                  <Icon name="tool" size={11} /> {t.tool_name}
                </span>
              ))}
              {m.sources?.slice(0, 3).map((s, i) => (
                <span key={`s${i}`} className="chip chip-source"><Icon name="file" size={11} /> {s.document_title}</span>
              ))}
            </div>
          )}
        </div>
      )))}
      {BUSY_STATES.has(state) && (
        <div className="bubble bubble-assistant bubble-typing"><span /><span /><span /></div>
      )}
      <div ref={end} />
    </div>
  );
}

function Summary({ summary, onRestart }) {
  if (!summary) return null;
  return (
    <div className="summary-card">
      <div className="summary-head">
        <div>
          <h3>Conversation summary</h3>
          <p className="muted">Stored with the transcript, tool calls and metrics.</p>
        </div>
        <Badge value={summary.outcome} />
      </div>
      <dl className="summary-grid">
        <div><dt>Issue</dt><dd>{summary.issue || '—'}</dd></div>
        <div><dt>Intent</dt><dd>{label(summary.primary_intent)}</dd></div>
        <div><dt>Resolution</dt><dd>{summary.resolution || '—'}</dd></div>
        <div><dt>Follow-up</dt><dd>{summary.follow_up || '—'}</dd></div>
        <div><dt>Actions taken</dt><dd>{summary.actions_taken?.length ? summary.actions_taken.join('; ') : 'None'}</dd></div>
        <div><dt>Tools used</dt><dd>{summary.tools_used?.length ? summary.tools_used.join(', ') : 'None'}</dd></div>
      </dl>
      <div className="summary-actions">
        <Link className="btn btn-secondary btn-sm" to={`/conversations/${summary.conversation_id}`}>Open full record</Link>
        <button className="btn btn-primary btn-sm" onClick={onRestart}><Icon name="phone" size={14} /> New conversation</button>
      </div>
    </div>
  );
}

export default function VoiceAgentPage() {
  const v = useVoiceAgent();
  const [customers, setCustomers] = useState([]);
  const [caller, setCaller] = useState('');
  const [text, setText] = useState('');
  const [showLog, setShowLog] = useState(false);

  useEffect(() => { api.getCustomers().then(setCustomers).catch(() => {}); }, []);
  useEffect(() => {
    if (!v.notice) return undefined;
    const t = setTimeout(v.dismissNotice, 6000);
    return () => clearTimeout(t);
  }, [v.notice, v.dismissNotice]);

  const submitText = (e) => {
    e.preventDefault();
    v.sendText(text);
    setText('');
  };
  const busy = BUSY_STATES.has(v.state);
  const s = v.session;
  const lastTools = s.tools.slice(-6).reverse();
  const pushToTalk = !v.prefs.handsFree;
  const recordingManual = pushToTalk && v.state === 'user_speaking';

  return (
    <div className="voice-page">
      <div className="page-header">
        <div>
          <h1>Voice Agent</h1>
          <p>Talk to Ava, the AI support agent. Real speech recognition, tools, knowledge base and speech output.</p>
        </div>
        <div className="page-actions">
          {v.active && (
            <span className={`conn conn-${v.connection}`}>
              <span className="conn-dot" />
              {{ open: 'Live', connecting: 'Connecting', reconnecting: 'Reconnecting…', rest: 'Fallback mode', closed: 'Offline' }[v.connection]}
            </span>
          )}
          {v.active ? (
            <button className="btn btn-danger" onClick={v.end}><Icon name="phoneOff" size={16} /> End conversation</button>
          ) : null}
        </div>
      </div>

      {v.notice && (
        <div className={`banner banner-${v.notice.kind}`} role="status">
          <Icon name={v.notice.kind === 'info' ? 'mic' : 'alert'} size={16} />
          <span>{v.notice.text}</span>
          <button className="btn-icon" onClick={v.dismissNotice} aria-label="Dismiss"><Icon name="x" size={14} /></button>
        </div>
      )}

      <div className="voice-grid">
        <section className="voice-stage card">
          {!v.active && v.state !== 'connecting' && !v.summary && (
            <div className="start-panel">
              <Orb state="idle" level={0} />
              <h2>Start a support call</h2>
              <p className="muted">Pick who is calling to simulate caller ID, or leave the caller unidentified so the agent has to verify them.</p>
              <div className="start-controls">
                <label className="sr-only" htmlFor="caller">Caller</label>
                <select id="caller" className="form-input" value={caller} onChange={(e) => setCaller(e.target.value)}>
                  <option value="">Unidentified caller (agent verifies identity)</option>
                  {customers.map((c) => <option key={c.customer_id} value={c.customer_id}>{c.name} · {c.customer_id}</option>)}
                </select>
                <button className="btn btn-primary btn-lg" onClick={() => v.start(caller)}>
                  <Icon name="phone" size={18} /> Start conversation
                </button>
              </div>
              <p className="hint">Your browser will ask for microphone access. Speak naturally. Ava detects when you stop talking.</p>
            </div>
          )}

          {(v.active || v.state === 'connecting') && (
            <>
              <div className="stage-top">
                <Orb state={v.state} level={v.level} />
                <div className="stage-status">
                  <div className={`state-label state-${v.state}`}>{v.label}{v.activity ? ` · ${v.activity}` : ''}</div>
                  <div className="muted small">
                    {v.state === 'listening' && 'Go ahead, I’m listening.'}
                    {v.state === 'user_speaking' && (pushToTalk ? 'Recording… press the mic again to send.' : 'I’ll respond when you pause.')}
                    {v.state === 'speaking' && (v.prefs.bargeIn && v.prefs.handsFree ? 'You can interrupt at any time.' : '')}
                    {v.state === 'ready' && (pushToTalk ? 'Press the mic to talk.' : 'Microphone is paused.')}
                  </div>
                  {s.human ? (
                    <div className="human-pill"><Icon name="user" size={13} /> {s.human.name} from support is on the line</div>
                  ) : s.escalated && <div className="escalation-pill"><Icon name="alert" size={13} /> Escalated to a human agent</div>}
                </div>
                <div className="stage-controls">
                  <button
                    className={`mic-btn ${recordingManual ? 'mic-recording' : ''} ${!v.micEnabled ? 'mic-off' : ''}`}
                    onClick={v.toggleMic}
                    disabled={!!v.micError || busy}
                    aria-label={pushToTalk ? (recordingManual ? 'Send recording' : 'Start talking') : (v.micEnabled ? 'Mute microphone' : 'Unmute microphone')}
                    title={pushToTalk ? 'Push to talk' : (v.micEnabled ? 'Mute' : 'Unmute')}
                  >
                    <Icon name={v.micEnabled || pushToTalk ? 'mic' : 'micOff'} size={22} />
                  </button>
                  {v.state === 'speaking' && (
                    <button className="btn btn-secondary btn-sm" onClick={v.interrupt}><Icon name="stop" size={13} /> Stop speaking</button>
                  )}
                </div>
              </div>

              {v.micError && <div className="banner banner-warning"><Icon name="micOff" size={16} /><span>{v.micError}</span></div>}

              {s.pending && (
                <div className="confirm-card" role="alert">
                  <div>
                    <div className="confirm-title"><Icon name="shield" size={15} /> Confirmation required</div>
                    <div className="confirm-text">{s.pending.summary}</div>
                    <div className="muted small">Say “yes” or “no”, or use the buttons. Nothing changes until the customer confirms.</div>
                  </div>
                  <div className="confirm-actions">
                    <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => v.sendText('Yes, please go ahead.')}><Icon name="check" size={14} /> Confirm</button>
                    <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => v.sendText("No, don't do that.")}>Decline</button>
                  </div>
                </div>
              )}

              {v.messages.length === 0 ? (
                <div className="suggestions">
                  <span className="muted small">Try saying</span>
                  {SUGGESTIONS.map((q) => (
                    <button key={q} className="chip chip-suggest" onClick={() => v.sendText(q)} disabled={v.connection === 'connecting'}>“{q}”</button>
                  ))}
                </div>
              ) : (
                <Transcript messages={v.messages} state={v.state} />
              )}

              <form className="composer" onSubmit={submitText}>
                <Icon name="keyboard" size={16} className="muted" />
                <input
                  className="composer-input" value={text} onChange={(e) => setText(e.target.value)}
                  placeholder="Type instead of speaking (fallback)…" aria-label="Type a message" disabled={busy}
                />
                <button className="btn-icon btn-send" type="submit" disabled={!text.trim() || busy} aria-label="Send">
                  <Icon name="send" size={16} />
                </button>
              </form>
            </>
          )}

          <Summary summary={v.summary} onRestart={() => v.start(caller)} />
        </section>

        <aside className="voice-side">
          <div className="side-card">
            <h3>Session</h3>
            <dl className="kv">
              <dt>Customer</dt>
              <dd>{s.customer ? <><Icon name="shield" size={12} className="text-success" /> {s.customer.name} <span className="muted">({s.customer.customer_id})</span></> : <span className="muted">Not verified</span>}</dd>
              <dt>Active order</dt><dd>{s.activeOrder || <span className="muted">—</span>}</dd>
              <dt>Intent</dt>
              <dd>
                {s.intent ? (
                  <span className="intent">
                    {label(s.intent)}
                    {s.confidence != null && <span className="conf"><span style={{ width: `${Math.round(s.confidence * 100)}%` }} /></span>}
                  </span>
                ) : <span className="muted">—</span>}
              </dd>
              <dt>Status</dt>
              <dd>{s.human ? <Badge value="assigned">With {s.human.name}</Badge> : s.escalated ? <Badge value="escalated" /> : s.pending ? <Badge value="pending">Awaiting confirmation</Badge> : v.active ? <Badge value="active" /> : <span className="muted">—</span>}</dd>
              <dt>Engine</dt>
              <dd className="small">{s.provider || '—'}{v.capabilities && <span className="muted"> · STT {v.capabilities.stt} · TTS {v.capabilities.tts}</span>}</dd>
            </dl>
          </div>

          <div className="side-card">
            <h3>Tool executions</h3>
            {lastTools.length === 0 ? <p className="muted small">Backend actions the agent runs appear here.</p> : (
              <ul className="tool-list">
                {lastTools.map((t, i) => (
                  <li key={i}>
                    <span className={`tool-dot ${t.success ? 'ok' : 'fail'}`} />
                    <div>
                      <div className="tool-name">{TOOL_LABELS[t.tool_name] || t.tool_name}</div>
                      <div className="muted small">
                        {t.tool_name}({Object.values(t.arguments || {}).filter((x) => typeof x === 'string' && x.length < 24).join(', ')})
                        {' · '}{t.result?.status || t.result?.error_code || (t.success ? 'ok' : 'failed')} · {ms(t.execution_time_ms)}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="side-card">
            <h3>Knowledge sources</h3>
            {s.sources.length === 0 ? <p className="muted small">Retrieved policy passages appear here for knowledge questions.</p> : (
              <ul className="source-list">
                {s.sources.map((src) => (
                  <li key={src.chunk_id} title={src.excerpt}>
                    <Icon name="file" size={13} />
                    <div><div>{src.document_title}</div><div className="muted small">{src.section} · relevance {(src.score * 100).toFixed(0)}%</div></div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {s.timings && (
            <div className="side-card">
              <h3>Last turn latency</h3>
              <div className="latency">
                <div><span className="muted small">STT</span><strong>{ms(s.timings.stt_ms)}</strong></div>
                <div><span className="muted small">Agent</span><strong>{ms(s.timings.agent_ms)}</strong></div>
                <div><span className="muted small">TTS</span><strong>{ms(s.timings.tts_ms)}</strong></div>
              </div>
            </div>
          )}

          <div className="side-card">
            <button className="side-toggle" onClick={() => setShowLog((x) => !x)} aria-expanded={showLog}>
              <h3>Real-time events</h3><span className="muted small">{v.events.length} {showLog ? '▴' : '▾'}</span>
            </button>
            {showLog && (
              <ul className="event-log">
                {v.events.slice().reverse().map((e, i) => (
                  <li key={i}><span className="muted">{e.at}</span> <code>{e.type}</code> {e.detail && <span className="muted">{String(e.detail).slice(0, 40)}</span>}</li>
                ))}
              </ul>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
