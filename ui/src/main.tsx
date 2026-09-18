import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  AlertCircle,
  ArrowDownUp,
  CheckCircle2,
  FileJson,
  Loader2,
  MapPin,
  MoreVertical,
  RefreshCcw,
  RotateCcw,
  RotateCw,
  Save,
  Search,
  ShieldCheck,
  Trash2,
  Upload,
} from 'lucide-react';
import './styles.css';

type Card = {
  id: string;
  status: string;
  ocr_direction?: string;
  thumbnail_path?: string;
  back_original_image_path?: string;
  back_processed_image_path?: string;
  back_thumbnail_path?: string;
  back_ocr_direction?: string;
  back_ocr_text?: string;
  back_ocr_duration_ms?: number;
  person_name?: string;
  person_name_kana?: string;
  company_name?: string;
  department?: string;
  title?: string;
  postal_code?: string;
  address?: string;
  tel?: string;
  mobile?: string;
  fax?: string;
  email?: string;
  website?: string;
  tags?: string;
  memo?: string;
  ocr_text?: string;
  extracted_json?: string;
  error_message?: string;
  ocr_duration_ms?: number;
  extraction_duration_ms?: number;
  created_at: string;
  updated_at: string;
};

type Session = {
  apiBase: string;
  token: string;
};

type RuntimeVersions = {
  ocr: {
    version?: string | null;
  };
  llm: {
    provider?: string;
    model?: string;
    status?: string;
    server_version?: string | null;
  };
};

type LiffProfile = {
  displayName?: string;
  pictureUrl?: string;
  userId?: string;
};

declare global {
  interface Window {
    liff?: {
      init: (options: { liffId: string }) => Promise<void>;
      isLoggedIn: () => boolean;
      login: (options?: { redirectUri?: string }) => void;
      getIDToken: () => string | null;
      getProfile: () => Promise<LiffProfile>;
      isInClient: () => boolean;
    };
  }
}

const defaultApiBase = import.meta.env.VITE_API_BASE_PATH || '/bzcard-api';
const fields: Array<[keyof Card, string]> = [
  ['person_name', '氏名'],
  ['person_name_kana', 'かな'],
  ['company_name', '会社'],
  ['department', '部署'],
  ['title', '役職'],
  ['postal_code', '郵便番号'],
  ['address', '住所'],
  ['mobile', '携帯'],
  ['tel', '電話'],
  ['fax', 'FAX'],
  ['email', 'メール'],
  ['website', 'Web'],
  ['tags', 'タグ'],
  ['memo', 'メモ'],
];
const wideFieldKeys = new Set<keyof Card>([
  'company_name',
  'address',
  'email',
  'website',
  'tags',
  'memo',
]);
const rowBreakFieldKeys = new Set<keyof Card>(['postal_code', 'mobile', 'tel']);
const terminalCardStatuses = new Set(['ready', 'not_card', 'error']);

function loadSession(): Session {
  return {
    apiBase: localStorage.getItem('bzcard.apiBase') || defaultApiBase,
    token: localStorage.getItem('bzcard.sessionToken') || '',
  };
}

