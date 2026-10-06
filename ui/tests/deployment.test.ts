import assert from 'node:assert/strict';
import test from 'node:test';
import { apiUrl, createSessionStore, isLiffLocation, normalizeApiBase, normalizeUiBasePath } from '../src/deployment.ts';

function storage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
    removeItem: (key: string) => { values.delete(key); },
  };
}

const origin = 'https://cards.example';

test('encoded and literal public paths identify the same home, LIFF route and session', () => {
  for (const base of ['/v1.0+cards/', '/名刺 管理/', '/cards/$special/']) {
    const encoded = normalizeUiBasePath(base);
    assert.equal(normalizeUiBasePath(encoded), encoded);
    assert.equal(isLiffLocation(base, '', encoded), false);
    assert.equal(isLiffLocation(encoded + 'liff', '', base), true);
    const saved = storage();
    assert.equal(createSessionStore(saved, base, '/api', origin).key,
      createSessionStore(saved, encoded, '/api', origin).key);
  }
});

test('root API URLs stay on the current origin for login, LIFF and images', () => {
  for (const base of ['', '/', '///', ' / ']) {
    for (const path of ['/api/auth/login', '/line/auth/login', '/api/cards/a/thumbnail?v=1']) {
      const url = new URL(apiUrl(base, path), origin);
      assert.equal(url.origin, origin);
      assert.equal(url.pathname + url.search, path);
    }
  }
});

test('API subpaths and absolute API URLs tolerate trailing slashes', () => {
  assert.equal(apiUrl('/tools/cards-api///', '/api/cards'), '/tools/cards-api/api/cards');
  assert.equal(apiUrl('https://api.example///', '/api/cards'), 'https://api.example/api/cards');
  assert.equal(normalizeApiBase(' / '), '');
});

test('a UI base ending in liff is a home page, and its liff child is the LIFF page', () => {
  for (const base of ['/', '/bzcard/', '/tools/liff/', '/liff/']) {
    const home = base === '/' ? '/' : base.slice(0, -1);
    assert.equal(isLiffLocation(home, '', base), false);
    assert.equal(isLiffLocation(base, '', base), false);
    assert.equal(isLiffLocation(base + 'liff', '', base), true);
    assert.equal(isLiffLocation(base + 'liff/', '', base), true);
    assert.equal(isLiffLocation(base, '?mode=liff&connection=a', base), true);
    assert.equal(isLiffLocation(base + 'other/liff', '', base), false);
  }
  assert.equal(isLiffLocation('/another/liff', '?mode=liff', '/bzcard/'), false);
});

test('installations on the same origin retain independent API settings and tokens', () => {
  const saved = storage();
  const first = createSessionStore(saved, '/cards-a/', '/api-a', origin);
  const second = createSessionStore(saved, '/cards-b/', '/api-b', origin);
  first.save({ apiBase: '/custom-api-a/', token: 'user-a' });
  second.save({ apiBase: '/', token: 'user-b' });
  assert.deepEqual(first.load(), { apiBase: '/custom-api-a', token: 'user-a' });
  assert.deepEqual(second.load(), { apiBase: '', token: 'user-b' });
  second.save({ apiBase: '/', token: '' });
  assert.equal(first.load().token, 'user-a');
  assert.deepEqual(second.load(), { apiBase: '', token: '' });
});

test('changing the configured API endpoint starts a fresh session', () => {
  const saved = storage();
  const old = createSessionStore(saved, '/cards/', '/old-api', origin);
  old.save({ apiBase: '/old-api', token: 'old-token' });
  const next = createSessionStore(saved, '/cards/', '/new-api', origin);
  assert.deepEqual(next.load(), { apiBase: '/new-api', token: '' });
  assert.equal(old.load().token, 'old-token');
});

test('equivalent API endpoints share the same deployment identity', () => {
  const saved = storage();
  const first = createSessionStore(saved, '/cards', '/cards-api/', origin);
  first.save({ apiBase: '/cards-api', token: 'user-a' });
  const equivalent = createSessionStore(saved, '/cards/', origin + '/cards-api', origin);
  assert.equal(equivalent.key, first.key);
  assert.equal(equivalent.load().token, 'user-a');
});

test('the original deployment migrates legacy login information and manual root API settings once', () => {
  const saved = storage();
  saved.setItem('bzcard.apiBase', '');
  saved.setItem('bzcard.sessionToken', 'legacy-user');
  const original = createSessionStore(saved, '/bzcard/', '/bzcard-api', origin);
  assert.deepEqual(original.load(), { apiBase: '', token: 'legacy-user' });
  assert.equal(saved.getItem('bzcard.apiBase'), null);
  assert.equal(saved.getItem('bzcard.sessionToken'), null);
  original.save({ apiBase: '/', token: '' });
  assert.deepEqual(original.load(), { apiBase: '', token: '' });
});

test('new installations and changed API configurations never claim unscoped legacy tokens', () => {
  const saved = storage();
  saved.setItem('bzcard.apiBase', '/legacy-api');
  saved.setItem('bzcard.sessionToken', 'legacy-user');
  const otherUi = createSessionStore(saved, '/other/', '/bzcard-api', origin);
  const otherApi = createSessionStore(saved, '/bzcard/', '/other-api', origin);
  assert.equal(otherUi.load().token, '');
  assert.equal(otherApi.load().token, '');
  assert.equal(saved.getItem('bzcard.sessionToken'), 'legacy-user');
  const original = createSessionStore(saved, '/bzcard/', '/bzcard-api', origin);
  assert.deepEqual(original.load(), { apiBase: '/legacy-api', token: 'legacy-user' });
});

test('existing scoped sessions take precedence over legacy tokens and malformed storage is recoverable', () => {
  const saved = storage();
  const store = createSessionStore(saved, '/bzcard/', '/bzcard-api', origin);
  store.save({ apiBase: '/bzcard-api', token: 'current-user' });
  saved.setItem('bzcard.sessionToken', 'old-user');
  assert.equal(store.load().token, 'current-user');
  for (const invalid of ['{invalid', 'null', '{"apiBase":null,"token":"old-user"}']) {
    saved.setItem(store.key, invalid);
    assert.deepEqual(store.load(), { apiBase: '/bzcard-api', token: '' });
  }
  store.save({ apiBase: '/', token: 'recovered-user' });
  assert.deepEqual(store.load(), { apiBase: '', token: 'recovered-user' });
});
