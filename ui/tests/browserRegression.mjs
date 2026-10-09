// Run against the separately built UI; every API request is synthetic.
import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';
import { createSessionStore, normalizeApiBase } from '../src/deployment.ts';
import { checkContactPagination } from './contactPagination.mjs';
import { checkMobileRegression } from './mobileRegression.mjs';

const base = process.env.BZCARD_TEST_UI_URL || 'http://127.0.0.1:15175/bzcard/';
const expectedBuildVersion = process.env.BZCARD_TEST_BUILD_VERSION;
const expectedBuildLabel = expectedBuildVersion || 'dev';
const apiBase = normalizeApiBase(process.env.BZCARD_TEST_API_BASE_PATH ?? '/bzcard-api');
const uiUrl = new URL(base);
const publicApiUrl = new URL(apiBase || '/', uiUrl);
const publicApiPath = publicApiUrl.pathname.replace(/\/+$/, '');
const sessionKey = createSessionStore(localStorageStub(), uiUrl.pathname, apiBase, uiUrl.origin).key;
const migrateLegacy = uiUrl.pathname === '/bzcard/' && apiBase === '/bzcard-api';
const apiRoute = url => url.origin === publicApiUrl.origin
  && (url.pathname.startsWith(publicApiPath + '/api/') || url.pathname.startsWith(publicApiPath + '/line/'));
const browser = process.env.BROWSER_CDP_URL
  ? await chromium.connectOverCDP(process.env.BROWSER_CDP_URL)
  : await chromium.launch({
    executablePath: process.env.BROWSER_EXECUTABLE,
    args: process.env.BROWSER_ARGS ? JSON.parse(process.env.BROWSER_ARGS) : ['--no-sandbox', '--disable-dev-shm-usage'],
    headless: true,
  });
