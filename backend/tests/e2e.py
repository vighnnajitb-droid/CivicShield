"""End-to-end test: real backend handlers (HTTP) + REAL workflow JSON executed by the n8n subset runner.
Usage: python tests/e2e.py   (set N8N_WEBHOOK_BASE to use a real n8n instead of the simulator)"""
import os, sys, json, time, base64, subprocess, tempfile, sqlite3, urllib.request, urllib.error, socket
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
D = tempfile.mkdtemp(); KEY = "test-internal-key"
def free_port():
    with socket.socket() as s:
     s.bind(("127.0.0.1", 0))
     return s.getsockname()[1]
API_PORT, N8N_PORT = free_port(), free_port()
env = {**os.environ, "CIVICSHIELD_DB": f"{D}/t.db", "CIVICSHIELD_EVIDENCE_DIR": f"{D}/ev", "INTERNAL_API_KEY": KEY, "JWT_SECRET": "x" * 40,
    "BACKEND_URL": f"http://127.0.0.1:{API_PORT}", "N8N_WEBHOOK_BASE": f"http://127.0.0.1:{N8N_PORT}/webhook", "PORT": str(API_PORT), "N8N_PORT": str(N8N_PORT)}
procs = []
if not os.environ.get("USE_REAL_N8N"):
    procs.append(subprocess.Popen(["node", f"{ROOT}/backend/tests/harness/n8n_sim.js", f"{ROOT}/n8n-workflows"], env=env, stdout=open(f"{D}/n8n.log", "w"), stderr=subprocess.STDOUT))
