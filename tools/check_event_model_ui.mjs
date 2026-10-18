// Offline browser acceptance against the already running local preview.
import {createRequire} from 'node:module';
import {mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const {chromium} = require('playwright');
const url = process.argv[2] || 'http://127.0.0.1:8799/';
const artifacts = path.resolve(process.argv[3] || 'output/weibo-qa');
await mkdir(artifacts, {recursive:true});
const browser = await chromium.launch({headless:true,
  executablePath:process.env.NEWSHOT_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args:['--no-sandbox']});
try {
  const page = await browser.newPage({viewport:{width:1280,height:900},locale:'zh-CN',timezoneId:'Asia/Shanghai'});
  const errors=[];
  page.on('pageerror', error=>errors.push(String(error)));
  await page.route('**/*', route=> {
    const target=new URL(route.request().url());
    if (target.pathname.startsWith('/api/')) return route.fulfill({status:503,contentType:'application/json',body:'{"ok":false,"error":"offline acceptance"}'});
    if (target.origin!==new URL(url).origin) return route.abort();
    return route.continue();
  });
  await page.goto(url,{waitUntil:'networkidle'});
  assert.equal(await page.title(),'今日领域分类');
  assert.equal(await page.locator('#home-freshness-note, #home-source-coverage').count(),0);
  assert.equal(await page.getByRole('heading',{name:'分类口径',exact:true}).count(),0);
  assert(await page.locator('#home-refresh-button').isVisible());
  assert(await page.locator('#home-source-counts').innerText());
  const all=page.locator('[data-category-section="all"]');
  const initial=await all.locator('.classified-row').evaluateAll(rows=>rows.map(r=>({id:r.dataset.eventId,
    progresses:JSON.parse(r.dataset.progressIds),docs:JSON.parse(r.dataset.docIds)})));
  assert(initial.length>0);
  assert.equal(new Set(initial.map(r=>r.id)).size,initial.length);
  assert(initial.every(r=>r.progresses.length>0 && r.docs.length>0 && new Set(r.docs).size===r.docs.length));
  const pids=initial.flatMap(r=>r.progresses);
  assert.equal(new Set(pids).size,pids.length);
  const counts={};
  for (const category of ['all','hardtech','commodities','consumer','auto','newenergy','finance','logistics','society']) {
    const button=page.locator(`#category-controls [data-category="${category}"]`);
    await button.click();
    const visible=page.locator('.category-section:visible .classified-row');
    const count=await visible.count();
    assert.equal(count,Number(await button.locator('span').innerText()));
    const scores=await visible.evaluateAll(rows=>rows.map(r=>Number(r.dataset.score)));
    assert.deepEqual(scores,[...scores].sort((a,b)=>b-a));
    counts[category]=count;
  }
  const society=await page.locator('[data-category-section="society"] h3').allInnerTexts();
  assert(!society.some(title=>/乌克兰|加拿大|纽约|伊朗|俄罗斯|军事|空袭|军演|使馆/.test(title)));
  await page.locator('#category-controls [data-category="consumer"]').click();
  const swisse=page.locator('.category-section:visible .classified-row').filter({has:page.locator('h3',{hasText:'Swisse'})});
  assert.equal(await swisse.count(),1);
  assert(Number(await swisse.getAttribute('data-progress-count'))>=2);
  await swisse.locator('summary').click();
  const nodes=swisse.locator('.progress-node');
  assert.equal(await nodes.count(),Number(await swisse.getAttribute('data-progress-count')));
  assert.equal(await swisse.locator('.progress-node[data-progress-type="response"]').count(),1);
  for (const node of await nodes.all()) {
    const ids=await node.locator('[data-doc-id]').evaluateAll(rows=>rows.map(r=>r.dataset.docId));
    assert.equal(ids.length,new Set(ids).size);
  }
  await swisse.scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(artifacts,'event-model-desktop.png')});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(artifacts,'event-model-mobile.png')});
  await page.reload({waitUntil:'networkidle'});
  assert.equal(await page.locator('#category-controls .active').getAttribute('data-category'),'consumer');
  const reloaded=await all.locator('.classified-row').evaluateAll(rows=>rows.map(r=>r.dataset.eventId));
  assert.deepEqual(reloaded,initial.map(r=>r.id));
  assert.equal(await page.locator('a[href="https://wallstreetcn.com/articles/3783652"]').count(),0);
  const transition=await all.locator('.classified-row').evaluateAll(rows=>{
    const row=rows.find(r=>JSON.parse(r.dataset.futureStates||'[]').length);
    return row?{id:row.dataset.eventId,expires:Number(row.dataset.expiresAt),count:Number(row.dataset.itemCount),progresses:JSON.parse(row.dataset.progressIds)}:null;
  });
  if (transition) {
    await page.evaluate(({expires})=>{window.qaOriginalNow=Date.now;Date.now=()=>expires+1000;document.dispatchEvent(new Event('visibilitychange'));},transition);
    const kept=all.locator(`[data-event-id="${transition.id}"]`);
    assert.equal(await kept.count(),1);
    const remaining=JSON.parse(await kept.getAttribute('data-progress-ids'));
    assert(remaining.every(id=>transition.progresses.includes(id)));
    assert(Number(await kept.getAttribute('data-item-count'))<transition.count);
  }
  await page.evaluate(()=>{const original=window.qaOriginalNow||Date.now;Date.now=()=>original()+25*3600000;document.dispatchEvent(new Event('visibilitychange'));});
  assert.equal(await page.locator('.classified-row').count(),0);
  assert.equal(await page.locator('#home-event-count').innerText(),'0');
  assert(await page.locator('#home-empty').isVisible());
  assert.deepEqual(errors,[]);
  const result={passed:true,counts,explanatory_panels_removed:true,refresh_control_preserved:true,unique_event_ids:true,unique_progress_ids:true,unique_docs_per_progress:true,
    same_progress_deduplication:true,different_progresses_preserved:true,reload_ids_stable:true,
    expiry_ids_stable:true,expiry_25h_empty:true,domestic_society:true,mobile_overflow:false,page_errors:errors};
  await writeFile(path.join(artifacts,'event-model-ui.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
} finally {await browser.close();}
