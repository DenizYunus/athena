"""Delete every compiled skill so the marketplace shows only the seed skills.

    backend/.venv/bin/python demo/reset_skills.py http://<mac-ip>:8000
"""

import sys

import httpx

url = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
skills = httpx.get(f"{url}/skills", timeout=10).json()
compiled = [s for s in skills if s.get("compiled")]
for s in compiled:
    status = httpx.delete(f"{url}/skills/{s['id']}", timeout=10).status_code
    print(f"deleted {s['id']} ({s['title']}): HTTP {status}")
print(f"{len(compiled)} compiled skill(s) removed; {len(skills) - len(compiled)} seed skill(s) remain")
