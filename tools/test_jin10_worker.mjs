import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../cloudflare/pages/_worker.js';

const endpoint = 'https://newshot-5jq.pages.dev/api/jin10/flash?refresh=1';
const fixedNow = Date.parse('2026-10-18T18:00:00+08:00');
const row = (id, extra = {}) => ({id, time:'2026-10-18 17:00:00', important:0, hot:'', data:{content:`普通公开快讯${id}`}, ...extra});

async function run(pages, callback, {classificationFailure = false} = {}) {
  const originalFetch = globalThis.fetch;
  const originalNow = Date.now;
  const calls = [];
  Date.now = () => fixedNow;
  globalThis.fetch = async (input, init) => {
    const url = new URL(input);
    calls.push(url);
    assert.equal(init.headers.Cookie, undefined, 'no login credentials');
    assert.equal(init.headers.Authorization, undefined);
    if (url.pathname === '/classify') {
      if (classificationFailure) return new Response('', {status:403});
      return Response.json({data:[]});
    }
    assert.equal(url.pathname, '/flash', 'no article/login/paid endpoint requests');
    const params = JSON.parse(url.searchParams.get('params'));
    assert.deepEqual(params.channel, [1,5,9]);
    assert.equal(params.hot, undefined, 'normal public stream, not hot-only');
    const page = pages.shift();
    if (page instanceof Error) throw page;
    assert.ok(page, 'unexpected extra upstream request');
    return Response.json({data:page});
  };
  try {
    const response = await worker.fetch(new Request(endpoint), {});
    await callback(await response.json(), response, calls);
  } finally {
    globalThis.fetch = originalFetch;
    Date.now = originalNow;
  }
}

test('ordinary and important flashes enter; all restricted flags and placeholders are excluded', async () => {
  const restricted = ['is_vip','is_plus','need_login','need_pay','login_required','paywall','requires_payment'].map((key,index) => row(`restricted-${index}`, {[key]:true}));
  restricted.push(row('plus', {data:{content:'金十PLUS专享快讯'}}), row('unlock', {data:{content:'解锁文章'}}), row('unknown', {access:'unverified'}));
  await run([[row('ordinary'), row('important', {important:1}), ...restricted, row('old', {time:'2026-10-17 17:00:00'})]], async (payload, response) => {
    assert.equal(response.status, 200);
    assert.equal(payload.count, 2);
    assert.equal(payload.public_only, true);
    assert.equal(payload.window_complete, true);
    assert.equal(payload.skipped_restricted, restricted.length);
    assert.equal(payload.items.find(item => item.id === 'ordinary').rank_label, '普通快讯');
    assert.ok(!JSON.stringify(payload).includes('专享快讯'));
  });
});

test('more than 80 ordinary flashes survive and classify failure does not block collection', async () => {
  const records = Array.from({length:130}, (_, index) => row(String(index)));
  await run([[...records, row('old', {time:'2026-10-17 17:00:00'})]], async payload => {
    assert.equal(payload.count, 130);
    assert.equal(payload.window_complete, true);
  }, {classificationFailure:true});
});

test('later public request failure returns partial data with honest coverage', async () => {
  await run([[row('new')], new Error('offline')], async payload => {
    assert.equal(payload.count, 1);
    assert.equal(payload.window_complete, false);
    assert.equal(payload.truncated, true);
    assert.ok(payload.errors.length);
  });
});

test('empty public stream does not claim a complete 24-hour window', async () => {
  await run([[]], async payload => {
    assert.equal(payload.count, 0);
    assert.equal(payload.window_complete, false);
    assert.ok(payload.errors.length);
  });
});
