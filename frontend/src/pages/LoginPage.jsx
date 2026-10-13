import { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import Icon from '../components/Icon';

export default function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const submit = async (e, creds) => {
    e?.preventDefault();
    setError('');
    setLoading(true);
    try {
      await login(creds?.email ?? email, creds?.password ?? password);
      navigate(location.state?.from || '/', { replace: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <div className="brand-mark brand-mark-lg"><Icon name="mic" size={22} strokeWidth={2.2} /></div>
          <h1>VoiceOps</h1>
          <p>AI voice agent for customer support</p>
        </div>
        {error && <div className="banner banner-danger" role="alert"><Icon name="alert" size={16} /><span>{error}</span></div>}
        <form onSubmit={submit}>
          <div className="form-group">
            <label className="form-label" htmlFor="email">Email</label>
            <input id="email" type="email" className="form-input" value={email} autoComplete="email"
              onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor="password">Password</label>
            <input id="password" type="password" className="form-input" value={password} autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)} required />
          </div>
          <button type="submit" className="btn btn-primary btn-block" disabled={loading}>
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
        <button className="btn btn-ghost btn-block demo-btn" disabled={loading}
          onClick={() => submit(null, { email: 'admin@voiceops.ai', password: 'admin123' })}>
          Use demo account (admin@voiceops.ai)
        </button>
        <div className="auth-footer">No account? <Link to="/register">Create one</Link></div>
      </div>
    </div>
  );
}
