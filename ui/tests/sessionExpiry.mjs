// Synthetic sessions only; never calls a live account API.
import assert from 'node:assert/strict';
import { createSessionStore, normalizeApiBase } from '../src/deployment.ts';

export async function checkSessionExpiry(browser, base, configuredApiBase) {
  const apiBase = normalizeApiBase(configuredApiBase);
  const home = new URL(base);
  const api = new URL(apiBase || '/', home);
  const prefix = api.pathname.replace(/\/+$/, '');
  const key = createSessionStore({getItem:()=>null,setItem:()=>{},removeItem:()=>{}}, home.pathname, apiBase, home.origin).key;
  for (const mobile of [false, true]) {
    const context = await browser.newContext(mobile ? {
      viewport:{width:390,height:844}, isMobile:true, hasTouch:true,
      userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1',
    } : {});
    try {
      if (mobile) await context.addInitScript(() => Object.defineProperty(navigator,'userAgentData',{value:undefined,configurable:true}));
      const page = await context.newPage();
      page.setDefaultTimeout(10000);
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      let failure = null;
      const card = {id:'expiry-card', person_name:'セッション確認用人物', status:'ready', revision:1};
      await context.route(url => url.origin === api.origin && url.pathname.startsWith(prefix + '/api/'), async route => {
        const path = new URL(route.request().url()).pathname.slice(prefix.length);
        const send = (body, status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
        if (path === '/api/auth/bootstrap-status') return send({needs_bootstrap:false});
        if (path === '/api/auth/login') return send({session_token:'renewed-session'});
        const matches = failure?.target === 'image' ? path.endsWith('/thumbnail') : path === '/api/contacts';
        if (failure && matches) {
          if (failure.status === 'offline') return route.abort('internetdisconnected');
          return send({detail:'synthetic failure'},failure.status);
        }
        if (path === '/api/auth/me') return send({user:{login_id:'test',role:'user'},multi_user_enabled:true});
        if (path === '/api/system/versions') return send({api:{version:'v-test'},ocr:{},llm:{}});
        if (path === '/api/contacts') return send({items:[{...card,representative_card_id:card.id,card_count:1}]});
        if (path === '/api/contacts/expiry-card') return send({...card,cards:[card],representative_card_id:card.id});
        if (path === '/api/cards/expiry-card') return send(card);
        if (path.endsWith('/corrections')) return send({items:[]});
        if (path.endsWith('/thumbnail') || path.endsWith('-image')) return route.fulfill({status:204});
        return send({detail:'not found'},404);
      });
      await page.goto(base);
      await page.evaluate(({key,apiBase}) => localStorage.setItem(key,JSON.stringify({apiBase,token:'expired-session'})),{key,apiBase});

      for (const status of [503, 403, 'offline']) {
        failure = {target:'contacts',status};
        await Promise.all([
          page.waitForEvent(status === 'offline' ? 'requestfailed' : 'requestfinished', {
            predicate: request => new URL(request.url()).pathname.endsWith('/api/contacts'),
          }),
          page.reload(),
        ]);
        assert.equal(await page.locator('input[autocomplete="username"]').count(),0);
        assert.equal(await page.evaluate(key => JSON.parse(localStorage.getItem(key)).token,key),'expired-session');
      }

      for (const target of ['contacts', 'image']) {
        failure = {target,status:401};
        await page.reload();
        await page.getByLabel('ログインID',{exact:true}).waitFor();
        assert.deepEqual(await page.evaluate(key => JSON.parse(localStorage.getItem(key)),key),{apiBase,token:''});
        assert.equal(await page.getByText(card.person_name,{exact:true}).count(),0);
        // Valid login must recover without a full page reload.
        failure = null;
        await page.getByLabel('ログインID',{exact:true}).fill('synthetic-user');
        await page.getByLabel('パスワード',{exact:true}).fill('synthetic-password');
        await page.getByRole('button',{name:'ログイン',exact:true}).click();
        await page.getByText(card.person_name,{exact:true}).first().waitFor();
        assert.equal(await page.evaluate(key => JSON.parse(localStorage.getItem(key)).token,key),'renewed-session');
      }

      const peer = await context.newPage();
      await peer.goto(base);
      await peer.getByText(card.person_name,{exact:true}).first().waitFor();
      let releaseOld;
      let oldRequest;
      let oldComplete;
      const oldReceived = new Promise(resolve => { oldRequest = resolve; });
      const oldFinished = new Promise(resolve => { oldComplete = resolve; });
      await page.route(url => url.pathname === prefix + '/api/contacts', async route => {
        if (route.request().headers().authorization !== 'Bearer renewed-session') return route.fallback();
        oldRequest();
        await new Promise(resolve => { releaseOld = resolve; });
        try { await sendOld401(route); } catch { /* Disposed requests can already be cancelled. */ }
        finally { oldComplete(); }
      });
      await page.reload();
      await oldReceived;
      await peer.evaluate(({key,apiBase}) => localStorage.setItem(key,JSON.stringify({apiBase,token:'replacement-session'})),{key,apiBase});
      await page.getByText(card.person_name,{exact:true}).first().waitFor();
      releaseOld();
      await oldFinished;
      assert.equal(await page.evaluate(key => JSON.parse(localStorage.getItem(key)).token,key),'replacement-session');
      assert.equal(await page.locator('input[autocomplete="username"]').count(),0);

      failure = {target:'contacts',status:401};
      await page.reload();
      await page.getByLabel('ログインID',{exact:true}).waitFor();
      await peer.getByLabel('ログインID',{exact:true}).waitFor();
      assert.deepEqual(errors,[]);
      console.log(`PASS: ${mobile?'mobile':'desktop'} expires JSON/image 401 sessions across tabs, preserves API URL, recovers on login, ignores old responses, keeps sessions on 403/503/network errors`);
    } finally {
      await context.close();
    }
  }
}

function sendOld401(route) {
  return route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({detail:'expired old session'})});
}
