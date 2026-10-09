// All data and mutations are synthetic. No live account or card API is used.
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { chromium } from 'playwright-core';
import { createSessionStore, normalizeApiBase } from '../src/deployment.ts';

const phoneUA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1';
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aGxkAAAAASUVORK5CYII=', 'base64');

export async function checkMobileRegression(browser, base, configuredApiBase) {
  const apiBase = normalizeApiBase(configuredApiBase);
  const home = new URL(base);
  const api = new URL(apiBase || '/', home);
  const prefix = api.pathname.replace(/\/+$/, '');
  const key = createSessionStore({getItem:()=>null,setItem:()=>{},removeItem:()=>{}}, home.pathname, apiBase, home.origin).key;
  const context = await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,userAgent:phoneUA});
  const errors = [];
  const requests = [];
  const state = { generation:1, pending:true, failCursor:false, staleCursor:false, userB:false, deferSave:false, releaseSave:null, uploads:0, backs:0, rotations:0, rescans:0 };
  const cards = Array.from({length:145}, (_,i) => ({
    id:`mobile-${i}`,status:i===50?'queued':'ready',person_name:`人物${i}`,company_name:'株式会社サンプル',
    person_name_kana:'じんぶつ',memo:'',tags:'営業, 東京',email:`person${i}@example.invalid`,
    created_at:'2026-10-01T00:00:00Z',updated_at:'2026-10-01T00:00:00Z',
  }));
  const summary = card => ({...card,revision:`r${state.generation}`,representative_card_id:card.id,card_count:card.id==='mobile-50'?2:1,has_in_progress:card.id==='mobile-50'&&state.pending});
  const historical = {...cards[50],id:'mobile-50-old',status:'ready',company_name:'以前の会社'};
  let page;
  try {
    page = await context.newPage();
    page.setDefaultTimeout(10000);
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(({key,apiBase}) => {
      localStorage.setItem(key,JSON.stringify({apiBase,token:'mobile-a'}));
      // Safari has no userAgentData; exercise the UA fallback explicitly.
      Object.defineProperty(navigator,'userAgentData',{value:undefined,configurable:true});
    }, {key,apiBase});
    const apiRoute = url => url.origin===api.origin && (url.pathname.startsWith(prefix+'/api/') || url.pathname.startsWith(prefix+'/line/'));
    await page.route(apiRoute, async route => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname.slice(prefix.length);
      requests.push({path,method:request.method(),params:Object.fromEntries(url.searchParams)});
      const send = (body,status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
      if (path==='/api/auth/bootstrap-status') return send({needs_bootstrap:false});
      if (path==='/api/auth/login') { state.userB=true; return send({session_token:'mobile-b'}); }
      if (path==='/api/auth/me') return send({user:{login_id:state.userB?'mobile-b':'mobile-a',role:'admin'},multi_user_enabled:true});
      if (path==='/api/auth/logout') return send({});
      if (path==='/api/auth/users') return send({items:[{id:'user',login_id:'一般ユーザー',role:'user',status:'active'}]});
      if (path==='/api/line-connections/me') return send({connection:{configured:true,line_login_channel_id:'test',liff_url:base+'liff'}});
      if (path==='/api/contacts') {
        if (state.userB) return send({detail:'新利用者の一覧取得失敗'},503);
        const params=url.searchParams, revision=`r${state.generation}`;
        if (params.get('cursor') && state.staleCursor) { state.staleCursor=false; state.generation++; return send({detail:'一覧が更新されました'},409); }
        if (params.get('cursor') && state.failCursor) { state.failCursor=false; return send({detail:'追加取得失敗'},503); }
        if (params.get('known_revision')===revision) return send({items:[],revision,unchanged:true,has_in_progress:state.pending});
        const [cursorRevision,position]=(params.get('cursor')||'').split(':');
        if (params.get('cursor') && cursorRevision!==revision) return send({detail:'一覧が更新されました'},409);
        const filtered=params.get('q') ? cards.filter(card => card.person_name===params.get('q')) : cards;
        const offset=Number(position||0), limit=Number(params.get('limit'));
        assert.equal(limit,50);
        return send({items:filtered.slice(offset,offset+limit).map(summary),revision,has_in_progress:state.pending,next_cursor:offset+limit<filtered.length?`${revision}:${offset+limit}`:null});
      }
      if (path==='/api/cards/upload') { state.uploads++; assert.match(request.postDataBuffer().toString(), /filename="test.png"/); return send({id:'uploaded',status:'queued'}); }
      if (path.endsWith('/back/upload')) { state.backs++; cards[50].back_original_image_path='back.jpg'; state.generation++; return send({id:'mobile-50',status:'queued'}); }
      if (path.endsWith('/rotate')) { state.rotations++; state.generation++; return send({}); }
      if (path.endsWith('/reprocess') || path.endsWith('/reextract')) { state.rescans++; return send({}); }
      if (path.endsWith('/corrections')) return send({items:[]});
      if (path.endsWith('-image') || path.endsWith('/thumbnail')) return route.fulfill({contentType:'image/png',body:png});
      const match=path.match(/^\/api\/(cards|contacts)\/(mobile-\d+(?:-old)?)$/);
      if (match) {
        const card=match[2]===historical.id?historical:cards.find(card=>card.id===match[2]);
        if (!card) return send({detail:'not found'},404);
        if (request.method()==='PATCH') {
          const submitted=request.postDataJSON();
          if (state.deferSave) await new Promise(resolve=>{state.releaseSave=resolve;});
          Object.assign(card,submitted); state.generation++;
        }
        if (match[1]==='contacts') return send({...summary(card),cards:card.id===cards[50].id||card.id===historical.id?[{...cards[50],revision:state.generation},historical]:[card]});
        return send({...card,revision:state.generation});
      }
      throw new Error('Unexpected synthetic API request: '+request.method()+' '+path);
    });
    await page.goto(base);
    await page.locator('[data-ui="mobile"]').waitFor();
    await page.getByText('人物0',{exact:true}).waitFor();
    assert.equal(await page.locator('.workspace').count(),0);
    assert.equal(requests.filter(request=>/^\/api\/cards\/mobile-\d+$/.test(request.path)).length,0,'list must not load an unselected detail');
    assert.equal(await page.evaluate(()=>location.href),base);
    if (process.env.BZCARD_SCREENSHOT_DIR) {
      await mkdir(process.env.BZCARD_SCREENSHOT_DIR,{recursive:true});
      await page.screenshot({path:process.env.BZCARD_SCREENSHOT_DIR+'/mobile-list.png'});
    }
    const viewport=page.locator('section[aria-label="人物一覧"] > div').last();
    await viewport.evaluate(element=>{element.scrollTop=element.scrollHeight;});
    await page.getByText('人物50',{exact:true}).waitFor();
    const scrollBefore=await viewport.evaluate(element=>element.scrollTop);
    await page.getByText('人物50',{exact:true}).click();
    await page.getByLabel('氏名',{exact:true}).waitFor();
    await page.getByLabel('メモ',{exact:true}).fill('未保存のメモ');
    state.pending=false; state.generation++; cards[50].status='ready'; cards[50].company_name='更新された会社';
    await page.waitForFunction(()=>document.querySelector('section[aria-label="名刺詳細"]')?.textContent.includes('完了'),null,{timeout:12000});
    await page.waitForFunction(()=>document.querySelector('input[aria-label="会社"]')?.value==='更新された会社');
    assert.equal(await page.getByLabel('メモ',{exact:true}).inputValue(),'未保存のメモ');
    assert.equal(await page.getByLabel('会社',{exact:true}).inputValue(),'更新された会社');
    await page.getByText('未保存の変更があります',{exact:true}).waitFor();
    page.once('dialog',dialog=>dialog.dismiss());
    await page.getByRole('button',{name:'一覧へ戻る',exact:true}).click();
    await page.waitForTimeout(100);
    assert.equal(await page.getByLabel('メモ',{exact:true}).inputValue(),'未保存のメモ');
    await page.setViewportSize({width:844,height:390});
    assert.equal(await page.locator('[data-ui="mobile"]').count(),1);
    assert.equal(await page.getByLabel('メモ',{exact:true}).inputValue(),'未保存のメモ');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'landscape must not overflow horizontally');
    await page.setViewportSize({width:390,height:844});

    await page.getByLabel('氏名',{exact:true}).fill('モバイル修正');
    state.deferSave=true;
    await page.getByRole('button',{name:'保存',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('button') && [...document.querySelectorAll('button')].some(button=>button.textContent==='保存'&&button.disabled));
    for (let attempt=0;!state.releaseSave&&attempt<100;attempt++) await page.waitForTimeout(10);
    assert.ok(state.releaseSave);
    await page.getByLabel('メモ',{exact:true}).fill('保存中に追記');
    state.deferSave=false; state.releaseSave();
    await page.getByText('保存しました',{exact:true}).waitFor();
    assert.equal(await page.getByLabel('メモ',{exact:true}).inputValue(),'保存中に追記');
    await page.getByRole('button',{name:'保存',exact:true}).click();
    await page.getByText('変更は保存済みです',{exact:true}).waitFor();
    assert.equal(cards[50].person_name,'モバイル修正');
    assert.equal(cards[50].memo,'保存中に追記');
    await page.getByLabel('裏面画像を選択',{exact:true}).setInputFiles({name:'back.png',mimeType:'image/png',buffer:png});
    await page.getByText('裏面の処理を開始しました',{exact:true}).waitFor();
    await page.getByRole('button',{name:'裏',exact:true}).waitFor({state:'visible'});
    await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(button=>button.textContent==='裏'&&!button.disabled));
    await page.getByRole('button',{name:'裏',exact:true}).click();
    assert.equal(state.backs,1);
    await page.getByRole('button',{name:'右回転',exact:true}).click();
    await page.getByText('右90度回転しました',{exact:true}).waitFor();
    assert.equal(state.rotations,1);
    await page.getByText('再解析・読み取り情報',{exact:true}).click();
    await page.getByRole('button',{name:'再スキャン',exact:true}).click();
    await page.getByText('再処理を開始しました',{exact:true}).waitFor();
    assert.equal(state.rescans,1);
    if (process.env.BZCARD_SCREENSHOT_DIR) {
      await page.locator('section[aria-label="名刺詳細"] > div').first().evaluate(element=>element.scrollTo({top:0}));
      const close=page.getByRole('button',{name:'通知を閉じる',exact:true});
      if (await close.count()) await close.click();
      await page.screenshot({path:process.env.BZCARD_SCREENSHOT_DIR+'/mobile-detail.png'});
    }
    await page.getByRole('button',{name:/以前の会社/}).click();
    await page.waitForFunction(()=>document.querySelector('input[aria-label="会社"]')?.value==='以前の会社');
    await page.goBack();
    await page.waitForFunction(()=>document.querySelector('input[aria-label="氏名"]')?.value==='モバイル修正');
    await page.getByRole('button',{name:'一覧へ戻る',exact:true}).click();
    await page.getByRole('navigation',{name:'メインメニュー'}).waitFor();
    assert.ok(Math.abs((await viewport.evaluate(element=>element.scrollTop))-scrollBefore)<2,'Back preserves list scroll');
    assert.equal(await page.evaluate(()=>location.href),base);
    state.staleCursor=true;
    await viewport.evaluate(element=>{element.scrollTop=element.scrollHeight;});
    await page.getByText('人物100',{exact:true}).waitFor();
    assert.ok(requests.some(request=>request.params.known_revision),'stale cursor recovery reuses the loaded count');
    console.log('PASS: mobile paging, processing updates, unsaved edits, save race, rotation, backside, rescan and Back');

    await page.getByLabel('検索',{exact:true}).fill('人物144');
    await page.getByText('人物144',{exact:true}).waitFor();
    assert.ok(requests.some(request=>request.params.q==='人物144'),'search covers unloaded people');
    await page.getByLabel('検索',{exact:true}).fill('');
    await page.getByText('人物0',{exact:true}).waitFor();
    state.failCursor=true;
    await viewport.evaluate(element=>{element.scrollTop=element.scrollHeight;});
    await page.getByText('追加取得失敗',{exact:false}).waitFor();
    const failedCount=requests.length;
    await page.waitForTimeout(200);
    assert.equal(requests.length,failedCount,'failed pagination must not loop');
    await page.getByRole('button',{name:'続きを再試行',exact:true}).click();
    await page.getByText('モバイル修正',{exact:true}).waitFor();
    await page.getByRole('button',{name:'追加',exact:true}).click();
    assert.equal(await page.getByLabel('カメラで撮影',{exact:true}).getAttribute('capture'),'environment');
    await page.getByLabel('画像を選択',{exact:true}).setInputFiles({name:'test.png',mimeType:'image/png',buffer:png});
    await page.getByText('test.png：登録しました。解析を開始します',{exact:true}).waitFor();
    assert.equal(state.uploads,1);
    await page.getByLabel('画像を選択',{exact:true}).setInputFiles({name:'test.heic',mimeType:'image/heic',buffer:png});
    await page.getByText('test.heic：JPEGまたはPNGの画像を選択してください',{exact:true}).waitFor();
    assert.equal(state.uploads,1,'unsupported formats are not sent to the API');
    await page.getByRole('button',{name:'設定',exact:true}).click();
    await page.getByRole('button',{name:'利用者管理',exact:true}).click();
    await page.getByRole('dialog',{name:'利用者管理'}).waitFor();
    await page.getByRole('button',{name:'閉じる',exact:true}).click();
    await page.getByRole('button',{name:'パスワード変更',exact:true}).click();
    await page.getByLabel('現在のパスワード',{exact:true}).waitFor();
    await page.locator('.imageModalHeader button').click();
    await page.getByRole('button',{name:'LINE設定',exact:true}).click();
    await page.getByLabel('LINE LoginチャネルID',{exact:true}).waitFor();
    await page.locator('.imageModalHeader button').click();
    await page.getByRole('button',{name:'ログアウト',exact:true}).click();
    await page.getByLabel('ログインID',{exact:true}).fill('mobile-b');
    await page.getByLabel('パスワード',{exact:true}).fill('synthetic-password');
    await page.getByRole('button',{name:'ログイン',exact:true}).click();
    await page.getByText('新利用者の一覧取得失敗',{exact:false}).waitFor();
    assert.equal(await page.getByText('モバイル修正',{exact:true}).count(),0);
    assert.equal(await page.getByText('保存中に追記',{exact:true}).count(),0);
    assert.equal(await page.locator('img').count(),0);
    await page.goBack();
    assert.equal(await page.locator('section[aria-label="名刺詳細"]').count(),0,'Back must not restore the old account detail');
    assert.deepEqual(errors,[]);
    console.log('PASS: mobile global search, pagination retry, upload, settings and account isolation');

    await page.unroute(apiRoute);
    await page.addInitScript(()=>{window.liff={init:async()=>{},isLoggedIn:()=>true,getIDToken:()=> 'test',getProfile:async()=>({displayName:'test'})};});
    await page.route(apiRoute,route=>{
      const path=new URL(route.request().url()).pathname;
      const body=path.endsWith('/liff-config')?{liff_id:'test'}:path.endsWith('/line/auth/login')?{session_token:'line'}:{items:[]};
      return route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
    });
    await page.goto(base+'liff?connection=test');
    await page.locator('.liffCards').waitFor();
    assert.equal(await page.locator('[data-ui="mobile"]').count(),0,'LIFF takes priority on smartphones');
    assert.deepEqual(errors,[]);
    console.log('PASS: smartphone LIFF keeps its existing authentication and UI');
  } finally { await context.close(); }

  for (const [userAgent,width,expected] of [
    ['Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome Safari',390,'desktop'],
    ['Mozilla/5.0 (iPad; CPU OS 18_0) AppleWebKit Mobile Safari',820,'desktop'],
    ['Mozilla/5.0 (Linux; Android 16) Chrome Mobile Safari',412,'mobile'],
  ]) {
    const check=await browser.newContext({userAgent,viewport:{width,height:844},hasTouch:true});
    try {
      const page=await check.newPage();
      await page.addInitScript(()=>Object.defineProperty(navigator,'userAgentData',{value:undefined,configurable:true}));
      await page.route(url=>url.origin===api.origin&&url.pathname.startsWith(prefix+'/api/'),route=>route.fulfill({contentType:'application/json',body:'{"needs_bootstrap":false}'}));
      await page.goto(base);
      await page.getByRole('button',{name:'ログイン',exact:true}).waitFor();
      assert.equal(await page.locator('[data-ui="mobile"]').count(),expected==='mobile'?1:0);
    } finally { await check.close(); }
  }
  console.log('PASS: narrow touch PCs and tablets retain desktop UI; Android phones use mobile UI');
}

if (process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href) {
  const browser=process.env.BROWSER_CDP_URL ? await chromium.connectOverCDP(process.env.BROWSER_CDP_URL) : await chromium.launch({executablePath:process.env.BROWSER_EXECUTABLE,args:['--no-sandbox','--disable-dev-shm-usage'],headless:true});
  try { await checkMobileRegression(browser,process.env.BZCARD_TEST_UI_URL||'http://127.0.0.1:15175/bzcard/',process.env.BZCARD_TEST_API_BASE_PATH??'/bzcard-api'); }
  finally { await browser.close(); }
}
