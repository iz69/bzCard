import type { ReactNode } from 'react';
import type { Card } from '../shared/types';
import { parseTags } from '../shared/format';

function PrintField({ label, children, wide = false }: { label: string; children: ReactNode; wide?: boolean }) {
  return <div className={`printField${wide ? ' printFieldWide' : ''}`}>
    <span className="printLabel">{label}</span>
    <span className="printValue">{children}</span>
  </div>;
}

// Each record owns its border and layout so it can later be repeated for a batch.
export function CardPrintRecord({ card, imageSrc }: { card: Card; imageSrc: string }) {
  const address = [card.postal_code?.trim() && `〒${card.postal_code.trim()}`, card.address?.trim()].filter(Boolean).join(' ');
  const role = [card.department?.trim(), card.title?.trim()].filter(Boolean).join(' ／ ');
  const tags = parseTags(card.tags || '').join(' ／ ');
  const memo = card.memo?.trim();
  const contacts: Array<[string, string | undefined, boolean?]> = [
    ['住所', address, true],
    ['携帯', card.mobile, false],
    ['電話', card.tel, false],
    ['FAX', card.fax, false],
    ['Web', card.website, false],
    ['メール', card.email, true],
  ];

  return <article className="printRecord" aria-label="名刺情報">
    <div className="printImages">
      <div className="printImageSide">
        <div className="printImageLabel">表面</div>
        <img src={imageSrc} alt="名刺の表面" />
      </div>
    </div>
    <div className="printInformation">
      {(card.person_name?.trim() || card.person_name_kana?.trim()) && <div className="printNameRow">
        {card.person_name?.trim() && <span className="printPersonName">{card.person_name.trim()}</span>}
        {card.person_name_kana?.trim() && <span className="printKana">{card.person_name_kana.trim()}</span>}
      </div>}
      {card.company_name?.trim() && <div className="printCompany">{card.company_name.trim()}</div>}
      {role && <div className="printRole">{role}</div>}
      <div className="printContacts">
        {contacts.filter(([, value]) => value?.trim()).map(([label, value, wide]) =>
          <PrintField key={label} label={label} wide={wide}>{value?.trim()}</PrintField>)}
      </div>
    </div>
    {(tags || memo) && <div className="printNotes">
      {tags && <PrintField label="タグ">{tags}</PrintField>}
      {memo && <PrintField label="メモ">{memo}</PrintField>}
    </div>}
  </article>;
}
