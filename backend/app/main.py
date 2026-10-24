"""CivicShield FastAPI backend. Business processes are executed by n8n (see n8n-workflows/); this API owns data + authorization."""
import base64, hmac, json, logging
from pathlib import Path
from fastapi import FastAPI, Depends, Header, HTTPException, UploadFile, File, Body
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from . import config, core, db, n8n_client, seed, security, ai_mock
from .core import CoreError
from .db import get_conn, rows, one

app = FastAPI(title="CivicShield API", version="1.0")

@app.on_event("startup")
def _startup():
    with get_conn() as c:
        db.init(c)
        if seed.seed_all(c): logging.info("Demo data seeded")

def err(e: CoreError): raise HTTPException(status_code=e.code, detail=str(e))

# ------------------------------------------------------------------ auth
def current_user(authorization: str = Header(None)):
    if not authorization or not authorization.lower().startswith("bearer "): raise HTTPException(401, "Missing token")
    try: claims = security.read_token(authorization.split(" ", 1)[1])
    except Exception: raise HTTPException(401, "Invalid or expired token")
    with get_conn() as c:
        u = one(c.execute("SELECT * FROM users WHERE id=? AND active=1", (int(claims["sub"]),)))
    if not u: raise HTTPException(401, "Unknown user")
    return u

def require(*roles):
    def dep(user=Depends(current_user)):
        if user["role"] not in roles: raise HTTPException(403, "Your role cannot perform this action")
        return user
    return dep

def internal(x_internal_key: str = Header(None)):
    if not x_internal_key or not hmac.compare_digest(x_internal_key, config.INTERNAL_API_KEY): raise HTTPException(401, "Bad internal key")

def public_user(u): return {k: u[k] for k in ("id", "email", "full_name", "role", "authority_id", "organisation")}

@app.post("/api/auth/register")
def register(payload: dict = Body(...)):
    email = (payload.get("email") or "").strip().lower(); pw = payload.get("password") or ""; name = (payload.get("full_name") or "").strip()
    if "@" not in email or len(email) > 200 or not (2 <= len(name) <= 120): raise HTTPException(422, "Valid email and full name required")
    if not security.password_ok(pw): raise HTTPException(422, "Password needs 8+ characters with letters and digits")
    with get_conn() as c:
        if c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone(): raise HTTPException(409, "Email already registered")
        uid = c.execute("INSERT INTO users(email,password_hash,full_name,phone,role,created_at) VALUES(?,?,?,?, 'citizen', ?)",
                        (email, security.hash_password(pw), name, (payload.get("phone") or "")[:20], core.iso(core.utcnow()))).lastrowid  # self-registration is citizen-only
    return {"token": security.make_token(uid, "citizen")}

@app.post("/api/auth/login")
def login(payload: dict = Body(...)):
    with get_conn() as c:
        u = one(c.execute("SELECT * FROM users WHERE email=? AND active=1", ((payload.get("email") or "").strip().lower(),)))
    ok = security.verify_password(payload.get("password") or "", u["password_hash"] if u else security.hash_password("x"))
    if not u or not ok: raise HTTPException(401, "Invalid credentials")
    return {"token": security.make_token(u["id"], u["role"]), "user": public_user(u)}

@app.get("/api/me")
def me(user=Depends(current_user)): return public_user(user)

@app.get("/api/meta")
def meta():
    with get_conn() as c:
        return {"categories": rows(c.execute("SELECT c.id,c.code,c.name,d.name AS department FROM categories c JOIN departments d ON d.id=c.department_id")),
                "jurisdictions": [r[0] for r in c.execute("SELECT DISTINCT jurisdiction FROM authorities WHERE level=1 ORDER BY 1")],
                "stages": core.STAGES, "ai_label": ai_mock.LABEL, "legal_disclaimer": core.LEGAL_DISCLAIMER}

# ------------------------------------------------------------------ visibility rules
def involved_authorities(c, cid):
    s = {r[0] for r in c.execute("SELECT previous_authority_id FROM escalations WHERE complaint_id=?", (cid,))}
    s |= {r[0] for r in c.execute("SELECT from_authority_id FROM authority_transfers WHERE complaint_id=?", (cid,))}
    s |= {r[0] for r in c.execute("SELECT authority_id FROM official_responses WHERE complaint_id=?", (cid,))}
    return {x for x in s if x}

