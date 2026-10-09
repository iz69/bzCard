import React, { useCallback, useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { makeApi } from '../api';
import { errorMessage } from '../format';

export function LineSettings({ api, onClose }: { api: ReturnType<typeof makeApi>; onClose: () => void }) {
  const [form, setForm] = useState({ channel_secret: '', access_token: '', line_login_channel_id: '', liff_url: '' });
  const [secretsConfigured, setSecretsConfigured] = useState(false);
  const [message, setMessage] = useState(''); const [link, setLink] = useState('');
  useEffect(() => { api.get('/api/line-connections/me').then((r) => { if (r.connection) { setSecretsConfigured(Boolean(r.connection.configured)); setForm((f) => ({ ...f, line_login_channel_id: r.connection.line_login_channel_id || '', liff_url: r.connection.liff_url || '' })); } }).catch((e) => setMessage(errorMessage(e))); }, [api]);
  async function save(e: React.FormEvent) { e.preventDefault(); try { await api.put('/api/line-connections/me', form); setMessage('保存しました。LINE IDの紐付けURLを発行してください。'); } catch (e) { setMessage(errorMessage(e)); } }
  async function createLink() { try { const r = await api.post('/api/line-connections/me/link-url', {}); setLink(r.url); } catch (e) { setMessage(errorMessage(e)); } }
  return <div className="imageModalBackdrop"><form className="imageModal compactModal" onSubmit={save}><div className="imageModalHeader"><b>自分の公式LINE設定</b><button type="button" onClick={onClose}>×</button></div><p>シークレットとトークンは暗号化して保存され、再表示されません。設定済みの場合は `********` と表示し、変更時だけ新しい値を入力します。</p><label>チャネルシークレット<input type="password" value={form.channel_secret} placeholder={secretsConfigured ? '********' : '未設定'} onChange={(e) => setForm({...form,channel_secret:e.target.value})}/></label><label>チャネルアクセストークン<input type="password" value={form.access_token} placeholder={secretsConfigured ? '********' : '未設定'} onChange={(e) => setForm({...form,access_token:e.target.value})}/></label><label>LINE LoginチャネルID<input value={form.line_login_channel_id} onChange={(e) => setForm({...form,line_login_channel_id:e.target.value})} required/></label><label>LIFF URL<input value={form.liff_url} onChange={(e) => setForm({...form,liff_url:e.target.value})} required/></label>{message && <div className="errorBox">{message}</div>}<button className="primaryButton">保存</button><button type="button" className="textButton" onClick={createLink}>LINE ID紐付けURLを発行</button>{link && <a href={link} target="_blank" rel="noreferrer">このURLをLINEアプリで開いて紐付ける</a>}</form></div>;
}

export function PasswordChange({
  api,
  onClose,
  onChanged,
}: {
  api: ReturnType<typeof makeApi>;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (newPassword !== confirmation) {
      setMessage('新しいパスワードが一致しません');
      return;
    }
    setBusy(true);
    setMessage('');
    try {
      await api.post('/api/auth/password', { current_password: currentPassword, new_password: newPassword });
      onClose();
      onChanged();
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return <div className="imageModalBackdrop"><form className="imageModal compactModal" onSubmit={submit}><div className="imageModalHeader"><b>パスワード変更</b><button type="button" onClick={onClose}>×</button></div><p>変更後は、すべての端末・LINE内画面を含む既存のログイン状態が無効になります。</p><label>現在のパスワード<input type="password" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} autoComplete="current-password" required /></label><label>新しいパスワード（12文字以上）<input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} autoComplete="new-password" minLength={12} required /></label><label>新しいパスワード（確認）<input type="password" value={confirmation} onChange={(e) => setConfirmation(e.target.value)} autoComplete="new-password" minLength={12} required /></label>{message && <div className="errorBox">{message}</div>}<button className="primaryButton" disabled={busy}>{busy ? <Loader2 className="spin" /> : null}変更してログアウト</button></form></div>;
}

export function UserManager({ api, onClose }: { api: ReturnType<typeof makeApi>; onClose: () => void }) {
  type ManagedUser = { id: string; login_id: string; role: string; status: string };
  const [items, setItems] = useState<ManagedUser[]>([]);
  const [loginId, setLoginId] = useState('');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ManagedUser | null>(null);
  const [confirmation, setConfirmation] = useState('');
  const reload = useCallback(async () => {
    const result = await api.get('/api/auth/users');
    setItems(result.items || []);
  }, [api]);
  useEffect(() => { void reload().catch((e) => setMessage(errorMessage(e))); }, [reload]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMessage('');
    try {
      await api.post('/api/auth/users', { login_id: loginId, password });
      setLoginId('');
      setPassword('');
      setMessage('一般ユーザーを作成しました');
      await reload();
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function changeStatus(user: ManagedUser) {
    const stopping = user.status === 'active';
    const action = stopping ? '停止' : '再開';
    if (!window.confirm(`「${user.login_id}」を${action}しますか？${stopping ? 'ログインと既存セッションが無効になります。データは残ります。' : ''}`)) return;
    setBusy(true);
    setMessage('');
    try {
      await api.post(`/api/auth/users/${encodeURIComponent(user.id)}/${stopping ? 'stop' : 'activate'}`, {});
      setMessage(`「${user.login_id}」を${action}しました`);
      await reload();
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function remove(e: React.FormEvent) {
    e.preventDefault();
    if (!deleteTarget || confirmation !== deleteTarget.login_id || busy) return;
    setBusy(true);
    setMessage('');
    try {
      await api.delete(`/api/auth/users/${encodeURIComponent(deleteTarget.id)}`, { confirm_login_id: confirmation });
      setMessage(`「${deleteTarget.login_id}」と所有データを削除しました`);
      setDeleteTarget(null);
      setConfirmation('');
      await reload();
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return <div className="imageModalBackdrop">
    <section className="imageModal compactModal" role="dialog" aria-modal="true" aria-labelledby="user-manager-title">
      <div className="imageModalHeader"><b id="user-manager-title">利用者管理</b><button type="button" disabled={busy} onClick={onClose} aria-label="閉じる">×</button></div>
      <p>停止するとログインできなくなり、既存セッションも無効になります。再開すると再ログインできます。管理者は停止・削除できません。</p>
      {message && <div className="errorBox" role="status">{message}</div>}
      {deleteTarget ? <form onSubmit={remove}>
        <b>「{deleteTarget.login_id}」を削除</b>
        <p>この利用者の名刺・画像・LINE連携設定・履歴を削除します。この操作は取り消せません。保存済みバックアップは対象外です。</p>
        <label>確認のためログインID「{deleteTarget.login_id}」を入力
          <input autoFocus value={confirmation} onChange={(e) => setConfirmation(e.target.value)} autoComplete="off" disabled={busy} required />
        </label>
        <div className="userManagerActions">
          <button type="button" className="textButton" disabled={busy} onClick={() => { setDeleteTarget(null); setConfirmation(''); }}>キャンセル</button>
          <button type="submit" className="dangerButton userDeleteButton" disabled={busy || confirmation !== deleteTarget.login_id}>{busy ? '削除中…' : 'データを含めて削除'}</button>
        </div>
      </form> : <>
        <form onSubmit={create}>
          <label>ログインID<input value={loginId} onChange={(e) => setLoginId(e.target.value)} maxLength={128} disabled={busy} required /></label>
          <label>初期パスワード（12文字以上）<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" minLength={12} disabled={busy} required /></label>
          <button className="primaryButton" disabled={busy}>一般ユーザーを追加</button>
        </form>
        <ul className="userManagerList">{items.map((user) => <li key={user.id}>
          <span>{user.login_id} — {user.role === 'admin' ? '管理者' : '一般'} / {user.status === 'active' ? '有効' : '停止中'}</span>
          {user.role !== 'admin' && <span className="userManagerActions">
            <button type="button" className="textButton" disabled={busy} onClick={() => changeStatus(user)}>{user.status === 'active' ? '停止' : '再開'}</button>
            <button type="button" className="dangerButton userDeleteButton" disabled={busy} onClick={() => { setDeleteTarget(user); setConfirmation(''); setMessage(''); }}>削除</button>
          </span>}
        </li>)}</ul>
      </>}
    </section>
  </div>;
}
