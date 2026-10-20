#!/usr/bin/env python3
"""Outbound HTTPS relay; never runs a collector or Feishu sender itself."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import tempfile
import time
import urllib.request

from public_site_bundle import pack

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'https://newshot-5jq.pages.dev'
LOCAL = 'http://127.0.0.1:8801'


def initialize(root=ROOT):
    directory = root/'.local-refresh/online'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    credentials = directory/'agent.json'
    password_path = directory/'管理员刷新口令.txt'
    if credentials.exists() or password_path.exists():
        raise ValueError('Credentials already exist; refusing accidental rotation')
    token, password, session = (secrets.token_urlsafe(32) for _ in range(3))
    for path, data in ((credentials, json.dumps({'agent_token':token})), (password_path,password+'\n')):
        with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as handle:
            handle.write(data)
    for name, value in {'NEWSHOT_REFRESH_ADMIN_HASH':hashlib.sha256(password.encode()).hexdigest(),
                        'NEWSHOT_REFRESH_AGENT_HASH':hashlib.sha256(token.encode()).hexdigest(),
                        'NEWSHOT_REFRESH_SESSION_SECRET':session}.items():
        subprocess.run(['gh','secret','set',name,'--repo','wyj8664/news'],input=value.encode(),
                       check=True,stdout=subprocess.DEVNULL)
    print(json.dumps({'credentials_saved':True,'admin_password_file':str(password_path)},ensure_ascii=False))


class Relay:
    def __init__(self,root=ROOT):
        self.root=Path(root)
        self.directory=self.root/'.local-refresh/online'
        credentials=self.directory/'agent.json'
        if credentials.stat().st_mode & 0o077:
            raise ValueError('Agent credentials must be private (0600)')
        self.token=json.loads(credentials.read_text())['agent_token']
        self.db=sqlite3.connect(self.directory/'relay.sqlite3')
        self.db.execute('CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL)')

    def get(self,key,default=None):
        row=self.db.execute('SELECT value FROM state WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self,key,value):
        self.db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',(key,json.dumps(value)))
        self.db.commit()

    def request(self,path,body=None,method=None):
        # Fixed origin and path; browser cannot supply destinations or shell args.
        headers={'Authorization':'Bearer '+self.token}
        if body is not None:
            headers['Content-Type']='application/json'
            body=json.dumps(body,ensure_ascii=False).encode()
        request=urllib.request.Request(ORIGIN+'/api/refresh/agent/'+path,data=body,headers=headers,method=method)
        with urllib.request.urlopen(request,timeout=60) as response:
            result=json.load(response)
        if not result.get('ok'):
            raise RuntimeError('Cloud relay did not confirm the operation')
        return result

    def spend(self,count=1):
        # Reserved headroom for authenticated commands on the free 1000-write plan.
        day=datetime.now(timezone.utc).strftime('%Y-%m-%d')
        used=self.get('budget:'+day,0)
        if used+count>750:
            raise RuntimeError('Daily safe cloud-write budget exhausted; retaining the previous public version')
        self.put('budget:'+day,used+count)  # count failed attempts conservatively

    def local(self,path,body=None,nonce=None):
        headers={'Origin':'http://127.0.0.1:8799'}
        if body is not None:
            headers.update({'Content-Type':'application/json','X-Refresh-Nonce':nonce})
        request=urllib.request.Request(LOCAL+path,data=None if body is None else json.dumps(body).encode(),headers=headers)
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=12) as response:
            return json.load(response)

    def accept(self,command):
        key='command:'+command['id']
        binding=self.get(key,{})
        if binding.get('job_id'):
            return binding['job_id']
        status=self.local('/status'); job=status.get('job')
        attempted=binding.get('attempted_at')
        # Recover an acknowledged-or-not local enqueue after relay restart.
        if attempted and job and datetime.fromisoformat(job['created_at']).timestamp()>=attempted-1:
            binding['job_id']=job['id']
        elif job and not job['terminal']:
            binding['job_id']=job['id']
        else:
            binding['attempted_at']=time.time(); self.put(key,binding)
            result=self.local('/start',{},status['nonce'])
            binding['job_id']=result['job']['id']
        self.put(key,binding)
        return binding['job_id']

    def upload(self,hash_value,data):
        self.spend()
        request=urllib.request.Request(ORIGIN+'/api/refresh/agent/asset?hash='+hash_value,data=data,
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/gzip'},method='PUT')
        with urllib.request.urlopen(request,timeout=60) as response:
            if not json.load(response).get('ok'): raise RuntimeError('Asset upload not confirmed')

    def publish(self,job):
        generation=job.get('published_generation')
        if not generation or job.get('phase')=='publishing' or self.get('published')==generation:
            return False
        # Existing runner sets publishing BEFORE writing; compare again after copy.
        with tempfile.TemporaryDirectory(prefix='public-stage-',dir=self.directory) as directory:
            dest=Path(directory)/'public'
            pack(self.root/'output',dest)
            latest=self.local('/status')['job']
            if not latest or latest['id']!=job['id'] or latest.get('phase')=='publishing' or latest.get('published_generation')!=generation:
                return False
            home=(dest/'index.html').read_text()
            if f'data-home-generation="{generation}"' not in home:
                return False
            manifest=json.loads((dest/'site-manifest.json').read_text())
            files={}
            for name in manifest['files']:
                if name in {'_worker.js','_headers'}: continue
                data=gzip.compress((dest/name).read_bytes(),mtime=0)
                digest=hashlib.sha256(data).hexdigest()
                if self.get('asset:'+digest,0)<time.time()-86400:
                    self.upload(digest,data)
                    self.put('asset:'+digest,time.time())
                files[name]=digest
            self.spend()
            self.request('publish',{'files':files,'generation':generation})
            self.put('published',generation)
        return True

    def tick(self):
        inbox=self.request('inbox')['commands']
        commands=[x for x in inbox if re.fullmatch(r'\d{10}',x.get('id','')) and
                  (time.time()-datetime.fromisoformat(x['created_at']).timestamp()<3600
                   or self.get('command:'+x['id'],{}).get('job_id'))]
        command=max(commands,key=lambda x:x['id']) if commands else None
        # Older commands are coalesced into the same authoritative local job.
        if command: self.accept(command)
        status=self.local('/status'); job=status.get('job')
        published=False
        if job and command:
            try:
                published=self.publish(job)
            except Exception as error:
                print('Public stage upload pending: '+type(error).__name__,flush=True)
        remote_job=dict(job) if job and command else None
        if remote_job and remote_job.get('published_generation')!=self.get('published'):
            remote_job['published_generation']=None
            if remote_job['terminal']:
                remote_job['terminal']=False; remote_job['phase']='uploading'; remote_job['message']='来源采集已结束，正在将新版页面同步到线上。'
        request_id=command['id'] if command else self.get('request_id','0000000000')
        payload={'request_id':request_id,'job':remote_job}
        fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        elapsed=time.time()-self.get('reported_at',0)
        if elapsed>=300 or elapsed>=30 and fingerprint!=self.get('reported') or published:
            self.spend(); self.request('status',payload)
            self.put('reported',fingerprint); self.put('reported_at',time.time()); self.put('request_id',request_id)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--initialize',action='store_true'); parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    if args.initialize: initialize(); return
    relay=Relay()
    with (relay.directory/'agent.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            try:
                relay.tick()
            except Exception as error:
                # No tokens, headers, request bodies or upstream diagnostics in logs.
                print('Online refresh relay retryable failure: '+type(error).__name__,flush=True)
                if args.once: raise SystemExit(1) from None
            if args.once: return
            time.sleep(20)


if __name__=='__main__': main()
