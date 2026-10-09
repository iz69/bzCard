import { useEffect, useState } from 'react';
import { makeApi } from '../api';
import { fields } from '../cardFields';
import { formatDate, errorMessage } from '../format';

type Correction = {
  id: string; field: string; before_value: string; corrected_value: string;
  model_value: string; automatic_value: string; eligible: number; active: number; superseded: number;
  cause: string; created_at: string;
};

export function CorrectionHistory({ api, cardId, revision }: { api: ReturnType<typeof makeApi>; cardId: string; revision?: string | number }) {
  const [items, setItems] = useState<Correction[]>([]);
  const [message, setMessage] = useState('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    api.get(`/api/cards/${cardId}/corrections`).then((r) => { if (active) { setItems(r.items || []); setMessage(''); } })
      .catch((e) => { if (active) setMessage(errorMessage(e)); });
    return () => { active = false; };
  }, [api, cardId, revision, refresh]);
  if (!items.length && !message) return null;
  return <details className="correctionHistory"><summary>補正履歴（{items.length}件）</summary>
    {message && <div className="errorBox">{message}</div>}
    {items.map((item) => <div className="correctionRow" key={item.id}>
      <strong>{fields.find(([key]) => key === item.field)?.[1] || item.field}：{item.before_value || '空欄'} → {item.corrected_value || '空欄'}</strong>
      <small>LLM推測：{item.model_value || '空欄'} ／ 自動採用：{item.automatic_value || '空欄'} ／ {formatDate(item.created_at)}</small>
      {item.superseded ? <span>再訂正済み</span> : item.eligible ? <label><input type="checkbox" checked={Boolean(item.active)} onChange={async (e) => {
        try { await api.patch(`/api/corrections/${item.id}`, { active: e.target.checked }); setRefresh((r) => r + 1); }
        catch (error) { setMessage(errorMessage(error)); }
      }} />読み取りの参考にする</label> : <span>変更履歴として保存</span>}
    </div>)}
  </details>;
}
