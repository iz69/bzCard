import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, ContactRound, Loader2, Plus, RefreshCcw, Settings, X } from 'lucide-react';
import type { Session } from '../deployment';
import { makeApi } from '../shared/api';
import { uiBuildVersion } from '../shared/config';
import { buildVersionLabel } from '../shared/buildVersion';
import { Login } from '../shared/components/Login';
import ContactsPage from './pages/ContactsPage';
import CardDetailPage from './pages/CardDetailPage';
import UploadPage from './pages/UploadPage';
import SettingsPage from './pages/SettingsPage';
import { useContacts } from './useContacts';
import { useMobileNavigation } from './useMobileNavigation';
import styles from './mobile.module.css';

type Props = { session: Session; saveSession: (session: Session) => void };

export default function MobileApp(props: Props) {
  if (!props.session.token.trim()) return <div className={styles.root} data-ui="mobile"><Login onLoggedIn={props.saveSession} /></div>;
  return <MobileWorkspace {...props} />;
}

function MobileWorkspace({ session, saveSession }: Props) {
  const api = useMemo(() => makeApi(session), [session]);
  useEffect(() => () => api.dispose(), [api]);
  const list = useContacts(api);
  const { route, navigate, back, setDirty } = useMobileNavigation();
  const [toast, setToast] = useState<{ text: string; id: number } | null>(null);
  const showToast = useCallback((text: string) => setToast({ text, id: Date.now() }), []);
  const refresh = useCallback(() => { void list.reload(); }, [list.reload]);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 5000);
    return () => window.clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    if (list.error.includes('シングルユーザーモード')) saveSession({ apiBase: session.apiBase, token: '' });
  }, [list.error, saveSession, session.apiBase]);
  const selected = route.screen === 'detail' ? list.contacts.find(contact => contact.id === route.contactId) : undefined;

  return <div className={styles.root} data-ui="mobile">
    <header className={styles.header}>
      {route.screen === 'detail' ? <><button type="button" aria-label="一覧へ戻る" onClick={back}><ArrowLeft /></button><h1>名刺詳細</h1></>
        : <><h1>bzCard <small>{buildVersionLabel(uiBuildVersion)}</small></h1><button type="button" aria-label="再読み込み" onClick={refresh}>{list.loading ? <Loader2 className="spin" /> : <RefreshCcw />}</button></>}
    </header>
    <main className={styles.main}>
      <ContactsPage api={api} list={list} hidden={route.screen !== 'list'} onSelect={contact => navigate({ screen: 'detail', contactId: contact.id, cardId: contact.representative_card_id })} />
      {route.screen === 'detail' && <CardDetailPage key={route.cardId} api={api} contactId={route.contactId} cardId={route.cardId} revision={selected?.revision} onChanged={refresh} onMessage={showToast} onDirty={setDirty} onSelectCard={cardId => navigate({ screen: 'detail', contactId: route.contactId, cardId })} onDeleted={() => navigate({ screen: 'list' })} />}
      {route.screen === 'upload' && <UploadPage api={api} onUploaded={refresh} />}
      {route.screen === 'settings' && <SettingsPage api={api} session={session} saveSession={saveSession} onMessage={showToast} />}
    </main>
    {route.screen !== 'detail' && <nav className={styles.nav} aria-label="メインメニュー">
      <button type="button" aria-current={route.screen === 'list' ? 'page' : undefined} onClick={() => { if (route.screen !== 'list') navigate({ screen: 'list' }); }}><ContactRound /><span>一覧</span></button>
      <button type="button" aria-current={route.screen === 'upload' ? 'page' : undefined} onClick={() => { if (route.screen !== 'upload') navigate({ screen: 'upload' }); }}><Plus /><span>追加</span></button>
      <button type="button" aria-current={route.screen === 'settings' ? 'page' : undefined} onClick={() => { if (route.screen !== 'settings') navigate({ screen: 'settings' }); }}><Settings /><span>設定</span></button>
    </nav>}
    {toast && <div className={styles.toast} role="status" aria-live="polite"><span>{toast.text}</span><button type="button" aria-label="通知を閉じる" onClick={() => setToast(null)}><X /></button></div>}
  </div>;
}
