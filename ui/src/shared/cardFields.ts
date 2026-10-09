import type { Card } from './types';

export const fields: Array<[keyof Card, string]> = [
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
export const wideFieldKeys = new Set<keyof Card>([
  'company_name',
  'address',
  'email',
  'website',
  'tags',
  'memo',
]);
export const rowBreakFieldKeys = new Set<keyof Card>(['postal_code', 'mobile', 'tel']);
export const terminalCardStatuses = new Set(['ready', 'not_card', 'error']);
