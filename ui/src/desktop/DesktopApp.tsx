import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowDownUp,
  FileJson,
  Loader2,
  MapPin,
  MoreVertical,
  Printer,
  RefreshCcw,
  RotateCcw,
  RotateCw,
  Save,
  Trash2,
  Upload,
} from 'lucide-react';
import type { Session } from '../deployment';
import { mergeServerCard } from '../cardSync';
import type { Card, Contact, ContactPage, RuntimeVersions } from '../shared/types';
import { makeApi, HttpError } from '../shared/api';
import { fields, wideFieldKeys, rowBreakFieldKeys, terminalCardStatuses } from '../shared/cardFields';
import { formatDate, directionLabel, imagePathFor, openGoogleMaps, combinedOcrText, formatJson, errorMessage } from '../shared/format';
import { BrandTitle } from '../shared/components/BrandTitle';
import { Login } from '../shared/components/Login';
import { LineSettings, PasswordChange, UserManager } from '../shared/components/AccountDialogs';
import { TagsInput, TagList } from '../shared/components/Tags';
import { AuthedImage, ThumbImage } from '../shared/components/Images';
import { StatusBadge } from '../shared/components/StatusBadge';
import { CorrectionHistory } from '../shared/components/CorrectionHistory';
import { printCard } from './printCard';
import { SearchInput } from '../shared/components/SearchInput';

