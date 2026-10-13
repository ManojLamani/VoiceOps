import Icon from './Icon';
import { label, tone } from '../lib/format';

export function PageHeader({ title, subtitle, actions }) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}

export function Badge({ value, kind, children }) {
  if (!value && !children) return <span className="muted">—</span>;
  return <span className={`badge badge-${kind || tone(value)}`}>{children || label(value)}</span>;
}

export function Spinner({ label: text = 'Loading…' }) {
  return <div className="loading" role="status"><div className="spinner" /><span>{text}</span></div>;
}

export function EmptyState({ icon = 'chat', title, children }) {
  return (
    <div className="empty-state">
      <div className="empty-icon"><Icon name={icon} size={22} /></div>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
    </div>
  );
}

export function ErrorBanner({ error, onRetry }) {
  if (!error) return null;
  return (
    <div className="banner banner-danger" role="alert">
      <Icon name="alert" size={16} />
      <span>{error}</span>
      {onRetry && <button className="btn btn-ghost btn-sm" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function StatCard({ label: title, value, suffix, hint, icon }) {
  return (
    <div className="stat-card">
      <div className="stat-top">
        <span className="stat-label">{title}</span>
        {icon && <Icon name={icon} size={16} className="stat-icon" />}
      </div>
      <div className="stat-value">{value}{suffix && <span className="stat-suffix">{suffix}</span>}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

