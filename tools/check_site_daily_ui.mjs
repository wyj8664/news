// Check the original list/archive layout using only saved local daily records.
import {createRequire} from 'node:module';
import {mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const {chromium} = require('playwright');
const origin = process.argv[2] || 'http://127.0.0.1:8799';
const artifacts = path.resolve(process.argv[3] || 'output/weibo-qa/site-daily-archive');
await mkdir(artifacts, {recursive:true});
const browser = await chromium.launch({headless:true,
  executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', args:['--no-sandbox']});
try {
  const page = await browser.newPage({viewport:{width:1280,height:900},locale:'zh-CN',timezoneId:'Asia/Shanghai'});
  const errors = [];
  page.on('pageerror',error=>errors.push(String(error)));
  await page.route('**/*',route=>{
    const target = new URL(route.request().url());
    if (target.pathname.startsWith('/api/')) return route.fulfill({status:503,contentType:'application/json',body:'{"ok":false}'});
    if (target.origin !== origin) return route.abort();
    return route.continue();
  });
  await page.goto(`${origin}/daily/`,{waitUntil:'networkidle'});
  const today = await page.locator('main').getAttribute('data-daily-date');
  assert.equal(await page.title(),`${today} 新闻日报 Top 50`);
  assert.equal(await page.locator('.global-topbar').count(),1);
  assert.equal(await page.locator('.classified-row, #daily-controls, [data-daily-tier]').count(),0);
  const dates = await page.locator('.archive-row').evaluateAll(rows=>rows.map(row=>row.getAttribute('href').match(/([\d-]+).html$/)[1]));
  assert.equal(dates.length,7);
  for (let index=0; index<7; index++) {
    const expected = new Date(`${today}T12:00:00Z`);
    expected.setUTCDate(expected.getUTCDate()-index);
    assert.equal(dates[index],expected.toISOString().slice(0,10));
  }
  assert.equal(await page.locator('.archive-row.active').count(),1);
  assert.equal(await page.locator('.archive-row.active').getAttribute('href'),`/daily/${today}.html`);
  assert((await page.locator('.shell').evaluate(el=>getComputedStyle(el).gridTemplateColumns)).startsWith('250px'));
  const currentIds = await page.locator('main .item').evaluateAll(rows=>rows.map(row=>row.dataset.eventId));
  await page.screenshot({path:path.join(artifacts,'daily-desktop.png')});
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({path:path.join(artifacts,'daily-mobile.png')});
  const allowed = ['hardtech','commodities','consumer','auto','newenergy','finance','logistics'];
  const counts = {};
  for (const date of dates) {
    await page.locator(`.archive-row[href="/daily/${date}.html"]`).click();
    await page.waitForURL(url=>url.pathname===`/daily/${date}.html` || url.pathname===`/daily/${date}`);
    assert.equal(await page.locator('main').getAttribute('data-daily-date'),date);
    const rows = page.locator('main .item');
    const values = await rows.evaluateAll(rows=>rows.map(row=>({id:row.dataset.eventId,score:Number(row.dataset.score),
      category:row.dataset.category,progresses:JSON.parse(row.dataset.progressIds),docs:JSON.parse(row.dataset.docIds),
      rank:row.querySelector('.rank').textContent})));
    assert(values.length<=50);
    assert.equal(values.length,new Set(values.map(row=>row.id)).size);
    assert(values.every(row=>allowed.includes(row.category) && row.progresses.length && row.docs.length));
    assert.deepEqual(values.map(row=>row.rank),values.map((_,index)=>String(index+1)));
    assert.deepEqual(values.map(row=>row.score),values.map(row=>row.score).sort((a,b)=>b-a));
    const data = await page.evaluate(async date=>(await fetch(`/daily/${date}.json`)).json(),date);
    assert.equal(data.pipeline,'site-daily-v2');
    assert.equal(data.date,date);
    assert.deepEqual(values.map(row=>row.id),data.events.map(event=>event.id));
    assert(data.events.every(event=>event.reports.every(doc=>doc.published_at.startsWith(date))));
    for (const [index,row] of values.entries()) {
      const event = data.events[index];
      assert.deepEqual(row.progresses,event.progresses.map(progress=>progress.id));
      assert.deepEqual(row.docs,event.reports.map(doc=>doc.doc_id));
    }
    const before = await rows.count();
    await page.evaluate(()=>{const original=Date.now;Date.now=()=>original()+8*86400000;document.dispatchEvent(new Event('visibilitychange'));});
    assert.equal(await rows.count(),before); // Archived day is not a live 24-hour view.
    if (!before) assert(await page.locator('#daily-empty').isVisible());
    assert.equal(await page.locator('nav a[href="/timeline/"]').count(),0);
    counts[date] = before;
  }
  await page.locator('.latest').click();
  await page.waitForURL(`${origin}/daily/`);
  assert.deepEqual(await page.locator('main .item').evaluateAll(rows=>rows.map(row=>row.dataset.eventId)),currentIds);
  const details = page.locator('main .item details').first();
  if (await details.count()) {
    await details.locator('summary').click();
    assert(await details.locator('[data-progress-id]').first().isVisible());
  }
  assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth));
  assert.deepEqual(errors,[]);
  const result = {passed:true,counts,original_archive_list_layout:true,seven_date_links:true,calendar_day_publication:true,
    sorted_max50_each_day:true,short_days_not_padded:true,stable_ids:true,history_not_expired:true,
    no_category_cards:true,mobile_overflow:false,timeline_removed:true,page_errors:errors};
  await writeFile(path.join(artifacts,'site-daily-ui.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
} finally {await browser.close();}
