import urllib.request
import json
import base64

token_req = urllib.request.Request("http://127.0.0.1:8000/api/auth/login", data=json.dumps({"email":"priya@demo.in","password":"Demo@12345"}).encode(), method="POST", headers={"Content-Type": "application/json"})
with urllib.request.urlopen(token_req) as r:
    token = json.loads(r.read())["token"]

import subprocess
cmd = [
    "curl", "-X", "POST",
    "-H", f"Authorization: Bearer {token}",
    "-F", "file=@/tmp/file_attachments/evidence_1_complaint_receipt.txt",
    "http://127.0.0.1:8000/api/complaints/7/evidence"
]
print("Running:", " ".join(cmd))
res = subprocess.run(cmd, capture_output=True, text=True)
print(res.stdout)
print(res.stderr)
