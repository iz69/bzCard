import assert from 'node:assert/strict';
import {createSessionStore, normalizeApiBase} from '../src/deployment.ts';

export async function checkContactPagination(browser, base, configuredApiBase) {
  const apiBase = normalizeApiBase(configuredApiBase);
  const origin = new URL(base);
  const api = new URL(apiBase || '/', origin);
  const apiPath = api.pathname.replace(/\/+$/, '');
  const key = createSessionStore({getItem:()=>null,setItem:()=>{},removeItem:()=>{}},
    origin.pathname, apiBase, origin.origin).key;
  const context = await browser.newContext({viewport:{width:1280,height:800}});
  const requests = [], errors = [];
  let generation = 1, failNext = false, failReload = false, stale = 0, failures = 0;
  const people = Array.from({length:145}, (_,i) => ({
    id: `page-${i}`, representative_card_id:`page-${i}`, card_count:1,
    person_name:`人物${i}`, company_name:'ページング確認', status:'ready',
    created_at:`2026-10-01T00:00:${String(i % 60).padStart(2,'0')}`, updated_at:'2026-10-01',
  }));
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aGxkAAAAASUVORK5CYII=', 'base64');
  let page;
  try {
    page = await context.newPage();
    page.setDefaultTimeout(10000);
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(({key,apiBase}) => localStorage.setItem(key,JSON.stringify({apiBase,token:'paged-user'})), {key,apiBase});
    await page.route(url => url.origin === api.origin && url.pathname.startsWith(apiPath+'/api/'), async route => {
      const url = new URL(route.request().url());
      const path = url.pathname;
      const send = (body,status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
      if (path.endsWith('/contacts')) {
        const params = Object.fromEntries(url.searchParams);
        requests.push(params);
        assert.equal(params.limit,'50');
        const revision = `r${generation}`;
        if (failReload && !params.cursor) {
          failReload=false; failures++;
          return send({detail:'再取得失敗'},503);
        }
        if (params.known_revision === revision) return send({items:[],revision,unchanged:true,has_in_progress:false});
        const [cursorRevision, position] = (params.cursor || '').split(':');
        if (params.cursor && cursorRevision !== revision) {
          stale++;
          return send({detail:'一覧が更新されました'},409);
        }
        if (failNext && params.cursor === 'r2:100') {
          failNext = false; failures++;
          return send({detail:'ページ取得失敗'},503);
        }
        const filtered = params.q ? people.filter(person => person.person_name === params.q) : people;
        const offset = Number(position || 0);
        return send({
          items:filtered.slice(offset,offset+50).map(person => ({...person,revision})),
          revision, has_in_progress:false,
          next_cursor:offset+50<filtered.length ? `${revision}:${offset+50}` : null,
        });
      }
      if (path.endsWith('/auth/me')) return send({user:{login_id:'paged',role:'user'}});
      if (path.endsWith('/versions')) return send({llm:{}});
      if (path.endsWith('/corrections')) return send({items:[]});
      if (path.endsWith('-image') || path.endsWith('/thumbnail')) return route.fulfill({contentType:'image/png',body:png});
      const match = path.match(/\/(cards|contacts)\/(page-\d+)$/);
      if (match) {
        const person = people.find(person => person.id === match[2]);
        return send({...person,revision:generation,...(match[1]==='contacts'?{cards:[{...person,revision:generation}]}:{})});
      }
      return send({},404);
    });
    await page.goto(base);
    await page.getByText('人物0',{exact:true}).first().waitFor();
    assert.equal(requests.length,1);
    assert.equal(requests[0].cursor,undefined);
    await page.getByPlaceholder('検索',{exact:true}).fill('人物144');
    await page.getByText('人物144',{exact:true}).first().waitFor();
    assert.ok(requests.some(request => request.q === '人物144'));
    await page.getByPlaceholder('検索',{exact:true}).fill('');
    await page.getByText('人物0',{exact:true}).first().waitFor();
    await page.locator('.tableWrap').evaluate(element => {element.scrollTop=element.scrollHeight;});
    await page.getByText('人物50',{exact:true}).first().waitFor();
    assert.ok(requests.some(request => request.cursor === 'r1:50'));
    await page.getByText('人物50',{exact:true}).first().click();
    await page.getByLabel('氏名',{exact:true}).fill('未保存の修正');
    generation=2; failNext=true;
    await page.locator('.tableWrap').evaluate(element => {element.scrollTop=element.scrollHeight;});
    await page.getByRole('button',{name:'続きを再試行',exact:true}).waitFor();
    assert.equal(stale,1);
    assert.equal(failures,1);
    assert.ok(requests.some(request => request.known_revision === 'r1'));
    assert.equal(await page.getByLabel('氏名',{exact:true}).inputValue(),'未保存の修正');
    // A stale cursor followed by a failed reload must stop automatic retries.
    generation=3; failReload=true;
    await page.getByRole('button',{name:'続きを再試行',exact:true}).click();
    await page.locator('.toastNotification').filter({hasText:'再取得失敗'}).waitFor();
    await page.getByRole('button',{name:'続きを再試行',exact:true}).waitFor();
    assert.equal(stale,2);
    assert.equal(failures,2);
    const failedRequestCount=requests.length;
    await page.waitForTimeout(300);
    assert.equal(requests.length,failedRequestCount);
    assert.equal(await page.getByLabel('氏名',{exact:true}).inputValue(),'未保存の修正');
    await page.getByRole('button',{name:'続きを再試行',exact:true}).click();
    await page.locator('.listLoadMore').waitFor({state:'hidden'});
    await page.locator('.tableWrap').evaluate(element => {element.scrollTop=element.scrollHeight;});
    await page.getByText('人物144',{exact:true}).first().waitFor();
    assert.equal(await page.getByLabel('氏名',{exact:true}).inputValue(),'未保存の修正');
    await page.getByRole('button',{name:'再読み込み',exact:true}).click();
    await page.waitForFunction(() => !document.querySelector('.topbar .spin'));
    assert.ok(requests.some(request => request.known_revision === 'r3'));
    assert.equal(await page.getByLabel('氏名',{exact:true}).inputValue(),'未保存の修正');
    assert.deepEqual(errors,[]);
    console.log('PASS: 50-person pages, global search, scrolling, stale cursor recovery, retry and unsaved edits');
  } catch (error) {
    console.error('Pagination diagnostics:', JSON.stringify({requests,errors,state:await page.evaluate(() => {
      const element = document.querySelector('.tableWrap');
      return {scrollTop:element.scrollTop,scrollHeight:element.scrollHeight,clientHeight:element.clientHeight,
        rows:[...element.querySelectorAll('tr')].map(row=>({text:row.textContent,height:row.getBoundingClientRect().height}))};
    })}));
    throw error;
  } finally {
    await context.close();
  }
}