function App() {
  if (isLiffRoute()) {
    return <LiffRegistration />;
  }

  const [session, setSession] = useState<Session>(loadSession);
  const [cards, setCards] = useState<Card[]>([]);
  const [selectedId, setSelectedId] = useState<string>('');
  const [selectedDetail, setSelectedDetail] = useState<Card | undefined>();
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const [toast, setToast] = useState<{ id: number; text: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [runtimeVersions, setRuntimeVersions] = useState<RuntimeVersions | null>(null);
  const [currentUser, setCurrentUser] = useState<{ login_id: string; role: string; multiUserEnabled: boolean } | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [usersOpen, setUsersOpen] = useState(false);
  const [passwordChangeOpen, setPasswordChangeOpen] = useState(false);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const listRequestRef = useRef(0);
  const accountMenuRef = useRef<HTMLDivElement>(null);
  const toastIdRef = useRef(0);

  const showToast = useCallback((text: string) => {
    toastIdRef.current += 1;
    setToast({ id: toastIdRef.current, text });
  }, []);

  const authed = session.token.trim().length > 0;
  const hasInProgressCards = cards.some((card) => !terminalCardStatuses.has(card.status));
  const selectedSummary = selectedId ? cards.find((card) => card.id === selectedId) : undefined;
  const selected = selectedDetail?.id === selectedId ? selectedDetail : selectedSummary;

  const api = useMemo(() => makeApi(session), [session]);

  const reload = useCallback(async () => {
    if (!authed) return;
    const requestId = listRequestRef.current + 1;
    listRequestRef.current = requestId;
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (query) params.set('q', query);
      if (status) params.set('status', status);
      const data = await api.get(`/api/cards?${params.toString()}`);
      if (requestId !== listRequestRef.current) return;
      const items = data.items || [];
      setCards(items);
      setSelectedId((current) => {
        if (current && items.some((card: Card) => card.id === current)) {
          return current;
        }
        return items[0]?.id || '';
      });
    } catch (error) {
      if (requestId === listRequestRef.current) {
        const text = errorMessage(error);
        showToast(text);
        if (text.includes('シングルユーザーモード')) {
          saveSession({ apiBase: session.apiBase, token: '' });
        }
      }
    } finally {
      if (requestId === listRequestRef.current) {
        setLoading(false);
      }
    }
  }, [api, authed, query, session.apiBase, showToast, status]);

  useEffect(() => {
    reload();
    return () => {
      listRequestRef.current += 1;
    };
  }, [reload]);

  useEffect(() => {
    if (!authed || !hasInProgressCards) return;
    const timer = window.setInterval(reload, 5000);
    return () => window.clearInterval(timer);
  }, [authed, hasInProgressCards, reload]);

  useEffect(() => {
    if (!authed || !selectedId) {
      setSelectedDetail(undefined);
      return;
    }
    let cancelled = false;
    api.get(`/api/cards/${selectedId}`)
      .then((card) => {
        if (!cancelled) setSelectedDetail(card);
      })
      .catch((error) => {
        if (!cancelled) showToast(errorMessage(error));
      });
    return () => {
      cancelled = true;
    };
  }, [api, authed, selectedId, selectedSummary?.updated_at, showToast]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 5_000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    if (!authed) {
      setRuntimeVersions(null);
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
    api.get('/api/auth/me')
      .then((r) => setCurrentUser({ ...r.user, multiUserEnabled: Boolean(r.multi_user_enabled) }))
      .catch(() => setCurrentUser(null));
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

  function saveSession(next: Session) {
    localStorage.setItem('bzcard.apiBase', next.apiBase);
    localStorage.removeItem('bzcard.token');
    localStorage.removeItem('bzcard.lineSessionToken');
    localStorage.removeItem('bzcard.lineSessionExpiresAt');
    if (next.token) {
      localStorage.setItem('bzcard.sessionToken', next.token);
    } else {
      localStorage.removeItem('bzcard.sessionToken');
    }
    setSession(next);
  }

  if (!authed) {
    return <Login onLoggedIn={saveSession} />;
  }

  return (
    <div className="appShell">
      <header className="topbar">
        <div>
          <h1>bzCard</h1>
          <p>{runtimeVersionLabel(runtimeVersions)}</p>
        </div>
        <div className="topActions">
          <UploadPanel api={api} onUploaded={reload} onMessage={showToast} />
          <button className="iconButton" onClick={reload} title="再読み込み">
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
                  <button role="menuitem" onClick={() => { setUsersOpen(true); setAccountMenuOpen(false); }}>利用者一覧・追加</button>
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
            <label className="searchBox">
              <Search size={16} />
              <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="検索" />
            </label>
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">すべて</option>
              <option value="queued">queued</option>
              <option value="preprocessing">preprocessing</option>
              <option value="ocr_processing">ocr_processing</option>
              <option value="extracting">extracting</option>
              <option value="ready">ready</option>
              <option value="not_card">not_card</option>
              <option value="error">error</option>
            </select>
          </div>

          <div className="tableWrap">
            <table>
              <thead>
                <tr>
                  <th>画像</th>
                  <th>状態</th>
                  <th>氏名</th>
                  <th>会社</th>
                  <th>タグ</th>
                  <th>更新</th>
                </tr>
              </thead>
              <tbody>
                {cards.map((card) => (
                  <tr
                    key={card.id}
                    className={selected?.id === card.id ? 'selected' : ''}
                    onClick={() => setSelectedId(card.id)}
                  >
                    <td className="thumbCell"><ThumbImage api={api} cardId={card.id} version={card.updated_at} /></td>
                    <td><StatusBadge status={card.status} /></td>
                    <td>{card.person_name || '-'}</td>
                    <td>{card.company_name || '-'}</td>
                    <td><TagList tags={card.tags} /></td>
                    <td>{formatDate(card.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="rightPane">
          {selected ? (
            <CardDetail
              api={api}
              card={selected}
              onChanged={reload}
              onMessage={showToast}
            />
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
  if (!versions) return 'yomitoku + local LLM verification console';

  const ocrVersion = versions.ocr?.version || 'unknown';
  const provider = versions.llm?.provider || 'LLM';
  const model = versions.llm?.model || 'model unknown';
  const serverVersion = versions.llm?.server_version;
  const service = serverVersion ? `${provider} ${serverVersion}` : `${provider} unavailable`;
  return `yomitoku ${ocrVersion} + local LLM verification console · ${service} / ${model}`;
}

function LiffRegistration() {
  const targetCardId = getLiffTargetCardId();
  const connectionId = getLiffParameter('connection');
  const linkToken = getLiffParameter('link');
  const [profile, setProfile] = useState<LiffProfile | null>(null);
  const [lineSessionToken, setLineSessionToken] = useState('');
  const [status, setStatus] = useState<'loading' | 'active' | 'error'>('loading');
  const [message, setMessage] = useState('LINE認証を確認しています');

  useEffect(() => {
    document.body.classList.add('liffBody');
    return () => {
      document.body.classList.remove('liffBody');
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function init() {
      try {
        if (!connectionId) {
          throw new Error('LIFF接続先が指定されていません。公式LINEから届いたリンクを開いてください。');
        }
        const config = await getLiffConfig(connectionId);
        await loadLiffSdk();
        if (!window.liff) {
          throw new Error('LIFF SDKを読み込めませんでした');
        }
        await window.liff.init({ liffId: config.liff_id });
        if (!window.liff.isLoggedIn()) {
          window.liff.login({ redirectUri: window.location.href });
          return;
        }
        const token = window.liff.getIDToken();
        if (!token) {
          throw new Error('LINE ID tokenを取得できませんでした。LIFFのscopeにopenidを追加してください。');
        }
        const nextProfile = await window.liff.getProfile();
        if (cancelled) return;
        setProfile(nextProfile);
        const result = await postLineLogin(token, connectionId, linkToken);
        if (!result.session_token) {
          throw new Error('bzCardセッションを開始できませんでした');
        }
        if (cancelled) return;
        setLineSessionToken(result.session_token);
        setStatus('active');
        setMessage('認証済みです');
      } catch (error) {
        if (cancelled) return;
        setStatus('error');
        setMessage(errorMessage(error));
      }
    }

    init();
    return () => {
      cancelled = true;
    };
  }, [connectionId, linkToken]);

  return (
    <main className="liffPage">
      <section className="liffPanel">
        <LiffHeader status={status} message={message} />

        {profile && (
          <div className="liffProfile">
            {profile.pictureUrl ? <img src={profile.pictureUrl} alt="" /> : <div className="liffAvatar" />}
            <div>
              <span>LINEアカウント</span>
              <strong>{profile.displayName || '名称未取得'}</strong>
            </div>
          </div>
        )}

        {status === 'loading' && (
          <div className="liffStatus">
            <Loader2 className="spin" />
            <span>認証中</span>
          </div>
        )}

        {status === 'active' && (
          targetCardId && lineSessionToken ? (
            <LiffCardDetail cardId={targetCardId} sessionToken={lineSessionToken} />
          ) : (
            <LiffCardList sessionToken={lineSessionToken} />
          )
        )}

        {status === 'error' && (
          <div className="liffError">
            <p>{message}</p>
          </div>
        )}
      </section>
    </main>
  );
}

function LiffHeader({
  status,
  message,
}: {
  status: 'loading' | 'active' | 'error';
  message: string;
}) {
  const title = status === 'active' ? 'bzCard' : 'bzCard 利用登録';
  const subtitle = status === 'active' ? '名刺を検索・確認できます' : message;
  return (
    <div className="liffBrand">
      <div className="liffMark">
        {status === 'active' ? <CheckCircle2 /> : status === 'error' ? <AlertCircle /> : <ShieldCheck />}
      </div>
      <div>
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>
    </div>
  );
}

function LiffCardList({ sessionToken }: { sessionToken: string }) {
  const [cards, setCards] = useState<Card[]>([]);
  const [selectedCardId, setSelectedCardId] = useState('');
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');

  useEffect(() => {
    let cancelled = false;
    getLineCards(sessionToken)
      .then((items) => {
        if (!cancelled) setCards(items);
      })
      .catch((error) => {
        if (!cancelled) setMessage(errorMessage(error));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionToken]);

  useEffect(() => {
    if (!selectedCardId && cards.length === 1) {
      setSelectedCardId(cards[0].id);
    }
  }, [cards, selectedCardId]);

  if (selectedCardId) {
    return <LiffCardDetail cardId={selectedCardId} sessionToken={sessionToken} />;
  }

  if (loading) {
    return (
      <div className="liffStatus">
        <Loader2 className="spin" />
        <span>名刺を読み込んでいます</span>
      </div>
    );
  }

  if (message) {
    return <div className="liffError"><p>{message}</p></div>;
  }

  if (!cards.length) {
    return (
      <div className="liffComplete">
        <p>このLINEアカウントで名刺登録を利用できます。</p>
        <p>公式LINEのトーク画面に戻り、名刺画像を送ってください。</p>
      </div>
    );
  }

  return (
    <div className="liffCards">
      {cards.slice(0, 12).map((card) => (
        <button key={card.id} type="button" className="liffCardRow" onClick={() => setSelectedCardId(card.id)}>
          <LineThumb sessionToken={sessionToken} cardId={card.id} />
          <div>
            <strong>{card.person_name || card.company_name || '処理中の名刺'}</strong>
            <span>{card.company_name || card.status}</span>
            <TagList tags={card.tags} showEmpty={false} />
          </div>
          <StatusBadge status={card.status} />
        </button>
      ))}
    </div>
  );
}

function LiffCardDetail({ cardId, sessionToken }: { cardId: string; sessionToken: string }) {
  const [card, setCard] = useState<Card | null>(null);
  const [draft, setDraft] = useState<Card | null>(null);
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getLineCard(sessionToken, cardId)
      .then((nextCard) => {
        if (cancelled) return;
        setCard(nextCard);
        setDraft(nextCard);
      })
      .catch((error) => {
        if (!cancelled) setMessage(errorMessage(error));
      });
    return () => {
      cancelled = true;
    };
  }, [sessionToken, cardId]);

  async function save() {
    if (!draft) return;
    const payload: Record<string, string | undefined> = {};
    fields.forEach(([key]) => {
      payload[key] = draft[key] as string | undefined;
    });
    setSaving(true);
    try {
      const updated = await patchLineCard(sessionToken, cardId, payload);
      setCard(updated);
      setDraft(updated);
      setMessage('保存しました');
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setSaving(false);
    }
  }

  if (!card || !draft) {
    return (
      <div className="liffStatus">
        <Loader2 className="spin" />
        <span>名刺を読み込んでいます</span>
      </div>
    );
  }

  return (
    <div className="liffDetail">
      {message && <div className="liffMessage">{message}</div>}
      <LineCardImage sessionToken={sessionToken} cardId={card.id} status={card.status} />
      <label className="imageTagEditor liffTagEditor">
        タグ
        <TagsInput
          value={draft.tags || ''}
          onChange={(value) => setDraft({ ...draft, tags: value })}
        />
      </label>
      <div className="liffDetailHeader">
        <div>
          <h2>{card.person_name || card.company_name || '名刺確認'}</h2>
          <StatusBadge status={card.status} />
        </div>
        <button className="primaryButton" onClick={save} disabled={saving}>
          {saving ? <Loader2 className="spin" /> : <Save size={16} />}
          保存
        </button>
      </div>
      <div className="liffForm">
        {fields.filter(([key]) => key !== 'tags').map(([key, label]) => (
          <label key={key}>
            {label}
            {key === 'memo' ? (
              <textarea
                value={(draft[key] as string) || ''}
                onChange={(event) => setDraft({ ...draft, [key]: event.target.value })}
              />
            ) : (
              <input
                value={(draft[key] as string) || ''}
                onChange={(event) => setDraft({ ...draft, [key]: event.target.value })}
              />
            )}
          </label>
        ))}
      </div>
    </div>
  );
}

function LineCardImage({
  sessionToken,
  cardId,
  status,
}: {
  sessionToken: string;
  cardId: string;
  status: string;
}) {
  const [src, setSrc] = useState('');

  useEffect(() => {
    let active = true;
    let url = '';
    lineBlob(sessionToken, `/api/cards/${cardId}/processed-image`)
      .catch(() => lineBlob(sessionToken, `/api/cards/${cardId}/original-image`))
      .then((blob) => {
        if (!active) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      })
      .catch(() => setSrc(''));
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [sessionToken, cardId, status]);

  if (!src) return <div className="liffImageEmpty">画像処理中</div>;
  return <img className="liffCardImage" src={src} alt="business card" />;
}

function LineThumb({ sessionToken, cardId }: { sessionToken: string; cardId: string }) {
  const [src, setSrc] = useState('');

  useEffect(() => {
    let active = true;
    let url = '';
    lineBlob(sessionToken, `/api/cards/${cardId}/thumbnail`)
      .then((blob) => {
        if (!active) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      })
      .catch(() => setSrc(''));
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [sessionToken, cardId]);

  if (!src) return <span className="liffThumbPlaceholder" />;
  return <img className="liffThumb" src={src} alt="" />;
}

function LineSettings({ api, onClose }: { api: ReturnType<typeof makeApi>; onClose: () => void }) {
  const [form, setForm] = useState({ channel_secret: '', access_token: '', line_login_channel_id: '', liff_url: '' });
  const [secretsConfigured, setSecretsConfigured] = useState(false);
  const [message, setMessage] = useState(''); const [link, setLink] = useState('');
  useEffect(() => { api.get('/api/line-connections/me').then((r) => { if (r.connection) { setSecretsConfigured(Boolean(r.connection.configured)); setForm((f) => ({ ...f, line_login_channel_id: r.connection.line_login_channel_id || '', liff_url: r.connection.liff_url || '' })); } }).catch((e) => setMessage(errorMessage(e))); }, [api]);
  async function save(e: React.FormEvent) { e.preventDefault(); try { await api.put('/api/line-connections/me', form); setMessage('保存しました。LINE IDの紐付けURLを発行してください。'); } catch (e) { setMessage(errorMessage(e)); } }
  async function createLink() { try { const r = await api.post('/api/line-connections/me/link-url', {}); setLink(r.url); } catch (e) { setMessage(errorMessage(e)); } }
  return <div className="imageModalBackdrop"><form className="imageModal compactModal" onSubmit={save}><div className="imageModalHeader"><b>自分の公式LINE設定</b><button type="button" onClick={onClose}>×</button></div><p>シークレットとトークンは暗号化して保存され、再表示されません。設定済みの場合は `********` と表示し、変更時だけ新しい値を入力します。</p><label>チャネルシークレット<input type="password" value={form.channel_secret} placeholder={secretsConfigured ? '********' : '未設定'} onChange={(e) => setForm({...form,channel_secret:e.target.value})}/></label><label>チャネルアクセストークン<input type="password" value={form.access_token} placeholder={secretsConfigured ? '********' : '未設定'} onChange={(e) => setForm({...form,access_token:e.target.value})}/></label><label>LINE LoginチャネルID<input value={form.line_login_channel_id} onChange={(e) => setForm({...form,line_login_channel_id:e.target.value})} required/></label><label>LIFF URL<input value={form.liff_url} onChange={(e) => setForm({...form,liff_url:e.target.value})} required/></label>{message && <div className="errorBox">{message}</div>}<button className="primaryButton">保存</button><button type="button" className="textButton" onClick={createLink}>LINE ID紐付けURLを発行</button>{link && <a href={link} target="_blank" rel="noreferrer">このURLをLINEアプリで開いて紐付ける</a>}</form></div>;
}

function PasswordChange({
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

function UserManager({ api, onClose }: { api: ReturnType<typeof makeApi>; onClose: () => void }) {
  const [items,setItems]=useState<Array<{id:string;login_id:string;role:string;status:string}>>([]); const [login_id,setId]=useState(''); const [password,setPassword]=useState(''); const [message,setMessage]=useState('');
  const reload=()=>api.get('/api/auth/users').then((r)=>setItems(r.items||[])).catch((e)=>setMessage(errorMessage(e)));
  useEffect(() => { void reload(); }, [api]);
  async function create(e:React.FormEvent){e.preventDefault();try{await api.post('/api/auth/users',{login_id,password});setId('');setPassword('');setMessage('ユーザーを作成しました');reload();}catch(e){setMessage(errorMessage(e));}}
  return <div className="imageModalBackdrop"><section className="imageModal compactModal"><div className="imageModalHeader"><b>利用者一覧・追加</b><button type="button" onClick={onClose}>×</button></div><p>ここで作成するアカウントは一般ユーザーです。管理者権限は付与されず、自分の名刺だけを閲覧・操作できます。</p><form onSubmit={create}><label>ログインID<input value={login_id} onChange={(e)=>setId(e.target.value)} required/></label><label>初期パスワード（12文字以上）<input type="password" value={password} onChange={(e)=>setPassword(e.target.value)} minLength={12} required/></label><button className="primaryButton">利用者を追加</button></form>{message&&<div className="errorBox">{message}</div>}<ul>{items.map((u)=><li key={u.id}>{u.login_id} — {u.role} / {u.status}</li>)}</ul></section></div>;
}

function Login({ onLoggedIn }: { onLoggedIn: (session: Session) => void }) {
  const [apiBase, setApiBase] = useState(localStorage.getItem('bzcard.apiBase') || defaultApiBase);
  const [loginId, setLoginId] = useState('');
  const [password, setPassword] = useState('');
  const [needsBootstrap, setNeedsBootstrap] = useState<boolean | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const base = apiBase.replace(/\/$/, '');
    fetch(`${base}/api/auth/bootstrap-status`)
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
    const base = apiBase.trim().replace(/\/$/, '');
    try {
      const response = await fetch(`${base}/api/auth/${needsBootstrap ? 'bootstrap' : 'login'}`, {
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
        <h1>bzCard</h1>
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
  onChanged,
  onMessage,
}: {
  api: ReturnType<typeof makeApi>;
  card: Card;
  onChanged: () => void;
  onMessage: (message: string) => void;
}) {
  const [draft, setDraft] = useState<Card>(card);
  const [imageMode, setImageMode] = useState<'processed' | 'original'>('processed');
  const [imageSide, setImageSide] = useState<'front' | 'back'>('front');
  const [direction, setDirection] = useState('auto');
  const [backBusy, setBackBusy] = useState(false);
  const [orientationBusy, setOrientationBusy] = useState(false);
  const [zoomPath, setZoomPath] = useState('');

  useEffect(() => setDraft(card), [card.id, card.updated_at]);
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
      await api.patch(`/api/cards/${card.id}`, payload);
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

function TagsInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const [input, setInput] = useState('');
  const tags = parseTags(value);

  function commit(rawValue: string) {
    const nextTags = [...tags];
    for (const tag of parseTags(rawValue)) {
      if (!nextTags.includes(tag)) {
        nextTags.push(tag);
      }
    }
    onChange(nextTags.join(', '));
    setInput('');
  }

  function remove(tag: string) {
    onChange(tags.filter((current) => current !== tag).join(', '));
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.nativeEvent.isComposing) return;
    if (event.key === 'Enter' || event.key === ',' || event.key === '、') {
      event.preventDefault();
      commit(input);
      return;
    }
    if (event.key === 'Backspace' && !input && tags.length) {
      event.preventDefault();
      remove(tags[tags.length - 1]);
    }
  }

  function onInputChange(nextValue: string) {
    if (/[,、\n\r]/.test(nextValue)) {
      commit(nextValue);
      return;
    }
    setInput(nextValue);
  }

  return (
    <div className="tagsInput">
      {tags.map((tag) => (
        <span className="tagPill tagPillEditable" key={tag}>
          {tag}
          <button type="button" onClick={() => remove(tag)} title={`${tag}を削除`}>
            ×
          </button>
        </span>
      ))}
      <input
        value={input}
        onChange={(event) => onInputChange(event.target.value)}
        onBlur={() => commit(input)}
        onKeyDown={onKeyDown}
        placeholder={tags.length ? '' : 'タグを入力'}
      />
    </div>
  );
}

function TagList({ tags, showEmpty = true }: { tags?: string; showEmpty?: boolean }) {
  const items = parseTags(tags || '');
  if (!items.length) {
    return showEmpty ? <span className="emptyTags">-</span> : null;
  }
  return (
    <div className="tagList">
      {items.map((tag) => (
        <span className="tagPill" key={tag}>{tag}</span>
      ))}
    </div>
  );
}

function AuthedImage({
  api,
  path,
  onClick,
}: {
  api: ReturnType<typeof makeApi>;
  path: string;
  onClick?: () => void;
}) {
  const [src, setSrc] = useState('');

  useEffect(() => {
    let active = true;
    let url = '';
    api.blob(path)
      .then((blob) => {
        if (!active) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      })
      .catch(() => setSrc(''));
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [api, path]);

  if (!src) return <div className="imageEmpty">画像待ち</div>;
  return <img className={onClick ? 'clickableImage' : undefined} src={src} alt="business card" onClick={onClick} />;
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

function ThumbImage({
  api,
  cardId,
  version,
}: {
  api: ReturnType<typeof makeApi>;
  cardId: string;
  version?: string;
}) {
  const [src, setSrc] = useState('');
  const [visible, setVisible] = useState(false);
  const rootRef = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    const element = rootRef.current;
    if (!element) return;
    if (!('IntersectionObserver' in window)) {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry?.isIntersecting) return;
        setVisible(true);
        observer.disconnect();
      },
      { rootMargin: '240px' },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!visible) return;
    let active = true;
    let url = '';
    api.blob(`/api/cards/${cardId}/thumbnail${version ? `?v=${encodeURIComponent(version)}` : ''}`)
      .then((blob) => {
        if (!active) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      })
      .catch(() => setSrc(''));
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [api, cardId, version, visible]);

  return (
    <span ref={rootRef} className="thumbSlot">
      {src ? <img className="thumb" src={src} alt="" /> : <span className="thumbPlaceholder" />}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  return <span className={`status status-${status}`}>{status}</span>;
}

function makeApi(session: Session) {
  const headers = {
    Authorization: `Bearer ${session.token}`,
  };

  async function request(path: string, init: RequestInit = {}) {
    const response = await fetch(`${session.apiBase}${path}`, {
      ...init,
      headers: {
        ...headers,
        ...(init.headers || {}),
      },
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(formatHttpError(response, text));
    }
    if (response.status === 204) return null;
    return response.json();
  }

  return {
    get: (path: string) => request(path),
    post: (path: string, body: unknown) =>
      request(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    patch: (path: string, body: unknown) =>
      request(path, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    put: (path: string, body: unknown) =>
      request(path, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    delete: (path: string) =>
      request(path, {
        method: 'DELETE',
      }),
    postForm: (path: string, body: FormData) =>
      request(path, {
        method: 'POST',
        body,
      }),
    blob: async (path: string) => {
      const response = await fetch(`${session.apiBase}${path}`, { headers });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return response.blob();
    },
  };
}

function formatHttpError(response: Response, text: string) {
  if (!text) return `${response.status} ${response.statusText}`;
  try {
    const json = JSON.parse(text);
    if (typeof json.detail === 'string') {
      return `${response.status}: ${json.detail}`;
    }
    return `${response.status}: ${JSON.stringify(json)}`;
  } catch {
    return `${response.status}: ${text}`;
  }
}

function formatDate(value: string) {
  if (!value) return '-';
  return new Date(value).toLocaleString();
}

function directionLabel(value?: string) {
  if (value === 'vertical') return '縦書き';
  if (value === 'horizontal') return '横書き';
  if (value === 'auto') return '自動';
  return '-';
}

function imagePathFor(card: Card, side: 'front' | 'back', mode: 'processed' | 'original') {
  const version = card.updated_at ? `?v=${encodeURIComponent(card.updated_at)}` : '';
  if (side === 'back') {
    return `/api/cards/${card.id}/${mode === 'processed' ? 'back-processed-image' : 'back-original-image'}${version}`;
  }
  return `/api/cards/${card.id}/${mode === 'processed' ? 'processed-image' : 'original-image'}${version}`;
}

function openGoogleMaps(address: string) {
  const url = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(address)}`;
  window.open(url, '_blank', 'noopener,noreferrer');
}

function combinedOcrText(card: Card) {
  const front = (card.ocr_text || '').trim();
  const back = (card.back_ocr_text || '').trim();
  if (front && back) return `【表面】\n${front}\n\n【裏面】\n${back}`;
  if (front) return front;
  if (back) return `【裏面】\n${back}`;
  return '';
}

function formatJson(value?: string) {
  if (!value) return '';
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

function parseTags(value: string) {
  const normalized = value.normalize('NFKC').trim();
  if (!normalized) return [];
  const tags: string[] = [];
  normalized
    .split(/[,、\n\r]+/)
    .map((tag) => tag.trim().replace(/^#+/, '').replace(/\s+/g, ' '))
    .filter(Boolean)
    .forEach((tag) => {
      if (!tags.includes(tag)) tags.push(tag);
    });
  return tags;
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function isLiffRoute() {
  const normalized = window.location.pathname.replace(/\/+$/, '');
  return normalized.endsWith('/liff') || new URLSearchParams(window.location.search).get('mode') === 'liff';
}

function getLiffTargetCardId() {
  return getLiffParameter('card');
}

function getLiffParameter(name: string) {
  const search = new URLSearchParams(window.location.search);
  const direct = search.get(name);
  if (direct) return direct;
  const liffState = search.get('liff.state');
  if (!liffState) return '';
  try {
    const stateUrl = new URL(decodeURIComponent(liffState), window.location.origin);
    return new URLSearchParams(stateUrl.search).get(name) || '';
  } catch {
    return '';
  }
}

async function loadLiffSdk() {
  if (window.liff) return;
  await new Promise<void>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>('script[data-liff-sdk="true"]');
    if (existing) {
      existing.addEventListener('load', () => resolve(), { once: true });
      existing.addEventListener('error', () => reject(new Error('LIFF SDKの読み込みに失敗しました')), { once: true });
      return;
    }
    const script = document.createElement('script');
    script.src = 'https://static.line-scdn.net/liff/edge/2/sdk.js';
    script.async = true;
    script.dataset.liffSdk = 'true';
    script.onload = () => resolve();
    script.onerror = () => reject(new Error('LIFF SDKの読み込みに失敗しました'));
    document.head.appendChild(script);
  });
}

async function getLiffConfig(connectionId: string) {
  const response = await fetch(`${defaultApiBase}/api/line-connections/${encodeURIComponent(connectionId)}/liff-config`);
  if (!response.ok) {
    throw new Error(formatHttpError(response, await response.text()));
  }
  return response.json() as Promise<{ connection_id: string; liff_id: string }>;
}

async function postLineLogin(idToken: string, connectionId: string, linkToken: string) {
  const response = await fetch(`${defaultApiBase}/line/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id_token: idToken, connection_id: connectionId, link_token: linkToken }),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(formatHttpError(response, text));
  }
  return response.json();
}

async function getLineCards(sessionToken: string) {
  const data = await lineJson('/api/cards', sessionToken);
  return data.items || [];
}

async function getLineCard(sessionToken: string, cardId: string) {
  return lineJson(`/api/cards/${cardId}`, sessionToken);
}

async function patchLineCard(sessionToken: string, cardId: string, payload: Record<string, string | undefined>) {
  return lineJson(`/api/cards/${cardId}`, sessionToken, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

async function lineJson(path: string, sessionToken: string, init: RequestInit = {}) {
  const response = await fetch(`${defaultApiBase}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${sessionToken}`,
      ...(init.headers || {}),
    },
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(formatHttpError(response, text));
  }
  return response.json();
}

async function lineBlob(sessionToken: string, path: string) {
  const response = await fetch(`${defaultApiBase}${path}`, {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.blob();
}

createRoot(document.getElementById('root')!).render(<App />);
