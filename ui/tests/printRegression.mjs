// Exercise the real print button and document with synthetic cards and images.
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { chromium } from 'playwright-core';
import { createSessionStore, normalizeApiBase } from '../src/deployment.ts';

const front = '<svg xmlns="http://www.w3.org/2000/svg" width="910" height="550"><rect width="910" height="550" fill="white"/><path d="M0 0H18V550H0Z" fill="#26313b"/><text x="60" y="100" font-size="34">株式会社サンプル</text><text x="60" y="195" font-size="26">営業部 第一営業課　課長</text><text x="60" y="280" font-size="64" font-weight="bold">山田 太郎</text><text x="60" y="350" font-size="24">〒100-0000 東京都千代田区丸の内1-2-3</text><text x="60" y="390" font-size="24">サンプルビル8F</text><text x="60" y="440" font-size="25">TEL 03-0000-1234</text><text x="60" y="490" font-size="25">taro.yamada@example.com</text></svg>';
const back = '<svg xmlns="http://www.w3.org/2000/svg" width="910" height="550"><rect width="910" height="550" fill="#f8f8f8"/><text x="60" y="110" font-size="36">人とサービスを、つなぐ。</text><text x="60" y="235" font-size="28">業務システムの設計・開発</text><text x="60" y="300" font-size="28">導入支援・運用サポート</text><text x="60" y="465" font-size="26">https://example.com</text></svg>';
const sample = {
  id:'print-card',status:'ready',revision:1,person_name:'山田 太郎',person_name_kana:'やまだ たろう',
  company_name:'株式会社サンプル',department:'営業部 第一営業課',title:'課長',
  postal_code:'100-0000',address:'東京都千代田区丸の内1-2-3\nサンプルビル8F',
  mobile:'090-0000-1234',tel:'03-0000-1234',fax:'03-0000-5678',
  email:'taro.yamada@example.com',website:'https://example.com',tags:'取引先, 営業, 東京',
  memo:'展示会で名刺交換。次回、サービス導入の相談予定。\n連絡はメールを希望。',
  back_original_image_path:'synthetic-back',ocr_text:'DO_NOT_PRINT_OCR',extracted_json:'DO_NOT_PRINT_JSON',
  created_at:'2026-10-01',updated_at:'2026-10-01',
};

