// Run against the separately built UI; every API request is synthetic.
import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';

const base = process.env.BZCARD_TEST_UI_URL || 'http://127.0.0.1:15175/bzcard/';
const browser = await chromium.launch({
  executablePath: process.env.BROWSER_EXECUTABLE,
  args: process.env.BROWSER_ARGS ? JSON.parse(process.env.BROWSER_ARGS) : ['--no-sandbox', '--disable-dev-shm-usage'],
  headless: true,
});
const errors = [];
const card = { id: 'card-a', person_name: 'USER_A_PRIVATE_NAME', company_name: 'USER_A_PRIVATE_COMPANY', status: 'ready', revision: 1, created_at: '2026-01-01', updated_at: '2026-01-01' };
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aGxkAAAAASUVORK5CYII=', 'base64');
function watch(page) { page.on('pageerror', e => errors.push(e.message)); }
async function fulfill(route, body, status = 200) { await route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)}); }
try {
  const page = await browser.newPage(); watch(page);
  await page.addInitScript(() => {
    localStorage.setItem('bzcard.apiBase', '/bzcard-api');
    localStorage.setItem('bzcard.sessionToken', 'user-a');
  });
  let bRequests = 0;
  await page.route('**/bzcard-api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const userB = route.request().headers().authorization === 'Bearer user-b';
    if (path.endsWith('/bootstrap-status')) return fulfill(route, {needs_bootstrap: false});
    if (path.endsWith('/login')) return fulfill(route, {session_token: 'user-b'});
    if (path.endsWith('/auth/me')) return fulfill(route, {user: {login_id: userB ? 'b':'a', role:'user'}, multi_user_enabled: true});
    if (path.endsWith('/versions')) return fulfill(route, {ocr:{},llm:{}});
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
  await page.locator('.imagePanel img').waitFor();
  const oldImage = await page.locator('.imagePanel img').getAttribute('src');
  await page.getByRole('button',{name:'メニュー',exact:true}).click();
  await page.getByRole('menuitem',{name:'ログアウト',exact:true}).click();
  await page.getByLabel('ログインID').fill('b');
  await page.getByLabel('パスワード',{exact:true}).fill('synthetic-password');
  await page.getByRole('button',{name:'ログイン',exact:true}).click();
  await page.waitForFunction(() => localStorage.getItem('bzcard.sessionToken') === 'user-b');
  await page.getByText('synthetic failure',{exact:false}).waitFor();
  assert.ok(bRequests > 0);
  assert.equal(await page.getByText(card.person_name,{exact:true}).count(),0);
  assert.equal(await page.getByText(card.company_name,{exact:true}).count(),0);
  assert.equal(await page.locator(`img[src="${oldImage}"]`).count(),0);
  console.log('PASS: account switch clears list, detail and images when the new list fails');

  const liff = page;
  await liff.unroute('**/bzcard-api/**');
  await liff.addInitScript(() => { window.liff = {init:async()=>{},isLoggedIn:()=>true,getIDToken:()=> 'synthetic-id-token',getProfile:async()=>({displayName:'test'})}; });
  let reads = 0, failDetail = true;
  const allCards = Array.from({length:15},(_,i)=>({...card,id:`liff-${i}`,person_name:`名刺 ${i}`,status:'ready'}));
  await liff.route('**/bzcard-api/**', async route => {
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
} finally { await browser.close(); }
