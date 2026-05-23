import sys, os
for root, dirs, files in os.walk("."):
    # Skip .git
    if ".git" in root:
        continue
    for fname in files:
        fpath = os.path.join(root, fname)
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if "fvwtbxzbjbawhlzm" in content or "Dejesusrichard89@gmail.com" in content:
                content = content.replace("fvwtbxzbjbawhlzm", "REDACTED_PW")
                content = content.replace("Dejesusrichard89@gmail.com", "REDACTED_EMAIL")
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(content)
        except:
            pass