def can_view(c, user, comp):
    r = user["role"]
    if r == "admin": return True
    if r == "citizen": return comp["citizen_id"] == user["id"]
    if r == "legal_aid": return bool(comp["legal_share"])
    if comp["status"] == "draft": return False
    aid = user["authority_id"]
    if not aid: return False
    scope = {aid} | (core.authority_descendants(c, aid) if r == "supervisor" else set())
    if comp["current_authority_id"] in scope: return True
    ap = c.execute("SELECT 1 FROM appeals WHERE complaint_id=? AND review_authority_id=?", (comp["id"], aid)).fetchone()
    return bool(ap) or aid in involved_authorities(c, comp["id"])

def can_see_evidence_files(c, user, comp):
    if user["role"] == "citizen": return comp["citizen_id"] == user["id"]
    if user["role"] in ("official", "supervisor"): return can_view(c, user, comp)
    return False  # admins and legal-aid never get raw private evidence

def load(c, cid, user):
    try: comp = core.get_complaint(c, cid)
    except CoreError as e: err(e)
    if comp.get("deleted_at"): raise HTTPException(404, "Complaint not found")
    if not can_view(c, user, comp): raise HTTPException(403, "You do not have access to this complaint")
    return comp

def present(c, user, comp):
    cid = comp["id"]; out = dict(comp)
    for k in ("citizen_report", "ai_analysis", "duplicate_flags", "triage"): out[k] = core.jl(out.get(k))
    cit = core.get_user(c, comp["citizen_id"])
    out["citizen"] = {"name": cit["full_name"], "phone": cit["phone"] if user["id"] == cit["id"] else security.mask_phone(cit["phone"]),
                      "email": cit["email"] if user["id"] == cit["id"] else security.mask_email(cit["email"])} if user["role"] != "legal_aid" else {"name": "Shared by citizen (masked)"}
    a = core.get_authority(c, comp["current_authority_id"]); out["current_authority"] = a["name"] if a else None
    if user["role"] == "citizen" and out["duplicate_flags"]:  # never reveal other people's complaints
        out["duplicate_flags"] = [{"note": "A similar report exists; the authority will review. Your complaint was not rejected."}]
    out["evidence"] = core.list_evidence(c, cid); out["can_download_evidence"] = can_see_evidence_files(c, user, comp)
    out["responses"] = rows(c.execute("SELECT * FROM official_responses WHERE complaint_id=? ORDER BY id", (cid,)))
    out["decisions"] = rows(c.execute("SELECT * FROM decisions WHERE complaint_id=? ORDER BY id", (cid,)))
    out["appeals"] = rows(c.execute("SELECT * FROM appeals WHERE complaint_id=? ORDER BY id", (cid,)))
    out["escalations"] = rows(c.execute("SELECT * FROM escalations WHERE complaint_id=? ORDER BY id", (cid,)))
    out["transfers"] = rows(c.execute("SELECT * FROM authority_transfers WHERE complaint_id=? ORDER BY id", (cid,)))
    out["status_history"] = rows(c.execute("SELECT * FROM status_history WHERE complaint_id=? ORDER BY id", (cid,)))
    out["audit_chain"] = core.audit_verify(c, cid)
    out["legal_assistance"] = core.latest_legal(c, cid) if user["role"] in ("citizen", "legal_aid", "admin") else None
    return out

# ------------------------------------------------------------------ citizen: complaints
@app.post("/api/complaints")
def create_complaint(payload: dict = Body(...), user=Depends(require("citizen"))):
    with get_conn() as c:
        try: return {"id": core.create_draft(c, user["id"], payload)}
        except CoreError as e: err(e)

@app.put("/api/complaints/{cid}")
def update_complaint_draft(cid: int, payload: dict = Body(...), user=Depends(require("citizen"))):
    with get_conn() as c:
        try: return {"id": core.update_draft(c, cid, user["id"], payload)}
        except CoreError as e: err(e)

@app.delete("/api/complaints/{cid}")
def delete_complaint_draft(cid: int, user=Depends(require("citizen"))):
    with get_conn() as c:
        try: return core.delete_draft(c, cid, user["id"])
        except CoreError as e: err(e)

