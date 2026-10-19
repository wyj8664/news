import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const sandbox = {};
vm.runInNewContext(fs.readFileSync(new URL('./news_ranking.js', import.meta.url),'utf8'),sandbox);
const ranking=sandbox.NEWSHOT_RANKING;
const tools=fileURLToPath(new URL('.',import.meta.url));
const python=spawnSync('python3',['-c',`import json; from news_ranking import POLICY,decay_factor; print(json.dumps({'policy':POLICY,'factors':[[h,decay_factor(h)] for h in (-1,0,1,1.001,3,6,12,18,24,48,55,72)]}))`],
  {env:{...process.env,PYTHONPATH:tools},encoding:'utf8'});
assert.equal(python.status,0,python.stderr);
const expected=JSON.parse(python.stdout);

test('Python and browser share the versioned policy and exact continuous coefficients',()=>{
  assert.deepEqual(JSON.parse(JSON.stringify(ranking.policy)),expected.policy);
  for(const [hours,factor] of expected.factors) assert.ok(Math.abs(ranking.factor(hours)-factor)<1e-12);
});
test('browser always decays original importance; repeated ticks cannot compound',()=>{
  const now=Date.parse('2026-10-19T14:43:00+08:00');
  const node={importance_score:100,score:0,first_reported_at:'2026-10-19T02:43:00+08:00'};
  const first=ranking.card([node],now);
  assert.equal(first,64.0307756);
  node.score=first;
  assert.equal(ranking.card([node],now),first);
  assert.ok(ranking.card([node],now+3600000)<first);
});
test('card uses max separately decayed progress, never old importance times a new clock',()=>{
  const now=Date.parse('2026-10-19T14:43:00+08:00');
  const nodes=[{importance_score:120,first_reported_at:'2026-10-18T15:43:00+08:00'},
    {importance_score:20,first_reported_at:'2026-10-19T14:43:00+08:00'}];
  assert.equal(ranking.card(nodes,now),ranking.progress(nodes[0],now));
  assert.ok(ranking.card(nodes,now)<120);
});
test('ties prefer real progress time then stable ID, not capture time',()=>{
  const rows=[{id:'event:b',score:80,first:10},{id:'event:a',score:80,first:10},{id:'event:c',score:80,first:20}];
  rows.sort(ranking.compare);
  assert.deepEqual(rows.map(row=>row.id),['event:c','event:a','event:b']);
});
