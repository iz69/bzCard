import styles from './mobile.module.css';

const labels: Record<string, string> = {
  queued: '待機中', preparing: '画像補正中', scanning: '文字認識中', extracting: '項目抽出中',
  ready: '完了', error: 'エラー', not_card: '名刺以外',
};

export function MobileStatus({ status }: { status: string }) {
  return <span className={`${styles.status} ${status === 'ready' ? styles.ready : status === 'error' || status === 'not_card' ? styles.failed : styles.processing}`}>{labels[status] || status}</span>;
}
