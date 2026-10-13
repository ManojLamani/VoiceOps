import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import Icon from '../components/Icon';

export default function RegisterPage() {
  const [form, setForm] = useState({ fullName: '', email: '', password: '' });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { register } = useAuth();
  const navigate = useNavigate();
  const update = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    if (form.password.length < 8) { setError('Password must be at least 8 characters.'); return; }
    setError('');
    setLoading(true);
    try {
      await register(form.email, form.password, form.fullName);
      navigate('/', { replace: true });
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
          <h1>Create your account</h1>
          <p>Read-only demo access. Customer contact details are masked.</p>
        </div>
        {error && <div className="banner banner-danger" role="alert"><Icon name="alert" size={16} /><span>{error}</span></div>}
        <form onSubmit={submit}>
          <div className="form-group">
            <label className="form-label" htmlFor="name">Full name</label>
            <input id="name" className="form-input" value={form.fullName} onChange={update('fullName')} autoComplete="name" required />
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor="email">Work email</label>
            <input id="email" type="email" className="form-input" value={form.email} onChange={update('email')} autoComplete="email" required />
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor="password">Password</label>
            <input id="password" type="password" className="form-input" value={form.password} onChange={update('password')}
              autoComplete="new-password" minLength={8} required />
            <div className="form-hint">At least 8 characters.</div>
          </div>
          <button type="submit" className="btn btn-primary btn-block" disabled={loading}>
            {loading ? 'Creating account…' : 'Create account'}
          </button>
        </form>
        <div className="auth-footer">Already have an account? <Link to="/login">Sign in</Link></div>
      </div>
    </div>
  );
}
