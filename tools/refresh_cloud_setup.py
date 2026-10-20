"""Inspect the existing Pages target without disclosing credentials."""
import json
import os
import argparse
import subprocess
import urllib.error
import urllib.request


def api(path, method='GET', body=None):
    account = os.environ['CLOUDFLARE_ACCOUNT_ID']
    request = urllib.request.Request(
        f'https://api.cloudflare.com/client/v4/accounts/{account}/{path}',
        headers={'Authorization': 'Bearer ' + os.environ['CLOUDFLARE_API_TOKEN'], 'Content-Type':'application/json'},
        method=method, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        # Do not print the request, headers, full error body or project env vars.
        return error.code, {'success': False}


def inspect(apply=False):
    status, payload = api('pages/projects/newshot')
    project = payload.get('result') or {}
    if status != 200 or project.get('subdomain') != 'newshot-5jq.pages.dev':
        raise SystemExit('Existing NEWSHOT target verification failed')
    production = (project.get('deployment_configs') or {}).get('production') or {}
    kv_status, kv = api('storage/kv/namespaces?per_page=100')
    db_status, db = api('d1/database?per_page=100')
    result = {
        'project': project['name'], 'domain': project['subdomain'],
        'existing_kv_bindings': sorted((production.get('kv_namespaces') or {}).keys()),
        'existing_d1_bindings': sorted((production.get('d1_databases') or {}).keys()),
        'kv_read_status': kv_status, 'd1_read_status': db_status,
        'kv_names': [item.get('title') for item in kv.get('result', [])],
        'd1_names': [item.get('name') for item in db.get('result', [])],
    }
    print(json.dumps(result, ensure_ascii=False))
    if not apply:
        return
    if kv_status != 200:
        raise SystemExit('Workers KV permission is required; no changes applied')
    existing=next((item for item in kv['result'] if item.get('title')=='newshot-refresh-relay'),None)
    if existing is None:
        status, created=api('storage/kv/namespaces','POST',{'title':'newshot-refresh-relay'})
        if status not in (200,201) or not created.get('success'):
            raise SystemExit('Cannot create the KV namespace with the existing credentials')
        existing=created['result']
    bindings=dict(production.get('kv_namespaces') or {})
    bindings['NEWSHOT_REFRESH']={'namespace_id':existing['id']}
    status, updated=api('pages/projects/newshot','PATCH',{'deployment_configs':{'production':{'kv_namespaces':bindings}}})
    if status!=200 or not updated.get('success'):
        raise SystemExit('KV namespace exists, but Pages binding was not confirmed')
    for name in ('NEWSHOT_REFRESH_ADMIN_HASH','NEWSHOT_REFRESH_AGENT_HASH','NEWSHOT_REFRESH_SESSION_SECRET'):
        value=os.environ.get(name,'')
        if len(value)<32:
            raise SystemExit('Missing private refresh secret: '+name)
        subprocess.run(['npx','--yes','wrangler@4.151.1','pages','secret','put',name,'--project-name','newshot'],
                       input=value.encode(),check=True)
    print(json.dumps({'configured':True,'binding':'NEWSHOT_REFRESH','paid_plan_changes':False}))


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true')
    inspect(parser.parse_args().apply)
