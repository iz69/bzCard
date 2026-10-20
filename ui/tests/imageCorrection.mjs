// Verify desktop/mobile image deletion with synthetic API responses only.
import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';
import { createSessionStore } from '../src/deployment.ts';

const base = process.env.BZCARD_TEST_UI_URL || 'http://127.0.0.1:15174/bzcard/';
const apiBase = '/bzcard-api';
const home = new URL(base);
const sessionKey = createSessionStore({getItem:()=>null,setItem:()=>{},removeItem:()=>{}}, home.pathname, apiBase, home.origin).key;
const browser = process.env.BROWSER_CDP_URL
  ? await chromium.connectOverCDP(process.env.BROWSER_CDP_URL)
  : await chromium.launch({executablePath:process.env.BROWSER_EXECUTABLE,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aGxkAAAAASUVORK5CYII=', 'base64');
try {
  for (const mobile of [false, true]) {
    const context = await browser.newContext({viewport:mobile?{width:390,height:844}:{width:1440,height:1000},isMobile:mobile,hasTouch:mobile,
      ...(mobile?{userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1'}:{})});
    try {
      const page = await context.newPage();
      page.setDefaultTimeout(10000);
      const errors=[];
      page.on('pageerror',error=>errors.push(error.message));
      await page.addInitScript(({sessionKey,apiBase,mobile})=>{
        localStorage.setItem(sessionKey,JSON.stringify({apiBase,token:'synthetic-session'}));
        Object.defineProperty(navigator,'userAgentData',{value:{mobile},configurable:true});
      },{sessionKey,apiBase,mobile});
      const card = {id:'synthetic-card',status:'ready',revision:1,person_name:'試験 太郎',company_name:'株式会社テスト',memo:'',
        processed_image_path:'front.jpg',back_original_image_path:'back.png',back_processed_image_path:'back.jpg',
        images:[{side:'front',correction_disabled:0},{side:'back',correction_disabled:0}],created_at:'2026-01-01',updated_at:'2026-01-01'};
      const deletions=[], imageRequests=[];
      let rejectDelete=true;
      const contact=()=>({...card,revision:String(card.revision),representative_card_id:card.id,card_count:1,cards:[card]});
      const reply=(route,value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
      await page.route(url=>url.pathname.startsWith(apiBase+'/api/'),async route=>{
        const request=route.request(),url=new URL(request.url()),path=url.pathname;
        if(path.endsWith('/bootstrap-status'))return reply(route,{needs_bootstrap:false});
        if(path.endsWith('/auth/me'))return reply(route,{user:{login_id:'test',role:'user'},multi_user_enabled:true});
        if(path.endsWith('/versions'))return reply(route,{api:{version:'dev'},llm:{},ocr:{}});
        if(request.method()==='DELETE'&&path.endsWith('/processed-image')){
          if(rejectDelete){rejectDelete=false;return reply(route,{detail:'synthetic deletion failure'},409);}
          const side=url.searchParams.get('side');deletions.push(side);
          card.images.find(image=>image.side===side).correction_disabled=1;card.revision++;
          return reply(route,{card,side});
        }
        if(path.endsWith('/contacts'))return reply(route,{items:[contact()],revision:String(card.revision)});
        if(path.endsWith('/contacts/'+card.id))return reply(route,contact());
        if(path.endsWith('/cards/'+card.id))return reply(route,card);
        if(path.endsWith('/corrections'))return reply(route,{items:[]});
        if(path.endsWith('-image')||path.endsWith('/thumbnail')){imageRequests.push(url.pathname+url.search);return route.fulfill({contentType:'image/png',body:png});}
        return reply(route,{});
      });
      await page.goto(base);
      await page.getByText(card.person_name,{exact:true}).first().waitFor();
      if(mobile)await page.getByRole('button').filter({hasText:card.person_name}).first().click();
      const name=page.getByRole('textbox',{name:'氏名',exact:true});
      await name.fill('未保存の氏名');
      const openZoom=async()=>mobile?page.getByRole('button',{name:'名刺画像を拡大'}).click():page.locator('.imagePanel img').click();
      const closeZoom=async()=>mobile?page.getByRole('button',{name:'画像を閉じる'}).click():page.getByRole('button',{name:'×',exact:true}).click();
      await openZoom();
      await page.getByRole('button',{name:'補正画像の削除',exact:true}).click();
      await page.getByText('409: synthetic deletion failure',{exact:true}).waitFor();
      assert.deepEqual(deletions,[]);
      await page.getByRole('button',{name:'補正画像の削除',exact:true}).click();
      await page.getByRole('button',{name:'元画像を表示中',exact:true}).last().waitFor();
      await page.waitForFunction(()=>!Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='補正画像の削除'));
      assert.deepEqual(deletions,['front']);
      await closeZoom();
      assert.equal(await name.inputValue(),'未保存の氏名');
      await page.getByRole('button',{name:'裏',exact:true}).click();
      await openZoom();
      const updatedBack = page.waitForRequest(request => request.url().includes('/back-processed-image?v=3'));
      await page.getByRole('button',{name:'補正画像の削除',exact:true}).click();
      await page.waitForFunction(()=>!Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='補正画像の削除'));
      await updatedBack;
      assert.deepEqual(deletions,['front','back']);
      assert.ok(imageRequests.some(path=>path.includes('/processed-image?v=2')));
      assert.ok(imageRequests.some(path=>path.includes('/back-processed-image?v=3')));
      assert.deepEqual(errors,[]);
      console.log(`PASS ${mobile?'mobile':'desktop'}: failure/retry, front/back deletion, refreshed image, preserved unsaved edits`);
    } finally {await context.close();}
  }
} finally {await browser.close();}
