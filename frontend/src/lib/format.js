export const label = (value) =>
  (value || '').replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());

// The API returns naive UTC timestamps; treat them as UTC.
export const parseDate = (iso) => new Date(/[zZ]$|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);

export const dateTime = (iso) => (iso ? parseDate(iso).toLocaleString([], {
  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
}) : '—');

export const date = (iso) => (iso ? parseDate(iso).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }) : '—');

export const time = (iso) => (iso ? parseDate(iso).toLocaleTimeString([], {
  hour: '2-digit', minute: '2-digit', second: '2-digit',
}) : '');

export const duration = (seconds) => {
  if (seconds == null) return '—';
  const s = Math.round(seconds);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
};

export const ms = (value) => (value == null ? '—' : value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${Math.round(value)}ms`);

export const money = (value) => (value == null ? '—' : `$${Number(value).toFixed(2)}`);

const STATUS_TONES = {
  completed: 'success', resolved: 'success', delivered: 'success', active: 'info', open: 'info', paid: 'neutral',
  escalated: 'danger', cancelled: 'neutral', suspended: 'danger', closed: 'neutral', abandoned: 'neutral',
  unresolved: 'warning', pending: 'warning', confirmed: 'info', processing: 'warning', shipped: 'info',
  in_transit: 'info', return_requested: 'warning', returned: 'neutral', refunded: 'neutral', refund_pending: 'warning',
  in_progress: 'warning', waiting_customer: 'warning', assigned: 'info',
  urgent: 'danger', high: 'warning', medium: 'info', low: 'neutral',
  positive: 'success', neutral: 'neutral', negative: 'danger',
};

export const tone = (status) => STATUS_TONES[status] || 'neutral';

export const TOOL_LABELS = {
  get_customer: 'Verifying account',
  update_customer: 'Updating account',
  get_order: 'Checking order',
  get_order_status: 'Checking order status',
  get_delivery_estimate: 'Checking delivery',
  cancel_order: 'Cancelling order',
  create_return_request: 'Creating return',
  create_support_ticket: 'Creating ticket',
  get_ticket: 'Looking up ticket',
  update_ticket: 'Updating ticket',
  escalate_to_human: 'Escalating to human',
};
