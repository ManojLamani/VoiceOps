import { useState } from 'react';
import api from '../api/api';
import { PageHeader, Badge, Spinner, ErrorBanner, EmptyState } from '../components/ui';
import useAsync from '../lib/useAsync';
import { date, money } from '../lib/format';

const STATUSES = ['', 'pending', 'confirmed', 'processing', 'shipped', 'in_transit', 'delivered', 'return_requested', 'cancelled'];

export default function OrdersPage() {
  const [status, setStatus] = useState('');
  const { data, loading, error, reload } = useAsync(() => api.getOrders(status), [status]);

  return (
    <div>
      <PageHeader title="Orders" subtitle="Order data the agent queries and updates through validated tools." />
      <div className="toolbar">
        <label className="form-label inline" htmlFor="status">Status</label>
        <select id="status" className="form-input select-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
          {STATUSES.map((s) => <option key={s} value={s}>{s ? s.replace('_', ' ') : 'All statuses'}</option>)}
        </select>
      </div>
      <ErrorBanner error={error} onRetry={reload} />
      <div className="card card-flush">
        {loading ? <Spinner /> : !data?.length ? <EmptyState icon="box" title="No orders match" /> : (
          <div className="table-container">
            <table>
              <thead><tr><th>Order</th><th>Customer</th><th>Items</th><th>Total</th><th>Ordered</th><th>Delivery</th><th>Status</th><th>Payment</th><th>Can cancel</th><th>Can return</th></tr></thead>
              <tbody>
                {data.map((o) => (
                  <tr key={o.id} title={o.notes || ''}>
                    <td className="mono">{o.order_id}</td>
                    <td className="nowrap">{o.customer_name} <span className="muted">{o.customer_code}</span></td>
                    <td>{o.products.map((p) => `${p.quantity}× ${p.name}`).join(', ')}</td>
                    <td className="nowrap">{money(o.total_amount)}</td>
                    <td className="nowrap muted">{date(o.order_date)}</td>
                    <td className="nowrap">{o.actual_delivery ? `Delivered ${date(o.actual_delivery)}` : o.estimated_delivery ? `ETA ${date(o.estimated_delivery)}` : '—'}</td>
                    <td><Badge value={o.status} /></td>
                    <td><Badge value={o.payment_status} /></td>
                    <td>{o.can_cancel ? <Badge kind="success">Yes</Badge> : <span className="muted">No</span>}</td>
                    <td>{o.can_return ? <Badge kind="success">Yes</Badge> : <span className="muted">No</span>}</td>
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
