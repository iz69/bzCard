import { useEffect, useState } from 'react';
import { Loader2, MapPin, RotateCcw, RotateCw, Save, Trash2, X } from 'lucide-react';
import { makeApi } from '../../shared/api';
import { fields } from '../../shared/cardFields';
import { AuthedImage } from '../../shared/components/Images';
import { TagsInput } from '../../shared/components/Tags';
import { CorrectionHistory } from '../../shared/components/CorrectionHistory';
import { combinedOcrText, errorMessage, formatDate, formatJson, imagePathFor, openGoogleMaps } from '../../shared/format';
import { MobileStatus } from '../MobileStatus';
import { useCardDetail } from '../useCardDetail';
import { useCardEditor } from '../useCardEditor';
import styles from '../mobile.module.css';

export default function CardDetailPage({ api, contactId, cardId, revision, onChanged, onMessage, onDirty, onSelectCard, onDeleted }: {
  api: ReturnType<typeof makeApi>; contactId: string; cardId: string; revision?: string | number;
  onChanged: () => void; onMessage: (message: string) => void; onDirty: (dirty: boolean) => void;
  onSelectCard: (cardId: string) => void; onDeleted: () => void;
}) {
  const { card, relatedCards, error, reload } = useCardDetail(api, contactId, cardId, revision);
  const { draft, setDraft, dirty, saving, save } = useCardEditor(api, card);
  const [side, setSide] = useState<'front' | 'back'>('front');
  const [mode, setMode] = useState<'processed' | 'original'>('processed');
  const [zoom, setZoom] = useState(false);
  const [busy, setBusy] = useState(false);
  const [direction, setDirection] = useState('auto');
  useEffect(() => { onDirty(dirty); }, [dirty, onDirty]);
  useEffect(() => () => onDirty(false), [onDirty]);
  useEffect(() => { if (!card?.back_original_image_path) setSide('front'); }, [card?.back_original_image_path]);

  async function action(path: string, message: string) {
    if (busy || saving) return;
    setBusy(true);
    try { await api.post(path, {}); onMessage(message); reload(); onChanged(); }
    catch (error) { onMessage(errorMessage(error)); }
    finally { setBusy(false); }
  }
  async function uploadBack(files: FileList | null) {
    const file = files?.[0];
    if (!file || busy || saving) return;
    setBusy(true);
    try {
      if (!['image/jpeg', 'image/png'].includes(file.type)) throw new Error('JPEGまたはPNGの画像を選択してください');
      const form = new FormData(); form.append('file', file);
      await api.postForm(`/api/cards/${encodeURIComponent(cardId)}/back/upload?direction=auto`, form);
      setSide('back'); onMessage('裏面の処理を開始しました'); reload(); onChanged();
    } catch (error) { onMessage(errorMessage(error)); }
    finally { setBusy(false); }
  }
  if (!card || !draft) return <div className={styles.page}>
    {error ? <div className={styles.error} role="alert"><p>{error}</p><button type="button" onClick={reload}>再試行</button></div> : <p className={styles.inlineStatus} role="status"><Loader2 className="spin" />名刺を読み込んでいます</p>}
  </div>;
  const imagePath = imagePathFor(card, side, mode);
  const historical = relatedCards.filter(item => item.id !== cardId);
  return <section className={styles.detailPage} aria-label="名刺詳細">
    <div className={styles.detailScroll}>
      <div className={styles.detailTitle}><h2>{card.person_name || card.company_name || '名刺詳細'}</h2><MobileStatus status={card.status} /></div>
      {error && <div className={styles.error} role="alert"><p>{error}</p><button type="button" onClick={reload}>再試行</button></div>}
      {card.error_message && <div className={styles.error} role="alert">{card.error_message}</div>}
      <div className={styles.imagePanel}>
        <div className={styles.imageControls}>
          <button type="button" aria-pressed={side === 'front'} onClick={() => setSide('front')}>表</button>
          <button type="button" aria-pressed={side === 'back'} disabled={!card.back_original_image_path} onClick={() => setSide('back')}>裏</button>
          <button type="button" onClick={() => setMode(mode === 'processed' ? 'original' : 'processed')}>{mode === 'processed' ? '元画像を表示' : '補正画像を表示'}</button>
        </div>
        <button type="button" className={styles.imageButton} aria-label="名刺画像を拡大" onClick={() => setZoom(true)}><AuthedImage api={api} path={imagePath} /></button>
        <div className={styles.imageControls}>
          <button type="button" disabled={busy || saving} onClick={() => void action(`/api/cards/${cardId}/rotate?side=${side}&degrees=-90`, '左90度回転しました')}><RotateCcw />左回転</button>
          <button type="button" disabled={busy || saving} onClick={() => void action(`/api/cards/${cardId}/rotate?side=${side}&degrees=90`, '右90度回転しました')}><RotateCw />右回転</button>
        </div>
        <div className={styles.backChoices}>
          <label>裏面を撮影<input aria-label="裏面を撮影" type="file" capture="environment" accept="image/jpeg,image/png" disabled={busy || saving} onChange={event => { void uploadBack(event.target.files); event.currentTarget.value = ''; }} /></label>
          <label>裏面画像を選択<input aria-label="裏面画像を選択" type="file" accept="image/jpeg,image/png" disabled={busy || saving} onChange={event => { void uploadBack(event.target.files); event.currentTarget.value = ''; }} /></label>
        </div>
      </div>
      <div className={styles.form}>
        {fields.map(([key, label]) => <label key={key}>
          <span className={styles.fieldLabel}>{label}{key === 'address' && <button type="button" aria-label="Googleマップで表示" disabled={!draft.address?.trim()} onClick={() => openGoogleMaps(draft.address || '')}><MapPin />地図</button>}</span>
          {key === 'tags' ? <TagsInput value={draft.tags || ''} onChange={value => setDraft({ ...draft, tags: value })} />
            : key === 'memo' ? <textarea aria-label={label} value={draft.memo || ''} onChange={event => setDraft({ ...draft, memo: event.target.value })} />
            : <input aria-label={label} value={(draft[key] as string) || ''} type="text" inputMode={key === 'email' ? 'email' : ['tel', 'mobile', 'fax'].includes(key) ? 'tel' : key === 'website' ? 'url' : 'text'} onChange={event => setDraft({ ...draft, [key]: event.target.value })} />}
        </label>)}
      </div>
      {historical.length > 0 && <section className={styles.related} aria-label="同一人物の名刺"><h3>以前の名刺（{relatedCards.length}枚）</h3>{historical.map(item => <button key={item.id} type="button" onClick={() => onSelectCard(item.id)}><strong>{item.company_name || '会社名未設定'}</strong><span>{[item.department, item.title].filter(Boolean).join(' / ')}</span><small>{formatDate(item.created_at)}</small></button>)}</section>}
      <details className={styles.tools}><summary>再解析・読み取り情報</summary>
        <p className={styles.help}>再解析が完了すると、保存済みの修正は解析結果で上書きされます。</p>
        <label>読み順<select value={direction} onChange={event => setDirection(event.target.value)}><option value="auto">自動判定</option><option value="horizontal">横書き</option><option value="vertical">縦書き</option></select></label>
        <div className={styles.imageControls}><button type="button" disabled={busy || saving} onClick={() => void action(`/api/cards/${cardId}/reprocess?direction=${direction}`, '再処理を開始しました')}>再スキャン</button><button type="button" disabled={busy || saving} onClick={() => void action(`/api/cards/${cardId}/reextract`, '再抽出を開始しました')}>再抽出</button></div>
        <h3>OCRテキスト</h3><pre>{combinedOcrText(card)}</pre><h3>抽出結果</h3><pre>{formatJson(card.extracted_json)}</pre>
      </details>
      <CorrectionHistory api={api} cardId={cardId} revision={card.revision} />
      <p className={styles.timestamps}>登録：{formatDate(card.created_at)}<br />更新：{formatDate(card.updated_at)}</p>
      <button type="button" className={styles.delete} disabled={busy || saving} onClick={async () => {
        if (!window.confirm('この名刺を削除しますか？')) return;
        setBusy(true);
        try { await api.delete(`/api/cards/${cardId}`); onDirty(false); onDeleted(); onChanged(); onMessage('削除しました'); }
        catch (error) { onMessage(errorMessage(error)); }
        finally { setBusy(false); }
      }}><Trash2 />この名刺を削除</button>
    </div>
    <div className={styles.saveBar}><span>{dirty ? '未保存の変更があります' : '変更は保存済みです'}</span><button type="button" className={styles.primary} disabled={busy || saving} onClick={async () => {
      try { if (await save()) { onMessage('保存しました'); reload(); onChanged(); } }
      catch (error) { onMessage(errorMessage(error)); }
    }}>{saving ? <Loader2 className="spin" /> : <Save />}保存</button></div>
    {zoom && <div className={styles.zoom} role="dialog" aria-modal="true" aria-label="名刺画像">
      <div className={styles.zoomHeader}><button type="button" onClick={() => setMode(mode === 'processed' ? 'original' : 'processed')}>{mode === 'processed' ? '元画像を表示' : '補正画像を表示'}</button><button type="button" aria-label="画像を閉じる" onClick={() => setZoom(false)}><X /></button></div>
      <AuthedImage api={api} path={imagePath} />
    </div>}
  </section>;
}
