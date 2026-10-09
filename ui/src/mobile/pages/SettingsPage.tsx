import { useEffect, useState } from 'react';
import { KeyRound, LogOut, MessageCircle, Users } from 'lucide-react';
import type { Session } from '../../deployment';
import { makeApi } from '../../shared/api';
import { LineSettings, PasswordChange, UserManager } from '../../shared/components/AccountDialogs';
import { errorMessage } from '../../shared/format';
import styles from '../mobile.module.css';

export default function SettingsPage({ api, session, saveSession, onMessage }: {
  api: ReturnType<typeof makeApi>; session: Session; saveSession: (session: Session) => void; onMessage: (message: string) => void;
}) {
  const [user, setUser] = useState<{ login_id: string; role: string } | null>(null);
  const [multiUser, setMultiUser] = useState(false);
  const [dialog, setDialog] = useState<'password' | 'line' | 'users' | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    api.get('/api/auth/me', controller.signal).then(result => {
      if (!controller.signal.aborted) { setUser(result.user); setMultiUser(Boolean(result.multi_user_enabled)); }
    }).catch(error => { if (!controller.signal.aborted) onMessage(errorMessage(error)); });
    return () => controller.abort();
  }, [api, onMessage]);
  const logout = () => saveSession({ apiBase: session.apiBase, token: '' });
  return <section className={styles.page} aria-label="設定">
    <h2>設定</h2>
    {user && <p className={styles.account}>{user.login_id}<small>{user.role === 'admin' ? '管理者' : '一般ユーザー'}</small></p>}
    <div className={styles.settingsList}>
      <button type="button" onClick={() => setDialog('password')}><KeyRound />パスワード変更</button>
      <button type="button" onClick={() => setDialog('line')}><MessageCircle />LINE設定</button>
      {user?.role === 'admin' && multiUser && <button type="button" onClick={() => setDialog('users')}><Users />利用者管理</button>}
      <button type="button" disabled={loggingOut} onClick={async () => {
        setLoggingOut(true);
        try { await api.post('/api/auth/logout', {}); } catch { /* Clear the local session even if the server is unavailable. */ }
        finally { logout(); }
      }}><LogOut />ログアウト</button>
    </div>
    {dialog === 'password' && <PasswordChange api={api} onClose={() => setDialog(null)} onChanged={logout} />}
    {dialog === 'line' && <LineSettings api={api} onClose={() => setDialog(null)} />}
    {dialog === 'users' && <UserManager api={api} onClose={() => setDialog(null)} />}
  </section>;
}
