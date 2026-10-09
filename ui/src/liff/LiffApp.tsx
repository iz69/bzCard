import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Save,
  Search,
  ShieldCheck,
} from 'lucide-react';
import { apiUrl } from '../deployment';
import { defaultApiBase } from '../shared/config';
import { mergeServerCard } from '../cardSync';
import type { Card } from '../shared/types';
import { makeApi, formatHttpError } from '../shared/api';
import { fields, terminalCardStatuses } from '../shared/cardFields';
import { errorMessage } from '../shared/format';
import { BrandTitle } from '../shared/components/BrandTitle';
import { TagsInput, TagList } from '../shared/components/Tags';
import { StatusBadge } from '../shared/components/StatusBadge';
import { CorrectionHistory } from '../shared/components/CorrectionHistory';

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

export default function LiffApp() {
  const [targetCardId, setTargetCardId] = useState(getLiffTargetCardId);
  const connectionId = getLiffParameter('connection');
  const linkToken = getLiffParameter('link');
  const [profile, setProfile] = useState<LiffProfile | null>(null);
  const [lineSessionToken, setLineSessionToken] = useState('');
  const [status, setStatus] = useState<'loading' | 'active' | 'error'>('loading');
  const [message, setMessage] = useState('LINE認証を確認しています');
  const [authAttempt, setAuthAttempt] = useState(0);

  useEffect(() => {
    document.body.classList.add('liffBody');
    return () => {
      document.body.classList.remove('liffBody');
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function init() {
      setStatus('loading');
      setLineSessionToken('');
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
            <LiffCardDetail key={targetCardId} cardId={targetCardId} sessionToken={lineSessionToken} onBack={() => setTargetCardId('')} />
          ) : (
            <LiffCardList sessionToken={lineSessionToken} />
          )
        )}

        {status === 'error' && (
          <div className="liffError">
            <p>{message}</p>
            <button onClick={() => setAuthAttempt((n) => n + 1)}>認証を再試行</button>
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
        <BrandTitle title={title} />
        <p>{subtitle}</p>
      </div>
    </div>
  );
}

function LiffCardList({ sessionToken }: { sessionToken: string }) {
  const api = useMemo(() => makeApi({ apiBase: defaultApiBase, token: sessionToken }), [sessionToken]);
  useEffect(() => () => api.dispose(), [api]);
  const [cards, setCards] = useState<Card[]>([]);
  const [selectedCardId, setSelectedCardId] = useState('');
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState(12);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const response = await api.get(`/api/cards?q=${encodeURIComponent(query)}`, controller.signal);
        if (!active) return;
        setCards(response.items || []);
        setMessage('');
        if ((response.items || []).some((card: Card) => !terminalCardStatuses.has(card.status))) timer = setTimeout(load, 5000);
      } catch (error) {
        if (active) setMessage(errorMessage(error));
      } finally { if (active) setLoading(false); }
    };
    const debounce = setTimeout(load, query ? 300 : 0);
    return () => { active = false; clearTimeout(debounce); clearTimeout(timer); controller.abort(); };
  }, [api, query, refresh]);
  if (selectedCardId) return <LiffCardDetail key={selectedCardId} cardId={selectedCardId} sessionToken={sessionToken} onBack={() => { setSelectedCardId(''); setRefresh((r) => r + 1); }} />;
  return <div className="liffCards">
    <label className="searchBox"><Search size={16} /><input value={query} placeholder="氏名・会社名を検索" onChange={(e) => { setQuery(e.target.value); setLimit(12); }} /></label>
    {loading && <div className="liffStatus"><Loader2 className="spin" />読み込み中</div>}
    {message && <div className="liffError"><p>{message}</p><button onClick={() => setRefresh((r) => r + 1)}>再試行</button></div>}
    {!loading && !message && !cards.length && <p>{query ? '一致する名刺はありません。' : '公式LINEのトーク画面から名刺画像を送ってください。'}</p>}
    {cards.slice(0, limit).map((card) => <button key={card.id} type="button" className="liffCardRow" onClick={() => setSelectedCardId(card.id)}>
      <LineThumb sessionToken={sessionToken} cardId={card.id} />
      <div><strong>{card.person_name || card.company_name || '処理中の名刺'}</strong><span>{card.company_name || card.status}</span><TagList tags={card.tags} showEmpty={false} /></div>
      <StatusBadge status={card.status} />
    </button>)}
    {cards.length > limit && <button onClick={() => setLimit((n) => n + 12)}>さらに表示（残り{cards.length - limit}件）</button>}
  </div>;
}

