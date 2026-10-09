import { createSessionStore, normalizeApiBase, normalizeUiBasePath, type Session } from '../deployment';

export const uiBasePath = normalizeUiBasePath(window.__BZCARD_CONFIG__?.uiBasePath ?? import.meta.env.BASE_URL);
export const defaultApiBase = normalizeApiBase(window.__BZCARD_CONFIG__?.apiBasePath ?? import.meta.env.VITE_API_BASE_PATH ?? '/bzcard-api');
export const uiBuildVersion = import.meta.env.VITE_BUILD_VERSION || 'dev';
export const sessionStore = createSessionStore(localStorage, uiBasePath, defaultApiBase, window.location.origin);
export function loadSession(): Session {
  return sessionStore.load();
}
