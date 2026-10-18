import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const source = fs.readFileSync(new URL('../cloudflare/pages/_worker.js', import.meta.url), 'utf8');
const worker = (await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))).default;

test('online endpoint is explicitly unavailable and never starts a local job', async () => {
  const response = await worker.fetch(new Request('https://newshot-5jq.pages.dev/api/refresh/start', {method:'POST'}), {});
  assert.equal(response.status, 503);
  assert.equal((await response.json()).available, false);
  assert.equal(response.headers.get('Access-Control-Allow-Origin'), null);
});
test('control routes reject cross-site writes and OPTIONS without wildcard CORS', async () => {
  for (const request of [new Request('http://127.0.0.1:8799/api/refresh/start', {method:'POST', headers:{Origin:'https://evil.example'}}), new Request('http://127.0.0.1:8799/api/refresh/start', {method:'OPTIONS'})]) {
    const response = await worker.fetch(request, {});
    assert.ok([403,405].includes(response.status));
    assert.equal(response.headers.get('Access-Control-Allow-Origin'), null);
  }
});
test('local proxy only forwards to fixed backend and preserves nonce', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    assert.equal(url, 'http://127.0.0.1:8801/start');
    assert.equal(init.headers['X-Refresh-Nonce'], 'test-nonce');
    assert.equal(init.body, '{}');
    return Response.json({ok:true, created:true}, {status:202});
  };
  try {
    const response = await worker.fetch(new Request('http://127.0.0.1:8799/api/refresh/start', {method:'POST', headers:{Origin:'http://127.0.0.1:8799', 'Content-Type':'application/json', 'X-Refresh-Nonce':'test-nonce'}, body:'{}'}), {});
    assert.equal(response.status, 202);
    assert.equal((await response.json()).created, true);
  } finally { globalThis.fetch = original; }
});
