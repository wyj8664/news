import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const script=fs.readFileSync(new URL('./home_refresh.js',import.meta.url),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function element() {
  return {disabled:true,hidden:false,textContent:'',dataset:{generation:'2026-10-20T09:38:00+08:00'},children:[],handlers:{},
    addEventListener(name,fn){this.handlers[name]=fn;},replaceChildren(){this.children=[];},append(node){this.children.push(node);}};
}
function harness(replies) {
  const nodes=new Map(),requests=[],timers=[];
  const document={hidden:false,handlers:{},getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);},
    createElement:element,addEventListener(name,fn){this.handlers[name]=fn;}};
  let pending=0,maxPending=0;
  vm.runInNewContext(script,{document,navigator:{onLine:true},Date,TypeError,AbortSignal,sessionStorage:{getItem(){return null;},removeItem(){},setItem(){}},
    location:{reload(){}},window:{prompt(){throw new Error('Reconnect must not prompt for a password');}},
    setTimeout(fn,delay){timers.push({fn,delay});return timers.length;},clearTimeout(){},
    async fetch(url,init){requests.push({url,method:init.method||'GET'});pending++;maxPending=Math.max(maxPending,pending);
      try {const reply=replies.shift();if(reply instanceof Error)throw reply;return Response.json(reply);}
      finally{pending--;}}});
  return {nodes,document,requests,timers,get maxPending(){return maxPending;}};
}
const good=job=>({ok:true,mode:'remote',authenticated:false,nonce:'',agent_online:true,cooldown_seconds:600,job});
const timeout=()=>Object.assign(new Error('signal timed out'),{name:'TimeoutError'});
test('a timeout offers GET-only reconnect, then restores the update button without starting collection',async()=>{
  const h=harness([timeout(),good(null)]);await tick();
  const button=h.nodes.get('home-refresh-button');
  assert.equal(button.textContent,'重试连接');assert.equal(button.disabled,false);
  assert(h.nodes.get('home-refresh-status').textContent.includes('读取更新状态超时'));
  assert(!h.nodes.get('home-refresh-status').textContent.includes('signal timed out'));
  assert.equal(h.timers.at(-1).delay,5000);
  await button.handlers.click();await tick();
  assert.equal(button.textContent,'更新领域分类');assert.equal(button.disabled,false);
  assert.deepEqual(h.requests.map(x=>x.method),['GET','GET']);
});
test('temporary failures preserve source progress, retry automatically, and never resubmit a running job',async()=>{
  const job={id:'job',terminal:false,finished_at:null,message:'微博排队中',done:15,total:16,
    updated_at:new Date().toISOString(),sources:[{name:'微博',state:'running',message:'5/15'}]};
  const h=harness([good(job),timeout(),timeout(),good(job)]);await tick();
  const button=h.nodes.get('home-refresh-button'),sources=h.nodes.get('home-refresh-sources');
  assert.equal(sources.children.length,1);assert.equal(button.textContent,'更新中…');
  await h.timers.at(-1).fn();await tick();
  assert.equal(button.textContent,'重试连接');assert.equal(sources.children.length,1);
  assert(h.nodes.get('home-refresh-status').textContent.includes('上次连接'));
  await button.handlers.click();await tick();assert.equal(h.timers.at(-1).delay,10000);
  await h.timers.at(-1).fn();await tick();
  assert.equal(button.textContent,'更新中…');assert.equal(button.disabled,true);
  assert(h.requests.every(x=>x.method==='GET'));assert.equal(h.maxPending,1);
});