@app.get("/api/complaints")
def list_complaints(user=Depends(current_user)):
    with get_conn() as c:
        if user["role"] == "citizen": q, a = "SELECT * FROM complaints WHERE citizen_id=? AND deleted_at IS NULL ORDER BY id DESC", (user["id"],)
        elif user["role"] == "admin": q, a = "SELECT * FROM complaints WHERE status!='draft' AND deleted_at IS NULL ORDER BY id DESC", ()
        elif user["role"] == "legal_aid": q, a = "SELECT * FROM complaints WHERE legal_share=1 AND deleted_at IS NULL ORDER BY id DESC", ()
        else:
            scope = {user["authority_id"]} | (core.authority_descendants(c, user["authority_id"]) if user["role"] == "supervisor" else set())
            ph = ",".join("?" * len(scope))
            q, a = f"SELECT * FROM complaints WHERE status!='draft' AND deleted_at IS NULL AND (current_authority_id IN ({ph}) OR id IN (SELECT complaint_id FROM appeals WHERE review_authority_id=?)) ORDER BY id DESC", (*scope, user["authority_id"])
        out = []
        for r in rows(c.execute(q, a)):
            out.append({k: r[k] for k in ("id", "code", "title", "status", "stage", "current_authority_id", "ack_deadline", "resolution_deadline", "submitted_at", "created_at", "jurisdiction")}
                       | {"authority": (core.get_authority(c, r["current_authority_id"]) or {}).get("name"), "has_duplicates": bool(core.jl(r["duplicate_flags"]))})
        return out

@app.get("/api/complaints/{cid}")
def get_complaint(cid: int, user=Depends(current_user)):
    with get_conn() as c: return present(c, user, load(c, cid, user))

@app.post("/api/complaints/{cid}/evidence")
async def upload_evidence(cid: int, file: UploadFile = File(...), user=Depends(require("citizen"))):
    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    with get_conn() as c:
        comp = load(c, cid, user)
        if comp["citizen_id"] != user["id"]: raise HTTPException(403, "Not your complaint")
        if comp.get("deleted_at") or (comp["status"] != "draft" and not comp["submitted_at"]): raise HTTPException(409, "Evidence cannot be added in this case state")
        try: core.validate_file(file.filename, data)  # fast fail; n8n re-validates and hashes
        except CoreError as e: err(e)
    import starlette.concurrency
    result = await starlette.concurrency.run_in_threadpool(n8n_client.trigger, "evidence_processing",
        {"complaint_id": cid, "uploader_id": user["id"], "filename": file.filename, "content_base64": base64.b64encode(data).decode()}, cid, record_payload=True)
    if result is None: raise HTTPException(503, "Evidence-processing workflow (n8n) is unavailable. The upload was not registered; retry it.")
    if result.get("ok") is not True: raise HTTPException(422, result.get("error") or "Evidence-processing workflow rejected the file")
    _event(cid, "evidence_added", user, {"evidence_id": result.get("evidence_id")})
    return {"status": "Registered", **result}

@app.get("/api/evidence/{eid}/download")
def download_evidence(eid: str, user=Depends(current_user)):
    with get_conn() as c:
        e = one(c.execute("SELECT * FROM evidence WHERE id=?", (eid,)))
        if not e: raise HTTPException(404, "Not found")
        comp = core.get_complaint(c, e["complaint_id"])
        if not can_see_evidence_files(c, user, comp): raise HTTPException(403, "Private evidence")
        core.audit_append(c, comp["id"], user["id"], "evidence_accessed", {"evidence_id": eid, "role": user["role"]})
    return FileResponse(e["stored_path"], media_type=e["mime"], filename=e["original_filename"])

@app.post("/api/complaints/{cid}/analyze")
def analyze(cid: int, user=Depends(require("citizen"))):
    with get_conn() as c:
        comp = load(c, cid, user)
        if comp["citizen_id"] != user["id"] or comp["status"] != "draft": raise HTTPException(409, "Only your own drafts can be analysed")
        payload = core.draft_analysis_input(c, cid)
    res = n8n_client.trigger("ai_analysis", payload, cid)
    if not res or "analysis" not in res: raise HTTPException(503, "AI analysis workflow (n8n) is unavailable. Start n8n and retry.")
    with get_conn() as c:
        try: return core.store_analysis(c, cid, res["analysis"])
        except CoreError as e: err(e)

@app.post("/api/complaints/{cid}/submit")
def submit(cid: int, payload: dict = Body(...), user=Depends(require("citizen"))):
    with get_conn() as c:
        load(c, cid, user)
        try: r = core.submit_complaint(c, cid, user["id"], payload.get("approved"), payload.get("citizen_report"))
        except CoreError as e: err(e)
    wf = n8n_client.trigger("complaint_intake", {"complaint_id": cid, "code": r["code"], "citizen_id": user["id"]}, cid)
    return {**r, "workflow": "completed" if wf else "pending", "workflow_result": wf}

