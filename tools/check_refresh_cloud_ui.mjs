// All browser traffic is intercepted: never contacts collectors or Feishu.
import {createRequire} from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url),{chromium}=require('playwright');
const html=fs.readFileSync(process.argv[2],'utf8'),artifacts=process.argv[3];
fs.mkdirSync(artifacts,{recursive:true});
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--no-sandbox']});
try {
  const context=await browser.newContext({viewport:{width:1280,height:900},locale:'zh-CN'});
  let authenticated=false,starts=0,logins=0,job=null,failStatus=false,slowStatusOnce=true;
  await context.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.pathname.startsWith('/api/refresh/')) {
      let body;
      if(url.pathname.endsWith('/login')) {
        body=JSON.parse(route.request().postData());assert.equal(body.password,'mock-only-password');
        authenticated=true;logins++;return route.fulfill({json:{ok:true,nonce:'mock-nonce'}});
      }
      if(url.pathname.endsWith('/start')) {
        assert(authenticated);assert.equal(route.request().headers()['x-refresh-nonce'],'mock-nonce');
        assert.equal(route.request().postData(),'{}');starts++;
        job={id:'mock-job',terminal:false,state:'queued',phase:'waiting-agent',message:'等待本机后台接单',
          created_at:new Date().toISOString(),updated_at:new Date().toISOString(),done:0,total:0,sources:[]};
        return route.fulfill({json:{ok:true,created:true,job}});
      }
      if(failStatus) return route.fulfill({status:503,json:{ok:false,error:'状态接口暂不可用。'}});
      if(slowStatusOnce) {slowStatusOnce=false;await new Promise(resolve=>setTimeout(resolve,13000));}
      return route.fulfill({json:{ok:true,mode:'remote',authenticated,nonce:authenticated?'mock-nonce':'',agent_online:false,job,cooldown_seconds:600}});
    }
    if(url.pathname==='/' || url.pathname==='/index.html') return route.fulfill({contentType:'text/html',body:html});
    if(url.pathname==='/markets/quotes.json') return route.fulfill({json:{items:[]}});
    return route.abort();
  });
  let page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(String(error)));
  await page.goto('https://newshot-5jq.pages.dev/',{waitUntil:'networkidle'});
  let button=page.locator('#home-refresh-button');
  await button.waitFor({state:'visible'});assert.equal(await button.isDisabled(),false);
  page.once('dialog',dialog=>dialog.dismiss());await button.click();
  await page.waitForTimeout(1500);assert.equal(logins,0);assert.equal(starts,0);assert.equal(await button.isDisabled(),false);
  await page.locator('#category-controls button[data-category="finance"]').click();
  page.once('dialog',dialog=>dialog.accept('mock-only-password'));await button.click();
  await page.waitForTimeout(100);assert.equal(starts,1);assert.equal(logins,1);assert.equal(await button.isDisabled(),true);
  assert((await page.locator('#home-refresh-status').innerText()).includes('等待'));
  await page.close();page=await context.newPage();page.on('pageerror',error=>errors.push(String(error)));
  await page.goto('https://newshot-5jq.pages.dev/',{waitUntil:'networkidle'});
  assert.equal(await page.locator('#home-refresh-button').isDisabled(),true);assert.equal(starts,1);
  assert.equal(await page.locator('#category-controls button.active').getAttribute('data-category'),'finance');
  failStatus=true;
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await page.waitForFunction(()=>document.querySelector('#home-refresh-button').textContent==='重试连接');
  assert.equal(starts,1);assert.equal(logins,1);
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  failStatus=false;
  await page.locator('#home-refresh-button').click();
  await page.waitForFunction(()=>document.querySelector('#home-refresh-button').textContent==='更新中…');
  assert.equal(starts,1);assert.equal(logins,1);
  assert.equal(await page.locator('#category-controls button.active').getAttribute('data-category'),'finance');
  await page.setViewportSize({width:1280,height:900});
  await page.screenshot({path:path.join(artifacts,'refresh-desktop.png')});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(artifacts,'refresh-mobile.png')});
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:true,mocked:true,logins,starts,slow_13s_status_tolerated:true,retry_recovers_without_resubmission:true,cancel_safe:true,reopen_restores_queue:true,category_preserved:true,mobile_overflow:false,page_errors:errors,actual_feishu_sends:0}));
} finally {await browser.close();}
