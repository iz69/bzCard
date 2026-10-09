import { useCallback, useEffect, useRef, useState } from 'react';
import { mergeServerCard } from '../cardSync';
import { makeApi } from '../shared/api';
import { fields } from '../shared/cardFields';
import type { Card } from '../shared/types';

const editable = fields.map(([key]) => key);

export function useCardEditor(api: ReturnType<typeof makeApi>, card: Card | null) {
  const [draft, setDraft] = useState<Card | null>(null);
  const [saving, setSaving] = useState(false);
  const previous = useRef<Card | null>(null);
  useEffect(() => {
    if (!card) return;
    const last = previous.current;
    setDraft(current => current && last ? mergeServerCard(current, last, card, editable) : card);
    previous.current = card;
  }, [card]);
  const baseline = previous.current;
  const dirty = Boolean(draft && baseline && editable.some(key => (draft[key] ?? '') !== (baseline[key] ?? '')));
  const save = useCallback(async () => {
    if (!draft || saving) return;
    const submitted = draft;
    setSaving(true);
    try {
      const payload = Object.fromEntries(editable.map(key => [key, submitted[key]]));
      const saved: Card = await api.patch(`/api/cards/${encodeURIComponent(submitted.id)}`, payload);
      previous.current = saved;
      setDraft(current => current ? mergeServerCard(current, submitted, saved, editable) : saved);
      return saved;
    } finally { setSaving(false); }
  }, [api, draft, saving]);
  return { draft, setDraft, dirty, saving, save };
}
