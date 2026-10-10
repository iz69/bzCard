import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import type { makeApi } from '../shared/api';
import type { Card } from '../shared/types';
import { imagePathFor } from '../shared/format';
import { CardPrintRecord } from './CardPrintRecord';
import printStyles from './cardPrint.css?raw';

async function imageBlob(api: ReturnType<typeof makeApi>, card: Card, signal: AbortSignal) {
  for (const mode of ['processed', 'original'] as const) {
    try {
      return await api.blob(imagePathFor(card, 'front', mode), signal);
    } catch {
      signal.throwIfAborted();
    }
  }
  throw new Error('表面の画像を読み込めませんでした。');
}

function fitRecord(record: HTMLElement) {
  const overflows = () => [record, ...record.querySelectorAll<HTMLElement>('.printImages, .printInformation')]
    .some(element => element.scrollHeight > element.clientHeight + 1);
  if (overflows()) record.classList.add('printRecordCompact');
  if (overflows()) record.classList.add('printRecordExpanded');
}

export async function printCard(api: ReturnType<typeof makeApi>, card: Card, signal: AbortSignal) {
  const urls: string[] = [];
  let frame: HTMLIFrameElement | undefined;
  let root: Root | undefined;
  const previousFocus = document.activeElement;
  try {
    const blob = await imageBlob(api, card, signal);
    signal.throwIfAborted();
    const imageSrc = URL.createObjectURL(blob);
    urls.push(imageSrc);

    frame = document.createElement('iframe');
    frame.title = '名刺の印刷帳票';
    frame.setAttribute('aria-hidden', 'true');
    frame.style.cssText = 'position:fixed;left:-10000px;top:0;width:210mm;height:297mm;border:0;';
    document.body.append(frame);
    const printWindow = frame.contentWindow;
    const printDocument = frame.contentDocument;
    if (!printWindow || !printDocument) throw new Error('印刷用の画面を開けませんでした。');
    printDocument.open();
    printDocument.write(`<!doctype html><html lang="ja"><head><meta charset="utf-8"><style>${printStyles}</style></head><body></body></html>`);
    printDocument.close();
    printDocument.title = `名刺 - ${card.person_name || card.company_name || '名刺情報'}`;
    root = createRoot(printDocument.body);
    flushSync(() => root!.render(<CardPrintRecord card={card} imageSrc={imageSrc} />));
    await Promise.all([printDocument.fonts.ready, ...Array.from(printDocument.images, image => image.decode())]);
    signal.throwIfAborted();
    fitRecord(printDocument.querySelector<HTMLElement>('.printRecord')!);

    // Keep the document and blob URLs alive through both printing and cancellation.
    await new Promise<void>((resolve, reject) => {
      const clear = () => {
        printWindow.removeEventListener('afterprint', done);
        signal.removeEventListener('abort', abort);
      };
      const done = () => { clear(); resolve(); };
      const abort = () => { clear(); reject(signal.reason); };
      printWindow.addEventListener('afterprint', done);
      signal.addEventListener('abort', abort, { once: true });
      try {
        signal.throwIfAborted();
        printWindow.focus();
        printWindow.print();
      } catch (error) {
        clear();
        reject(error);
      }
    });
  } finally {
    root?.unmount();
    frame?.remove();
    urls.forEach(url => URL.revokeObjectURL(url));
    if (!signal.aborted && previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
  }
}
