import { useState } from 'react';
import api from '../api/api';
import Icon from '../components/Icon';
import { useAuth } from '../context/AuthContext';
import { PageHeader, Spinner, ErrorBanner, Badge } from '../components/ui';
import useAsync from '../lib/useAsync';
import { dateTime, label } from '../lib/format';

export default function KnowledgePage() {
  const { user } = useAuth();
  const { data: docs, loading, error, reload } = useAsync(() => api.getKnowledgeDocs());
  const [query, setQuery] = useState('');
  const [results, setResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [message, setMessage] = useState(null);
  const [open, setOpen] = useState(null);

  const search = async (e) => {
    e.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    try { setResults(await api.searchKnowledge(query, 5)); } catch (err) { setMessage({ kind: 'danger', text: err.message }); }
    finally { setSearching(false); }
  };

  const reindex = async () => {
    setMessage(null);
    try {
      const r = await api.ingestKnowledge();
      setMessage({ kind: 'success', text: `Indexed ${r.documents} documents into ${r.chunks} chunks (${r.backend} index).` });
      reload();
    } catch (err) { setMessage({ kind: 'danger', text: err.message }); }
  };

  const chunks = docs?.reduce((n, d) => n + d.chunk_count, 0) || 0;

  return (
    <div>
      <PageHeader title="Knowledge base" subtitle={docs ? `${docs.length} documents · ${chunks} indexed chunks · all-MiniLM-L6-v2 embeddings` : 'RAG source documents'}
        actions={user?.role === 'admin' && <button className="btn btn-secondary btn-sm" onClick={reindex}><Icon name="refresh" size={14} /> Re-index</button>} />
      {message && <div className={`banner banner-${message.kind}`}><span>{message.text}</span></div>}

      <div className="card">
        <h3 className="card-title">Test retrieval</h3>
        <p className="muted small">Runs the same semantic search the agent uses. Only results above the relevance threshold are given to the agent.</p>
        <form className="search-row" onSubmit={search}>
          <div className="search-input grow">
            <Icon name="search" size={15} />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. How long do refunds take?" aria-label="Search knowledge" />
          </div>
          <button className="btn btn-primary btn-sm" disabled={searching}>{searching ? 'Searching…' : 'Search'}</button>
        </form>
        {results && (
          <ul className="results">
            {results.results.map((r) => (
              <li key={r.chunk_id} className={r.relevant ? '' : 'result-dim'}>
                <div className="result-head">
                  <strong>{r.document_title}</strong><span className="muted"> › {r.section}</span>
                  <span className="score">{(r.score * 100).toFixed(0)}%</span>
                  {r.relevant ? <Badge kind="success">Used by agent</Badge> : <Badge kind="neutral">Below threshold</Badge>}
                </div>
                <p className="result-text">{r.content.split('\n').slice(1).join(' ')}</p>
              </li>
            ))}
          </ul>
        )}
      </div>

      <ErrorBanner error={error} onRetry={reload} />
      <div className="card card-flush">
        {loading ? <Spinner /> : (
          <ul className="doc-list">
            {docs.map((d) => (
              <li key={d.id}>
                <button className="doc-row" onClick={() => setOpen(open === d.id ? null : d.id)} aria-expanded={open === d.id}>
                  <Icon name="file" size={16} />
                  <span className="strong">{d.title}</span>
                  <span className="chip chip-muted">{label(d.category)}</span>
                  <span className="muted small grow-right">{d.chunk_count} chunks · {d.source} · indexed {dateTime(d.last_indexed_at)}</span>
                </button>
                {open === d.id && <pre className="doc-content">{d.content}</pre>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
