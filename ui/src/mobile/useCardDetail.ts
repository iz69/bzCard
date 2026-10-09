import { useCallback, useEffect, useState } from 'react';
import { makeApi } from '../shared/api';
import { terminalCardStatuses } from '../shared/cardFields';
import { errorMessage } from '../shared/format';
import type { Card, Contact } from '../shared/types';

export function useCardDetail(api: ReturnType<typeof makeApi>, contactId: string, cardId: string, revision?: string | number) {
  const [card, setCard] = useState<Card | null>(null);
  const [relatedCards, setRelatedCards] = useState<Card[]>([]);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const reload = useCallback(() => setRefresh(value => value + 1), []);

  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    const controller = new AbortController();
    const load = async () => {
      try {
        const [next, contact]: [Card, Contact] = await Promise.all([
          api.get(`/api/cards/${encodeURIComponent(cardId)}`, controller.signal),
          api.get(`/api/contacts/${encodeURIComponent(cardId)}`, controller.signal),
        ]);
        if (!active) return;
        setCard(next);
        setRelatedCards(contact.cards || []);
        setError('');
        if (!terminalCardStatuses.has(next.status) || contact.has_in_progress) {
          timer = window.setTimeout(() => { if (document.visibilityState !== 'hidden') void load(); }, 5000);
        }
      } catch (error) {
        if (active && !controller.signal.aborted) setError(errorMessage(error));
      }
    };
    const visible = () => {
      if (document.visibilityState !== 'hidden') { window.clearTimeout(timer); void load(); }
    };
    void load();
    document.addEventListener('visibilitychange', visible);
    return () => { active = false; controller.abort(); window.clearTimeout(timer); document.removeEventListener('visibilitychange', visible); };
  }, [api, contactId, cardId, revision, refresh]);

  return { card, relatedCards, error, reload };
}