@app.post("/api/complaints/{cid}/reply")
def reply(cid: int, payload: dict = Body(...), user=Depends(require("citizen"))):
    with get_conn() as c:
        try: core.citizen_reply(c, cid, user["id"], payload.get("body"))
        except CoreError as e: err(e)
    _event(cid, "citizen_replied", user); return {"ok": True}

@app.post("/api/complaints/{cid}/legal-share")
def legal_share(cid: int, payload: dict = Body(...), user=Depends(require("citizen"))):
    with get_conn() as c:
        comp = load(c, cid, user); v = 1 if payload.get("share") else 0
        c.execute("UPDATE complaints SET legal_share=? WHERE id=?", (v, cid))
        core.audit_append(c, cid, user["id"], "legal_aid_sharing_" + ("enabled" if v else "disabled"), {})
    return {"legal_share": bool(v)}

@app.get("/api/complaints/{cid}/audit")
def audit(cid: int, user=Depends(current_user)):
    with get_conn() as c:
        load(c, cid, user)
        evs = core.audit_list(c, cid)
        if user["role"] == "citizen": evs = [{k: e[k] for k in ("event_id", "timestamp", "event_type", "previous_hash", "current_hash")} for e in evs]
        return {"verification": core.audit_verify(c, cid), "events": evs}

@app.get("/api/complaints/{cid}/summary")
def summary(cid: int, user=Depends(current_user)):
    with get_conn() as c: load(c, cid, user)
    res = n8n_client.trigger("case_summary", {"complaint_id": cid}, cid)   # Workflow 12 builds the summary
    if res is None: raise HTTPException(503, "Case-summary workflow (n8n) unavailable")
    return res

@app.post("/api/complaints/{cid}/appeal")
def appeal(cid: int, payload: dict = Body(...), user=Depends(require("citizen"))):
    with get_conn() as c:
        try: r = core.create_appeal(c, cid, user["id"], payload.get("grounds"))
        except CoreError as e: err(e)
    wf = n8n_client.trigger("appeal_processing", {"appeal_id": r["appeal_id"], "complaint_id": cid}, cid)
    return {**r, "workflow": "completed" if wf else "pending"}

@app.get("/api/complaints/{cid}/legal-help")
def legal_help(cid: int, user=Depends(require("citizen", "legal_aid", "admin"))):
    with get_conn() as c:
        load(c, cid, user); r = core.latest_legal(c, cid)
    return r or {"available": False, "disclaimer": core.LEGAL_DISCLAIMER, "message": "Legal assistance is generated when a case is unresolved after escalation or an appeal is upheld against you. You can also request it."}

@app.post("/api/complaints/{cid}/legal-help/request")
def legal_request(cid: int, user=Depends(require("citizen"))):
    with get_conn() as c: load(c, cid, user)
    res = n8n_client.trigger("legal_assistance", {"complaint_id": cid, "reason": "citizen_requested"}, cid)
    if not res: raise HTTPException(503, "Legal-assistance workflow (n8n) unavailable")
    with get_conn() as c: return core.latest_legal(c, cid)

# ------------------------------------------------------------------ officials / supervisors
def _event(cid, event, user, extra=None):  # audit-trail workflow: verifies chain + channel fan-out
    n8n_client.trigger("audit_trail", {"complaint_id": cid, "event": event, "actor_id": user["id"], **(extra or {})}, cid, timeout=20)

def _act(cid, user, fn, event, *a):
    with get_conn() as c:
        load(c, cid, user)
        try: res = fn(c, cid, user, *a)
        except CoreError as e: err(e)
    _event(cid, event, user); return {"ok": True, "result": res}

