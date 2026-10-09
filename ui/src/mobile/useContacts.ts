import { useCallback, useEffect, useRef, useState } from 'react';
import { HttpError, makeApi } from '../shared/api';
import { errorMessage } from '../shared/format';
import type { Contact, ContactPage } from '../shared/types';

type Snapshot = { items: Contact[]; cursor: string | null; revision: string; criteria: string };

export function useContacts(api: ReturnType<typeof makeApi>) {
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [processing, setProcessing] = useState(false);
  const snapshot = useRef<Snapshot>({ items: [], cursor: null, revision: '', criteria: '' });
  const active = useRef<AbortController | null>(null);
  const sequence = useRef(0);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  const reload = useCallback(async () => {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    const requestId = ++sequence.current;
    const criteria = JSON.stringify([debouncedQuery, status]);
    const previous = snapshot.current;
    const same = criteria === previous.criteria;
    const targetCount = same ? Math.max(50, previous.items.length) : 50;
    setLoading(true);
    setLoadingMore(false);
    setError('');
    try {
      let page: ContactPage = { items: [] };
      let items: Contact[] = [];
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          const params = new URLSearchParams({ limit: '50' });
          if (debouncedQuery) params.set('q', debouncedQuery);
          if (status) params.set('status', status);
          if (same && previous.revision) params.set('known_revision', previous.revision);
          page = await api.get(`/api/contacts?${params}`, controller.signal);
          if (controller.signal.aborted || requestId !== sequence.current) return;
          setProcessing(Boolean(page.has_in_progress));
          if (page.unchanged) return;
          items = page.items || [];
          while (items.length < targetCount && page.next_cursor) {
            params.delete('known_revision');
            params.set('cursor', page.next_cursor);
            page = await api.get(`/api/contacts?${params}`, controller.signal);
            items.push(...(page.items || []));
          }
          break;
        } catch (error) {
          if (!(error instanceof HttpError && error.status === 409 && attempt === 0)) throw error;
        }
      }
      if (controller.signal.aborted || requestId !== sequence.current) return;
      snapshot.current = { items, cursor: page.next_cursor || null, revision: page.revision || '', criteria };
      setContacts(items);
      setNextCursor(snapshot.current.cursor);
    } catch (error) {
      if (!controller.signal.aborted && requestId === sequence.current) setError(errorMessage(error));
    } finally {
      if (requestId === sequence.current) { setLoading(false); active.current = null; }
    }
  }, [api, debouncedQuery, status]);

  const loadMore = useCallback(async () => {
    const previous = snapshot.current;
    if (active.current || !previous.cursor || previous.criteria !== JSON.stringify([debouncedQuery, status])) return;
    const controller = new AbortController();
    active.current = controller;
    const requestId = ++sequence.current;
    setLoadingMore(true);
    setError('');
    try {
      const params = new URLSearchParams({ limit: '50', cursor: previous.cursor });
      if (debouncedQuery) params.set('q', debouncedQuery);
      if (status) params.set('status', status);
      const page: ContactPage = await api.get(`/api/contacts?${params}`, controller.signal);
      if (controller.signal.aborted || requestId !== sequence.current) return;
      const items = [...previous.items, ...(page.items || [])];
      snapshot.current = { ...previous, items, cursor: page.next_cursor || null, revision: page.revision || '' };
      setContacts(items);
      setNextCursor(snapshot.current.cursor);
      setProcessing(Boolean(page.has_in_progress));
    } catch (error) {
      if (!controller.signal.aborted && requestId === sequence.current) {
        if (error instanceof HttpError && error.status === 409) await reload();
        else setError(errorMessage(error));
      }
    } finally {
      if (requestId === sequence.current) { setLoadingMore(false); active.current = null; }
    }
  }, [api, debouncedQuery, status, reload]);

  useEffect(() => {
    void reload();
    return () => { ++sequence.current; active.current?.abort(); active.current = null; };
  }, [reload]);

  useEffect(() => {
    if (!processing || error) return;
    const refresh = () => {
      if (document.visibilityState !== 'hidden' && !active.current) void reload();
    };
    const timer = window.setInterval(refresh, 5000);
    document.addEventListener('visibilitychange', refresh);
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', refresh); };
  }, [processing, error, reload]);

  return { query, setQuery, status, setStatus, contacts, nextCursor, loading, loadingMore, error, reload, loadMore };
}
