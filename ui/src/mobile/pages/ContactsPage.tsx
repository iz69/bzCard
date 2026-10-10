import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { Loader2 } from 'lucide-react';
import { makeApi } from '../../shared/api';
import { ThumbImage } from '../../shared/components/Images';
import { TagList } from '../../shared/components/Tags';
import { SearchInput } from '../../shared/components/SearchInput';
import type { Contact } from '../../shared/types';
import { useContacts } from '../useContacts';
import { MobileStatus } from '../MobileStatus';
import styles from '../mobile.module.css';

const rowHeight = 84;
const overscan = 5;

export default function ContactsPage({ api, list, hidden, onSelect }: {
  api: ReturnType<typeof makeApi>;
  list: ReturnType<typeof useContacts>;
  hidden: boolean;
  onSelect: (contact: Contact) => void;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(0);
  const first = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const count = Math.ceil(height / rowHeight) + overscan * 2;
  const visible = list.contacts.slice(first, first + count);
  const trailing = Math.max(0, list.contacts.length - first - visible.length);

  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setHeight(element.clientHeight));
    observer.observe(element);
    setHeight(element.clientHeight);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    viewport.current?.scrollTo({ top: 0 });
    setScrollTop(0);
  }, [list.query, list.status]);
  useEffect(() => {
    if (!hidden && height > 0 && list.nextCursor && !list.loading && !list.loadingMore && !list.error
      && scrollTop + height >= list.contacts.length * rowHeight - rowHeight * 3) void list.loadMore();
  }, [hidden, height, scrollTop, list.nextCursor, list.loading, list.loadingMore, list.error, list.contacts.length, list.loadMore]);

  return <section className={styles.listPage} style={{ '--contact-row-height': `${rowHeight}px` } as CSSProperties} hidden={hidden} aria-label="人物一覧">
    <div className={styles.filters}>
      <SearchInput className={styles.search} iconSize={20} placeholder="氏名・会社・タグを検索" value={list.query} onChange={list.setQuery} />
      <select aria-label="処理状態" value={list.status} onChange={event => list.setStatus(event.target.value)}>
        <option value="">すべての状態</option><option value="ready">処理完了</option><option value="queued">待機中</option>
        <option value="preparing">画像補正中</option><option value="scanning">文字認識中</option><option value="extracting">項目抽出中</option>
        <option value="error">エラー</option><option value="not_card">名刺以外</option>
      </select>
      <span className={styles.count}>{list.contacts.length}人{list.nextCursor ? 'を表示中' : ''}</span>
    </div>
    <div ref={viewport} className={styles.contactViewport} onScroll={event => setScrollTop(event.currentTarget.scrollTop)}>
      {list.loading && !list.contacts.length && <div className={styles.empty} role="status"><Loader2 className="spin" />読み込み中…</div>}
      {list.error && <div className={styles.error} role="alert"><p>{list.error}</p><button type="button" onClick={() => void (list.nextCursor ? list.loadMore() : list.reload())}>再試行</button></div>}
      {!list.loading && !list.error && !list.contacts.length && <div className={styles.empty}>{list.query || list.status ? '一致する人物がいません' : '名刺がありません。「追加」から登録できます。'}</div>}
      <div role="list">
        {first > 0 && <div aria-hidden="true" style={{ height: first * rowHeight }} />}
        {visible.map(contact => <div key={contact.id} role="listitem" className={styles.contactItem}>
          <button type="button" className={styles.contactRow} onClick={() => onSelect(contact)}>
            <ThumbImage api={api} cardId={contact.representative_card_id} version={contact.revision || contact.updated_at} />
            <span className={styles.contactText}>
              <strong>{contact.person_name || contact.company_name || '処理中の名刺'}</strong>
              <span>{contact.company_name || '会社名未設定'}</span>
              <TagList tags={contact.tags} showEmpty={false} />
            </span>
            <span className={styles.contactMeta}><MobileStatus status={contact.status} />{contact.card_count > 1 && <small>{contact.card_count}枚</small>}</span>
          </button>
        </div>)}
        {trailing > 0 && <div aria-hidden="true" style={{ height: trailing * rowHeight }} />}
      </div>
      {list.nextCursor && <button className={styles.loadMore} type="button" disabled={list.loading || list.loadingMore} onClick={() => void list.loadMore()}>{list.loadingMore ? '読み込み中…' : list.error ? '続きを再試行' : 'さらに表示'}</button>}
    </div>
  </section>;
}
