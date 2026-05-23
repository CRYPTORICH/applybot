import subprocess, urllib.parse

user = "dejesusrichard89@gmail.com"
password = "$0undWav3!$"
encoded_pw = urllib.parse.quote(password, safe='')
repo_name = "applybot"

# Create repo via GitHub API
import json, urllib.request

url = "https://api.github.com/user/repos"
data = json.dumps({"name": repo_name, "private": False}).encode()
req = urllib.request.Request(url, data=data, method="POST")
req.add_header("Authorization", "Basic " + __import__('base64').b64encode(f"{user}:{password}".encode()).decode())
req.add_header("Accept", "application/vnd.github+json")

try:
    resp = urllib.request.urlopen(req)
    result = json.loads(resp.read())
    print(f"Repo created: {result['html_url']}")
    
    # Now push
    import os
    os.chdir("C:/Users/dejes/applybot")
    subprocess.run(["git", "remote", "add", "origin", f"https://{user}:{encoded_pw}@github.com/{result['full_name']}.git"], 
                   capture_output=True)
    push = subprocess.run(["git", "push", "-u", "origin", "master"], capture_output=True, text=True)
    print("Push:", push.stdout or push.stderr)
except Exception as e:
    err = e.read().decode() if hasattr(e, 'read') else str(e)
    print(f"Error: {err}")
