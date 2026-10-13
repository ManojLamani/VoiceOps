import { useState } from 'react';
import api from '../api/api';
import Icon from '../components/Icon';
import { PageHeader, Badge, Spinner, ErrorBanner } from '../components/ui';
import useAsync from '../lib/useAsync';
import { date, money, label } from '../lib/format';

function CustomerOrders({ customerId }) {
  const { data, loading, error } = useAsync(() => api.getCustomerOrders(customerId), [customerId]);
  if (loading) return <Spinner label="Loading orders…" />;
  if (error) return <ErrorBanner error={error} />;
  if (!data.length) return <p className="muted small">No orders.</p>;
  return (
    <ul className="mini-orders">
      {data.map((o) => (
        <li key={o.order_id}>
          <div><strong>{o.order_id}</strong> <span className="muted">· {o.products.map((p) => p.name).join(', ')}</span></div>
          <div className="mini-order-meta"><Badge value={o.status} /><span>{money(o.total_amount)}</span><span className="muted">{date(o.order_date)}</span></div>
        </li>
      ))}
    </ul>
  );
}

export default function CustomersPage() {
  const { data, loading, error, reload } = useAsync(() => api.getCustomers());
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(null);

  const customers = (data || []).filter((c) =>
    `${c.customer_id} ${c.name} ${c.email} ${c.phone}`.toLowerCase().includes(query.toLowerCase()));

  return (
    <div>
      <PageHeader title="Customers" subtitle={data ? `${data.length} customers` : 'Customer accounts'} />
      <div className="toolbar">
        <div className="search-input">
          <Icon name="search" size={15} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search name, email, phone or ID" aria-label="Search customers" />
        </div>
      </div>
      <ErrorBanner error={error} onRetry={reload} />
      <div className="split">
        <div className="card card-flush">
          {loading ? <Spinner /> : (
            <div className="table-container">
              <table>
                <thead><tr><th>ID</th><th>Name</th><th>Email</th><th>Phone</th><th>Status</th><th>Customer since</th></tr></thead>
                <tbody>
                  {customers.map((c) => (
                    <tr key={c.id} className={`clickable ${selected?.id === c.id ? 'row-selected' : ''}`} onClick={() => setSelected(c)}>
                      <td className="mono">{c.customer_id}</td>
                      <td className="strong">{c.name}</td>
                      <td>{c.email}</td>
                      <td className="nowrap">{c.phone || '—'}</td>
                      <td><Badge value={c.account_status} /></td>
                      <td className="muted">{date(c.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        {selected && (
          <aside className="card detail-drawer">
            <div className="card-header">
              <h3 className="card-title">{selected.name}</h3>
              <button className="btn-icon" onClick={() => setSelected(null)} aria-label="Close"><Icon name="x" size={14} /></button>
            </div>
            <dl className="kv">
              <dt>Customer ID</dt><dd className="mono">{selected.customer_id}</dd>
              <dt>Email</dt><dd>{selected.email}</dd>
              <dt>Phone</dt><dd>{selected.phone}</dd>
              <dt>Address</dt><dd>{selected.address}</dd>
              <dt>Account</dt><dd>{label(selected.account_status)}</dd>
            </dl>
            <h4 className="subhead">Orders</h4>
            <CustomerOrders customerId={selected.customer_id} />
          </aside>
        )}
      </div>
    </div>
  );
}
