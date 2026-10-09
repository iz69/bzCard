import { apiUrl, type Session } from '../deployment';

export class HttpError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

export function makeApi(session: Session) {
  const lifetime = new AbortController();
  const headers = {
    Authorization: `Bearer ${session.token}`,
  };

  async function request(path: string, init: RequestInit = {}) {
    const response = await fetch(apiUrl(session.apiBase, path), {
      ...init,
      signal: init.signal ? AbortSignal.any([init.signal, lifetime.signal]) : lifetime.signal,
      headers: {
        ...headers,
        ...(init.headers || {}),
      },
    });
    if (!response.ok) {
      const text = await response.text();
      throw new HttpError(response.status, formatHttpError(response, text));
    }
    if (response.status === 204) return null;
    return response.json();
  }

  return {
    dispose: () => lifetime.abort(),
    get: (path: string, signal?: AbortSignal) => request(path, { signal }),
    post: (path: string, body: unknown) =>
      request(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    patch: (path: string, body: unknown) =>
      request(path, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    put: (path: string, body: unknown) =>
      request(path, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    delete: (path: string, body?: unknown) =>
      request(path, {
        method: 'DELETE',
        headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      }),
    postForm: (path: string, body: FormData) =>
      request(path, {
        method: 'POST',
        body,
      }),
    blob: async (path: string) => {
      const response = await fetch(apiUrl(session.apiBase, path), { headers, signal: lifetime.signal });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return response.blob();
    },
  };
}

export function formatHttpError(response: Response, text: string) {
  if (!text) return `${response.status} ${response.statusText}`;
  try {
    const json = JSON.parse(text);
    if (typeof json.detail === 'string') {
      return `${response.status}: ${json.detail}`;
    }
    return `${response.status}: ${JSON.stringify(json)}`;
  } catch {
    return `${response.status}: ${text}`;
  }
}