function LiffCardDetail({ cardId, sessionToken, onBack }: { cardId: string; sessionToken: string; onBack: () => void }) {
  const api = useMemo(() => makeApi({ apiBase: defaultApiBase, token: sessionToken }), [sessionToken]);
  useEffect(() => () => api.dispose(), [api]);
  const previous = useRef<Card | null>(null);
  const [imageSide, setImageSide] = useState<'front' | 'back'>('front');
  const [refresh, setRefresh] = useState(0);
  const [card, setCard] = useState<Card | null>(null);
  const [draft, setDraft] = useState<Card | null>(null);
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const next: Card = await api.get(`/api/cards/${cardId}`, controller.signal);
        if (!active) return;
        setDraft((current) => current && previous.current ? mergeServerCard(current, previous.current, next, fields.map(([key]) => key)) : next);
        previous.current = next;
        setCard(next);
        setMessage('');
        if (!terminalCardStatuses.has(next.status)) timer = setTimeout(load, 5000);
      } catch (error) { if (active) setMessage(errorMessage(error)); }
    }
    void load();
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [api, cardId, refresh]);

  async function save() {
    if (!draft) return;
    const payload: Record<string, string | undefined> = {};
    fields.forEach(([key]) => {
      payload[key] = draft[key] as string | undefined;
    });
    setSaving(true);
    try {
      const updated = await api.patch(`/api/cards/${cardId}`, payload);
      previous.current = updated;
      setCard(updated);
      setDraft(updated);
      setMessage('保存しました');
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setSaving(false);
    }
  }

  if (!card || !draft) return <div>
    <button onClick={onBack}>一覧へ戻る</button>
    {message ? <div className="liffError"><p>{message}</p><button onClick={() => { setMessage(''); setRefresh((r) => r + 1); }}>再試行</button></div>
      : <div className="liffStatus"><Loader2 className="spin" />名刺を読み込んでいます</div>}
  </div>;

  return (
    <div className="liffDetail">
      <button onClick={onBack}>一覧へ戻る</button>
      {message && <div className="liffMessage">{message}</div>}
      {message && <button onClick={() => setRefresh((r) => r + 1)}>更新・再試行</button>}
      {card.error_message && <div className="liffError">{card.error_message}<button onClick={async () => { try { await api.post(`/api/cards/${cardId}/reprocess`, {}); setRefresh((r) => r + 1); } catch (e) { setMessage(errorMessage(e)); } }}>画像を再解析</button></div>}
      {card.back_original_image_path && <div className="imageTabs"><button className={imageSide === 'front' ? 'active' : ''} onClick={() => setImageSide('front')}>表</button><button className={imageSide === 'back' ? 'active' : ''} onClick={() => setImageSide('back')}>裏</button></div>}
      <LineCardImage sessionToken={sessionToken} cardId={card.id} status={String(card.revision ?? card.status)} side={imageSide} />
      <CorrectionHistory api={api} cardId={card.id} revision={card.revision} />
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
  side,
}: {
  sessionToken: string;
  cardId: string;
  status: string;
  side: 'front' | 'back';
}) {
  const [src, setSrc] = useState('');

  useEffect(() => {
    let active = true;
    let url = '';
    setSrc('');
    lineBlob(sessionToken, `/api/cards/${cardId}/${side === 'back' ? 'back-' : ''}processed-image?v=${encodeURIComponent(status)}`)
      .catch(() => lineBlob(sessionToken, `/api/cards/${cardId}/${side === 'back' ? 'back-' : ''}original-image?v=${encodeURIComponent(status)}`))
      .then((blob) => {
        if (!active) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      })
      .catch(() => { if (active) setSrc(''); });
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [sessionToken, cardId, status, side]);

  if (!src) return <div className="liffImageEmpty">画像処理中</div>;
  return <img className="liffCardImage" src={src} alt="business card" />;
}

function LineThumb({ sessionToken, cardId }: { sessionToken: string; cardId: string }) {
  const [src, setSrc] = useState('');

  useEffect(() => {
    let active = true;
    let url = '';
    setSrc('');
    lineBlob(sessionToken, `/api/cards/${cardId}/thumbnail`)
      .then((blob) => {
        if (!active) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      })
      .catch(() => { if (active) setSrc(''); });
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [sessionToken, cardId]);

  if (!src) return <span className="liffThumbPlaceholder" />;
  return <img className="liffThumb" src={src} alt="" />;
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
    script.onerror = () => { script.remove(); reject(new Error('LIFF SDKの読み込みに失敗しました')); };
    document.head.appendChild(script);
  });
}

async function getLiffConfig(connectionId: string) {
  const response = await fetch(apiUrl(defaultApiBase, `/api/line-connections/${encodeURIComponent(connectionId)}/liff-config`));
  if (!response.ok) {
    throw new Error(formatHttpError(response, await response.text()));
  }
  return response.json() as Promise<{ connection_id: string; liff_id: string }>;
}

async function postLineLogin(idToken: string, connectionId: string, linkToken: string) {
  const response = await fetch(apiUrl(defaultApiBase, '/line/auth/login'), {
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

async function lineBlob(sessionToken: string, path: string) {
  const response = await fetch(apiUrl(defaultApiBase, path), {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.blob();
}
