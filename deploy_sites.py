import subprocess, os, json, requests, sys

LEADS_FILE = r'C:\Users\dejes\applybot\sites\leads.json'
SITES_DIR = r'C:\Users\dejes\applybot\sites'

with open(LEADS_FILE) as f:
    leads = json.load(f)

hot = ['HVAC', 'Plumber', 'Electrician']
no_site = [l for l in leads if l.get('no_site') and l.get('cat') in hot][:6]

result = subprocess.run(['git', 'credential', 'fill'], 
    input='url=https://github.com\n\n', capture_output=True, text=True, cwd=SITES_DIR)
token = [l.split('=',1)[1] for l in result.stdout.split('\n') if l.startswith('password=')][0]

headers = {'Authorization': f'token {token}', 'Accept': 'application/vnd.github.v3+json'}

for lead in no_site:
    slug = lead['slug']
    cat = lead['cat']
    name = lead['name']
    phone = lead['phone']
    site_path = os.path.join(SITES_DIR, slug)
    idx = os.path.join(site_path, 'index.html')
    
    if not os.path.exists(idx):
        print(f'SKIP {slug}')
        continue
    
    desc = f'{cat} services — {name} | {phone}'
    r = requests.post('https://api.github.com/user/repos', headers=headers,
        json={'name': slug, 'description': desc, 'private': False, 'has_pages': True})
    
    if r.status_code == 201:
        print(f'NEW: {slug}')
    elif r.status_code == 422:
        print(f'EXISTS: {slug}')
    else:
        print(f'FAIL {slug}: {r.status_code} {r.json().get("message","")}')
        continue
    
    os.chdir(site_path)
    subprocess.run(['git', 'init'], capture_output=True)
    subprocess.run(['git', 'add', '-A'], capture_output=True)
    subprocess.run(['git', 'commit', '-m', f'{name} — {cat} Services'], capture_output=True)
    subprocess.run(['git', 'remote', 'remove', 'origin'], capture_output=True)
    subprocess.run(['git', 'remote', 'add', 'origin', f'https://github.com/CRYPTORICH/{slug}.git'], capture_output=True)
    push = subprocess.run(['git', 'push', '-u', 'origin', 'master', '--force'], capture_output=True, text=True)
    
    if push.returncode == 0:
        r2 = requests.post(f'https://api.github.com/repos/CRYPTORICH/{slug}/pages', headers=headers,
            json={'source': {'branch': 'master', 'path': '/'}})
        print(f'  LIVE: https://cryptorich.github.io/{slug}/')
    else:
        err = push.stderr[-150:] if push.stderr else 'unknown'
        print(f'  PUSH FAIL: {err}')

print('DONE')
