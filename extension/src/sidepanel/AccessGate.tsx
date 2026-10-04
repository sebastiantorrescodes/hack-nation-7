import { useEffect, useState } from 'react';
import { api } from '../lib/api';

type Role = 'expert' | 'trainee' | 'admin';
export default function AccessGate({children}: {children: (role: Role) => React.ReactNode}) {
  const [mode,setMode] = useState('');
  const [role,setRole] = useState<Role | null>(null);
  const [email,setEmail] = useState(''), [password,setPassword] = useState('');
  const [error,setError] = useState(''), [busy,setBusy] = useState(false);
  const load = async () => {
    try {
      const config = await api<{mode: string}>('/api/access');
      setMode(config.mode);
      if (config.mode==='local') setRole('admin');
      else { const saved = await chrome.storage.session.get(['accessToken','accessRole']); if (saved.accessToken) setRole(saved.accessRole as Role); }
    } catch(e) {setError(String(e));}
  };
  useEffect(() => {
    void load();
    const expired = () => {setRole(null); void chrome.storage.session.remove(['accessToken','accessRole']);};
    window.addEventListener('apprentice-sign-in',expired);
    return () => window.removeEventListener('apprentice-sign-in',expired);
  },[]);
  if (role) return <>{children(role)}{mode==='supabase' && <button onClick={() => {setRole(null); void chrome.storage.session.remove(['accessToken','accessRole']);}}>Sign out</button>}</>;
  return <main><h1>AI Apprentice</h1><p>Sign in to your team workspace.</p>
    {error && <p className="error">{error}</p>}
    {!mode ? <button onClick={() => void load()}>Reconnect</button> : <form onSubmit={async e => {
      e.preventDefault(); setBusy(true); setError('');
      try {
        const result = await api<{access_token: string; role: Role}>('/api/access/login',{body:{email,password}});
        await chrome.storage.session.set({accessToken:result.access_token,accessRole:result.role});
        setPassword(''); setRole(result.role);
      } catch(e) {setError(String(e));} finally {setBusy(false);}
    }}>
      <label>Email<input type="email" autoComplete="username" value={email} onChange={e => setEmail(e.target.value)} required /></label>
      <label>Password<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>
      <button className="primary" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
    </form>}
  </main>;
}