export default function DesktopApp({ session, saveSession }: { session: Session; saveSession: (next: Session) => void }) {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreFailed, setLoadMoreFailed] = useState(false);
  const [serverHasInProgress, setServerHasInProgress] = useState(false);
  const listDataRef = useRef({items: [] as Contact[], cursor: null as string | null, revision: '', criteria: ''});
  const [selectedContactId, setSelectedContactId] = useState<string>('');
  const [selectedCardId, setSelectedCardId] = useState<string>('');
  const [selectedDetail, setSelectedDetail] = useState<Card | undefined>();
  const [detailLoading, setDetailLoading] = useState(false);
  const [selectedContactDetail, setSelectedContactDetail] = useState<Contact | undefined>();
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [detailRefresh, setDetailRefresh] = useState(0);
  const [status, setStatus] = useState('');
  const [toast, setToast] = useState<{ id: number; text: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [runtimeVersions, setRuntimeVersions] = useState<RuntimeVersions | null>(null);
  const [currentUser, setCurrentUser] = useState<{ login_id: string; role: string; multiUserEnabled: boolean } | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [usersOpen, setUsersOpen] = useState(false);
  const [passwordChangeOpen, setPasswordChangeOpen] = useState(false);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const [listScrollTop, setListScrollTop] = useState(0);
  const [listViewportHeight, setListViewportHeight] = useState(0);
  const listRequestRef = useRef(0);
  const listControllerRef = useRef<AbortController | null>(null);
  const selectionRef = useRef({ contactId: selectedContactId, cardId: selectedCardId });
  selectionRef.current = { contactId: selectedContactId, cardId: selectedCardId };
  const listViewportRef = useRef<HTMLDivElement>(null);
  const accountMenuRef = useRef<HTMLDivElement>(null);
  const toastIdRef = useRef(0);

  const showToast = useCallback((text: string) => {
    toastIdRef.current += 1;
    setToast({ id: toastIdRef.current, text });
  }, []);

  const authed = session.token.trim().length > 0;
  const hasInProgressCards = serverHasInProgress || contacts.some((contact) => contact.has_in_progress ?? !terminalCardStatuses.has(contact.status));
  const selectedContact = selectedContactId ? contacts.find((contact) => contact.id === selectedContactId) : undefined;
  const selectedRevision = selectedContact?.revision || `${selectedContact?.status}:${selectedContact?.updated_at}`;
  const selected = selectedDetail?.id === selectedCardId ? selectedDetail : undefined;
  // A status filter can hide a processing historical card from the summary.
  // Keep that selected detail polling until its own processing has finished.
  const needsDetailPolling = Boolean(selected && !terminalCardStatuses.has(selected.status) && !selectedContact?.has_in_progress);
  const relatedCards = selectedContactDetail?.id === selectedContactId ? selectedContactDetail.cards || [] : [];
  const listRowHeight = 61;
  const listOverscan = 8;
  const firstVisibleContact = Math.max(0, Math.floor(listScrollTop / listRowHeight) - listOverscan);
  const visibleContactCount = Math.ceil(listViewportHeight / listRowHeight) + listOverscan * 2;
  const visibleContacts = contacts.slice(firstVisibleContact, firstVisibleContact + visibleContactCount);
  const trailingContacts = Math.max(0, contacts.length - firstVisibleContact - visibleContacts.length);

  const api = useMemo(() => makeApi(session), [session]);
  useEffect(() => () => api.dispose(), [api]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  const reload = useCallback(async () => {
    if (!authed) return;
    listControllerRef.current?.abort();
    const controller = new AbortController();
    listControllerRef.current = controller;
    const requestId = ++listRequestRef.current;
    const criteria = JSON.stringify([debouncedQuery, status]);
    const previous = listDataRef.current;
    const sameCriteria = previous.criteria === criteria;
    const targetCount = sameCriteria ? Math.max(50, previous.items.length) : 50;
    setLoading(true);
    setLoadingMore(false);
    setLoadMoreFailed(false);
    try {
      let data: ContactPage = {items: []};
      let items: Contact[] = [];
      // A card can finish OCR between pages. Restart a changed snapshot once.
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          const params = new URLSearchParams({limit: '50'});
          if (debouncedQuery) params.set('q', debouncedQuery);
          if (status) params.set('status', status);
          if (sameCriteria && previous.revision) params.set('known_revision', previous.revision);
          data = await api.get(`/api/contacts?${params}`, controller.signal);
          if (requestId !== listRequestRef.current) return;
          setServerHasInProgress(Boolean(data.has_in_progress));
          if (data.unchanged) return;
          items = data.items || [];
          while (items.length < targetCount && data.next_cursor) {
            params.delete('known_revision');
            params.set('cursor', data.next_cursor);
            data = await api.get(`/api/contacts?${params}`, controller.signal);
            items.push(...(data.items || []));
          }
          break;
        } catch (error) {
          if (!(error instanceof HttpError && error.status === 409 && attempt === 0)) throw error;
        }
      }
      if (requestId !== listRequestRef.current) return;
      const selection = selectionRef.current;
      let resolved: Contact | undefined;
      if (sameCriteria && selection.cardId && !items.some(item => item.id === selection.contactId)) {
        try {
          resolved = await api.get(`/api/contacts/${selection.cardId}`, controller.signal);
        } catch (error) {
          if (controller.signal.aborted) throw error;
        }
      }
      if (requestId !== listRequestRef.current) return;
      const cursor = data.next_cursor || null;
      listDataRef.current = {items, cursor, revision: data.revision || '', criteria};
      setContacts(items);
      setNextCursor(cursor);
      if (selectionRef.current.cardId === selection.cardId && selectionRef.current.contactId === selection.contactId) {
        const retained = sameCriteria
          ? items.find(item => item.id === selection.contactId || item.id === resolved?.id) || resolved
          : items.find(item => item.id === selection.contactId);
        const next = retained || items[0];
        setSelectedContactId(next?.id || '');
        setSelectedCardId(retained && selection.cardId ? selection.cardId : next?.representative_card_id || '');
      }
    } catch (error) {
      if (!controller.signal.aborted && requestId === listRequestRef.current) {
        setLoadMoreFailed(true);
        const text = errorMessage(error);
        showToast(text);
        if (text.includes('シングルユーザーモード')) saveSession({apiBase: session.apiBase, token: ''});
      }
    } finally {
      if (requestId === listRequestRef.current) {
        setLoading(false);
        listControllerRef.current = null;
      }
    }
  }, [api, authed, debouncedQuery, session.apiBase, showToast, status]);

  const loadMore = useCallback(async () => {
    const previous = listDataRef.current;
    if (!authed || !previous.cursor || listControllerRef.current
        || previous.criteria !== JSON.stringify([debouncedQuery, status])) return;
    const controller = new AbortController();
    listControllerRef.current = controller;
    const requestId = ++listRequestRef.current;
    setLoadingMore(true);
    setLoadMoreFailed(false);
    try {
      const params = new URLSearchParams({limit: '50', cursor: previous.cursor});
      if (debouncedQuery) params.set('q', debouncedQuery);
      if (status) params.set('status', status);
      const data = await api.get(`/api/contacts?${params}`, controller.signal);
      if (requestId !== listRequestRef.current) return;
      const items = [...previous.items, ...(data.items || [])];
      const cursor = data.next_cursor || null;
      listDataRef.current = {items, cursor, revision: data.revision || '', criteria: previous.criteria};
      setContacts(items);
      setNextCursor(cursor);
      setServerHasInProgress(Boolean(data.has_in_progress));
    } catch (error) {
      if (!controller.signal.aborted && requestId === listRequestRef.current) {
        if (error instanceof HttpError && error.status === 409) {
          await reload();
        } else {
          setLoadMoreFailed(true);
          showToast(errorMessage(error));
        }
      }
    } finally {
      if (requestId === listRequestRef.current) {
        setLoadingMore(false);
        listControllerRef.current = null;
      }
    }
  }, [api, authed, debouncedQuery, reload, showToast, status]);

  useEffect(() => {
    if (nextCursor && !loading && !loadingMore && !loadMoreFailed && listViewportHeight > 0
        && listScrollTop + listViewportHeight >= contacts.length * listRowHeight - listOverscan * listRowHeight) {
      void loadMore();
    }
  }, [contacts.length, listScrollTop, listViewportHeight, nextCursor, loading, loadingMore, loadMoreFailed, loadMore]);


  const refresh = useCallback(async () => {
    await reload();
    setDetailRefresh((value) => value + 1);
  }, [reload]);

  useEffect(() => {
    reload();
    return () => {
      listRequestRef.current += 1;
      listControllerRef.current?.abort();
      listControllerRef.current = null;
    };
  }, [reload]);

  useEffect(() => {
    if (!authed || (!hasInProgressCards && !needsDetailPolling)) return;
    const timer = window.setInterval(() => {
      if (!listControllerRef.current) {
        void reload();
        if (needsDetailPolling) setDetailRefresh((value) => value + 1);
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [authed, hasInProgressCards, needsDetailPolling, reload]);

  useEffect(() => {
    if (!authed || !selectedCardId) {
      setSelectedDetail(undefined);
      setDetailLoading(false);
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    setSelectedDetail((current) => current?.id === selectedCardId ? current : undefined);
    setDetailLoading(true);
    api.get(`/api/cards/${selectedCardId}`, controller.signal)
      .then((card) => {
        if (!cancelled) setSelectedDetail(card);
      })
      .catch((error) => {
        if (!cancelled && !controller.signal.aborted) showToast(errorMessage(error));
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [api, authed, selectedCardId, selectedRevision, detailRefresh, showToast]);

  useEffect(() => {
    if (!authed || !selectedContactId) {
      setSelectedContactDetail(undefined);
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    api.get(`/api/contacts/${selectedContactId}`, controller.signal)
      .then((contact) => {
        if (!cancelled) {
          setSelectedContactDetail(contact);
          setSelectedCardId((current) => contact.cards?.some((card: Card) => card.id === current) ? current : contact.representative_card_id);
        }
      })
      .catch((error) => {
        if (!cancelled && !controller.signal.aborted) showToast(errorMessage(error));
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [api, authed, selectedContactId, selectedRevision, detailRefresh, showToast]);

  useEffect(() => {
    if (!authed) return;
    const viewport = listViewportRef.current;
    if (!viewport) return;
    const updateHeight = () => setListViewportHeight(viewport.clientHeight);
    updateHeight();
    const observer = new ResizeObserver(updateHeight);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, [authed]);

  useEffect(() => {
    setListScrollTop(0);
    listViewportRef.current?.scrollTo({ top: 0 });
  }, [query, status]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 5_000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    setRuntimeVersions(null);
    if (!authed) {
      return;
    }
    let cancelled = false;
    api.get('/api/system/versions')
      .then((versions) => {
        if (!cancelled) setRuntimeVersions(versions);
      })
      .catch(() => {
        if (!cancelled) setRuntimeVersions(null);
      });
    return () => {
      cancelled = true;
    };
  }, [api, authed]);

  useEffect(() => {
    if (!authed) return;
    let active = true;
    api.get('/api/auth/me')
      .then((r) => { if (active) setCurrentUser({ ...r.user, multiUserEnabled: Boolean(r.multi_user_enabled) }); })
      .catch(() => { if (active) setCurrentUser(null); });
    return () => { active = false; };
  }, [api, authed]);

  useEffect(() => {
    if (!accountMenuOpen) return;
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!accountMenuRef.current?.contains(event.target as Node)) setAccountMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setAccountMenuOpen(false);
    };
    document.addEventListener('mousedown', closeOnOutsideClick);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [accountMenuOpen]);

  if (!authed) {
    return <Login onLoggedIn={saveSession} />;
  }

  return (
    <div className="appShell">
      <header className="topbar">
        <div>
          <BrandTitle apiVersion={runtimeVersions?.api?.version ?? null} />
          <p>{runtimeVersionLabel(runtimeVersions)}</p>
        </div>
        <div className="topActions">
          <UploadPanel api={api} onUploaded={refresh} onMessage={showToast} />
          <button className="iconButton" onClick={refresh} title="再読み込み">
            {loading ? <Loader2 className="spin" /> : <RefreshCcw />}
          </button>
          <div className="accountMenu" ref={accountMenuRef}>
            <button
              className="iconButton"
              onClick={() => setAccountMenuOpen((open) => !open)}
              title="メニュー"
              aria-label="メニュー"
              aria-expanded={accountMenuOpen}
            >
              <MoreVertical />
            </button>
            {accountMenuOpen && (
              <div className="accountMenuPanel" role="menu">
                <button role="menuitem" onClick={() => { setPasswordChangeOpen(true); setAccountMenuOpen(false); }}>パスワード変更</button>
                <button role="menuitem" onClick={() => { setSettingsOpen(true); setAccountMenuOpen(false); }}>LINE設定</button>
                {currentUser?.role === 'admin' && currentUser.multiUserEnabled && (
                  <button role="menuitem" onClick={() => { setUsersOpen(true); setAccountMenuOpen(false); }}>利用者管理</button>
                )}
                <div className="accountMenuDivider" />
                <button
                  role="menuitem"
                  onClick={() => {
                    setAccountMenuOpen(false);
                    api.post('/api/auth/logout', {})
                      .catch(() => undefined)
                      .finally(() => saveSession({ apiBase: session.apiBase, token: '' }));
                  }}
                >
                  ログアウト
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      <main className="workspace">
        <section className="leftPane">
          <div className="filterBar">
            <SearchInput value={query} onChange={setQuery} placeholder="検索" />
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">すべて</option>
              <option value="queued">queued</option>
              <option value="preparing">preparing</option>
              <option value="scanning">scanning</option>
              <option value="extracting">extracting</option>
              <option value="ready">ready</option>
              <option value="not_card">not_card</option>
              <option value="error">error</option>
            </select>
          </div>

          <div
            ref={listViewportRef}
            className="tableWrap"
            onScroll={(event) => setListScrollTop(event.currentTarget.scrollTop)}
          >
            <table>
              <thead>
                <tr>
                  <th>画像</th>
                  <th>状態</th>
                  <th>氏名</th>
                  <th>会社</th>
                  <th>タグ</th>
                  <th>登録日</th>
                </tr>
              </thead>
              <tbody>
                {firstVisibleContact > 0 && (
                  <tr className="virtualSpacer" aria-hidden="true">
                    <td colSpan={6} style={{ height: firstVisibleContact * listRowHeight }} />
                  </tr>
                )}
                {visibleContacts.map((contact) => (
                  <tr
                    key={contact.id}
                    className={selectedContactId === contact.id ? 'selected' : ''}
                    onClick={() => {
                      setSelectedContactId(contact.id);
                      setSelectedCardId(contact.representative_card_id);
                    }}
                  >
                    <td className="thumbCell"><ThumbImage api={api} cardId={contact.representative_card_id} version={contact.revision || contact.updated_at} /></td>
                    <td><StatusBadge status={contact.status} /></td>
                    <td>{contact.person_name || '-'}</td>
                    <td>{contact.company_name || '-'}</td>
                    <td><TagList tags={contact.tags} /></td>
                    <td>{formatDate(contact.created_at)}</td>
                  </tr>
                ))}
                {trailingContacts > 0 && (
                  <tr className="virtualSpacer" aria-hidden="true">
                    <td colSpan={6} style={{ height: trailingContacts * listRowHeight }} />
                  </tr>
                )}
                {nextCursor && (
                  <tr className="listLoadMore"><td colSpan={6}>
                    <button type="button" onClick={() => void loadMore()} disabled={loading || loadingMore}>
                      {loadingMore ? '読み込み中…' : loadMoreFailed ? '続きを再試行' : 'さらに表示'}
                    </button>
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="rightPane">
          {selected ? (
            <CardDetail
              api={api}
              card={selected}
              relatedCards={relatedCards}
              onSelectRelatedCard={setSelectedCardId}
              onChanged={refresh}
              onMessage={showToast}
            />
          ) : detailLoading ? (
            <div className="empty">名刺情報を読み込んでいます</div>
          ) : (
            <div className="empty">名刺がありません</div>
          )}
        </section>
      </main>
      {toast && (
        <div className="toastNotification" role="status" aria-live="polite">
          <span>{toast.text}</span>
          <button type="button" onClick={() => setToast(null)} aria-label="閉じる">×</button>
        </div>
      )}
      {passwordChangeOpen && (
        <PasswordChange
          api={api}
          onClose={() => setPasswordChangeOpen(false)}
          onChanged={() => saveSession({ apiBase: session.apiBase, token: '' })}
        />
      )}
      {settingsOpen && <LineSettings api={api} onClose={() => setSettingsOpen(false)} />}
      {usersOpen && <UserManager api={api} onClose={() => setUsersOpen(false)} />}
    </div>
  );
}

function runtimeVersionLabel(versions: RuntimeVersions | null) {
  if (!versions) return 'local LLM verification console';

  const provider = versions.llm?.provider || 'LLM';
  const model = versions.llm?.model || 'model unknown';
  const serverVersion = versions.llm?.server_version;
  const service = serverVersion ? `${provider} ${serverVersion}` : `${provider} unavailable`;
  const kana = versions.kana?.status === 'ok' ? versions.kana.model : `unavailable (${versions.kana?.status || 'unknown'})`;
  return `local LLM verification console · ${service} / ${model} · kana ${kana}`;
}

function UploadPanel({
  api,
  onUploaded,
  onMessage,
}: {
  api: ReturnType<typeof makeApi>;
  onUploaded: () => void;
  onMessage: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);

  async function uploadMany(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return;
    const files = Array.from(fileList);
    setBusy(true);
    const results: string[] = [];
    try {
      for (const file of files) {
        const form = new FormData();
        form.append('file', file);
        try {
          const result = await api.postForm('/api/cards/upload', form);
          const suffix = result.duplicate ? 'duplicate' : 'queued';
          results.push(`OK: ${file.name} (${suffix})`);
        } catch (error) {
          results.push(`NG: ${file.name}: ${errorMessage(error)}`);
        }
      }
      onMessage(results.join('\n'));
      onUploaded();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="uploadBand">
      <label className="fileButton">
        <Upload size={16} />
        名刺を追加
        <input
          type="file"
          accept="image/png,image/jpeg"
          multiple
          disabled={busy}
          onChange={(event) => {
            uploadMany(event.target.files);
            event.currentTarget.value = '';
          }}
        />
      </label>
      {busy && <Loader2 className="spin" size={18} />}
    </div>
  );
}

function CardDetail({
  api,
  card,
  relatedCards,
  onSelectRelatedCard,
  onChanged,
  onMessage,
}: {
  api: ReturnType<typeof makeApi>;
  card: Card;
  relatedCards: Card[];
  onSelectRelatedCard: (cardId: string) => void;
  onChanged: () => void;
  onMessage: (message: string) => void;
}) {
  const [draft, setDraft] = useState<Card>(card);
  const previousServerCard = useRef(card);
  const [imageMode, setImageMode] = useState<'processed' | 'original'>('processed');
  const [imageSide, setImageSide] = useState<'front' | 'back'>('front');
  const [direction, setDirection] = useState('auto');
  const [backBusy, setBackBusy] = useState(false);
  const [orientationBusy, setOrientationBusy] = useState(false);
  const [zoomPath, setZoomPath] = useState('');
  const [printBusy, setPrintBusy] = useState(false);
  const printController = useRef<AbortController | null>(null);

  useEffect(() => () => printController.current?.abort(), []);

  useEffect(() => {
    const previous = previousServerCard.current;
    setDraft((current) => mergeServerCard(current, previous, card, fields.map(([key]) => key)));
    previousServerCard.current = card;
  }, [card]);
  useEffect(() => {
    if (imageSide === 'back' && !card.back_original_image_path) {
      setImageSide('front');
    }
  }, [card.id, card.back_original_image_path, imageSide]);

  async function save() {
    const payload: Record<string, string | undefined> = {};
    fields.forEach(([key]) => {
      payload[key] = draft[key] as string | undefined;
    });
    try {
      const saved: Card = await api.patch(`/api/cards/${card.id}`, payload);
      setDraft((current) => mergeServerCard(current, draft, saved, fields.map(([key]) => key)));
      previousServerCard.current = saved;
      onMessage('保存しました');
      onChanged();
    } catch (error) {
      onMessage(errorMessage(error));
    }
  }

  async function run(path: string, success: string) {
    try {
      await api.post(path, {});
      onMessage(success);
      onChanged();
    } catch (error) {
      onMessage(errorMessage(error));
    }
  }

  async function print() {
    if (printController.current) return;
    const controller = new AbortController();
    printController.current = controller;
    setPrintBusy(true);
    try {
      await printCard(api, { ...draft }, controller.signal);
    } catch (error) {
      if (!controller.signal.aborted) onMessage(`印刷できませんでした: ${errorMessage(error)}`);
    } finally {
      if (!controller.signal.aborted) setPrintBusy(false);
      if (printController.current === controller) printController.current = null;
    }
  }

  async function uploadBack(fileList: FileList | null) {
    const file = fileList?.[0];
    if (!file) return;
    setBackBusy(true);
    try {
      const form = new FormData();
      form.append('file', file);
      await api.postForm(`/api/cards/${card.id}/back/upload?direction=auto`, form);
      onMessage('裏面の処理を開始しました');
      setImageSide('back');
      onChanged();
    } catch (error) {
      onMessage(errorMessage(error));
    } finally {
      setBackBusy(false);
    }
  }

  async function rotateImage(degrees: -90 | 90) {
    setOrientationBusy(true);
    try {
      await api.post(`/api/cards/${card.id}/rotate?side=${imageSide}&degrees=${degrees}`, {});
      onMessage(degrees === 90 ? '右90度回転しました' : '左90度回転しました');
      onChanged();
    } catch (error) {
      onMessage(errorMessage(error));
    } finally {
      setOrientationBusy(false);
    }
  }

  const hasBack = Boolean(card.back_original_image_path);
  const imagePath = imagePathFor(card, imageSide, imageMode);
  const historicalCards = relatedCards.filter((relatedCard) => relatedCard.id !== card.id);

  return (
    <div className="detail">
      <div className="detailHeader">
        <div>
          <h2>{card.person_name || card.company_name || card.id.slice(0, 8)}</h2>
          <div className="meta">
            <StatusBadge status={card.status} />
            <span>読み順 {directionLabel(card.ocr_direction)}</span>
            <span>OCR 表 {card.ocr_duration_ms ?? '-'} ms</span>
            {card.back_original_image_path && <span>裏 {card.back_ocr_duration_ms ?? '-'} ms</span>}
            <span>LLM {card.extraction_duration_ms ?? '-'} ms</span>
          </div>
        </div>
        <div className="detailActions">
          <button className="primaryButton detailActionButton detailSaveButton" onClick={save} title="保存">
            <Save />
            保存
          </button>
          <button
            className="dangerButton detailActionButton detailDeleteButton"
            onClick={async () => {
              if (!window.confirm('この名刺を削除しますか？')) return;
              try {
                await api.delete(`/api/cards/${card.id}`);
                onMessage('削除しました');
                onChanged();
              } catch (error) {
                onMessage(errorMessage(error));
              }
            }}
            title="削除"
          >
            <Trash2 />
            削除
          </button>
          <button
            type="button"
            className="textButton detailActionButton detailPrintButton"
            onClick={print}
            disabled={printBusy}
            aria-busy={printBusy}
            title="印刷"
          >
            {printBusy ? <Loader2 className="spin" /> : <Printer />}
            印刷
          </button>
        </div>
      </div>

      {card.error_message && <div className="errorBox">{card.error_message}</div>}

      <div className="detailGrid">
        <div className="imagePanel">
          <div className="imageTabs">
            <button
              className={imageSide === 'front' ? 'active' : ''}
              onClick={() => setImageSide('front')}
            >
              表
            </button>
            <button
              className={imageSide === 'back' ? 'active' : ''}
              onClick={() => setImageSide('back')}
              disabled={!hasBack}
            >
              裏
            </button>
            <label className="miniFileButton">
              {backBusy ? <Loader2 className="spin" size={14} /> : <Upload size={14} />}
              裏面も追加
              <input
                type="file"
                accept="image/png,image/jpeg"
                disabled={backBusy}
                onChange={(event) => {
                  uploadBack(event.target.files);
                  event.currentTarget.value = '';
                }}
              />
            </label>
          </div>
          <AuthedImage api={api} path={imagePath} onClick={() => setZoomPath(imagePath)} />
          <div className="imageTools">
            <button
              type="button"
              onClick={() => rotateImage(-90)}
              disabled={orientationBusy}
              title="左90度回転"
            >
              <RotateCcw size={15} />
              左90度回転
            </button>
            <button
              type="button"
              onClick={() => rotateImage(90)}
              disabled={orientationBusy}
              title="右90度回転"
            >
              <RotateCw size={15} />
              右90度回転
            </button>
            {orientationBusy && <Loader2 className="spin" size={16} />}
          </div>
          <label className="imageTagEditor">
            タグ
            <TagsInput
              value={draft.tags || ''}
              onChange={(value) => setDraft({ ...draft, tags: value })}
            />
          </label>
        </div>

        <div className="formPanel">
          {fields.filter(([key]) => key !== 'tags').map(([key, label]) => {
            if (key === 'address') {
              const address = (draft.address || '').trim();
              return (
                <div key={key} className="fieldGroup fieldWide">
                  <div className="fieldLabelRow">
                    <span>{label}</span>
                    <button
                      type="button"
                      className="mapButton"
                      disabled={!address}
                      title="Googleマップで表示"
                      onClick={() => openGoogleMaps(address)}
                    >
                      <MapPin size={14} />
                      地図
                    </button>
                  </div>
                  <input
                    value={draft.address || ''}
                    onChange={(e) => setDraft({ ...draft, address: e.target.value })}
                  />
                </div>
              );
            }
            const className = [
              wideFieldKeys.has(key) ? 'fieldWide' : '',
              rowBreakFieldKeys.has(key) ? 'fieldRowBreak' : '',
            ].filter(Boolean).join(' ') || undefined;
            return (
              <label key={key} className={className}>
                {label}
                {key === 'memo' ? (
                  <textarea
                    value={(draft[key] as string) || ''}
                    onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
                  />
                ) : (
                  <input
                    value={(draft[key] as string) || ''}
                    onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
                  />
                )}
              </label>
            );
          })}
        </div>
      </div>

      {historicalCards.length > 0 && (
        <section className="contactHistory" aria-label="同一人物の名刺">
          <div className="contactHistoryHeader">
            <h3>以前の名刺</h3>
            <span>{relatedCards.length}枚</span>
          </div>
          <div className="contactHistoryList">
            {historicalCards.map((relatedCard) => (
              <button
                key={relatedCard.id}
                type="button"
                className="contactHistoryCard"
                onClick={() => onSelectRelatedCard(relatedCard.id)}
              >
                <span>{formatDate(relatedCard.created_at)}</span>
                <strong>{relatedCard.company_name || '会社名未設定'}</strong>
                <small>{[relatedCard.department, relatedCard.title].filter(Boolean).join(' / ') || '名刺を表示'}</small>
              </button>
            ))}
          </div>
        </section>
      )}

      <div className="processBar">
        <select value={direction} onChange={(e) => setDirection(e.target.value)}>
          <option value="auto">自動判定</option>
          <option value="horizontal">横書き</option>
          <option value="vertical">縦書き</option>
        </select>
        <button
          className="textButton"
          onClick={() => run(`/api/cards/${card.id}/reprocess?direction=${direction}`, '再処理を開始しました')}
        >
          <RefreshCcw size={16} />
          再スキャン
        </button>
        <button
          className="textButton"
          onClick={() => run(`/api/cards/${card.id}/reextract`, '再抽出を開始しました')}
        >
          <ArrowDownUp size={16} />
          再抽出
        </button>
      </div>

      <div className="rawGrid">
        <section>
          <h3>OCR Text</h3>
          <pre>{combinedOcrText(card)}</pre>
        </section>
        <section>
          <h3><FileJson size={16} /> Extracted JSON</h3>
          <pre>{formatJson(card.extracted_json)}</pre>
        </section>
      </div>
      <CorrectionHistory api={api} cardId={card.id} revision={card.revision} />
      <div className="cardTimestamps">
        <span>登録日時: {formatDate(card.created_at)}</span>
        <span>最終更新日時: {formatDate(card.updated_at)}</span>
      </div>
      {zoomPath && (
        <ImageModal
          api={api}
          path={imagePath}
          title={`${card.person_name || card.company_name || '名刺'} ${imageSide === 'back' ? '裏' : '表'}`}
          imageMode={imageMode}
          onToggleMode={() => setImageMode(imageMode === 'processed' ? 'original' : 'processed')}
          onClose={() => setZoomPath('')}
        />
      )}
    </div>
  );
}

function ImageModal({
  api,
  path,
  title,
  imageMode,
  onToggleMode,
  onClose,
}: {
  api: ReturnType<typeof makeApi>;
  path: string;
  title: string;
  imageMode: 'processed' | 'original';
  onToggleMode: () => void;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  return (
    <div className="imageModalBackdrop" onClick={onClose}>
      <div className="imageModal" onClick={(event) => event.stopPropagation()}>
        <div className="imageModalHeader">
          <span>{title}</span>
          <div className="imageModalActions">
            <button
              type="button"
              className="imageModeButton"
              onClick={onToggleMode}
              title={imageMode === 'processed' ? '補正画像を表示中' : '元画像を表示中'}
            >
              補正画像 ↔ 元画像
            </button>
            <button type="button" onClick={onClose} title="閉じる">×</button>
          </div>
        </div>
        <AuthedImage api={api} path={path} />
      </div>
    </div>
  );
}