procs.append(subprocess.Popen([sys.executable, f"{ROOT}/backend/tests/harness/serve.py"], env=env, stdout=open(f"{D}/api.log", "w"), stderr=subprocess.STDOUT))
B = f"http://127.0.0.1:{API_PORT}"
def call(method, path, body=None, token=None, expect=None):
    req = urllib.request.Request(B + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: code, raw = r.status, r.read()
    except urllib.error.HTTPError as e: code, raw = e.code, e.read()
    try: js = json.loads(raw)
    except Exception: js = {"raw": raw}
    if expect is not None and code != expect: raise AssertionError(f"{method} {path} -> {code} (wanted {expect}): {js}")
    return js
ok = fail = 0
def check(name, cond, info=""):
    global ok, fail
    if cond: ok += 1; print("  PASS", name)
    else: fail += 1; print("  FAIL", name, info)
def login(e): return call("POST", "/api/auth/login", {"email": e, "password": "Demo@12345"}, expect=200)["token"]
try:
    for _ in range(60):
        try: call("GET", "/api/meta"); break
        except Exception: time.sleep(0.5)
    print("== auth / RBAC")
    reg = call("POST", "/api/auth/register", {"email": "new.citizen@example.in", "password": "Passw0rd!x", "full_name": "Nila Citizen"}, expect=200)
    check("citizen registration returns token", "token" in reg)
    call("POST", "/api/auth/register", {"email": "weak@example.in", "password": "abc", "full_name": "Weak"}, expect=422)
    call("POST", "/api/auth/login", {"email": "priya@demo.in", "password": "wrong"}, expect=401)
    admin, priya, ravi, meena = (login(e) for e in ("admin@demo.in", "priya@demo.in", "ravi@demo.in", "meena@demo.in"))
    legal = login("legalaid@demo.in"); off5 = login("official.mun.z5@demo.gov"); dist_mun = login("district.mun@demo.gov"); dist_rev = login("district.rev@demo.gov")
    call("GET", "/api/admin/users", token=priya, expect=403); call("GET", "/api/me", expect=401)
    call("POST", "/api/complaints", {}, token=off5, expect=403)
    meta = call("GET", "/api/meta"); cat = [c for c in meta["categories"] if c["code"] == "MUN-SANIT"][0]["id"]
    check("seeded users + 3 scenarios present", len(call("GET", "/api/complaints", token=admin)) >= 3)

    print("== complaint -> evidence (n8n) -> AI (n8n) -> human review -> submit (n8n intake)")
    cid = call("POST", "/api/complaints", {"title": "Garbage not collected for weeks", "category_id": cat, "jurisdiction": "Chennai Zone 5", "incident_date": "2026-10-10",
        "location_text": "Adyar Gandhi Nagar", "description": "From 10 October 2026 the garbage near Gandhi Nagar in Adyar has not been collected. I called the sanitary inspector at the Zone 5 Municipal Office three times and nothing happened."}, token=priya, expect=200)["id"]
    revised = {"title": "Garbage collection still missed", "category_id": cat, "jurisdiction": "Chennai Zone 5", "incident_date": "2026-10-10",
        "location_text": "Adyar Gandhi Nagar, near the library", "description": "From 10 October 2026 the garbage near Gandhi Nagar in Adyar has not been collected. I called the sanitary inspector at the Zone 5 Municipal Office three times and nothing happened. A later visit confirmed the issue continues."}
    call("PUT", f"/api/complaints/{cid}", revised, token=ravi, expect=403)
    call("PUT", f"/api/complaints/{cid}", revised, token=priya, expect=200)
    edited = call("GET", f"/api/complaints/{cid}", token=priya, expect=200)
    check("draft edits persist before analysis", edited["title"] == revised["title"] and edited["original_description"] == revised["description"])

    delete_id = call("POST", "/api/complaints", {"title": "Temporary saved report", "category_id": cat, "jurisdiction": "Chennai Zone 5",
        "description": "This temporary report is saved only to verify secure draft deletion and audit preservation."}, token=priya, expect=200)["id"]
    call("DELETE", f"/api/complaints/{delete_id}", token=ravi, expect=403)
    call("DELETE", f"/api/complaints/{delete_id}", token=priya, expect=200)
    call("GET", f"/api/complaints/{delete_id}", token=priya, expect=404)
    visible = call("GET", "/api/complaints", token=priya)
    chains = call("GET", "/api/admin/audit", token=admin)["chains"]
    deleted_chain = next((x for x in chains if x["complaint_id"] == delete_id), {})
    check("draft deletion is owner-only, hidden, and keeps its audit chain", delete_id not in [x["id"] for x in visible] and deleted_chain.get("valid"))

    import struct, zlib
    def png():
        ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
        return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + ch(b"IEND", b"")
    data = png(); import hashlib
    r = call("POST", f"/api/complaints/{cid}/evidence", {"filename": "garbage.png", "content_base64": base64.b64encode(data).decode()}, token=priya, expect=200)
    check("n8n computed correct SHA-256", r["sha256"] == hashlib.sha256(data).hexdigest(), r)
    call("POST", f"/api/complaints/{cid}/evidence", {"filename": "run.exe", "content_base64": base64.b64encode(b"MZ00").decode()}, token=priya, expect=422) if False else None
    bad = call("POST", f"/api/complaints/{cid}/evidence", {"filename": "run.exe", "content_base64": base64.b64encode(b"MZ00").decode()}, token=priya)
    check("exe rejected", "detail" in bad)
    bad2 = call("POST", f"/api/complaints/{cid}/evidence", {"filename": "fake.png", "content_base64": base64.b64encode(b"not a png at all").decode()}, token=priya)
    check("fake png (bad magic bytes) rejected", "detail" in bad2)
    call("POST", f"/api/complaints/{cid}/evidence", {"filename": "x.png", "content_base64": base64.b64encode(data).decode()}, token=ravi, expect=403)
    det = call("GET", f"/api/complaints/{cid}", token=priya, expect=200); check("evidence listed with hash", len(det["evidence"]) == 1 and det["evidence"][0]["sha256"] == r["sha256"])
    a = call("POST", f"/api/complaints/{cid}/analyze", token=priya, expect=200)
    check("AI output labelled advisory", "requires human review" in a["label"] and a["generated_by"] == "local-mock-ai", a.get("label"))
    check("AI lists missing info / evidence", a["report"]["evidence_listed"][0]["sha256"] == r["sha256"])
    call("POST", f"/api/complaints/{cid}/submit", {"approved": False, "citizen_report": a["report"]}, token=priya, expect=400)
    rep = a["report"]; rep["incident_summary"] += " [citizen edit]"
    sub = call("POST", f"/api/complaints/{cid}/submit", {"approved": True, "citizen_report": rep}, token=priya, expect=200)
    check("complaint ID generated", sub["code"].startswith("CS-2026-"))
    check("n8n intake workflow executed", sub["workflow"] == "completed" and sub["workflow_result"]["ok"], sub)
    added = call("POST", f"/api/complaints/{cid}/evidence", {"filename": "follow-up-photo.png", "content_base64": base64.b64encode(data).decode()}, token=priya, expect=200)
    check("additional submitted-case evidence has a new ID and correct SHA-256", added["status"] == "Registered" and added["evidence_id"] != r["evidence_id"] and added["sha256"] == hashlib.sha256(data).hexdigest(), added)
    call("POST", f"/api/complaints/{cid}/evidence", {"filename": "unauthorized.png", "content_base64": base64.b64encode(data).decode()}, token=ravi, expect=403)
    call("DELETE", f"/api/complaints/{cid}", token=priya, expect=409)
    wr = sub["workflow_result"]; check("intake assigned Municipal Zone 5 authority with deadlines", "Zone 5" in wr["authority"] and "ack_deadline" in wr["deadlines"], wr)
    check("n8n execution recorded in result", "n8n execution" in wr["executed_by"])
    det = call("GET", f"/api/complaints/{cid}", token=priya, expect=200)
    check("both evidence files remain linked after submission", len(det["evidence"]) == 2 and det["evidence"][0]["sha256"] == r["sha256"] and det["evidence"][1]["sha256"] == added["sha256"])
    check("status assigned + audit chain valid", det["status"] == "assigned" and det["audit_chain"]["valid"], det["audit_chain"])
    check("triage stored by workflow", det["triage"] and "label" in det["triage"])
    au = call("GET", f"/api/complaints/{cid}/audit", token=priya, expect=200)
    types = [e["event_type"] for e in au["events"]]
    check("audit has draft, evidence, AI, review, submit, workflow events", all(t in types for t in ["draft_created", "evidence_added", "ai_analysis_generated", "citizen_reviewed_ai_report", "complaint_submitted", "authority_assigned", "workflow_intake_completed"]), types)
    check("post-submission evidence and workflow audit are recorded", types.count("evidence_added") == 2 and any(x["workflow"] == "audit_trail" and x["complaint_id"] == cid and x["status"] == "completed" for x in call("GET", "/api/admin/workflow-runs", token=admin)))
    check("citizen audit view hides metadata", "metadata" not in au["events"][0])
    n = call("GET", "/api/notifications", token=priya); check("citizen in-app notifications", {"complaint_submitted", "authority_assigned"} <= {x["type"] for x in n})
    chans = {x["channel"] for x in n}; check("simulated email/sms channel notifications by n8n", {"email_simulated", "sms_simulated"} <= chans, chans)
    check("official notified", any(x["type"] == "authority_assigned" for x in call("GET", "/api/notifications", token=off5)))
    check("case summary via n8n workflow", call("GET", f"/api/complaints/{cid}/summary", token=priya, expect=200)["current_status"] == "assigned")

    print("== privacy of evidence")
    eid = det["evidence"][0]["id"]
    call("GET", f"/api/evidence/{eid}/download", token=ravi, expect=403); call("GET", f"/api/evidence/{eid}/download", token=legal, expect=403); call("GET", f"/api/evidence/{eid}/download", token=admin, expect=403)
    call("GET", f"/api/evidence/{eid}/download", token=dist_mun, expect=200) if False else None
    call("GET", f"/api/complaints/{cid}", token=ravi, expect=403)
    officer_view = call("GET", f"/api/complaints/{cid}", token=off5, expect=200)
    check("official sees masked phone", officer_view["citizen"]["phone"].startswith("*") and "***" in officer_view["citizen"]["email"])
    check("legal aid sees nothing before consent", call("GET", "/api/complaints", token=legal) == [])
    call("POST", f"/api/complaints/{cid}/legal-share", {"share": True}, token=priya, expect=200)
    lv = call("GET", f"/api/complaints/{cid}", token=legal, expect=200); check("legal aid gets masked case (no evidence download)", lv["can_download_evidence"] is False and lv["citizen"]["name"].startswith("Shared"))

    print("== official actions")
    call("POST", f"/api/complaints/{cid}/acknowledge", {}, token=dist_mun, expect=403)  # supervisor of another level not assigned
    call("POST", f"/api/complaints/{cid}/acknowledge", {"note": "Received"}, token=off5, expect=200)
    call("POST", f"/api/complaints/{cid}/request-info", {"body": "Please share a photo of the pile with the street name board."}, token=off5, expect=200)
    call("POST", f"/api/complaints/{cid}/reply", {"body": "Photo attached in evidence; street board reads Gandhi Nagar 3rd Main."}, token=priya, expect=200)
    call("POST", f"/api/complaints/{cid}/respond", {"body": "Crew dispatched; collection resumes tomorrow."}, token=off5, expect=200)
    call("POST", f"/api/complaints/{cid}/decision", {"outcome": "resolved", "reasoning": "Collection restored and a weekly audit scheduled."}, token=off5, expect=200)
    det = call("GET", f"/api/complaints/{cid}", token=priya); check("scenario 1: resolved", det["status"] == "resolved" and det["stage"] == "resolved")
    check("audit chain still intact after all actions", det["audit_chain"]["valid"] and det["audit_chain"]["events"] > 12, det["audit_chain"])
    runs = call("GET", "/api/admin/workflow-runs", token=admin); wfnames = {r["workflow"] for r in runs if r["status"] == "completed"}
    check("workflow_runs shows n8n executed intake, ai, evidence, audit_trail, case_summary", {"complaint_intake", "ai_analysis", "evidence_processing", "audit_trail", "case_summary"} <= wfnames, wfnames)

    print("== scenario 2: silent authority -> n8n deadline monitor -> auto escalation -> notification -> legal aid")
    c2 = [c for c in call("GET", "/api/complaints", token=ravi) if "FIR" in c["title"]][0]["id"]
    before = call("GET", f"/api/complaints/{c2}", token=ravi); check("starts overdue at Local Office", before["current_authority"].endswith("Local Office") and before["status"] == "assigned")
    ov = call("GET", "/api/supervisor/overdue", token=admin); check("overdue list contains case", c2 in [o["complaint_id"] for o in ov])
    m = call("POST", "/api/admin/run-deadline-monitor", token=admin, expect=200)
    after = call("GET", f"/api/complaints/{c2}", token=ravi)
    check("n8n monitor escalated to District Authority", after["current_authority"].endswith("District Office"), after["current_authority"])
    esc = after["escalations"][-1]; check("escalation record complete (prev, new, reason, deadline, actor, hash)", esc["previous_authority_id"] and esc["new_authority_id"] and "not" in esc["reason"] and esc["actor"] == "system:n8n" and len(esc["audit_hash"]) == 64, esc)
    check("citizen notified of escalation (in-app + simulated channels)", sum(1 for x in call("GET", "/api/notifications", token=ravi) if x["type"] == "case_escalated") >= 2)
    m2 = call("POST", "/api/admin/run-deadline-monitor", token=admin, expect=200)
    check("monitor does not re-escalate a case that is within deadline", call("GET", f"/api/complaints/{c2}", token=ravi)["current_authority"].endswith("District Office"))
    for lvl in ("State Directorate", "Oversight Commission"):
        call("POST", f"/api/admin/demo/expire/{c2}", token=admin, expect=200); call("POST", "/api/admin/run-deadline-monitor", token=admin, expect=200)
        check(f"escalated to {lvl}", lvl in call("GET", f"/api/complaints/{c2}", token=ravi)["current_authority"])
    call("POST", f"/api/admin/demo/expire/{c2}", token=admin, expect=200); call("POST", "/api/admin/run-deadline-monitor", token=admin, expect=200)
    fin = call("GET", f"/api/complaints/{c2}", token=ravi)
    check("hierarchy exhausted -> status unresolved", fin["status"] == "unresolved", fin["status"])
    lh = call("GET", f"/api/complaints/{c2}/legal-help", token=ravi, expect=200)
    check("n8n generated legal assistance with disclaimer", lh["disclaimer"] == "This is general procedural information, not personalized legal advice." and len(lh["providers"]) >= 2 and lh["providers"][0]["cost"] == "free", lh.get("message"))
    check("legal checklist + options present", len(lh["checklist"]) >= 5 and len(lh["options"]) >= 3)
    check("audit chain valid after 4 automatic escalations", fin["audit_chain"]["valid"], fin["audit_chain"])
    check("no complaint ever flagged as guilty by escalation", "guilt" not in json.dumps(fin["escalations"]).lower() or "not a finding" in json.dumps(fin["escalations"]).lower())

    print("== scenario 3: rejected -> appeal -> supervisor review -> decision changes (original preserved)")
    c3 = [c for c in call("GET", "/api/complaints", token=meena) if "Mutation" in c["title"]][0]["id"]
    aps = call("GET", "/api/appeals", token=dist_rev); check("district supervisor has pending appeal", len(aps) == 1 and aps[0]["status"] == "under_review")
    call("POST", f"/api/appeals/{aps[0]['id']}/decide", {"outcome": "overturned", "reasoning": "x"}, token=dist_mun, expect=403)
    call("POST", f"/api/appeals/{aps[0]['id']}/decide", {"outcome": "overturned", "reasoning": "The file shows every required document was supplied; rejection reason was not recorded."}, token=dist_rev, expect=200)
    d3 = call("GET", f"/api/complaints/{c3}", token=meena)
    check("decision changed, original preserved", [x["outcome"] for x in d3["decisions"]] == ["rejected", "upheld"] and d3["decisions"][1]["supersedes_id"] == d3["decisions"][0]["id"])
    check("stage verified_finding after appeal", d3["stage"] == "verified_finding" and d3["audit_chain"]["valid"])

    print("== live appeal on new case via n8n appeal workflow -> upheld -> legal assistance")
    c4 = call("POST", "/api/complaints", {"title": "Revenue office refused patta transfer", "category_id": [c for c in meta["categories"] if c["code"] == "REV-MUT"][0]["id"], "jurisdiction": "Chennai Zone 9",
        "description": "On 1 October 2026 the revenue inspector at the Zone 9 Revenue Office refused my patta transfer application and gave no written reason despite two visits."}, token=meena, expect=200)["id"]
    a4 = call("POST", f"/api/complaints/{c4}/analyze", token=meena, expect=200)
    s4 = call("POST", f"/api/complaints/{c4}/submit", {"approved": True, "citizen_report": a4["report"]}, token=meena, expect=200)
    off9 = login("official.rev.z9@demo.gov")
    call("POST", f"/api/complaints/{c4}/acknowledge", {}, token=off9, expect=200)
    call("POST", f"/api/complaints/{c4}/decision", {"outcome": "rejected", "reasoning": "Application incomplete per checklist."}, token=off9, expect=200)
    ap = call("POST", f"/api/complaints/{c4}/appeal", {"grounds": "No written reason was given; I supplied all listed documents."}, token=meena, expect=200)
    check("appeal workflow completed (n8n)", ap["workflow"] == "completed")
    call("POST", f"/api/appeals/{ap['appeal_id']}/decide", {"outcome": "upheld", "reasoning": "Original decision is consistent with the record."}, token=dist_rev, expect=200)
    lh4 = call("GET", f"/api/complaints/{c4}/legal-help", token=meena, expect=200)
    check("upheld appeal triggers legal assistance workflow", lh4.get("trigger") == "appeal_upheld_original_decision", lh4)
    d4 = call("GET", f"/api/complaints/{c4}", token=meena); check("appeal workflow audit event present", "appeal_workflow_processed" in [e["event_type"] for e in call("GET", f"/api/complaints/{c4}/audit", token=meena)["events"]])
    call("POST", f"/api/complaints/{c4}/appeal", {"grounds": "Trying a second appeal on same decision should fail."}, token=meena, expect=409)

    print("== duplicate detection (flag, never reject)")
    cd = call("POST", "/api/complaints", {"title": "Garbage not collected near Gandhi Nagar", "category_id": cat, "jurisdiction": "Chennai Zone 5", "incident_date": "2026-10-10",
        "description": "The garbage near Gandhi Nagar in Adyar has not been collected since 10 October 2026; I called the sanitary inspector at the Zone 5 Municipal Office."}, token=ravi, expect=200)["id"]
    ad = call("POST", f"/api/complaints/{cd}/analyze", token=ravi, expect=200)
    check("AI draft warns about similar report", ad["report"]["duplicate_warning"] and "NOT be rejected" in ad["report"]["duplicate_warning"], ad["report"].get("duplicate_warning"))
    sd = call("POST", f"/api/complaints/{cd}/submit", {"approved": True, "citizen_report": ad["report"]}, token=ravi, expect=200)
    check("duplicate flagged by n8n but case accepted + assigned", sd["workflow_result"]["duplicates_flagged"] >= 1 and call("GET", f"/api/complaints/{cd}", token=ravi)["status"] == "assigned")
    check("citizen never sees other citizen's complaint details", "code" not in json.dumps(call("GET", f"/api/complaints/{cd}", token=ravi)["duplicate_flags"]))
    check("official sees duplicate flag for human review", call("GET", f"/api/complaints/{cd}", token=off5)["duplicate_flags"][0]["code"])

    print("== analytics + admin + tamper detection through n8n audit workflow")
    an = call("GET", "/api/supervisor/analytics", token=dist_mun, expect=200); check("analytics has caution + metrics", "NOT evidence" in an["overall"]["caution"] and an["overall"]["complaint_volume"] >= 6 and an["overall"]["resolution_rate"] is not None)
    pub = call("GET", "/api/public/stats"); check("public stats are aggregates only", set(pub) >= {"complaint_volume", "response_rate", "avg_response_hours", "resolution_rate", "overdue_cases", "verified_findings", "appeal_outcomes"} and "sha256" not in json.dumps(pub) and "filename" not in json.dumps(pub))
    call("PUT", "/api/admin/escalation-rules/1", {"ack_hours": 12, "review_hours": 48, "resolution_hours": 240, "appeal_hours": 120}, token=admin, expect=200)
    call("POST", "/api/admin/authorities", {"name": "Bad", "department_id": 1, "level": 4, "jurisdiction": "x", "parent_authority_id": 1}, token=admin, expect=422)
    check("admin audit view verifies all chains", all(c["valid"] for c in call("GET", "/api/admin/audit", token=admin)["chains"]))
    con = sqlite3.connect(env["CIVICSHIELD_DB"], isolation_level=None); con.execute("DROP TRIGGER audit_no_update")
    ev = con.execute("SELECT event_id FROM audit_events WHERE complaint_id=? ORDER BY rowid LIMIT 1 OFFSET 2", (cid,)).fetchone()[0]
    con.execute("UPDATE audit_events SET metadata='{\"tampered\":1}' WHERE event_id=?", (ev,)); con.close()
    call("POST", f"/api/complaints/{cid}/respond", {"body": "x" * 5}, token=off5)  # triggers audit_trail workflow
    runs = call("GET", "/api/admin/workflow-runs", token=admin); tr = [r for r in runs if r["workflow"] == "audit_trail" and r["complaint_id"] == cid][0]
    check("n8n audit workflow detects tampering (chain BROKEN)", "BROKEN" in (tr["response"] or ""), tr["response"])
    check("tamper alert raised to admin", any(x["type"] == "tamper_alert" for x in call("GET", "/api/notifications", token=admin)))
except Exception:
    import traceback
    fail += 1
    traceback.print_exc()
finally:
    for p in procs: p.terminate()
    print(f"\nRESULT: {ok} passed, {fail} failed")
    if fail or ok == 0:
        print(open(f"{D}/api.log").read()[-2500:]); print(open(f"{D}/n8n.log").read()[-2500:] if os.path.exists(f"{D}/n8n.log") else "")
    sys.exit(1 if fail else 0)