const context = await browser.newContext();
const errors = [];
const card = { id: 'card-a', person_name: 'USER_A_PRIVATE_NAME', company_name: 'USER_A_PRIVATE_COMPANY', status: 'ready', revision: 1, created_at: '2026-01-01', updated_at: '2026-01-01' };
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aGxkAAAAASUVORK5CYII=', 'base64');
function watch(page) { page.on('pageerror', e => errors.push(e.message)); }
async function fulfill(route, body, status = 200) { await route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)}); }
function localStorageStub() { return {getItem:()=>null,setItem:()=>{},removeItem:()=>{}}; }
try {
  const page = await context.newPage(); watch(page);
  await page.addInitScript(({key,apiBase,legacy}) => {
    if (localStorage.getItem(key) !== null) return;
    if (legacy) {
      localStorage.setItem('bzcard.apiBase', apiBase);
      localStorage.setItem('bzcard.sessionToken', 'user-a');
    } else {
      localStorage.setItem(key, JSON.stringify({apiBase,token:'user-a'}));
    }
  }, {key:sessionKey,apiBase,legacy:migrateLegacy});
  let bRequests = 0;
  let versionsReply = {api:{version:'v0.9.3'},ocr:{},llm:{}};
  await page.route(apiRoute, async route => {
    const path = new URL(route.request().url()).pathname;
    const userB = route.request().headers().authorization === 'Bearer user-b';
    if (path.endsWith('/bootstrap-status')) return fulfill(route, {needs_bootstrap: false});
    if (path.endsWith('/login')) return fulfill(route, {session_token: 'user-b'});
    if (path.endsWith('/auth/me')) return fulfill(route, {user: {login_id: userB ? 'b':'a', role:'user'}, multi_user_enabled: true});
    if (path.endsWith('/versions')) return fulfill(route, versionsReply);
    if (path.endsWith('/logout')) return fulfill(route, {});
    if (path.endsWith('/contacts')) {
      if (userB) { bRequests++; return fulfill(route, {detail:'synthetic failure'}, 503); }
      return fulfill(route, {items:[{...card, representative_card_id:card.id, card_count:1,revision:'a'}]});
    }
    if (path.endsWith('/contacts/card-a')) return fulfill(route, {...card,representative_card_id:card.id,card_count:1,cards:[card]});
    if (path.endsWith('/cards/card-a')) return fulfill(route, card);
    if (path.endsWith('/corrections')) return fulfill(route, {items:[]});
    if (path.endsWith('-image') || path.endsWith('/thumbnail')) return route.fulfill({contentType:'image/png',body:png});
    return fulfill(route, {detail:'not found'}, 404);
  });
  await page.goto(base);
  await page.getByText(card.person_name,{exact:true}).first().waitFor();
  assert.equal(await page.locator('.topbar .uiBuildVersion').textContent(), 'WebUI ' + expectedBuildLabel);
  await page.locator('.topbar .apiBuildVersion').filter({hasText:'API v0.9.3'}).waitFor();
  console.log('PASS: WebUI and API display their own build versions');
  if (migrateLegacy) {
    assert.equal(await page.evaluate(() => localStorage.getItem('bzcard.sessionToken')), null);
    assert.equal(await page.evaluate(key => JSON.parse(localStorage.getItem(key)).token, sessionKey), 'user-a');
    console.log('PASS: original deployment migrates its legacy session');
  }
  versionsReply = {ocr:{},llm:{}};
  await page.reload();
  await page.locator('.topbar .apiBuildVersion').filter({hasText:'API 不明'}).waitFor();
  await page.getByText(card.person_name,{exact:true}).first().waitFor();
  versionsReply = {api:{version:'dev'},ocr:{},llm:{}};
  await page.reload();
  await page.locator('.topbar .apiBuildVersion').filter({hasText:'API dev'}).waitFor();
  assert.equal(await page.locator('.topbar .buildVersion').textContent(), `WebUI ${expectedBuildLabel} / API dev`);
  await page.getByText(card.person_name,{exact:true}).first().waitFor();
  console.log('PASS: development API builds and older APIs without version metadata remain usable');
  await page.locator('.imagePanel img').waitFor();
  const oldImage = await page.locator('.imagePanel img').getAttribute('src');
  await page.getByRole('button',{name:'メニュー',exact:true}).click();
  await page.getByRole('menuitem',{name:'ログアウト',exact:true}).click();
  await page.getByLabel('ログインID').fill('b');
  await page.getByLabel('パスワード',{exact:true}).fill('synthetic-password');
  await page.getByRole('button',{name:'ログイン',exact:true}).click();
  await page.waitForFunction(key => JSON.parse(localStorage.getItem(key)).token === 'user-b', sessionKey);
  await page.getByText('synthetic failure',{exact:false}).waitFor();
  assert.ok(bRequests > 0);
  assert.equal(await page.getByText(card.person_name,{exact:true}).count(),0);
  assert.equal(await page.getByText(card.company_name,{exact:true}).count(),0);
  assert.equal(await page.locator(`img[src="${oldImage}"]`).count(),0);
  console.log('PASS: account switch clears list, detail and images when the new list fails');

  if (apiBase === '') {
    await page.reload();
    await page.getByText('synthetic failure',{exact:false}).waitFor();
    assert.equal(await page.evaluate(key => JSON.parse(localStorage.getItem(key)).apiBase, sessionKey), '');
    console.log('PASS: root API settings survive reload');
  }

  const sibling = await page.context().newPage();
  await sibling.route('**/*', route => route.fulfill({contentType:'text/html',body:'<!doctype html><title>Storage test</title>'}));
  await sibling.goto(base);
  const requestsBefore = bRequests;
  const unrelatedKey = sessionKey + ':another-installation';
  const storageReceived = page.evaluate(key => new Promise(resolve => {
    window.addEventListener('storage', function changed(event) {
      if (event.key === key) {
        window.removeEventListener('storage', changed);
        resolve();
      }
    });
  }), unrelatedKey);
  await sibling.evaluate(key => localStorage.setItem(key, JSON.stringify({apiBase:'/other-api',token:'other-user'})), unrelatedKey);
  await storageReceived;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(bRequests, requestsBefore);
  await sibling.evaluate(({key,apiBase}) => localStorage.setItem(key, JSON.stringify({apiBase,token:'user-a'})), {key:sessionKey,apiBase});
  await page.getByText(card.person_name,{exact:true}).first().waitFor();
  assert.equal(await page.evaluate(key => JSON.parse(localStorage.getItem(key)).token, unrelatedKey), 'other-user');
  await sibling.close();
  console.log('PASS: unrelated installations do not affect this tab; matching sessions synchronize');

  const liff = page;
  await liff.unroute(apiRoute);
  await liff.addInitScript(() => { window.liff = {init:async()=>{},isLoggedIn:()=>true,getIDToken:()=> 'synthetic-id-token',getProfile:async()=>({displayName:'test'})}; });
  let reads = 0, failDetail = true;
  const allCards = Array.from({length:15},(_,i)=>({...card,id:`liff-${i}`,person_name:`名刺 ${i}`,status:'ready'}));
  await liff.route(apiRoute, async route => {
    const url = new URL(route.request().url()); const path = url.pathname;
    if (path.endsWith('/liff-config')) return fulfill(route,{liff_id:'test'});
    if (path.endsWith('/line/auth/login')) return fulfill(route,{session_token:'line-user'});
    if (path.endsWith('/cards/liff-0')) {
      if (failDetail) { failDetail=false; return fulfill(route,{detail:'synthetic detail failure'},503); }
      reads++;
      return fulfill(route,{...allCards[0], status:reads===1?'queued':'ready', person_name:reads===1?'':'処理完了した名刺', revision:reads});
    }
    if (path.endsWith('/cards')) return fulfill(route,{items:url.searchParams.get('q') ? allCards.filter(c=>c.person_name.includes(url.searchParams.get('q'))) : allCards});
    if (path.endsWith('/corrections')) return fulfill(route,{items:[]});
    if (path.endsWith('-image') || path.endsWith('/thumbnail')) return route.fulfill({contentType:'image/png',body:png});
    return fulfill(route,{},404);
  });
  await liff.goto(base+'liff?connection=test&card=liff-0');
  await liff.getByText('synthetic detail failure',{exact:false}).waitFor();
  await liff.getByRole('button',{name:'再試行',exact:true}).click();
  await liff.getByRole('heading',{name:'処理完了した名刺',exact:true}).waitFor({timeout:12000});
  assert.ok(reads >= 2);
  await liff.getByRole('button',{name:'一覧へ戻る',exact:true}).click();
  await liff.locator('.liffCardRow').first().waitFor();
  assert.equal(await liff.locator('.liffCardRow').count(),12);
  await liff.getByRole('button',{name:/さらに表示/}).click();
  assert.equal(await liff.locator('.liffCardRow').count(),15);
  await liff.getByPlaceholder('氏名・会社名を検索').fill('名刺 14');
  await liff.getByText('名刺 14',{exact:true}).waitFor();
  await liff.waitForFunction(()=>document.querySelectorAll('.liffCardRow').length===1);
  console.log('PASS: LIFF shows failures, retries, follows queued→ready, returns to list, searches and shows more');
  assert.deepEqual(errors,[]);
  await checkContactPagination(browser, base, apiBase);
  await checkMobileRegression(browser, base, apiBase);
} finally { await context.close(); await browser.close(); }
