import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {gzipSync} from 'node:zlib';
const source=fs.readFileSync(new URL('../cloudflare/pages/_worker.js',import.meta.url),'utf8');
const worker=(await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'))).default;
const origin='https://newshot-5jq.pages.dev';
const hash=async value=>Buffer.from(await crypto.subtle.digest('SHA-256',typeof value==='string'?new TextEncoder().encode(value):value)).toString('hex');
function storage() {
  const data=new Map();
  return {data,async get(key,options){const v=data.get(key); if(v==null)return null;
    if(options==='json'||options?.type==='json')return JSON.parse(v);return v;},
    async put(k,v){data.set(k,v);},async list({prefix}){return {keys:[...data.keys()].filter(k=>k.startsWith(prefix)).map(name=>({name}))};}};
}
async function setup() {
  return {NEWSHOT_REFRESH:storage(),NEWSHOT_REFRESH_ADMIN_HASH:await hash('a-strong-test-password'),
    NEWSHOT_REFRESH_AGENT_HASH:await hash('a'.repeat(43)),NEWSHOT_REFRESH_SESSION_SECRET:'s'.repeat(43)};
}
const request=(path,options={})=>new Request(origin+'/api/refresh/'+path,options);
const write=(body,extra={})=>({method:'POST',headers:{Origin:origin,'Content-Type':'application/json',...extra},body:JSON.stringify(body)});
async function login(env) {
  const response=await worker.fetch(request('login',write({password:'a-strong-test-password'})),env);
  assert.equal(response.status,200); const data=await response.json();
  return {Cookie:response.headers.get('Set-Cookie').split(';')[0],'X-Refresh-Nonce':data.nonce};
}
test('remote status is available without exposing secrets; writes require admin and same origin',async()=>{
  const env=await setup(); const result=await (await worker.fetch(request('status'),env)).json();
  assert.equal(result.mode,'remote');assert.equal(result.authenticated,false);assert.equal(result.nonce,'');
  assert.equal((await worker.fetch(request('start',write({})),env)).status,401);
  assert.equal((await worker.fetch(request('login',write({password:'wrong'})),env)).status,401);
  const headers=await login(env);
  const cross=write({},headers);cross.headers.Origin='https://evil.example';
  assert.equal((await worker.fetch(request('start',cross),env)).status,403);
  assert.equal((await worker.fetch(request('start',write({command:'send-feishu'},headers)),env)).status,400);
  assert.equal(env.NEWSHOT_REFRESH.data.size,0);
});
test('duplicate clicks coalesce; agent authentication is distinct and durable status is shown',async()=>{
  const env=await setup(),headers=await login(env);
  const first=await (await worker.fetch(request('start',write({},headers)),env)).json();
  const second=await (await worker.fetch(request('start',write({},headers)),env)).json();
  assert.equal(first.job.id,second.job.id);assert.equal(second.created,false);
  assert.equal((await worker.fetch(request('agent/inbox'),env)).status,401);
  const agent={Authorization:'Bearer '+'a'.repeat(43)};
  const inbox=await (await worker.fetch(request('agent/inbox',{headers:agent}),env)).json();
  assert.equal(inbox.commands.length,1);
  const job={...first.job,id:'local-job',state:'running',phase:'weibo',message:'等待微博',terminal:false};
  assert.equal((await worker.fetch(request('agent/status',write({request_id:first.job.id,job},agent)),env)).status,200);
  const status=await (await worker.fetch(request('status',{headers}),env)).json();
  assert.equal(status.job.id,'local-job');assert.equal(status.agent_online,true);assert.equal(status.authenticated,true);
});
test('publish rejects private paths and missing files; only complete versions become visible',async()=>{
  const env=await setup(),agent={Authorization:'Bearer '+'a'.repeat(43)};
  const bytes=gzipSync(Buffer.from('<html>new page</html>')); const digest=await hash(bytes);
  const generation=new Date().toISOString();
  const publish=files=>worker.fetch(request('agent/publish',write({files,generation},agent)),env);
  assert.equal((await publish({'index.html':digest})).status,409);
  assert.equal(env.NEWSHOT_REFRESH.data.has('site:current'),false);
  assert.equal((await publish({'index.html':digest,'briefing/sent_history.json':digest})).status,409);
  assert.equal((await worker.fetch(request('agent/asset?hash='+digest),{...env})).status,401);
  const upload=await worker.fetch(request('agent/asset?hash='+digest,{method:'PUT',headers:{...agent,'Content-Type':'application/gzip'},body:bytes}),env);
  assert.equal(upload.status,200);
  assert.equal((await publish({'index.html':digest,'briefing/sent_history.json':digest})).status,400);
  assert.equal((await publish({'index.html':digest})).status,200);
  const page=await worker.fetch(new Request(origin+'/'),env);
  assert.equal(page.headers.get('Content-Encoding'),'gzip');assert.equal(page.headers.get('X-Newshot-Generation'),generation);
});
