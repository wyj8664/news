import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../cloudflare/pages/_worker.js';

const endpoint = 'https://newshot-5jq.pages.dev/api/weibo/followed';
const snapshot = {ok: true, count: 1, items: [{id: 'weibo:123456789'}], accounts: [], fetched_at: new Date().toISOString()};

test('public endpoint reads the KV snapshot without exposing bindings', async () => {
  const response = await worker.fetch(new Request(endpoint), {
    NEWSHOT_WEIBO: {get: async key => { assert.equal(key, 'weibo:followed:v1'); return snapshot; }},
    PRIVATE_TOKEN: 'must-not-leak',
  });
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(payload.count, 1);
  assert.equal(payload.stale, false);
  assert.ok(!JSON.stringify(payload).includes('must-not-leak'));
});

test('old successful snapshot is marked stale rather than assigned a new sync time', async () => {
  const old = {...snapshot, fetched_at: '2020-01-01T00:00:00+08:00'};
  const response = await worker.fetch(new Request(endpoint), {NEWSHOT_WEIBO: {get: async () => old}});
  const payload = await response.json();
  assert.equal(payload.stale, true);
  assert.equal(payload.fetched_at, old.fetched_at);
});

test('local development can use the static public snapshot without KV', async () => {
  const response = await worker.fetch(new Request(endpoint), {
    ASSETS: {fetch: async request => {
      assert.equal(new URL(request.url).pathname, '/weibo/latest.json');
      return Response.json(snapshot);
    }},
  });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).count, 1);
});

test('endpoint rejects writes and never returns an HTML fallback as valid JSON', async () => {
  assert.equal((await worker.fetch(new Request(endpoint, {method: 'POST'}), {})).status, 405);
  const response = await worker.fetch(new Request(endpoint), {
    ASSETS: {fetch: async () => new Response('<html>not found</html>', {headers: {'content-type': 'text/html'}})},
  });
  assert.equal(response.status, 503);
});

test('deployment snapshot supersedes older KV and does not lose newly followed accounts', async () => {
  const asset = {...snapshot, count: 15, accounts: [{id:'1'},{id:'2'}]};
  for (const cached of [
    {...asset, count: 1, fetched_at:'2020-01-01T00:00:00+08:00'},
    {...asset, count: 1, accounts:[{id:'1'}], fetched_at:'2099-01-01T00:00:00+08:00'},
  ]) {
    const response=await worker.fetch(new Request(endpoint),{
      NEWSHOT_WEIBO:{get:async()=>cached},ASSETS:{fetch:async()=>Response.json(asset)}});
    assert.equal((await response.json()).count,15);
  }
});

test('newer complete KV is retained, while KV failures fall back to assets', async () => {
  const asset={...snapshot,accounts:[{id:'1'}],fetched_at:'2020-01-01T00:00:00+08:00'};
  const current={...snapshot,count:2,accounts:[{id:'1'}]};
  const fresh=await worker.fetch(new Request(endpoint),{
    NEWSHOT_WEIBO:{get:async()=>current},ASSETS:{fetch:async()=>Response.json(asset)}});
  assert.equal((await fresh.json()).count,2);
  const fallback=await worker.fetch(new Request(endpoint),{
    NEWSHOT_WEIBO:{get:async()=>{throw new Error('unavailable')}},ASSETS:{fetch:async()=>Response.json(asset)}});
  assert.equal(fallback.status,200);
});