@app.post("/api/complaints/{cid}/acknowledge")
def acknowledge(cid: int, payload: dict = Body({}), user=Depends(require("official", "supervisor"))): return _act(cid, user, core.acknowledge, "complaint_acknowledged", payload.get("note", ""))
@app.post("/api/complaints/{cid}/respond")
def respond(cid: int, payload: dict = Body(...), user=Depends(require("official", "supervisor"))): return _act(cid, user, core.respond, "response_received", payload.get("body"), payload.get("kind", "response"), payload.get("corrects_id"))
@app.post("/api/complaints/{cid}/request-info")
def request_info(cid: int, payload: dict = Body(...), user=Depends(require("official", "supervisor"))): return _act(cid, user, core.request_info, "information_requested", payload.get("body"))
@app.post("/api/complaints/{cid}/decision")
def decision(cid: int, payload: dict = Body(...), user=Depends(require("official", "supervisor"))): return _act(cid, user, core.decide, "decision_made", payload.get("outcome"), payload.get("reasoning"))
@app.post("/api/complaints/{cid}/transfer")
def transfer(cid: int, payload: dict = Body(...), user=Depends(require("official", "supervisor"))): return _act(cid, user, core.transfer, "authority_transferred", payload.get("to_authority_id"), payload.get("reason"))

@app.get("/api/authorities/peers")
def peers(user=Depends(require("official", "supervisor"))):
    with get_conn() as c:
        me_a = core.get_authority(c, user["authority_id"])
        return rows(c.execute("SELECT id,name FROM authorities WHERE department_id=? AND level=? AND id!=?", (me_a["department_id"], me_a["level"], me_a["id"])))

@app.get("/api/appeals")
def list_appeals(user=Depends(require("supervisor", "admin", "citizen"))):
    with get_conn() as c:
        if user["role"] == "citizen": return rows(c.execute("SELECT a.*, c.code FROM appeals a JOIN complaints c ON c.id=a.complaint_id WHERE a.citizen_id=? ORDER BY a.id DESC", (user["id"],)))
        w, a = ("", ()) if user["role"] == "admin" else ("WHERE a.review_authority_id=?", (user["authority_id"],))
        return rows(c.execute(f"SELECT a.*, c.code, c.title FROM appeals a JOIN complaints c ON c.id=a.complaint_id {w} ORDER BY a.id DESC", a))

@app.post("/api/appeals/{aid}/decide")
def decide_appeal(aid: int, payload: dict = Body(...), user=Depends(require("supervisor"))):
    with get_conn() as c:
        try: r = core.decide_appeal(c, aid, user, payload.get("outcome"), payload.get("reasoning"))
        except CoreError as e: err(e)
        cid = core.one(c.execute("SELECT complaint_id FROM appeals WHERE id=?", (aid,)))["complaint_id"] if hasattr(core, "one") else one(c.execute("SELECT complaint_id FROM appeals WHERE id=?", (aid,)))["complaint_id"]
    _event(cid, "appeal_outcome", user)
    if r["legal_assistance_recommended"]: n8n_client.trigger("legal_assistance", {"complaint_id": cid, "reason": "appeal_upheld_original_decision"}, cid)
    return r

@app.get("/api/supervisor/overdue")
def sup_overdue(user=Depends(require("supervisor", "admin"))):
    with get_conn() as c:
        scope = None if user["role"] == "admin" else {user["authority_id"]} | core.authority_descendants(c, user["authority_id"])
        return [o | {"title": core.get_complaint(c, o["complaint_id"])["title"]} for o in core.find_overdue(c) if scope is None or o["current_authority_id"] in scope]

@app.get("/api/supervisor/escalated")
def sup_escalated(user=Depends(require("supervisor", "admin"))):
    with get_conn() as c:
        scope = None if user["role"] == "admin" else {user["authority_id"]} | core.authority_descendants(c, user["authority_id"])
        out = rows(c.execute("SELECT e.*, c.code, c.title, c.status FROM escalations e JOIN complaints c ON c.id=e.complaint_id ORDER BY e.id DESC"))
        return [e for e in out if scope is None or e["previous_authority_id"] in scope or e["new_authority_id"] in scope]

@app.get("/api/supervisor/analytics")
def analytics(user=Depends(require("supervisor", "admin"))):
    with get_conn() as c:
        depts = rows(c.execute("SELECT id,name FROM departments"))
        return {"overall": core.metrics(c), "departments": [{"department": d["name"], **core.metrics(c, d["id"])} for d in depts]}

@app.get("/api/public/stats")
def public_stats():
    with get_conn() as c: return core.metrics(c)   # aggregates only: no evidence, no names

