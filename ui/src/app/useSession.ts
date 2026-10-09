import { useEffect, useRef, useState } from 'react';
import type { Session } from '../deployment';
import { loadSession, sessionStore } from '../shared/config';

export function useSession() {
  const [session, setSession] = useState<Session>(loadSession);
  const activeSession = useRef(session);
  activeSession.current = session;
  useEffect(() => {
    const changed = (event: StorageEvent) => {
      if (event.storageArea === localStorage && (event.key === null || event.key === sessionStore.key)) {
        setSession(loadSession());
      }
    };
    window.addEventListener('storage', changed);
    return () => window.removeEventListener('storage', changed);
  }, []);
  function saveSession(next: Session) {
    // An old account's outstanding callback cannot replace the new session.
    if (activeSession.current !== session) return;
    const saved = sessionStore.save(next);
    activeSession.current = saved;
    setSession(saved);
  }
  return { session, saveSession };
}
