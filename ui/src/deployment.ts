export type Session = {
  apiBase: string;
  token: string;
};

type SessionStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

export function normalizeApiBase(base: string): string {
  return base.trim().replace(/\/+$/, '');
}

export function apiUrl(base: string, path: string): string {
  return normalizeApiBase(base) + '/' + path.replace(/^\/+/, '');
}

export function normalizeUiBasePath(base: string): string {
  const path = new URL(base || '/', 'https://bzcard.invalid/').pathname
    .split('/').map(segment => encodeURIComponent(decodeURIComponent(segment))).join('/').replace(/\/+/g, '/');
  return path.replace(/\/+$/, '') + '/';
}

export function isLiffLocation(pathname: string, search: string, uiBasePath: string): boolean {
  const base = normalizeUiBasePath(uiBasePath);
  const path = normalizeUiBasePath(pathname).replace(/\/+$/, '') || '/';
  const home = base.replace(/\/+$/, '') || '/';
  const route = path === home ? '' : path.startsWith(base) ? path.slice(base.length) : null;
  return route !== null && (route === 'liff' || new URLSearchParams(search).get('mode') === 'liff');
}

export function createSessionStore(
  storage: SessionStorage,
  uiBasePath: string,
  defaultApiBase: string,
  origin: string,
) {
  const uiBase = normalizeUiBasePath(uiBasePath);
  const defaultBase = normalizeApiBase(defaultApiBase);
  const publicApiBase = new URL(defaultBase || '/', new URL(uiBase, origin)).href.replace(/\/+$/, '');
  // The browser already separates origins. Also separate UI installations and
  // configured API endpoints so a configuration change cannot reuse a token.
  const key = 'bzcard.session.v1:' + encodeURIComponent(uiBase) + ':' + encodeURIComponent(publicApiBase);
  const emptySession = (): Session => ({ apiBase: defaultBase, token: '' });

  function save(session: Session): Session {
    const normalized = { apiBase: normalizeApiBase(session.apiBase), token: session.token };
    // Store the API endpoint and token together so other tabs see one update.
    storage.setItem(key, JSON.stringify(normalized));
    return normalized;
  }

  function load(): Session {
    const value = storage.getItem(key);
    if (value !== null) {
      try {
        const saved: unknown = JSON.parse(value);
        if (saved && typeof saved === 'object' && 'apiBase' in saved && 'token' in saved
          && typeof saved.apiBase === 'string' && typeof saved.token === 'string') {
          return { apiBase: normalizeApiBase(saved.apiBase), token: saved.token };
        }
      } catch {
        // Corrupt browser storage starts a fresh session.
      }
      return emptySession();
    }

    // Unscoped legacy keys do not identify their installation. Only claim them
    // for the original default deployment, preserving manual API overrides.
    if (uiBase === '/bzcard/' && publicApiBase === new URL('/bzcard-api', origin).href) {
      const apiBase = storage.getItem('bzcard.apiBase');
      const token = storage.getItem('bzcard.sessionToken');
      if (apiBase !== null || token !== null) {
        const migrated = save({ apiBase: apiBase ?? defaultBase, token: token ?? '' });
        for (const legacyKey of [
          'bzcard.apiBase', 'bzcard.sessionToken', 'bzcard.token',
          'bzcard.lineSessionToken', 'bzcard.lineSessionExpiresAt',
        ]) storage.removeItem(legacyKey);
        return migrated;
      }
    }
    return emptySession();
  }

  return { key, load, save };
}