export async function checkPrintRegression(browser, base, configuredApiBase) {
  const apiBase = normalizeApiBase(configuredApiBase);
  const home = new URL(base);
  const api = new URL(apiBase || '/', home);
  const prefix = api.pathname.replace(/\/+$/, '');
  const key = createSessionStore({getItem:()=>null,setItem:()=>{},removeItem:()=>{}},home.pathname,apiBase,home.origin).key;
  const context = await browser.newContext({viewport:{width:1440,height:1000}});
  const errors = [];
  const requests = [];
  const pending = [];
  const state = {card:{...sample},pause:false,failFrontProcessed:false,failAll:false};
  let page;
  try {
    page = await context.newPage();
    page.setDefaultTimeout(10000);
    page.on('pageerror',error=>errors.push(error.message));
    await page.addInitScript(({key,apiBase})=>{
      localStorage.setItem(key,JSON.stringify({apiBase,token:'print-test-user'}));
      window.__printSnapshots = [];
      // Intercept only the native dialog. The application still creates, loads,
      // measures and cleans up its actual iframe document.
      const descriptor = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype,'contentWindow');
      Object.defineProperty(HTMLIFrameElement.prototype,'contentWindow',{
        ...descriptor,
        get() {
          const frameWindow = descriptor.get.call(this);
          if (frameWindow && this.title === '名刺の印刷帳票') {
            frameWindow.print = () => {
              const document = frameWindow.document;
              window.__printSnapshots.push({html:document.documentElement.outerHTML,text:document.body.textContent,
                complete:[...document.images].every(image=>image.complete && image.naturalWidth>0)});
              window.__finishPrint = () => frameWindow.dispatchEvent(new frameWindow.Event('afterprint'));
            };
          }
          return frameWindow;
        },
      });
    },{key,apiBase});
    await page.route(url=>url.origin===api.origin && url.pathname.startsWith(prefix+'/api/'),async route=>{
      const request = route.request();
      const path = new URL(request.url()).pathname.slice(prefix.length);
      requests.push({path,method:request.method(),authorization:request.headers().authorization});
      const send = (body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
      if (path.endsWith('/auth/me')) return send({user:{login_id:'print-test',role:'user'},multi_user_enabled:true});
      if (path.endsWith('/bootstrap-status')) return send({needs_bootstrap:false});
      if (path.endsWith('/versions')) return send({api:{version:'dev'},llm:{}});
      if (path.endsWith('/corrections')) return send({items:[]});
      const another = {...sample,id:'another-card',person_name:'別の名刺',back_original_image_path:undefined};
      const summary = card=>({...card,representative_card_id:card.id,card_count:1});
      if (path==='/api/contacts') return send({items:[summary(state.card),summary(another)],revision:'print-r1'});
      if (path==='/api/contacts/print-card') return send({...summary(state.card),cards:[state.card]});
      if (path==='/api/cards/print-card') return send(state.card);
      if (path==='/api/contacts/another-card') return send({...summary(another),cards:[another]});
      if (path==='/api/cards/another-card') return send(another);
      if (path.endsWith('-image') || path.endsWith('/thumbnail')) {
        if (state.pause && path.startsWith('/api/cards/print-card/') && !path.endsWith('/thumbnail')) await new Promise(resolve=>pending.push(resolve));
        if (state.failAll || (state.failFrontProcessed && path.endsWith('/processed-image'))) return send({detail:'synthetic image failure'},404);
        return route.fulfill({contentType:'image/svg+xml',body:path.includes('/back-')?back:front});
      }
      return send({detail:'synthetic unknown request'},404);
    });
    const reload = async () => {
      await page.goto(base);
      await page.getByLabel('氏名',{exact:true}).waitFor();
      if (!state.failFrontProcessed) await page.locator('.imagePanel img').waitFor();
    };
    const waitPrint = async () => {
      await page.waitForFunction(()=>window.__printSnapshots.length>0);
      return (await page.locator('iframe[title="名刺の印刷帳票"]').elementHandle()).contentFrame();
    };
    const finish = async () => {
      await page.evaluate(()=>window.__finishPrint());
      await page.waitForFunction(()=>!document.querySelector('iframe[title="名刺の印刷帳票"]'));
      assert.equal(await page.getByRole('button',{name:'印刷',exact:true}).isEnabled(),true);
    };
    const assertLayout = async frame => {
      const metrics = await frame.evaluate(()=>{
        const record = document.querySelector('.printRecord');
        const information = document.querySelector('.printInformation');
        const images = document.querySelector('.printImages');
        const notes = document.querySelector('.printNotes');
        return {width:record.getBoundingClientRect().width,height:record.getBoundingClientRect().height,
          expanded:record.classList.contains('printRecordExpanded'),font:parseFloat(getComputedStyle(record).fontSize),
          overflowing:[record,information,images].some(node=>node.scrollHeight>node.clientHeight+1),
          overlapping:notes && Math.max(information.getBoundingClientRect().bottom,images.getBoundingClientRect().bottom)>notes.getBoundingClientRect().top+1,
          horizontal:document.documentElement.scrollWidth>innerWidth,
          imageFit:[...document.images].every(image=>getComputedStyle(image).objectFit==='contain')};
      });
      assert.ok(Math.abs(metrics.width-190*96/25.4)<1);
      assert.equal(metrics.overflowing,false);
      assert.ok(!metrics.overlapping);
      assert.equal(metrics.horizontal,false);
      assert.equal(metrics.imageFit,true);
      assert.ok(metrics.font>=12); // 9pt minimum.
      return metrics;
    };

    await reload();
    assert.deepEqual(await page.locator('.detailActions button').allTextContents(),['保存','削除','印刷']);
    const colors = await page.locator('.detailActions button').evaluateAll(buttons=>buttons.map(button=>getComputedStyle(button).color));
    assert.equal(new Set(colors).size,3);
    for (const width of [1440,1024,800]) {
      await page.setViewportSize({width,height:1000});
      const boxes = await page.locator('.detailActions button').evaluateAll(buttons=>buttons.map(button=>{
        const {left,right,top,bottom}=button.getBoundingClientRect();return {left,right,top,bottom};
      }));
      assert.ok(boxes.every(box=>box.left>=0 && box.right<=width));
      assert.ok(boxes[0].right<=boxes[1].left && boxes[1].right<=boxes[2].left);
    }
    await page.setViewportSize({width:1440,height:1000});
    state.pause=true;
    await page.locator('.formPanel textarea').fill('印刷時点の未保存メモ\n2行目');
    await page.getByRole('button',{name:'印刷',exact:true}).click();
    for (let i=0;pending.length<1 && i<100;i++) await page.waitForTimeout(10);
    assert.equal(pending.length,1,'the authenticated front image loads before printing');
    assert.equal(await page.getByRole('button',{name:'印刷',exact:true}).isEnabled(),false);
    await page.locator('.formPanel textarea').fill('画像読み込み中の追記');
    state.pause=false;
    pending.splice(0).forEach(resolve=>resolve());
    let frame = await waitPrint();
    assert.ok(frame);
    let snapshot = await page.evaluate(()=>window.__printSnapshots[0]);
    assert.equal(snapshot.complete,true);
    assert.ok(snapshot.text.includes('印刷時点の未保存メモ\n2行目'));
    assert.ok(!snapshot.text.includes('画像読み込み中の追記'));
    assert.ok(!snapshot.text.includes('DO_NOT_PRINT'));
    assert.equal(await frame.locator('img').count(),1);
    const normal = await assertLayout(frame);
    assert.ok(Math.abs(normal.height-90*96/25.4)<1);
    assert.equal(normal.expanded,false);
    const imageWidth=await frame.locator('img').evaluate(image=>image.getBoundingClientRect().width);
    assert.ok(Math.abs(imageWidth-65*96/25.4)<1);
    await finish();
    assert.equal(await page.locator('.formPanel textarea').inputValue(),'画像読み込み中の追記');
    assert.ok(requests.every(request=>request.method==='GET'));
    assert.ok(requests.every(request=>request.authorization==='Bearer print-test-user'));
    assert.ok(requests.every(request=>!request.path.includes('/back-')));
    console.log('PASS: button placement/colors, snapshot, larger front image only, A4 dimensions, cancellation and no save');

    state.card={...sample}; state.failFrontProcessed=true;
    await reload();
    await page.getByRole('button',{name:'印刷',exact:true}).click();
    frame=await waitPrint();
    assert.equal(await frame.locator('img').count(),1);
    assert.ok(requests.some(request=>request.path.endsWith('/original-image')));
    await assertLayout(frame);
    if (process.env.BZCARD_SCREENSHOT_DIR) {
      const output=process.env.BZCARD_SCREENSHOT_DIR;
      await mkdir(output,{recursive:true});
      const report=await context.newPage();
      const html=await frame.content();
      await report.setContent(html.replace(/src="blob:[^"]+"/g,()=>`src="data:image/svg+xml;base64,${Buffer.from(front).toString('base64')}"`));
      await report.evaluate(()=>Promise.all([document.fonts.ready,...Array.from(document.images,image=>image.decode())]));
      await report.locator('.printRecord').screenshot({path:output+'/print-record.png'});
      const pdf=await report.pdf({path:output+'/print-a4.pdf',preferCSSPageSize:true,printBackground:true});
      assert.equal((pdf.toString('latin1').match(/\/Type \/Page\b/g)||[]).length,1);
      await report.close();
      console.log('PASS: exported A4 PDF contains one page');
    }
    await finish();
    if (process.env.BZCARD_SCREENSHOT_DIR) await page.screenshot({path:process.env.BZCARD_SCREENSHOT_DIR+'/desktop-detail.png'});
    console.log('PASS: processed-image fallback');

    state.failFrontProcessed=false;
    state.card={...sample,back_original_image_path:undefined,tel:'',fax:'',mobile:'',website:'',email:'',address:'',postal_code:'',tags:'',memo:'',person_name:'<script>literal text</script>'};
    await reload();
    await page.getByRole('button',{name:'印刷',exact:true}).click();
    frame=await waitPrint();
    assert.equal(await frame.locator('img').count(),1);
    assert.equal(await frame.locator('.printField').count(),0);
    assert.equal(await frame.locator('script').count(),0);
    assert.ok((await frame.locator('.printPersonName').textContent()).includes('<script>literal text</script>'));
    await assertLayout(frame); await finish();
    console.log('PASS: single-sided cards, empty fields and literal HTML are handled');

    state.card={...sample,person_name:'長い氏名'.repeat(8),company_name:'長い会社名'.repeat(20),
      website:'https://example.com/'+ 'verylongpath'.repeat(40),memo:Array.from({length:15},(_,i)=>`メモ${i+1}：`+'長文でも情報を欠落させず折り返して印刷する。'.repeat(2)).join('\n')};
    await reload();
    await page.getByRole('button',{name:'印刷',exact:true}).click();
    frame=await waitPrint();
    const long=await assertLayout(frame);
    assert.equal(long.expanded,true);
    assert.ok(long.height>normal.height);
    assert.ok((await frame.locator('.printNotes').textContent()).includes('メモ15：'));
    await finish();
    console.log('PASS: long names, URLs and multiline notes expand without overlap or lost text');

    state.card={...sample}; state.failAll=true;
    await page.goto(base);
    await page.getByLabel('氏名',{exact:true}).waitFor();
    await page.getByRole('button',{name:'印刷',exact:true}).click();
    await page.getByText('印刷できませんでした:',{exact:false}).waitFor();
    assert.equal(await page.evaluate(()=>window.__printSnapshots.length),0);
    assert.equal(await page.locator('iframe[title="名刺の印刷帳票"]').count(),0);
    assert.equal(await page.getByRole('button',{name:'印刷',exact:true}).isEnabled(),true);
    state.failAll=false; state.pause=true;
    await page.getByRole('button',{name:'印刷',exact:true}).click();
    for (let i=0;pending.length<1 && i<100;i++) await page.waitForTimeout(10);
    await page.getByText('別の名刺',{exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.formPanel input')?.value==='別の名刺');
    state.pause=false; pending.splice(0).forEach(resolve=>resolve());
    await page.waitForTimeout(100);
    assert.equal(await page.evaluate(()=>window.__printSnapshots.length),0);
    assert.equal(await page.locator('iframe[title="名刺の印刷帳票"]').count(),0);
    console.log('PASS: image failures remain retryable and changing cards cancels pending printing');
    assert.deepEqual(errors,[]);
  } finally {
    pending.splice(0).forEach(resolve=>resolve());
    await context.close();
  }
}

if (process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href) {
  const browser=process.env.BROWSER_CDP_URL
    ? await chromium.connectOverCDP(process.env.BROWSER_CDP_URL)
    : await chromium.launch({executablePath:process.env.BROWSER_EXECUTABLE,args:['--no-sandbox','--disable-dev-shm-usage'],headless:true});
  try { await checkPrintRegression(browser,process.env.BZCARD_TEST_UI_URL||'http://127.0.0.1:15175/bzcard/',process.env.BZCARD_TEST_API_BASE_PATH??'/bzcard-api'); }
  finally { await browser.close(); }
}
