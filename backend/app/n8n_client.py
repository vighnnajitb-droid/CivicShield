"""Thin client the backend uses to start n8n workflows. Every call is recorded in workflow_runs (visible to admins)."""
import json, urllib.request, urllib.error
from . import config, core
from .db import get_conn

WORKFLOW_PATHS = {  # workflow name -> n8n webhook path
    "complaint_intake": "complaint-created", "ai_analysis": "ai-analysis", "evidence_processing": "evidence-uploaded",
    "audit_trail": "case-event", "authority_assignment": "authority-assignment", "deadline_monitor": "deadline-monitor-run",
    "automatic_escalation": "escalate-case", "notifications": "notify", "duplicate_detection": "duplicate-check",
    "appeal_processing": "appeal-submitted", "legal_assistance": "legal-assistance", "case_summary": "case-summary"}

class N8NUnavailable(Exception): pass

def _post(path, payload, timeout):
    req = urllib.request.Request(f"{config.N8N_WEBHOOK_BASE}/{path}", data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-CivicShield-Key": config.INTERNAL_API_KEY})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode() or "{}"
        return json.loads(body)

def trigger(workflow, payload, complaint_id=None, timeout=60, raise_on_fail=False, record_payload=True):
    """Run an n8n workflow synchronously (webhook -> Respond to Webhook). Returns the workflow's JSON response."""
    now = core.iso(core.utcnow())
    with get_conn() as c:
        slim = json.dumps({k: v for k, v in payload.items() if k != "content_base64"})[:4000] if record_payload else "{}"
        rid = c.execute("INSERT INTO workflow_runs(workflow,complaint_id,status,request,attempts,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                        (workflow, complaint_id, "running", slim, 1, now, now)).lastrowid
    try:
        res = _post(WORKFLOW_PATHS[workflow], payload, timeout)
        status, err = "completed", None
    except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError, OSError) as e:
        res, status, err = None, "pending", f"{type(e).__name__}: {e}"
    with get_conn() as c:
        c.execute("UPDATE workflow_runs SET status=?, response=?, error=?, updated_at=? WHERE id=?",
                  (status, json.dumps(res)[:8000] if res is not None else None, err, core.iso(core.utcnow()), rid))
    if res is None and raise_on_fail: raise N8NUnavailable(err)
    return res

def retry(run_id):
    with get_conn() as c:
        r = c.execute("SELECT * FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
        if not r: raise core.CoreError("Run not found", 404)
        r = dict(r)
    if r["workflow"] == "evidence_processing": raise core.CoreError("Evidence uploads must be re-sent by the citizen", 422)
    payload = json.loads(r["request"] or "{}")
    now = core.iso(core.utcnow())
    try:
        res, status, err = _post(WORKFLOW_PATHS[r["workflow"]], payload, 60), "completed", None
    except Exception as e:
        res, status, err = None, "pending", f"{type(e).__name__}: {e}"
    with get_conn() as c:
        c.execute("UPDATE workflow_runs SET status=?, response=?, error=?, attempts=attempts+1, updated_at=? WHERE id=?",
                  (status, json.dumps(res)[:8000] if res is not None else None, err, now, run_id))
    return {"status": status, "error": err}
