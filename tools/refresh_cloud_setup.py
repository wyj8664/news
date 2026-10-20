"""Inspect the existing Pages target without disclosing credentials."""
import json
import os
import urllib.error
import urllib.request


def api(path):
    account = os.environ['CLOUDFLARE_ACCOUNT_ID']
    request = urllib.request.Request(
        f'https://api.cloudflare.com/client/v4/accounts/{account}/{path}',
        headers={'Authorization': 'Bearer ' + os.environ['CLOUDFLARE_API_TOKEN']})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        # Do not print the request, headers, full error body or project env vars.
        return error.code, {'success': False}


def inspect():
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


if __name__ == '__main__':
    inspect()
