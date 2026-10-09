import { useState, type ChangeEvent } from 'react';
import { Camera, ImagePlus, Loader2 } from 'lucide-react';
import { makeApi } from '../../shared/api';
import { errorMessage } from '../../shared/format';
import styles from '../mobile.module.css';

export default function UploadPage({ api, onUploaded }: { api: ReturnType<typeof makeApi>; onUploaded: () => void }) {
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState('');
  const [results, setResults] = useState<string[]>([]);
  async function upload(files: FileList | null) {
    if (!files?.length || busy) return;
    setBusy(true);
    setResults([]);
    try {
      const selected = Array.from(files);
      const messages: string[] = [];
      for (const [index, file] of selected.entries()) {
        setProgress(`${index + 1} / ${selected.length}枚を送信中`);
        try {
          if (!['image/jpeg', 'image/png'].includes(file.type)) throw new Error('JPEGまたはPNGの画像を選択してください');
          const form = new FormData();
          form.append('file', file);
          const result = await api.postForm('/api/cards/upload', form);
          messages.push(`${file.name}：${result.duplicate ? '登録済みの名刺です' : '登録しました。解析を開始します'}`);
        } catch (error) { messages.push(`${file.name}：${errorMessage(error)}`); }
        setResults([...messages]);
      }
      onUploaded();
    } finally { setBusy(false); setProgress(''); }
  }
  const selected = (event: ChangeEvent<HTMLInputElement>) => {
    void upload(event.target.files);
    event.currentTarget.value = '';
  };
  return <section className={styles.page} aria-label="名刺追加">
    <h2>名刺を追加</h2><p className={styles.help}>名刺の表面を撮影するか、保存済みの画像を選択してください。</p>
    <div className={styles.uploadChoices}>
      <label className={styles.uploadChoice}><Camera size={30} /><strong>カメラで撮影</strong><span>名刺の表面を撮影</span><input aria-label="カメラで撮影" type="file" accept="image/jpeg,image/png" capture="environment" disabled={busy} onChange={selected} /></label>
      <label className={styles.uploadChoice}><ImagePlus size={30} /><strong>画像を選択</strong><span>複数の画像を選択できます</span><input aria-label="画像を選択" type="file" accept="image/jpeg,image/png" multiple disabled={busy} onChange={selected} /></label>
    </div>
    <p className={styles.help}>JPEG・PNGに対応。裏面は登録した名刺の詳細から追加できます。</p>
    {busy && <p className={styles.inlineStatus} role="status"><Loader2 className="spin" />{progress}</p>}
    {results.length > 0 && <ul className={styles.uploadResults} aria-live="polite">{results.map((result, index) => <li key={index}>{result}</li>)}</ul>}
  </section>;
}
