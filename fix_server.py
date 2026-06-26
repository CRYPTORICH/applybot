#!/usr/bin/env python3
"""Fix server.py /setup endpoint to allow direct shop creation without prior session."""
import re

with open("/home/ubuntu/rewards-backend/backend/server.py", "r") as f:
    content = f.read()

# Fix /setup endpoint: add fallback for missing session (direct setup)
old_setup = '@app.route("/setup", methods=["POST"])\ndef setup():\n    auth = request.headers.get("Authorization", "")\n    token = auth.replace("Bearer ", "")\n    if not token or token not in sessions:\n        return jsonify({"error": "Session expired. Start over."}), 401\n\n    s = sessions[token]'

new_setup = '''@app.route("/setup", methods=["POST"])
def setup():
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "")
    data = request.get_json() or {}
    
    # Fallback: direct setup without prior session (for admin.html onboarding)
    if not token or token not in sessions:
        email = data.get("email", "")
        shop_name = data.get("shop_name", "")
        slug = data.get("slug", "")
        if email and shop_name and slug:
            token = secrets.token_hex(32)
            sessions[token] = {
                "email": email,
                "shop_name": shop_name,
                "slug": slug,
                "verified": True,
                "created_at": datetime.now().isoformat()
            }
        else:
            return jsonify({"error": "Session expired. Start over."}), 401

    s = sessions[token]'''

if old_setup in content:
    content = content.replace(old_setup, new_setup)
    print("✅ /setup endpoint patched with direct-setup fallback")
else:
    print("❌ Could not find old_setup pattern")
    # Try to find the setup function
    idx = content.find('def setup():')
    if idx > 0:
        print(f"   Found setup() at position {idx}")
        print(f"   Context: {content[idx:idx+200]}")

# Add import secrets if not present
if "import secrets" not in content[:500]:
    content = content.replace("import os\n", "import os\nimport secrets\n", 1)
    print("✅ Added import secrets")

with open("/home/ubuntu/rewards-backend/backend/server.py", "w") as f:
    f.write(content)

print(f"✅ server.py updated ({len(content)} bytes)")
