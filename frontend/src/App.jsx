import { lazy, Suspense, useState } from 'react';
import { BrowserRouter, Routes, Route, NavLink, Navigate, useNavigate, useLocation } from 'react-router-dom';
import { AuthProvider, useAuth } from './context/AuthContext';
import Icon from './components/Icon';
import { Spinner } from './components/ui';
import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const VoiceAgentPage = lazy(() => import('./pages/VoiceAgentPage'));
const ConversationsPage = lazy(() => import('./pages/ConversationsPage'));
const ConversationDetailPage = lazy(() => import('./pages/ConversationDetailPage'));
const CustomersPage = lazy(() => import('./pages/CustomersPage'));
const OrdersPage = lazy(() => import('./pages/OrdersPage'));
const TicketsPage = lazy(() => import('./pages/TicketsPage'));
const KnowledgePage = lazy(() => import('./pages/KnowledgePage'));
const AnalyticsPage = lazy(() => import('./pages/AnalyticsPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));

const NAV = [
  { section: 'Support', items: [
    { to: '/', icon: 'dashboard', label: 'Dashboard', end: true },
    { to: '/voice', icon: 'mic', label: 'Voice Agent' },
    { to: '/conversations', icon: 'chat', label: 'Conversations' },
  ] },
  { section: 'Operations', items: [
    { to: '/customers', icon: 'users', label: 'Customers' },
    { to: '/orders', icon: 'box', label: 'Orders' },
    { to: '/tickets', icon: 'ticket', label: 'Tickets' },
  ] },
  { section: 'Intelligence', items: [
    { to: '/knowledge', icon: 'book', label: 'Knowledge Base' },
    { to: '/analytics', icon: 'chart', label: 'Analytics' },
    { to: '/settings', icon: 'settings', label: 'Settings' },
  ] },
];

function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="center-screen"><Spinner /></div>;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return children;
}

function Brand() {
  return (
    <div className="brand">
      <div className="brand-mark"><Icon name="mic" size={16} strokeWidth={2.2} /></div>
      <span>VoiceOps</span>
    </div>
  );
}

function AppLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  return (
    <div className="app-layout">
      <header className="mobile-bar">
        <Brand />
        <button className="btn-icon" onClick={() => setOpen((o) => !o)} aria-label="Toggle navigation" aria-expanded={open}>
          <Icon name={open ? 'x' : 'menu'} />
        </button>
      </header>
      <aside className={`sidebar ${open ? 'open' : ''}`}>
        <div className="sidebar-header"><Brand /></div>
        <nav className="sidebar-nav" aria-label="Main">
          {NAV.map((group) => (
            <div key={group.section} className="nav-group">
              <div className="nav-section">{group.section}</div>
              {group.items.map((item) => (
                <NavLink key={item.to} to={item.to} end={item.end} onClick={() => setOpen(false)}
                  className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
                  <Icon name={item.icon} size={17} /> {item.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="user-chip">
            <div className="avatar">{user?.full_name?.[0] || 'U'}</div>
            <div className="user-meta">
              <div className="user-name">{user?.full_name}</div>
              <div className="user-role">{user?.role} · {user?.email}</div>
            </div>
          </div>
          <button className="nav-link" onClick={() => { logout(); navigate('/login'); }}>
            <Icon name="logout" size={17} /> Sign out
          </button>
        </div>
      </aside>
      {open && <div className="scrim" onClick={() => setOpen(false)} />}
      <main className="main-content">
        <Suspense fallback={<Spinner />}>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/voice" element={<VoiceAgentPage />} />
          <Route path="/conversations" element={<ConversationsPage />} />
          <Route path="/conversations/:id" element={<ConversationDetailPage />} />
          <Route path="/customers" element={<CustomersPage />} />
          <Route path="/orders" element={<OrdersPage />} />
          <Route path="/tickets" element={<TicketsPage />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </Suspense>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/*" element={<ProtectedRoute><AppLayout /></ProtectedRoute>} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
