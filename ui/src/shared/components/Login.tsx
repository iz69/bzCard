import React, { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { apiUrl, normalizeApiBase, type Session } from '../../deployment';
import { loadSession } from '../config';
import { formatHttpError } from '../api';
import { errorMessage } from '../format';
import { BrandTitle } from './BrandTitle';

export function Login({ onLoggedIn }: { onLoggedIn: (session: Session) => void }) {
  const [apiBase, setApiBase] = useState(() => loadSession().apiBase || '/');
  const [loginId, setLoginId] = useState('');
  const [password, setPassword] = useState('');
  const [needsBootstrap, setNeedsBootstrap] = useState<boolean | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const base = normalizeApiBase(apiBase);
    fetch(apiUrl(base, '/api/auth/bootstrap-status'))
      .then(async (response) => {
        if (!response.ok) throw new Error(await response.text());
        return response.json();
      })
      .then((data) => {
        if (!cancelled) setNeedsBootstrap(Boolean(data.needs_bootstrap));
      })
      .catch(() => {
        if (!cancelled) setNeedsBootstrap(null);
      });
    return () => { cancelled = true; };
  }, [apiBase]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage('');
    const base = normalizeApiBase(apiBase);
    try {
      const response = await fetch(apiUrl(base, `/api/auth/${needsBootstrap ? 'bootstrap' : 'login'}`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ login_id: loginId, password }),
      });
      const text = await response.text();
      if (!response.ok) throw new Error(formatHttpError(response, text));
      const result = JSON.parse(text);
      onLoggedIn({ apiBase: base, token: result.session_token });
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <form onSubmit={submit}>
        <BrandTitle />
        <p>{needsBootstrap ? '最初の管理者アカウントを作成します。既存の名刺はこのアカウントに移行されます。' : 'ログインしてください。'}</p>
        <label>API URL<input value={apiBase} onChange={(event) => setApiBase(event.target.value)} required /></label>
        <label>ログインID<input value={loginId} onChange={(event) => setLoginId(event.target.value)} autoComplete="username" required /></label>
        <label>パスワード<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={needsBootstrap ? 'new-password' : 'current-password'} minLength={12} required /></label>
        {needsBootstrap && <small>パスワードは12文字以上にしてください。</small>}
        {message && <div className="errorBox">{message}</div>}
        <button className="primaryButton" disabled={busy || needsBootstrap === null}>
          {busy ? <Loader2 className="spin" /> : null}
          {needsBootstrap ? '管理者を作成して開始' : 'ログイン'}
        </button>
        {needsBootstrap === null && <small>API URLへ接続できません。URLを確認してください。</small>}
      </form>
    </main>
  );
}
