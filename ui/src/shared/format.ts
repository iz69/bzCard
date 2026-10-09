import type { Card } from './types';

export function formatDate(value: string) {
  if (!value) return '-';
  return new Date(value).toLocaleString();
}

export function directionLabel(value?: string) {
  if (value === 'vertical') return '縦書き';
  if (value === 'horizontal') return '横書き';
  if (value === 'auto') return '自動';
  return '-';
}

export function imagePathFor(card: Card, side: 'front' | 'back', mode: 'processed' | 'original') {
  const version = `?v=${encodeURIComponent(String(card.revision ?? card.updated_at))}`;
  if (side === 'back') {
    return `/api/cards/${card.id}/${mode === 'processed' ? 'back-processed-image' : 'back-original-image'}${version}`;
  }
  return `/api/cards/${card.id}/${mode === 'processed' ? 'processed-image' : 'original-image'}${version}`;
}

export function openGoogleMaps(address: string) {
  const url = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(address)}`;
  window.open(url, '_blank', 'noopener,noreferrer');
}

export function combinedOcrText(card: Card) {
  const front = (card.ocr_text || '').trim();
  const back = (card.back_ocr_text || '').trim();
  if (front && back) return `【表面】\n${front}\n\n【裏面】\n${back}`;
  if (front) return front;
  if (back) return `【裏面】\n${back}`;
  return '';
}

export function formatJson(value?: string) {
  if (!value) return '';
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

export function parseTags(value: string) {
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

export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}
