"""CivicShield domain logic. Pure stdlib + sqlite3 so it can be tested without the web layer.
n8n workflows call these functions through the /internal API; user actions call them through /api."""
import json, re, uuid, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from . import config, ai_mock
from .db import transactional, rows, one
from .security import sha256_hex

GENESIS = "0" * 64
STAGES = ["allegation", "under_review", "investigation", "verified_finding", "rejected", "resolved", "appealed"]
ACTIVE = ("assigned", "acknowledged", "info_requested", "under_review", "investigation")
LEGAL_DISCLAIMER = "This is general procedural information, not personalized legal advice."
ALLOWED_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".pdf": "application/pdf",
               ".mp3": "audio/mpeg", ".mp4": "video/mp4", ".txt": "text/plain"}
MAGIC = {"image/jpeg": [b"\xff\xd8\xff"], "image/png": [b"\x89PNG\r\n\x1a\n"], "application/pdf": [b"%PDF-"],
         "audio/mpeg": [b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"], "video/mp4": [b"\x00\x00\x00"]}

class CoreError(Exception):
    def __init__(self, msg, code=400): super().__init__(msg); self.code = code

def utcnow(): return datetime.now(timezone.utc)
def iso(dt): return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def parse(s): return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
def j(x): return json.dumps(x, ensure_ascii=False, sort_keys=True)
def jl(s, default=None):
    try: return json.loads(s) if s else default
    except Exception: return default

# ---------------------------------------------------------------- audit hash chain
def _canon(d): return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def event_hash(ev):
    body = {k: ev[k] for k in ("event_id", "complaint_id", "actor_id", "timestamp", "event_type", "previous_hash")}
    body["metadata"] = ev["metadata"] if isinstance(ev["metadata"], dict) else jl(ev["metadata"], {})
    return hashlib.sha256(_canon(body).encode()).hexdigest()

@transactional
def audit_append(conn, complaint_id, actor_id, event_type, metadata=None, ts=None):
    last = conn.execute("SELECT current_hash FROM audit_events WHERE complaint_id=? ORDER BY rowid DESC LIMIT 1", (complaint_id,)).fetchone()
    ev = {"event_id": "EVT-" + uuid.uuid4().hex[:16].upper(), "complaint_id": complaint_id, "actor_id": str(actor_id),
          "timestamp": ts or iso(utcnow()), "event_type": event_type, "previous_hash": last[0] if last else GENESIS,
          "metadata": metadata or {}}
    ev["current_hash"] = event_hash(ev)
    conn.execute("INSERT INTO audit_events(event_id,complaint_id,actor_id,timestamp,event_type,previous_hash,current_hash,metadata) VALUES(?,?,?,?,?,?,?,?)",
                 (ev["event_id"], complaint_id, ev["actor_id"], ev["timestamp"], event_type, ev["previous_hash"], ev["current_hash"], _canon(ev["metadata"])))
    return ev

def audit_verify(conn, complaint_id):
    prev, n = GENESIS, 0
    for r in conn.execute("SELECT * FROM audit_events WHERE complaint_id=? ORDER BY rowid", (complaint_id,)):
        r = dict(r); n += 1
        if r["previous_hash"] != prev:
            return {"valid": False, "events": n, "broken_at": r["event_id"], "reason": "previous_hash does not match prior event"}
        if event_hash(r) != r["current_hash"]:
            return {"valid": False, "events": n, "broken_at": r["event_id"], "reason": "event content does not match its hash"}
        prev = r["current_hash"]
    return {"valid": True, "events": n, "head_hash": prev}

def audit_list(conn, complaint_id):
    out = rows(conn.execute("SELECT * FROM audit_events WHERE complaint_id=? ORDER BY rowid", (complaint_id,)))
    for e in out: e["metadata"] = jl(e["metadata"], {})
    return out

# ---------------------------------------------------------------- helpers
def get_complaint(conn, cid):
    c = one(conn.execute("SELECT * FROM complaints WHERE id=?", (cid,)))
    if not c: raise CoreError("Complaint not found", 404)
    return c

def get_user(conn, uid): return one(conn.execute("SELECT * FROM users WHERE id=?", (uid,)))
def get_authority(conn, aid): return one(conn.execute("SELECT * FROM authorities WHERE id=?", (aid,))) if aid else None

def authority_descendants(conn, aid):
    out, frontier = set(), [aid]
    while frontier:
        cur = frontier.pop()
        for r in conn.execute("SELECT id FROM authorities WHERE parent_authority_id=?", (cur,)):
            if r[0] not in out: out.add(r[0]); frontier.append(r[0])
    return out

@transactional
def set_status(conn, cid, new_status, stage=None, reason="", actor="system"):
    c = get_complaint(conn, cid)
    stage = stage or c["stage"]
    conn.execute("UPDATE complaints SET status=?, stage=? WHERE id=?", (new_status, stage, cid))
    conn.execute("INSERT INTO status_history(complaint_id,from_status,to_status,stage,reason,actor,created_at) VALUES(?,?,?,?,?,?,?)",
                 (cid, c["status"], new_status, stage, reason, str(actor), iso(utcnow())))

@transactional
def notify(conn, user_id, complaint_id, ntype, title, message, channel="in_app"):
    if not user_id: return None
    cur = conn.execute("INSERT INTO notifications(user_id,complaint_id,type,title,message,channel,created_at) VALUES(?,?,?,?,?,?,?)",
                       (user_id, complaint_id, ntype, title, message, channel, iso(utcnow())))
    return cur.lastrowid

def authority_users(conn, aid):
    return [r["id"] for r in conn.execute("SELECT id FROM users WHERE authority_id=? AND active=1", (aid,))]

def notify_authority(conn, aid, cid, ntype, title, message):
    for uid in authority_users(conn, aid): notify(conn, uid, cid, ntype, title, message)

def rule_for(conn, level):
    return one(conn.execute("SELECT * FROM escalation_rules WHERE level=?", (level,))) or \
        {"level": level, "ack_hours": 24, "review_hours": 72, "resolution_hours": 720, "appeal_hours": 168}

def deadlines_for(conn, level, start=None):
    r, s = rule_for(conn, level), start or utcnow()
    return {k: iso(s + timedelta(hours=r[h])) for k, h in
            (("ack_deadline", "ack_hours"), ("review_deadline", "review_hours"), ("resolution_deadline", "resolution_hours"))}

# ---------------------------------------------------------------- drafts, evidence, AI, duplicates
def _draft_values(conn, d):
    title = (d.get("title") or "").strip(); desc = (d.get("description") or "").strip()
    if not (5 <= len(title) <= 200): raise CoreError("Title must be 5-200 characters")
    if not (20 <= len(desc) <= 8000): raise CoreError("Description must be 20-8000 characters")
    cat = one(conn.execute("SELECT * FROM categories WHERE id=?", (d.get("category_id"),)))
    if not cat: raise CoreError("Unknown category")
    jur = (d.get("jurisdiction") or "").strip()
    if not conn.execute("SELECT 1 FROM authorities WHERE jurisdiction=?", (jur,)).fetchone(): raise CoreError("Unknown jurisdiction")
    inc = (d.get("incident_date") or "").strip() or None
    if inc and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", inc): raise CoreError("incident_date must be YYYY-MM-DD")
    return title, desc, cat, jur, inc, (d.get("location_text") or "")[:300]

@transactional
def create_draft(conn, citizen_id, d):
    title, desc, cat, jur, inc, location = _draft_values(conn, d)
    cur = conn.execute("""INSERT INTO complaints(citizen_id,category_id,department_id,title,original_description,original_hash,location_text,
        jurisdiction,incident_date,status,stage,created_at) VALUES(?,?,?,?,?,?,?,?,?,'draft','allegation',?)""",
        (citizen_id, cat["id"], cat["department_id"], title, desc, sha256_hex(desc.encode()), location, jur, inc, iso(utcnow())))
    cid = cur.lastrowid
    audit_append(conn, cid, citizen_id, "draft_created", {"original_sha256": sha256_hex(desc.encode()), "category": cat["code"]})
    return cid

@transactional
def update_draft(conn, complaint_id, citizen_id, d):
    c = get_complaint(conn, complaint_id)
    if c["citizen_id"] != citizen_id: raise CoreError("Not your complaint", 403)
    if c["status"] != "draft" or c.get("deleted_at"): raise CoreError("Only active drafts can be updated", 409)
    title, desc, cat, jur, inc, location = _draft_values(conn, d)
    new_hash = sha256_hex(desc.encode())
    changed = [key for key, old, new in (("title", c["title"], title), ("category_id", c["category_id"], cat["id"]),
        ("description", c["original_description"], desc), ("jurisdiction", c["jurisdiction"], jur),
        ("incident_date", c["incident_date"], inc), ("location_text", c["location_text"], location)) if old != new]
    if changed:
        conn.execute("""UPDATE complaints SET title=?,category_id=?,department_id=?,original_description=?,original_hash=?,
            jurisdiction=?,incident_date=?,location_text=? WHERE id=?""",
            (title, cat["id"], cat["department_id"], desc, new_hash, jur, inc, location, complaint_id))
        audit_append(conn, complaint_id, citizen_id, "draft_updated", {"fields": changed,
            "previous_description_sha256": c["original_hash"], "description_sha256": new_hash})
    return complaint_id

@transactional
def delete_draft(conn, complaint_id, citizen_id):
    c = get_complaint(conn, complaint_id)
    if c["citizen_id"] != citizen_id: raise CoreError("Not your complaint", 403)
    if c.get("deleted_at"): raise CoreError("Draft not found", 404)
    if c["status"] != "draft": raise CoreError("Only drafts can be deleted", 409)
    deleted_at = iso(utcnow())
    conn.execute("UPDATE complaints SET deleted_at=? WHERE id=?", (deleted_at, complaint_id))
    audit_append(conn, complaint_id, citizen_id, "draft_deleted", {"deleted_at": deleted_at})
    return {"deleted": True}

def sniff_ok(mime, data):
    sigs = MAGIC.get(mime)
    if mime == "text/plain": return b"\x00" not in data[:2048]
    return bool(sigs) and any(data.startswith(s) or (s == b"\x00\x00\x00" and data[4:8] == b"ftyp") for s in sigs)

def validate_file(filename, data):
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXT: raise CoreError(f"File type {ext or '(none)'} not allowed. Allowed: {', '.join(sorted(ALLOWED_EXT))}")
    if not data: raise CoreError("Empty file")
    if len(data) > config.MAX_UPLOAD_BYTES: raise CoreError("File too large", 413)
    mime = ALLOWED_EXT[ext]
    if not sniff_ok(mime, data): raise CoreError("File content does not match its extension")
    return mime

@transactional
def register_evidence(conn, complaint_id, uploader_id, filename, data, claimed_sha=None, evidence_id=None):
    """Called by the n8n Evidence Processing workflow (validated + hashed there); re-verified here (defence in depth)."""
    c = get_complaint(conn, complaint_id)
    if c.get("deleted_at"): raise CoreError("Deleted drafts cannot receive evidence", 409)
    if c.get("deleted_at") or (c["status"] != "draft" and not c["submitted_at"]): raise CoreError("Evidence cannot be added in this case state")
    mime = validate_file(filename, data)
    sha = sha256_hex(data)
    if claimed_sha and claimed_sha != sha: raise CoreError("Hash mismatch between workflow and backend", 409)
    eid = evidence_id or "EV-" + uuid.uuid4().hex[:12].upper()
    d = config.EVIDENCE_DIR / str(complaint_id); d.mkdir(parents=True, exist_ok=True)
    path = d / f"{eid}{Path(filename).suffix.lower()}"
    path.write_bytes(data)
    safe_name = re.sub(r"[^\w.\- ]", "_", Path(filename).name)[:120]
    conn.execute("INSERT INTO evidence(id,complaint_id,uploader_id,original_filename,stored_path,mime,size,sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                 (eid, complaint_id, uploader_id, safe_name, str(path), mime, len(data), sha, iso(utcnow())))
    audit_append(conn, complaint_id, uploader_id, "evidence_added", {"evidence_id": eid, "filename": safe_name, "sha256": sha, "size": len(data), "mime": mime})
    return {"evidence_id": eid, "sha256": sha, "mime": mime, "size": len(data), "filename": safe_name}

def list_evidence(conn, cid):
    return rows(conn.execute("SELECT id,original_filename,mime,size,sha256,created_at,uploader_id FROM evidence WHERE complaint_id=? ORDER BY created_at", (cid,)))

def verify_evidence_integrity(conn, eid):
    e = one(conn.execute("SELECT * FROM evidence WHERE id=?", (eid,)))
    if not e: raise CoreError("Evidence not found", 404)
    p = Path(e["stored_path"])
    ok = p.exists() and sha256_hex(p.read_bytes()) == e["sha256"]
    return {"evidence_id": eid, "intact": ok}

STOP = set("the a an and or of to in on at for with was were is are i my me we our they their he she it this that from by as be been had has have not no but so very".split())
def _tokens(t): return {w for w in re.findall(r"[a-z0-9]{3,}", (t or "").lower()) if w not in STOP}

def find_duplicates(conn, cid):
    c = get_complaint(conn, cid)
    mine = _tokens(c["title"] + " " + c["original_description"])
    cutoff = iso(utcnow() - timedelta(days=90))
    flags = []
    for o in conn.execute("SELECT * FROM complaints WHERE id!=? AND status!='draft' AND created_at>=? AND (category_id=? OR jurisdiction=?)",
                          (cid, cutoff, c["category_id"], c["jurisdiction"])):
        theirs = _tokens(o["title"] + " " + o["original_description"])
        sim = len(mine & theirs) / max(1, len(mine | theirs))
        reasons = []
        if sim >= 0.35: reasons.append(f"text similarity {sim:.0%}")
        if o["category_id"] == c["category_id"] and o["jurisdiction"] == c["jurisdiction"] and c["incident_date"] and c["incident_date"] == o["incident_date"]:
            reasons.append("same category, jurisdiction and incident date")
        if reasons: flags.append({"code": o["code"], "complaint_id": o["id"], "similarity": round(sim, 2), "reasons": reasons,
                                  "action": "flagged for human review - complaint NOT rejected"})
    return flags

@transactional
def store_duplicate_flags(conn, cid, flags):
    conn.execute("UPDATE complaints SET duplicate_flags=? WHERE id=?", (j(flags), cid))
    if flags: audit_append(conn, cid, "system:n8n", "duplicate_flagged", {"matches": [f["code"] for f in flags], "auto_rejected": False})
    return flags

def draft_analysis_input(conn, cid):
    c = get_complaint(conn, cid)
    cat = one(conn.execute("SELECT * FROM categories WHERE id=?", (c["category_id"],)))
    ev = [{"id": e["id"], "original_filename": e["original_filename"], "sha256": e["sha256"], "mime": e["mime"]} for e in list_evidence(conn, cid)]
    return {"complaint_id": cid, "description": c["original_description"], "title": c["title"], "category": cat["code"],
            "location": c["location_text"], "jurisdiction": c["jurisdiction"], "evidence": ev, "mode": "draft"}

@transactional
def store_analysis(conn, cid, analysis):
    c = get_complaint(conn, cid)
    if c["status"] != "draft" or c.get("deleted_at"): raise CoreError("Analysis can only be generated for active drafts")
    analysis["label"] = ai_mock.LABEL
    analysis["disclaimer"] = ai_mock.DISCLAIMER
    conn.execute("UPDATE complaints SET ai_analysis=?, ai_reviewed_at=NULL WHERE id=?", (j(analysis), cid))
    audit_append(conn, cid, "system:n8n", "ai_analysis_generated", {"generated_by": analysis.get("generated_by"), "label": ai_mock.LABEL})
    return analysis

# ---------------------------------------------------------------- submission (human review gate)
@transactional
def submit_complaint(conn, cid, citizen_id, approved, citizen_report):
    c = get_complaint(conn, cid)
    if c["citizen_id"] != citizen_id: raise CoreError("Not your complaint", 403)
    if c.get("deleted_at"): raise CoreError("Deleted drafts cannot be submitted", 409)
    if c["status"] != "draft": raise CoreError("Already submitted", 409)
    if not c["ai_analysis"]: raise CoreError("Generate and review the AI-assisted report before submitting")
    if approved is not True: raise CoreError("You must review and approve the AI-assisted report to submit")
    if not isinstance(citizen_report, dict) or not (citizen_report.get("incident_summary") or "").strip():
        raise CoreError("The reviewed report needs an incident summary")
    original = jl(c["ai_analysis"], {}).get("report", {})
    edited = citizen_report != original
    year = utcnow().year
    n = conn.execute("SELECT COUNT(*) FROM complaints WHERE code IS NOT NULL").fetchone()[0] + 1
    code = f"CS-{year}-{n:06d}"
    now = iso(utcnow())
    conn.execute("UPDATE complaints SET code=?, citizen_report=?, ai_reviewed_at=?, submitted_at=? WHERE id=?",
                 (code, j(citizen_report), now, now, cid))
    set_status(conn, cid, "submitted", "allegation", "Citizen approved reviewed report", citizen_id)
    audit_append(conn, cid, citizen_id, "citizen_reviewed_ai_report", {"edited_by_citizen": edited, "report_sha256": sha256_hex(j(citizen_report).encode())})
    audit_append(conn, cid, citizen_id, "complaint_submitted", {"code": code, "original_sha256": c["original_hash"],
                 "evidence_count": len(list_evidence(conn, cid))})
    notify(conn, citizen_id, cid, "complaint_submitted", "Complaint submitted", f"Your complaint {code} was submitted. It is being routed to the competent authority.")
    return {"id": cid, "code": code}

# ---------------------------------------------------------------- authority assignment (workflow 5)
def pick_authority(conn, department_id, jurisdiction):
    a = one(conn.execute("SELECT * FROM authorities WHERE department_id=? AND jurisdiction=? ORDER BY level LIMIT 1", (department_id, jurisdiction)))
    if a: return a, "exact jurisdiction match"
    a = one(conn.execute("SELECT * FROM authorities WHERE department_id=? AND level=1 ORDER BY id LIMIT 1", (department_id,)))
    if a: return a, "fallback: first local office of department"
    a = one(conn.execute("SELECT * FROM authorities WHERE department_id=? ORDER BY level LIMIT 1", (department_id,)))
    if a: return a, "fallback: lowest configured authority of department"
    raise CoreError("No authority configured for this department", 422)

@transactional
def assign_authority(conn, cid, actor="system:n8n"):
    c = get_complaint(conn, cid)
    if c["status"] != "submitted": raise CoreError(f"Cannot assign in status {c['status']}", 409)
    a, why = pick_authority(conn, c["department_id"], c["jurisdiction"])
    dl = deadlines_for(conn, a["level"])
    conn.execute("UPDATE complaints SET current_authority_id=?, ack_deadline=?, review_deadline=?, resolution_deadline=? WHERE id=?",
                 (a["id"], dl["ack_deadline"], dl["review_deadline"], dl["resolution_deadline"], cid))
    set_status(conn, cid, "assigned", "allegation", f"Assigned to {a['name']} ({why})", actor)
    ev = audit_append(conn, cid, actor, "authority_assigned", {"authority_id": a["id"], "authority": a["name"], "level": a["level_name"], "basis": why, **dl})
    notify(conn, c["citizen_id"], cid, "authority_assigned", "Authority assigned",
           f"{c['code']} was assigned to {a['name']}. Acknowledgement due by {dl['ack_deadline']}.")
    notify_authority(conn, a["id"], cid, "authority_assigned", "New case assigned", f"{c['code']} - {c['title']}. Acknowledge by {dl['ack_deadline']}.")
    return {"authority": a, "deadlines": dl, "basis": why, "audit_hash": ev["current_hash"]}

# ---------------------------------------------------------------- officials
def _can_act(conn, user, c):
    if user["role"] not in ("official", "supervisor"): raise CoreError("Only officials/supervisors can act on cases", 403)
    if not user["authority_id"] or c["current_authority_id"] != user["authority_id"]:
        raise CoreError("This case is not assigned to your authority", 403)

@transactional
def acknowledge(conn, cid, user, note=""):
    c = get_complaint(conn, cid); _can_act(conn, user, c)
    if c["status"] != "assigned": raise CoreError("Case is not awaiting acknowledgement", 409)
    now = iso(utcnow())
    conn.execute("UPDATE complaints SET acknowledged_at=? WHERE id=?", (now, cid))
    conn.execute("INSERT INTO official_responses(complaint_id,authority_id,author_id,kind,body,created_at) VALUES(?,?,?,?,?,?)",
                 (cid, user["authority_id"], user["id"], "acknowledgement", note or "Complaint received and acknowledged.", now))
    set_status(conn, cid, "acknowledged", "under_review", "Acknowledged by authority", user["id"])
    audit_append(conn, cid, user["id"], "complaint_acknowledged", {"authority_id": user["authority_id"]})
    notify(conn, c["citizen_id"], cid, "complaint_acknowledged", "Complaint acknowledged", f"{c['code']} was acknowledged by the authority.")

@transactional
def respond(conn, cid, user, body, kind="response", corrects_id=None):
    c = get_complaint(conn, cid); _can_act(conn, user, c)
    if kind not in ("response", "correction"): raise CoreError("Invalid kind")
    if not (body or "").strip(): raise CoreError("Response text required")
    if kind == "correction" and not one(conn.execute("SELECT id FROM official_responses WHERE id=? AND complaint_id=?", (corrects_id, cid))):
        raise CoreError("A correction must reference an existing response on this case")
    now = iso(utcnow())
    conn.execute("INSERT INTO official_responses(complaint_id,authority_id,author_id,kind,body,corrects_id,created_at) VALUES(?,?,?,?,?,?,?)",
                 (cid, user["authority_id"], user["id"], kind, body.strip(), corrects_id, now))
    if c["status"] in ("assigned", "acknowledged", "info_requested"):
        if c["status"] == "assigned": conn.execute("UPDATE complaints SET acknowledged_at=? WHERE id=?", (now, cid))
        set_status(conn, cid, "under_review", "under_review", "Official response recorded", user["id"])
    audit_append(conn, cid, user["id"], "official_response_recorded" if kind == "response" else "official_correction_recorded",
                 {"authority_id": user["authority_id"], "body_sha256": sha256_hex(body.encode()), "corrects_id": corrects_id})
    notify(conn, c["citizen_id"], cid, "response_received", "Response received", f"The authority responded on {c['code']}.")

@transactional
def request_info(conn, cid, user, body):
    c = get_complaint(conn, cid); _can_act(conn, user, c)
    if not (body or "").strip(): raise CoreError("Describe the information needed")
    conn.execute("INSERT INTO official_responses(complaint_id,authority_id,author_id,kind,body,created_at) VALUES(?,?,?,?,?,?)",
                 (cid, user["authority_id"], user["id"], "info_request", body.strip(), iso(utcnow())))
    if c["status"] == "assigned": conn.execute("UPDATE complaints SET acknowledged_at=? WHERE id=?", (iso(utcnow()), cid))
    set_status(conn, cid, "info_requested", "under_review", "Information requested from citizen (deadline clock paused)", user["id"])
    audit_append(conn, cid, user["id"], "information_requested", {"body_sha256": sha256_hex(body.encode())})
    notify(conn, c["citizen_id"], cid, "information_requested", "Information requested", f"The authority needs more information on {c['code']}: {body.strip()[:200]}")

@transactional
def citizen_reply(conn, cid, citizen_id, body):
    c = get_complaint(conn, cid)
    if c["citizen_id"] != citizen_id: raise CoreError("Not your complaint", 403)
    if c["status"] != "info_requested": raise CoreError("No information is currently requested", 409)
    conn.execute("INSERT INTO official_responses(complaint_id,authority_id,author_id,kind,body,created_at) VALUES(?,?,?,?,?,?)",
                 (cid, c["current_authority_id"], citizen_id, "citizen_reply", (body or "").strip(), iso(utcnow())))
    set_status(conn, cid, "under_review", "under_review", "Citizen supplied information", citizen_id)
    audit_append(conn, cid, citizen_id, "citizen_replied", {"body_sha256": sha256_hex((body or "").encode())})
    notify_authority(conn, c["current_authority_id"], cid, "response_received", "Citizen replied", f"{c['code']}: the citizen supplied the requested information.")

@transactional
def transfer(conn, cid, user, to_authority_id, reason):
    c = get_complaint(conn, cid); _can_act(conn, user, c)
    to = get_authority(conn, to_authority_id); cur = get_authority(conn, c["current_authority_id"])
    if not to or to["department_id"] != cur["department_id"] or to["level"] != cur["level"] or to["id"] == cur["id"]:
        raise CoreError("Transfers are only allowed to a peer authority in the same department", 422)
    if not (reason or "").strip(): raise CoreError("Reason required")
    dl = deadlines_for(conn, to["level"])
    ev = audit_append(conn, cid, user["id"], "authority_transferred", {"from": cur["id"], "to": to["id"], "reason": reason.strip()})
    conn.execute("INSERT INTO authority_transfers(complaint_id,from_authority_id,to_authority_id,reason,actor,audit_hash,created_at) VALUES(?,?,?,?,?,?,?)",
                 (cid, cur["id"], to["id"], reason.strip(), str(user["id"]), ev["current_hash"], iso(utcnow())))
    conn.execute("UPDATE complaints SET current_authority_id=?, ack_deadline=?, review_deadline=?, resolution_deadline=?, acknowledged_at=NULL WHERE id=?",
                 (to["id"], dl["ack_deadline"], dl["review_deadline"], dl["resolution_deadline"], cid))
    set_status(conn, cid, "assigned", "allegation", f"Transferred to {to['name']}", user["id"])
    notify(conn, c["citizen_id"], cid, "authority_transferred", "Case transferred", f"{c['code']} was transferred to {to['name']}.")
    notify_authority(conn, to["id"], cid, "authority_assigned", "Case transferred to you", f"{c['code']} - {c['title']}")

OUTCOMES = {"upheld": ("verified_finding", 1), "partially_upheld": ("verified_finding", 1), "rejected": ("rejected", 0), "resolved": ("resolved", 0)}

@transactional
def decide(conn, cid, user, outcome, reasoning, verified=None):
    c = get_complaint(conn, cid); _can_act(conn, user, c)
    if outcome not in OUTCOMES: raise CoreError("Outcome must be one of " + ", ".join(OUTCOMES))
    if len((reasoning or "").strip()) < 10: raise CoreError("A reasoned decision is required")
    if c["status"] not in ACTIVE: raise CoreError("Case is not open for a decision", 409)
    stage, vf = OUTCOMES[outcome]
    prev = one(conn.execute("SELECT id FROM decisions WHERE complaint_id=? ORDER BY id DESC LIMIT 1", (cid,)))
    cur = conn.execute("INSERT INTO decisions(complaint_id,authority_id,author_id,outcome,reasoning,verified_finding,supersedes_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (cid, user["authority_id"], user["id"], outcome, reasoning.strip(), vf if verified is None else int(bool(verified)), prev["id"] if prev else None, iso(utcnow())))
    set_status(conn, cid, "resolved" if outcome == "resolved" else "decided", stage, f"Decision: {outcome}", user["id"])
    audit_append(conn, cid, user["id"], "decision_recorded", {"decision_id": cur.lastrowid, "outcome": outcome, "reasoning_sha256": sha256_hex(reasoning.encode())})
    notify(conn, c["citizen_id"], cid, "decision_made", "Decision recorded", f"A decision ('{outcome.replace('_', ' ')}') was recorded on {c['code']}. You may appeal if you disagree.")
    return cur.lastrowid

# ---------------------------------------------------------------- appeals (workflow 10)
@transactional
def create_appeal(conn, cid, citizen_id, grounds):
    c = get_complaint(conn, cid)
    if c["citizen_id"] != citizen_id: raise CoreError("Not your complaint", 403)
    d = one(conn.execute("SELECT * FROM decisions WHERE complaint_id=? ORDER BY id DESC LIMIT 1", (cid,)))
    if not d or c["status"] != "decided": raise CoreError("There is no appealable decision", 409)
    if d["outcome"] not in ("rejected", "partially_upheld"): raise CoreError("Only rejected or partially upheld decisions can be appealed", 409)
    if conn.execute("SELECT 1 FROM appeals WHERE decision_id=?", (d["id"],)).fetchone(): raise CoreError("This decision was already appealed", 409)
    if len((grounds or "").strip()) < 20: raise CoreError("Please explain your grounds of appeal (20+ characters)")
    cur_auth = get_authority(conn, d["authority_id"])
    review = get_authority(conn, cur_auth["parent_authority_id"]) or cur_auth
    dl = iso(utcnow() + timedelta(hours=rule_for(conn, review["level"])["appeal_hours"]))
    cur = conn.execute("INSERT INTO appeals(complaint_id,decision_id,citizen_id,grounds,status,review_authority_id,deadline,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (cid, d["id"], citizen_id, grounds.strip(), "under_review", review["id"], dl, iso(utcnow())))
    conn.execute("UPDATE complaints SET current_authority_id=?, resolution_deadline=? WHERE id=?", (review["id"], dl, cid))
    set_status(conn, cid, "appealed", "appealed", f"Appeal lodged; original decision #{d['id']} preserved", citizen_id)
    ev = audit_append(conn, cid, citizen_id, "appeal_submitted", {"appeal_id": cur.lastrowid, "decision_id": d["id"], "review_authority": review["name"], "deadline": dl})
    notify(conn, citizen_id, cid, "appeal_submitted", "Appeal submitted", f"Your appeal on {c['code']} is with {review['name']}. Decision due by {dl}.")
    notify_authority(conn, review["id"], cid, "appeal_submitted", "Appeal to review", f"{c['code']}: appeal against decision #{d['id']}.")
    return {"appeal_id": cur.lastrowid, "review_authority": review, "deadline": dl, "audit_hash": ev["current_hash"]}

@transactional
def decide_appeal(conn, appeal_id, user, outcome, reasoning):
    a = one(conn.execute("SELECT * FROM appeals WHERE id=?", (appeal_id,)))
    if not a: raise CoreError("Appeal not found", 404)
    if user["role"] != "supervisor" or user["authority_id"] != a["review_authority_id"]: raise CoreError("Only the designated review authority can decide this appeal", 403)
    if a["status"] != "under_review": raise CoreError("Appeal already decided", 409)
    if outcome not in ("overturned", "upheld", "modified"): raise CoreError("Outcome must be overturned, upheld or modified")
    if len((reasoning or "").strip()) < 10: raise CoreError("Reasoning required")
    c = get_complaint(conn, a["complaint_id"])
    now = iso(utcnow())
    conn.execute("UPDATE appeals SET status='decided', outcome=?, outcome_reasoning=?, decided_by=?, decided_at=? WHERE id=?", (outcome, reasoning.strip(), user["id"], now, appeal_id))
    orig = one(conn.execute("SELECT * FROM decisions WHERE id=?", (a["decision_id"],)))
    legal = False
    if outcome in ("overturned", "modified"):  # original decision is preserved; a NEW decision supersedes it
        new_out = "upheld" if outcome == "overturned" else "partially_upheld"
        conn.execute("INSERT INTO decisions(complaint_id,authority_id,author_id,outcome,reasoning,verified_finding,supersedes_id,via_appeal_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (c["id"], user["authority_id"], user["id"], new_out, f"Appeal outcome ({outcome}): {reasoning.strip()}", 1, orig["id"], appeal_id, now))
        set_status(conn, c["id"], "decided", "verified_finding", f"Appeal {outcome}; supersedes decision #{orig['id']}", user["id"])
    else:
        set_status(conn, c["id"], "decided", "rejected", "Appeal upheld the original decision; administrative remedies exhausted", user["id"])
        legal = True
    audit_append(conn, c["id"], user["id"], "appeal_decided", {"appeal_id": appeal_id, "outcome": outcome, "reasoning_sha256": sha256_hex(reasoning.encode()), "original_decision_preserved": orig["id"]})
    notify(conn, c["citizen_id"], c["id"], "appeal_outcome", "Appeal outcome", f"Appeal on {c['code']}: {outcome}.")
    return {"appeal_id": appeal_id, "outcome": outcome, "legal_assistance_recommended": legal}

# ---------------------------------------------------------------- deadline monitoring + escalation (workflows 6/7)
def find_overdue(conn, now=None):
    now_s = iso(now or utcnow()); out = []
    for c in conn.execute("SELECT * FROM complaints WHERE status IN ('assigned','acknowledged','under_review','investigation','appealed')"):
        c = dict(c); due = None
        if c["status"] == "assigned" and c["ack_deadline"] and c["ack_deadline"] < now_s: due = ("acknowledgement", c["ack_deadline"])
        elif c["status"] in ("acknowledged",) and c["review_deadline"] and c["review_deadline"] < now_s: due = ("review", c["review_deadline"])
        elif c["resolution_deadline"] and c["resolution_deadline"] < now_s and c["status"] != "assigned": due = ("resolution", c["resolution_deadline"])
        if due:
            nxt = one(conn.execute("SELECT id,name,level_name FROM authorities WHERE id=(SELECT parent_authority_id FROM authorities WHERE id=?)", (c["current_authority_id"],)))
            out.append({"complaint_id": c["id"], "code": c["code"], "current_authority_id": c["current_authority_id"], "deadline_type": due[0],
                        "deadline": due[1], "next_authority": nxt, "can_escalate": bool(nxt)})
    return out

def approaching(conn, hours=6):
    lo, hi = iso(utcnow()), iso(utcnow() + timedelta(hours=hours)); out = []
    for c in conn.execute("SELECT id,code,citizen_id,current_authority_id,ack_deadline,status FROM complaints WHERE status='assigned' AND ack_deadline BETWEEN ? AND ?", (lo, hi)):
        out.append(dict(c))
    return out

@transactional
def escalate(conn, cid, actor="system:n8n", now=None):
    """Escalate ONLY along the configured hierarchy (authorities.parent_authority_id). Overdue != guilt."""
    c = get_complaint(conn, cid)
    items = [o for o in find_overdue(conn, now) if o["complaint_id"] == cid]
    if not items: raise CoreError("Escalation condition not satisfied (no overdue deadline)", 409)
    o = items[0]; cur = get_authority(conn, c["current_authority_id"]); nxt = get_authority(conn, cur["parent_authority_id"])
    reason = f"{o['deadline_type'].capitalize()} deadline {o['deadline']} passed without the required action. Non-response is not a finding of misconduct."
    if not nxt:
        ev = audit_append(conn, cid, actor, "escalation_exhausted", {"authority_id": cur["id"], "deadline_type": o["deadline_type"], "reason": reason})
        conn.execute("INSERT INTO escalations(complaint_id,previous_authority_id,new_authority_id,reason,deadline_type,trigger_deadline,actor,audit_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (cid, cur["id"], None, reason, o["deadline_type"], o["deadline"], str(actor), ev["current_hash"], iso(utcnow())))
        conn.execute("UPDATE complaints SET ack_deadline=NULL, review_deadline=NULL, resolution_deadline=NULL WHERE id=?", (cid,))
        set_status(conn, cid, "unresolved", c["stage"], "Highest oversight authority also overdue; escalation exhausted", actor)
        notify(conn, c["citizen_id"], cid, "case_escalated", "Administrative escalation exhausted", f"{c['code']} remains unresolved at the highest oversight authority. Legal-assistance options are available.")
        return {"exhausted": True, "audit_hash": ev["current_hash"]}
    dl = deadlines_for(conn, nxt["level"], now)
    ev = audit_append(conn, cid, actor, "case_escalated", {"previous_authority": cur["id"], "new_authority": nxt["id"], "reason": reason, "deadline_type": o["deadline_type"], "deadline": o["deadline"], **dl})
    conn.execute("INSERT INTO escalations(complaint_id,previous_authority_id,new_authority_id,reason,deadline_type,trigger_deadline,actor,audit_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                 (cid, cur["id"], nxt["id"], reason, o["deadline_type"], o["deadline"], str(actor), ev["current_hash"], iso(utcnow())))
    conn.execute("UPDATE complaints SET current_authority_id=?, ack_deadline=?, review_deadline=?, resolution_deadline=?, acknowledged_at=NULL WHERE id=?",
                 (nxt["id"], dl["ack_deadline"], dl["review_deadline"], dl["resolution_deadline"], cid))
    set_status(conn, cid, "assigned", c["stage"], f"Escalated from {cur['name']} to {nxt['name']}", actor)
    notify(conn, c["citizen_id"], cid, "case_escalated", "Your case was escalated", f"{c['code']} was automatically escalated from {cur['name']} to {nxt['name']} because a deadline passed. This is not a finding about anyone's conduct.")
    notify_authority(conn, nxt["id"], cid, "case_escalated", "Escalated case received", f"{c['code']} - {c['title']} escalated from {cur['name']}.")
    notify_authority(conn, cur["id"], cid, "case_escalated", "Case escalated from your authority", f"{c['code']} passed its {o['deadline_type']} deadline.")
    return {"exhausted": False, "previous": cur, "new": nxt, "deadlines": dl, "audit_hash": ev["current_hash"], "reason": reason}

# ---------------------------------------------------------------- case summary + legal assistance (workflows 11/12)
def case_summary(conn, cid):
    c = get_complaint(conn, cid)
    rep = jl(c["citizen_report"], {}) or jl(c["ai_analysis"], {}).get("report", {})
    auth = lambda i: (get_authority(conn, i) or {}).get("name")
    return {"complaint_id": c["code"], "title": c["title"], "incident_summary": rep.get("incident_summary") or c["original_description"][:400],
            "timeline": rep.get("timeline", []),
            "authorities_involved": [{"name": a["name"], "level": a["level_name"]} for a in
                (get_authority(conn, i) for i in dict.fromkeys([r[0] for r in conn.execute("SELECT to_authority_id FROM authority_transfers WHERE complaint_id=?", (cid,))]
                 + [r[0] for r in conn.execute("SELECT new_authority_id FROM escalations WHERE complaint_id=? AND new_authority_id IS NOT NULL", (cid,))] + [c["current_authority_id"]])) if a],
            "transfers": [{"from": auth(t["from_authority_id"]), "to": auth(t["to_authority_id"]), "reason": t["reason"], "at": t["created_at"]} for t in rows(conn.execute("SELECT * FROM authority_transfers WHERE complaint_id=?", (cid,)))],
            "escalations": [{"from": auth(e["previous_authority_id"]), "to": auth(e["new_authority_id"]), "reason": e["reason"], "at": e["created_at"]} for e in rows(conn.execute("SELECT * FROM escalations WHERE complaint_id=?", (cid,)))],
            "evidence_inventory": [{"id": e["id"], "filename": e["original_filename"], "sha256": e["sha256"]} for e in list_evidence(conn, cid)],
            "responses": [{"kind": r["kind"], "at": r["created_at"], "text": r["body"]} for r in rows(conn.execute("SELECT * FROM official_responses WHERE complaint_id=? ORDER BY id", (cid,)))],
            "decisions": [{"id": d["id"], "outcome": d["outcome"], "reasoning": d["reasoning"], "supersedes": d["supersedes_id"], "at": d["created_at"]} for d in rows(conn.execute("SELECT * FROM decisions WHERE complaint_id=? ORDER BY id", (cid,)))],
            "appeals": [{"id": a["id"], "status": a["status"], "outcome": a["outcome"], "grounds": a["grounds"]} for a in rows(conn.execute("SELECT * FROM appeals WHERE complaint_id=?", (cid,)))],
            "current_status": c["status"], "stage": c["stage"],
            "audit_chain": audit_verify(conn, cid),
            "next_procedural_options": _options(c),
            "note": "Allegations are unverified unless stage is 'verified_finding'."}

def _options(c):
    base = [{"option": "Right to Information (RTI) request", "what": "Ask the department for the file notings and action taken on your complaint.", "typical_forum": "Public Information Officer of the department"},
            {"option": "State Human Rights / Lokayukta style oversight complaint", "what": "Where the matter involves public servants and the administrative route has stalled, an independent oversight body may accept a complaint.", "typical_forum": "State oversight / ombudsman body"},
            {"option": "Writ petition (mandamus) for failure to act", "what": "Courts can direct a public authority to perform a legal duty. Needs a lawyer or legal-aid support.", "typical_forum": "High Court"},
            {"option": "Free legal-aid consultation", "what": "Legal Services Authorities provide free advice to eligible persons.", "typical_forum": "District / State Legal Services Authority"}]
    if "bribe" in (c.get("title", "") + c.get("original_description", "")).lower():
        base.append({"option": "Anti-corruption agency complaint", "what": "Allegations of bribery can be reported to the anti-corruption bureau.", "typical_forum": "State anti-corruption bureau / vigilance"})
    return base

@transactional
def legal_assistance(conn, cid, reason="manual"):
    c = get_complaint(conn, cid)
    cat = one(conn.execute("SELECT code FROM categories WHERE id=?", (c["category_id"],)))["code"]
    summary = case_summary(conn, cid)
    order = {"free": 0, "low_cost": 1, "paid": 2}
    m = [r for r in rows(conn.execute("SELECT * FROM legal_aid")) if r["category_codes"] in ("*", cat) or cat in r["category_codes"].split(",")]
    m.sort(key=lambda r: (order[r["cost"]], r["name"]))
    checklist = ["Keep your complaint ID " + (c["code"] or "") + " and the case summary below.", "Download your evidence list with SHA-256 hashes (proves files were not altered).",
                 "Note every deadline that passed and the escalation history.", "Collect ID proof and any income certificate for legal-aid eligibility.",
                 "Prepare a one-page chronology of events.", "Ask the legal-aid provider about limitation periods that may apply to you."]
    opts = _options(c)
    conn.execute("INSERT INTO legal_assistance(complaint_id,trigger_reason,summary,options,matches,checklist,created_at) VALUES(?,?,?,?,?,?,?)",
                 (cid, reason, j(summary), j(opts), j(m), j(checklist), iso(utcnow())))
    audit_append(conn, cid, "system:n8n", "legal_assistance_generated", {"trigger": reason, "providers_matched": len(m)})
    notify(conn, c["citizen_id"], cid, "legal_assistance", "Legal assistance options available", f"Free and low-cost legal-aid options for {c['code']} are ready. {LEGAL_DISCLAIMER}")
    return {"disclaimer": LEGAL_DISCLAIMER, "summary": summary, "options": opts, "providers": m, "checklist": checklist}

def latest_legal(conn, cid):
    r = one(conn.execute("SELECT * FROM legal_assistance WHERE complaint_id=? ORDER BY id DESC LIMIT 1", (cid,)))
    if not r: return None
    return {"disclaimer": LEGAL_DISCLAIMER, "trigger": r["trigger_reason"], "summary": jl(r["summary"]), "options": jl(r["options"]), "providers": jl(r["matches"]), "checklist": jl(r["checklist"]), "created_at": r["created_at"]}

# ---------------------------------------------------------------- accountability metrics
def metrics(conn, department_id=None):
    where, args = ("AND department_id=?", (department_id,)) if department_id else ("", ())
    base = f"FROM complaints WHERE status!='draft' {where}"
    total = conn.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
    responded = conn.execute(f"SELECT COUNT(*) {base} AND id IN (SELECT complaint_id FROM official_responses WHERE kind IN ('acknowledgement','response'))", args).fetchone()[0]
    lat = [(parse(r[1]) - parse(r[0])).total_seconds() / 3600 for r in conn.execute(
        f"SELECT c.submitted_at, MIN(r.created_at) FROM complaints c JOIN official_responses r ON r.complaint_id=c.id AND r.kind IN ('acknowledgement','response') WHERE c.status!='draft' {where.replace('department_id','c.department_id')} GROUP BY c.id", args)]
    resolved = conn.execute(f"SELECT COUNT(*) {base} AND (stage='resolved' OR status='resolved')", args).fetchone()[0]
    overdue = len(find_overdue(conn)) if not department_id else sum(1 for o in find_overdue(conn) if get_complaint(conn, o["complaint_id"])["department_id"] == department_id)
    verified = conn.execute(f"SELECT COUNT(*) FROM decisions d JOIN complaints c ON c.id=d.complaint_id WHERE d.verified_finding=1 AND NOT EXISTS (SELECT 1 FROM decisions n WHERE n.supersedes_id=d.id) {where.replace('department_id','c.department_id')}", args).fetchone()[0]
    ap = {r[0] or "pending": r[1] for r in conn.execute(f"SELECT a.outcome, COUNT(*) FROM appeals a JOIN complaints c ON c.id=a.complaint_id WHERE 1=1 {where.replace('department_id','c.department_id')} GROUP BY a.outcome", args)}
    stages = {r[0]: r[1] for r in conn.execute(f"SELECT stage, COUNT(*) {base} GROUP BY stage", args)}
    return {"complaint_volume": total, "response_rate": round(responded / total, 3) if total else None, "avg_response_hours": round(sum(lat) / len(lat), 2) if lat else None,
            "resolution_rate": round(resolved / total, 3) if total else None, "overdue_cases": overdue, "verified_findings": verified, "appeal_outcomes": ap, "by_stage": stages,
            "caution": "Complaint volume is NOT evidence of misconduct. Only 'verified findings' reflect a completed review."}

@transactional
def snapshot_metrics(conn):
    for d in rows(conn.execute("SELECT id FROM departments")):
        m = metrics(conn, d["id"])
        conn.execute("INSERT INTO accountability_metrics(department_id,total,responded,avg_response_hours,resolved,overdue,verified_findings,computed_at) VALUES(?,?,?,?,?,?,?,?)",
                     (d["id"], m["complaint_volume"], round((m["response_rate"] or 0) * m["complaint_volume"]), m["avg_response_hours"], round((m["resolution_rate"] or 0) * m["complaint_volume"]), m["overdue_cases"], m["verified_findings"], iso(utcnow())))
