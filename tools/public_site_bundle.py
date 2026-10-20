"""Allowlisted Pages assets only; never deploy raw output or private state."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
FIXED = {
    'index.html','curated/index.html','daily/index.html','daily/latest.md','timeline/index.html',
    'hotlists/index.html','hotlists/weibo/index.html','hotlists/telegraph/index.html','hotlists/telegraph/latest.json',
    'telegraph/index.html','telegraph/latest.json','assets/weibo-feed.js','weibo/latest.json',
    'source-pool/home-events.json','source-pool/cls-telegraph.json','source-pool/jin10-all.json',
    'source-pool/jiemian-flash.json','source-pool/status.json','markets/quotes.json','freshness/report.json',
    '_worker.js','_headers','site-manifest.json',
}
SECRET_KEYS = {'cookie','cookies','password','admin_password','authorization','api_token','access_token',
               'refresh_token','webhook','client_secret','private_key'}

def allowed(name):
    return (name in FIXED or bool(re.fullmatch(r'daily/20\d{2}-\d{2}-\d{2}\.(html|json|md)',name))) and '..' not in PurePosixPath(name).parts

def validate(name, data):
    if not allowed(name) or data.startswith(b'SQLite format 3'):
        raise ValueError(f'Not a public asset: {name}')
    if len(data)>20_000_000:
        raise ValueError(f'Asset too large: {name}')
    if re.search(rb'https://open\.feishu\.cn/open-apis/bot/v2/hook/|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|gh[pous]_[A-Za-z0-9]{30,}',data):
        raise ValueError(f'Credential-shaped content in {name}')
    if name.endswith('.json'):
        def check(value):
            if isinstance(value,dict):
                if SECRET_KEYS.intersection(str(key).lower() for key in value):
                    raise ValueError(f'Credential-shaped field in {name}')
                for child in value.values(): check(child)
            elif isinstance(value,list):
                for child in value: check(child)
        check(json.loads(data))

def pack(output, destination, archive=None):
    output, destination = Path(output).resolve(), Path(destination).resolve()
    if destination==output or output in destination.parents or destination==ROOT or ROOT in destination.parents and not destination.name.startswith('.site-deploy'):
        raise ValueError('Use a dedicated empty deployment directory')
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Deployment directory must be empty')
    destination.mkdir(parents=True,exist_ok=True)
    index = (output/'daily/index.html').read_text()
    match = re.search(r'data-daily-date="(\d{4}-\d{2}-\d{2})"',index)
    if not match: raise ValueError('Daily archive is not the approved calendar-day template')
    day=datetime.strptime(match[1],'%Y-%m-%d')
    dates=[(day-timedelta(days=n)).strftime('%Y-%m-%d') for n in range(7)]
    names=set(FIXED)-{'_worker.js','_headers','site-manifest.json'}
    names.update(f'daily/{date}.{ext}' for date in dates for ext in ('html','json','md'))
    hashes={}
    for name in sorted(names|{'_worker.js','_headers'}):
        source = ROOT/'cloudflare/pages'/name if name in {'_worker.js','_headers'} else output/name
        if source.is_symlink(): raise ValueError('Symlink asset rejected')
        if not source.is_file():
            if name in {'index.html','daily/index.html','hotlists/index.html','weibo/latest.json','source-pool/home-events.json','_worker.js','_headers'} or name.startswith('daily/20'):
                raise ValueError(f'Missing public page: {name}')
            continue
        data=source.read_bytes();validate(name,data)
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        hashes[name]=hashlib.sha256(data).hexdigest()
    artifact=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
    manifest={'schema_version':1,'artifact_id':artifact,'daily_date':match[1],
              'built_at':datetime.now(timezone.utc).isoformat(),'files':hashes}
    generation=re.search(r'data-home-generation="([^"]+)"',(destination/'index.html').read_text())
    if generation: manifest['generation']=generation[1]
    (destination/'site-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    if archive:
        archive=Path(archive);archive.parent.mkdir(parents=True,exist_ok=True)
        with tarfile.open(archive,'w:gz') as bundle:
            for path in sorted(destination.rglob('*')):
                if path.is_file(): bundle.add(path,arcname=path.relative_to(destination).as_posix(),recursive=False)
    return {'artifact_id':artifact,'files':len(hashes)+1,'bytes':sum(p.stat().st_size for p in destination.rglob('*') if p.is_file()),'daily_date':match[1]}

def restore(archive, output):
    output=Path(output).resolve();entries={}
    with tarfile.open(archive,'r:gz') as bundle:
        for member in bundle.getmembers():
            if not member.isfile() or not allowed(member.name) or member.size>20_000_000:
                raise ValueError(f'Unsafe snapshot member: {member.name}')
            data=bundle.extractfile(member).read();validate(member.name,data)
            if member.name in entries: raise ValueError('Duplicate snapshot member')
            entries[member.name]=data
    manifest=json.loads(entries['site-manifest.json'])
    expected=manifest['files']
    if set(entries)-{'site-manifest.json'}!=set(expected): raise ValueError('Snapshot membership mismatch')
    if any(hashlib.sha256(entries[name]).hexdigest()!=digest for name,digest in expected.items()):
        raise ValueError('Snapshot content hash mismatch')
    for name,data in entries.items():
        target=output/name
        if target.is_symlink() or any(parent.is_symlink() for parent in target.parents if parent!=output.parent):
            raise ValueError('Symlink target rejected')
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    return {'restored':len(entries),'artifact_id':manifest['artifact_id']}

def verify_target(expected_domain):
    # Existing GitHub secrets stay in runner memory; never print request headers.
    account=os.environ['CLOUDFLARE_ACCOUNT_ID'];token=os.environ['CLOUDFLARE_API_TOKEN']
    project=os.environ.get('CLOUDFLARE_PAGES_PROJECT','newshot')
    request=urllib.request.Request(f'https://api.cloudflare.com/client/v4/accounts/{account}/pages/projects/{project}',
                                   headers={'Authorization':f'Bearer {token}'})
    with urllib.request.urlopen(request,timeout=30) as response: payload=json.load(response)
    result=payload.get('result') or {}
    if not payload.get('success') or result.get('subdomain')!=expected_domain:
        raise ValueError('Configured account/project is not the existing NEWSHOT site; refusing deployment')
    return {'verified_project':result['name'],'domain':result['subdomain']}

def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='mode',required=True)
    create=sub.add_parser('pack');create.add_argument('--output-root',type=Path,default=Path('output'))
    create.add_argument('--destination',type=Path,required=True);create.add_argument('--archive',type=Path)
    recover=sub.add_parser('restore');recover.add_argument('--archive',type=Path,required=True)
    recover.add_argument('--output-root',type=Path,default=Path('output'))
    target=sub.add_parser('verify-target');target.add_argument('--expected-domain',default='newshot-5jq.pages.dev')
    args=parser.parse_args()
    if args.mode=='pack': result=pack(args.output_root,args.destination,args.archive)
    elif args.mode=='restore': result=restore(args.archive,args.output_root)
    else: result=verify_target(args.expected_domain)
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__': main()
