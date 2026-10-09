import { useCallback, useEffect, useRef, useState } from 'react';

export type MobileRoute =
  | { screen: 'list' | 'upload' | 'settings' }
  | { screen: 'detail'; contactId: string; cardId: string };

export function useMobileNavigation() {
  const [route, setRoute] = useState<MobileRoute>({ screen: 'list' });
  const current = useRef(route);
  const dirty = useRef(false);
  const scope = useRef(globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`);
  current.current = route;

  const canLeave = useCallback(() => !dirty.current || window.confirm('未保存の変更があります。変更を破棄して移動しますか？'), []);
  const stateFor = useCallback((next: MobileRoute) => ({ ...window.history.state, bzcardMobile: { scope: scope.current, route: next } }), []);

  useEffect(() => {
    // History contains navigation IDs only. Every login starts a fresh scope,
    // so Back cannot resurrect another account's screen or private data.
    window.history.replaceState(stateFor({ screen: 'list' }), '', window.location.href);
    const pop = (event: PopStateEvent) => {
      if (!canLeave()) {
        window.history.pushState(stateFor(current.current), '', window.location.href);
        return;
      }
      const saved = event.state?.bzcardMobile;
      dirty.current = false;
      setRoute(saved?.scope === scope.current ? saved.route : { screen: 'list' });
    };
    const unload = (event: BeforeUnloadEvent) => {
      if (dirty.current) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('popstate', pop);
    window.addEventListener('beforeunload', unload);
    return () => { window.removeEventListener('popstate', pop); window.removeEventListener('beforeunload', unload); };
  }, [canLeave, stateFor]);

  const navigate = useCallback((next: MobileRoute) => {
    if (!canLeave()) return;
    dirty.current = false;
    window.history.pushState(stateFor(next), '', window.location.href);
    setRoute(next);
  }, [canLeave, stateFor]);
  const setDirty = useCallback((value: boolean) => { dirty.current = value; }, []);
  const back = useCallback(() => window.history.back(), []);
  return { route, navigate, setDirty, back };
}