# ------------------------------------------------------------------ notifications
@app.get("/api/notifications")
def notifications(user=Depends(current_user)):
    with get_conn() as c: return rows(c.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 100", (user["id"],)))
@app.post("/api/notifications/{nid}/read")
def read_notification(nid: int, user=Depends(current_user)):
    with get_conn() as c: c.execute("UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?", (nid, user["id"]))
    return {"ok": True}

# ------------------------------------------------------------------ legal aid provider
@app.get("/api/legal-aid")
def legal_directory(user=Depends(current_user)):
    with get_conn() as c: return rows(c.execute("SELECT * FROM legal_aid ORDER BY CASE cost WHEN 'free' THEN 0 WHEN 'low_cost' THEN 1 ELSE 2 END, name"))

# ------------------------------------------------------------------ admin
@app.get("/api/admin/users")
def admin_users(user=Depends(require("admin"))):
    with get_conn() as c: return rows(c.execute("SELECT id,email,full_name,role,authority_id,organisation,active FROM users ORDER BY id"))
@app.post("/api/admin/users")
def admin_create_user(payload: dict = Body(...), user=Depends(require("admin"))):
    if payload.get("role") not in ("official", "supervisor", "legal_aid", "admin", "citizen"): raise HTTPException(422, "Invalid role")
    if not security.password_ok(payload.get("password") or ""): raise HTTPException(422, "Weak password")
    with get_conn() as c:
        try: uid = c.execute("INSERT INTO users(email,password_hash,full_name,role,authority_id,organisation,created_at) VALUES(?,?,?,?,?,?,?)",
                             (payload["email"].lower(), security.hash_password(payload["password"]), payload["full_name"], payload["role"], payload.get("authority_id"), payload.get("organisation"), core.iso(core.utcnow()))).lastrowid
        except Exception as e: raise HTTPException(422, str(e))
    return {"id": uid}
@app.post("/api/admin/users/{uid}/active")
def admin_active(uid: int, payload: dict = Body(...), user=Depends(require("admin"))):
    with get_conn() as c: c.execute("UPDATE users SET active=? WHERE id=?", (1 if payload.get("active") else 0, uid))
    return {"ok": True}
@app.get("/api/admin/config")
def admin_config(user=Depends(require("admin"))):
    with get_conn() as c:
        return {"departments": rows(c.execute("SELECT * FROM departments")), "authorities": rows(c.execute("SELECT * FROM authorities ORDER BY department_id, level DESC")),
                "rules": rows(c.execute("SELECT * FROM escalation_rules ORDER BY level")), "categories": rows(c.execute("SELECT * FROM categories")), "legal_aid": rows(c.execute("SELECT * FROM legal_aid"))}
@app.put("/api/admin/escalation-rules/{level}")
def admin_rule(level: int, payload: dict = Body(...), user=Depends(require("admin"))):
    vals = [float(payload.get(k, -1)) for k in ("ack_hours", "review_hours", "resolution_hours", "appeal_hours")]
    if any(v <= 0 for v in vals): raise HTTPException(422, "Hours must be positive")
    with get_conn() as c: c.execute("INSERT INTO escalation_rules(level,ack_hours,review_hours,resolution_hours,appeal_hours) VALUES(?,?,?,?,?) ON CONFLICT(level) DO UPDATE SET ack_hours=excluded.ack_hours, review_hours=excluded.review_hours, resolution_hours=excluded.resolution_hours, appeal_hours=excluded.appeal_hours", (level, *vals))
    return {"ok": True}
@app.post("/api/admin/authorities")
def admin_authority(payload: dict = Body(...), user=Depends(require("admin"))):
    with get_conn() as c:
        parent = core.get_authority(c, payload.get("parent_authority_id"))
        lvl = int(payload["level"])
        if parent and parent["level"] <= lvl: raise HTTPException(422, "Parent must be a higher level: hierarchies cannot loop")
        return {"id": c.execute("INSERT INTO authorities(name,department_id,level,level_name,jurisdiction,parent_authority_id,is_oversight) VALUES(?,?,?,?,?,?,?)",
                (payload["name"], payload["department_id"], lvl, payload.get("level_name", f"Level {lvl}"), payload["jurisdiction"], payload.get("parent_authority_id"), 1 if not parent else 0)).lastrowid}
@app.post("/api/admin/departments")
def admin_dept(payload: dict = Body(...), user=Depends(require("admin"))):
    with get_conn() as c: return {"id": c.execute("INSERT INTO departments(code,name,description) VALUES(?,?,?)", (payload["code"], payload["name"], payload.get("description"))).lastrowid}
@app.post("/api/admin/categories")
def admin_cat(payload: dict = Body(...), user=Depends(require("admin"))):
    with get_conn() as c: return {"id": c.execute("INSERT INTO categories(code,name,department_id) VALUES(?,?,?)", (payload["code"], payload["name"], payload["department_id"])).lastrowid}
@app.post("/api/admin/legal-aid")
def admin_legal(payload: dict = Body(...), user=Depends(require("admin", "legal_aid"))):
    with get_conn() as c:
        if payload.get("cost") not in ("free", "low_cost", "paid"): raise HTTPException(422, "cost must be free, low_cost or paid")
        return {"id": c.execute("INSERT INTO legal_aid(name,org_type,services,category_codes,region,cost,eligibility,contact,url) VALUES(?,?,?,?,?,?,?,?,?)",
                (payload["name"], payload.get("org_type", "NGO"), payload["services"], payload.get("category_codes", "*"), payload["region"], payload["cost"], payload.get("eligibility"), payload.get("contact"), payload.get("url"))).lastrowid}
@app.get("/api/admin/audit")
def admin_audit(user=Depends(require("admin"))):
    with get_conn() as c:
        ids = [r[0] for r in c.execute("SELECT DISTINCT complaint_id FROM audit_events")]
        return {"chains": [{"complaint_id": i} | core.audit_verify(c, i) for i in ids],
                "recent": [e | {"metadata": None} for e in rows(c.execute("SELECT event_id,complaint_id,actor_id,timestamp,event_type,current_hash FROM audit_events ORDER BY rowid DESC LIMIT 100"))]}  # metadata withheld in list view (privacy)
@app.get("/api/admin/workflow-runs")
def admin_runs(user=Depends(require("admin"))):
    with get_conn() as c: return rows(c.execute("SELECT * FROM workflow_runs ORDER BY id DESC LIMIT 100"))
@app.post("/api/admin/workflow-runs/{rid}/retry")
def admin_retry(rid: int, user=Depends(require("admin"))):
    try: return n8n_client.retry(rid)
    except CoreError as e: err(e)
@app.post("/api/admin/run-deadline-monitor")
def admin_monitor(user=Depends(require("admin", "supervisor"))):
    res = n8n_client.trigger("deadline_monitor", {"triggered_by": user["id"]})
    if res is None: raise HTTPException(503, "n8n unreachable")
    return res
@app.post("/api/admin/demo/expire/{cid}")
def demo_expire(cid: int, user=Depends(require("admin"))):
    if not config.ALLOW_DEMO_ENDPOINTS: raise HTTPException(404, "Disabled")
    past = core.iso(core.utcnow() - core.timedelta(hours=1))
    with get_conn() as c:
        comp = core.get_complaint(c, cid)
        col = "ack_deadline" if comp["status"] == "assigned" else "resolution_deadline"
        c.execute(f"UPDATE complaints SET {col}=? WHERE id=?", (past, cid))
        core.audit_append(c, cid, user["id"], "demo_deadline_forced_to_past", {"column": col})
    return {"ok": True, "deadline": col}

# ------------------------------------------------------------------ internal API: called ONLY by n8n workflows
@app.get("/internal/analysis-input/{cid}", dependencies=[Depends(internal)])
def i_input(cid: int):
    with get_conn() as c: return core.draft_analysis_input(c, cid)
@app.post("/internal/triage", dependencies=[Depends(internal)])
def i_triage(p: dict = Body(...)):
    with get_conn() as c:
        c.execute("UPDATE complaints SET triage=? WHERE id=?", (json.dumps(p["triage"]), p["complaint_id"]))
        core.audit_append(c, p["complaint_id"], "system:n8n", "ai_triage_recorded", {"advisory": True, "priority": p["triage"].get("priority")})
    return {"ok": True}
@app.post("/internal/ai/mock-analyze", dependencies=[Depends(internal)])
def i_mock(p: dict = Body(...)): return ai_mock.analyze(p.get("description"), p.get("evidence"), p.get("category"), p.get("location"), p.get("mode", "draft"))
@app.post("/internal/ai/normalise", dependencies=[Depends(internal)])
def i_norm(p: dict = Body(...)): return ai_mock.normalise_llm(p)
@app.post("/internal/duplicates", dependencies=[Depends(internal)])
def i_dups(p: dict = Body(...)):
    with get_conn() as c:
        flags = core.find_duplicates(c, p["complaint_id"])
        if p.get("store"): core.store_duplicate_flags(c, p["complaint_id"], flags)
        return {"flags": flags, "count": len(flags), "auto_rejected": False}
@app.post("/internal/assign", dependencies=[Depends(internal)])
def i_assign(p: dict = Body(...)):
    with get_conn() as c:
        try: return core.assign_authority(c, p["complaint_id"])
        except CoreError as e: err(e)
@app.post("/internal/audit", dependencies=[Depends(internal)])
def i_audit(p: dict = Body(...)):
    if p.get("event_type") not in {"workflow_intake_completed", "workflow_notification_dispatched", "deadline_warning_sent", "workflow_evidence_validated", "appeal_workflow_processed", "case_summary_generated", "monitor_cycle_completed"}: raise HTTPException(422, "event_type not permitted from workflows")
    with get_conn() as c:
        ev = core.audit_append(c, p["complaint_id"], p.get("actor_id", "system:n8n"), p["event_type"], p.get("metadata") or {})
        return {"event_id": ev["event_id"], "current_hash": ev["current_hash"], "previous_hash": ev["previous_hash"]}
@app.get("/internal/audit/verify/{cid}", dependencies=[Depends(internal)])
def i_verify(cid: int):
    with get_conn() as c: return core.audit_verify(c, cid)
@app.post("/internal/notify", dependencies=[Depends(internal)])
def i_notify(p: dict = Body(...)):
    with get_conn() as c:
        comp = core.get_complaint(c, p["complaint_id"]) if p.get("complaint_id") else None
        uid = p.get("user_id") or (comp["citizen_id"] if comp and p.get("to") == "citizen" else None)
        ids = [uid] if uid else (core.authority_users(c, p["authority_id"]) if p.get("authority_id") else [])
        out = [core.notify(c, i, p.get("complaint_id"), p["type"], p["title"], p["message"], p.get("channel", "in_app")) for i in ids]
        return {"created": out}
@app.get("/internal/complaints/{cid}", dependencies=[Depends(internal)])
def i_complaint(cid: int):
    with get_conn() as c:
        comp = core.get_complaint(c, cid); u = core.get_user(c, comp["citizen_id"])
        return {"id": cid, "code": comp["code"], "status": comp["status"], "citizen_id": u["id"], "citizen_email": security.mask_email(u["email"]),
                "citizen_phone": security.mask_phone(u["phone"]), "current_authority_id": comp["current_authority_id"], "title": comp["title"]}
@app.get("/internal/overdue", dependencies=[Depends(internal)])
def i_overdue():
    with get_conn() as c: return {"overdue": core.find_overdue(c), "approaching": core.approaching(c)}
@app.post("/internal/escalate", dependencies=[Depends(internal)])
def i_escalate(p: dict = Body(...)):
    with get_conn() as c:
        try: return core.escalate(c, p["complaint_id"])
        except CoreError as e: err(e)
@app.post("/internal/legal-assistance", dependencies=[Depends(internal)])
def i_legal(p: dict = Body(...)):
    with get_conn() as c:
        try: r = core.legal_assistance(c, p["complaint_id"], p.get("reason", "workflow"))
        except CoreError as e: err(e)
        return {"ok": True, "providers": len(r["providers"]), "options": len(r["options"])}
@app.get("/internal/case-summary/{cid}", dependencies=[Depends(internal)])
def i_summary(cid: int):
    with get_conn() as c:
        try: return core.case_summary(c, cid)
        except CoreError as e: err(e)
@app.post("/internal/evidence/register", dependencies=[Depends(internal)])
def i_evidence(p: dict = Body(...)):
    try: data = base64.b64decode(p["content_base64"], validate=True)
    except Exception: raise HTTPException(422, "Bad base64")
    with get_conn() as c:
        try: return {"ok": True, **core.register_evidence(c, p["complaint_id"], p["uploader_id"], p["filename"], data, p.get("sha256"), p.get("evidence_id"))}
        except CoreError as e: raise HTTPException(e.code, str(e))
@app.post("/internal/appeals/process", dependencies=[Depends(internal)])
def i_appeal(p: dict = Body(...)):
    with get_conn() as c:
        a = one(c.execute("SELECT a.*, au.name AS review_authority FROM appeals a JOIN authorities au ON au.id=a.review_authority_id WHERE a.id=?", (p["appeal_id"],)))
        if not a: raise HTTPException(404, "Appeal not found")
        return {"appeal": a, "chain": core.audit_verify(c, a["complaint_id"])}

_fe = Path(__file__).resolve().parents[2] / "frontend"
if _fe.exists(): app.mount("/", StaticFiles(directory=str(_fe), html=True), name="frontend")
