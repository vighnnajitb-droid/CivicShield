import os, sys, tempfile, unittest, sqlite3
from datetime import timedelta
d = tempfile.mkdtemp()
os.environ["CIVICSHIELD_DB"] = os.path.join(d, "t.db"); os.environ["CIVICSHIELD_EVIDENCE_DIR"] = os.path.join(d, "ev")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app import core, db, seed, security, ai_mock, config
from app.core import CoreError

class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = db.connect(); db.init(cls.conn); seed.seed_all(cls.conn); cls.c1, cls.c2, cls.c3 = 1, 2, 3
    conn = None
    def u(self, e): return seed.U(self.conn, e)

    def test_01_password_hashing_and_jwt(self):
        h = security.hash_password("Secret123"); self.assertTrue(security.verify_password("Secret123", h)); self.assertFalse(security.verify_password("x", h))
        t = security.make_token(5, "citizen"); self.assertEqual(security.read_token(t)["sub"], "5")
        self.assertEqual(security.mask_phone("9840011122"), "******1122")

    def test_02_audit_chain_valid_for_all_seeded_cases(self):
        for cid in (1, 2, 3):
            v = core.audit_verify(self.conn, cid); self.assertTrue(v["valid"], v); self.assertGreater(v["events"], 5)
        evs = core.audit_list(self.conn, 1); self.assertEqual(evs[0]["previous_hash"], core.GENESIS)
        for a, b in zip(evs, evs[1:]): self.assertEqual(b["previous_hash"], a["current_hash"])

    def test_03_db_blocks_tampering_and_deletion(self):
        for sql in ("UPDATE audit_events SET event_type='x'", "DELETE FROM audit_events", "DELETE FROM complaints", "DELETE FROM evidence",
                    "UPDATE complaints SET original_description='changed' WHERE id=1", "UPDATE decisions SET outcome='upheld'", "DELETE FROM decisions",
                    "DELETE FROM official_responses", "UPDATE evidence SET sha256='0'"):
            with self.assertRaises(sqlite3.DatabaseError, msg=sql): self.conn.execute(sql)

    def test_04_tamper_detection(self):  # simulate an attacker with raw DB access who bypasses triggers on a COPY
        import shutil; p2 = os.path.join(d, "copy.db"); self.conn.execute("PRAGMA wal_checkpoint(FULL)"); shutil.copy(config.DB_PATH, p2)
        c = db.connect(p2); c.execute("DROP TRIGGER audit_no_update")
        row = c.execute("SELECT event_id FROM audit_events WHERE complaint_id=1 ORDER BY rowid LIMIT 1 OFFSET 2").fetchone()[0]
        c.execute("UPDATE audit_events SET metadata='{\"forged\":true}' WHERE event_id=?", (row,))
        v = core.audit_verify(c, 1); self.assertFalse(v["valid"]); self.assertEqual(v["broken_at"], row)

    def test_05_submission_requires_ai_review_and_approval(self):
        p = self.u("priya@demo.in")
        cid = core.create_draft(self.conn, p["id"], {"title": "Pothole not repaired", "category_id": seed.cat(self.conn, "MUN-SANIT"), "jurisdiction": "Chennai Zone 5", "description": "A large pothole near the Adyar signal has been reported but not repaired for two months."})
        with self.assertRaises(CoreError): core.submit_complaint(self.conn, cid, p["id"], True, {"incident_summary": "x"})  # no AI analysis yet
        a = core.store_analysis(self.conn, cid, seed.ai_analysis(self.conn, cid))
        self.assertIn("requires human review", a["label"])
        with self.assertRaises(CoreError): core.submit_complaint(self.conn, cid, p["id"], False, a["report"])  # not approved
        with self.assertRaises(CoreError): core.submit_complaint(self.conn, cid, self.u("ravi@demo.in")["id"], True, a["report"])  # not owner
        rep = dict(a["report"]); rep["incident_summary"] += " (edited by citizen)"
        r = core.submit_complaint(self.conn, cid, p["id"], True, rep); self.assertRegex(r["code"], r"^CS-\d{4}-\d{6}$")
        ev = [e["event_type"] for e in core.audit_list(self.conn, cid)]
        self.assertIn("citizen_reviewed_ai_report", ev); self.assertTrue(core.audit_list(self.conn, cid)[-2]["metadata"]["edited_by_citizen"])
        self.assertEqual(core.get_complaint(self.conn, cid)["original_description"][:6], "A larg")  # original preserved
        with self.assertRaises(CoreError): core.submit_complaint(self.conn, cid, p["id"], True, rep)  # double submit
        TestState.pothole = cid

    def test_06_evidence_validation_and_hash(self):
        p = self.u("priya@demo.in"); cid = TestState.pothole
        with self.assertRaises(CoreError): core.register_evidence(self.conn, cid, p["id"], "evil.exe", b"MZ....")
        with self.assertRaises(CoreError): core.register_evidence(self.conn, cid, p["id"], "fake.png", b"not a png")
        with self.assertRaises(CoreError): core.register_evidence(self.conn, cid, p["id"], "big.pdf", b"%PDF-" + b"0" * (config.MAX_UPLOAD_BYTES + 1))
        with self.assertRaises(CoreError): core.register_evidence(self.conn, cid, p["id"], "p.png", seed._png(), claimed_sha="0" * 64)
        r = core.register_evidence(self.conn, cid, p["id"], "../../etc/p a$s.png", seed._png())
        self.assertEqual(len(r["sha256"]), 64); self.assertNotIn("/", r["filename"]); self.assertTrue(core.verify_evidence_integrity(self.conn, r["evidence_id"])["intact"])
        self.assertEqual(core.audit_list(self.conn, cid)[-1]["event_type"], "evidence_added")

    def test_07_authority_assignment_and_deadlines(self):
        cid = TestState.pothole; r = core.assign_authority(self.conn, cid)
        self.assertEqual(r["authority"]["level"], 1); self.assertIn("Municipal", r["authority"]["name"]); self.assertIn("Zone 5", r["authority"]["name"])
        self.assertTrue(r["deadlines"]["ack_deadline"] < r["deadlines"]["resolution_deadline"])
        with self.assertRaises(CoreError): core.assign_authority(self.conn, cid)  # already assigned

    def test_08_authorization(self):
        cid = TestState.pothole; wrong = self.u("official.pol.z5@demo.gov"); right = self.u("official.mun.z9@demo.gov")
        for u in (wrong, right, self.u("priya@demo.in")):
            with self.assertRaises(CoreError) as e: core.acknowledge(self.conn, cid, u)
            self.assertEqual(e.exception.code, 403)

    def test_09_scenario1_resolved(self):
        c = core.get_complaint(self.conn, 1); self.assertEqual((c["status"], c["stage"]), ("resolved", "resolved"))
        n = [x["type"] for x in db.rows(self.conn.execute("SELECT type FROM notifications WHERE complaint_id=1"))]
        for t in ("complaint_submitted", "authority_assigned", "complaint_acknowledged", "response_received", "decision_made"): self.assertIn(t, n)

    def test_10_scenario2_overdue_detected_and_escalated_up_the_hierarchy(self):
        od = core.find_overdue(self.conn); self.assertEqual([o["complaint_id"] for o in od if o["complaint_id"] == 2], [2])
        self.assertEqual(od[0]["deadline_type"], "acknowledgement")
        self.assertNotIn(1, [o["complaint_id"] for o in od]); self.assertNotIn(3, [o["complaint_id"] for o in od])
        with self.assertRaises(CoreError): core.escalate(self.conn, 1)  # no arbitrary escalation of non-overdue
        path = []
        for _ in range(3):
            c = core.get_complaint(self.conn, 2); path.append(core.get_authority(self.conn, c["current_authority_id"])["level"])
            r = core.escalate(self.conn, 2); self.assertFalse(r["exhausted"]); self.assertNotIn("guilt", r["reason"].lower().replace("not a finding", ""))
            self.conn.execute("UPDATE complaints SET ack_deadline=? WHERE id=2", (core.iso(core.utcnow() - timedelta(hours=1)),))
        self.assertEqual(path, [1, 2, 3])
        r = core.escalate(self.conn, 2); self.assertTrue(r["exhausted"])  # oversight overdue -> end of configured hierarchy
        c = core.get_complaint(self.conn, 2); self.assertEqual(c["status"], "unresolved")
        esc = db.rows(self.conn.execute("SELECT * FROM escalations WHERE complaint_id=2 ORDER BY id")); self.assertEqual(len(esc), 4)
        self.assertEqual(esc[0]["audit_hash"], [e for e in core.audit_list(self.conn, 2) if e["event_type"] == "case_escalated"][0]["current_hash"])
        self.assertTrue(core.audit_verify(self.conn, 2)["valid"])
        types = [x["type"] for x in db.rows(self.conn.execute("SELECT type FROM notifications WHERE complaint_id=2"))]; self.assertIn("case_escalated", types)

    def test_11_legal_assistance_after_exhaustion(self):
        r = core.legal_assistance(self.conn, 2, "escalation_exhausted")
        self.assertEqual(r["disclaimer"], "This is general procedural information, not personalized legal advice.")
        costs = [p["cost"] for p in r["providers"]]; self.assertEqual(costs, sorted(costs, key=["free", "low_cost", "paid"].index))
        self.assertTrue(all(p["category_codes"] in ("*",) or "POL-FIR" in p["category_codes"] for p in r["providers"]))
        self.assertGreaterEqual(len(r["checklist"]), 5); self.assertEqual(r["summary"]["escalations"][-1]["to"], None)

    def test_12_scenario3_appeal_overturn_preserves_original(self):
        ap = db.one(self.conn.execute("SELECT * FROM appeals WHERE complaint_id=3")); self.assertEqual(ap["status"], "under_review")
        review = core.get_authority(self.conn, ap["review_authority_id"]); self.assertEqual(review["level"], 2)
        with self.assertRaises(CoreError): core.decide_appeal(self.conn, ap["id"], self.u("official.rev.z5@demo.gov"), "overturned", "I want to")
        with self.assertRaises(CoreError): core.decide_appeal(self.conn, ap["id"], self.u("district.mun@demo.gov"), "overturned", "wrong department")
        r = core.decide_appeal(self.conn, ap["id"], self.u("district.rev@demo.gov"), "overturned", "File shows all required documents were supplied.")
        ds = db.rows(self.conn.execute("SELECT * FROM decisions WHERE complaint_id=3 ORDER BY id"))
        self.assertEqual([x["outcome"] for x in ds], ["rejected", "upheld"]); self.assertEqual(ds[1]["supersedes_id"], ds[0]["id"])
        self.assertEqual(core.get_complaint(self.conn, 3)["stage"], "verified_finding"); self.assertTrue(core.audit_verify(self.conn, 3)["valid"])
        with self.assertRaises(CoreError): core.decide_appeal(self.conn, ap["id"], self.u("district.rev@demo.gov"), "upheld", "again again again")

    def test_13_duplicate_detection_flags_not_rejects(self):
        p = self.u("ravi@demo.in")
        cid = core.create_draft(self.conn, p["id"], {"title": "Police refused FIR for stolen phone", "category_id": seed.cat(self.conn, "POL-FIR"), "jurisdiction": "Chennai Zone 9", "incident_date": "2026-10-02",
            "description": "My phone was stolen near Anna Nagar Tower and the officer at Anna Nagar Police Station refused to register an FIR for the stolen phone."})
        core.store_analysis(self.conn, cid, seed.ai_analysis(self.conn, cid)); flags = core.find_duplicates(self.conn, cid)
        self.assertTrue(flags and flags[0]["code"]); self.assertIn("NOT rejected", flags[0]["action"])
        core.submit_complaint(self.conn, cid, p["id"], True, seed.ai_analysis(self.conn, cid)["report"]); self.assertEqual(core.get_complaint(self.conn, cid)["status"], "submitted")

    def test_14_info_request_pauses_clock_and_correction_flow(self):
        cid = TestState.pothole; off = self.u("official.mun.z5@demo.gov")
        core.request_info(self.conn, cid, off, "Please share the exact pole number."); self.assertEqual(core.get_complaint(self.conn, cid)["status"], "info_requested")
        self.conn.execute("UPDATE complaints SET ack_deadline='2000-01-01T00:00:00Z', resolution_deadline='2000-01-01T00:00:00Z' WHERE id=?", (cid,))
        self.assertNotIn(cid, [o["complaint_id"] for o in core.find_overdue(self.conn)])
        core.citizen_reply(self.conn, cid, self.u("priya@demo.in")["id"], "Pole 14B near the signal.")
        core.respond(self.conn, cid, off, "Work order raised.")
        rid = db.one(self.conn.execute("SELECT id FROM official_responses WHERE kind='response' AND complaint_id=?", (cid,)))["id"]
        core.respond(self.conn, cid, off, "Correction: work order number is WO-77.", "correction", rid)
        with self.assertRaises(sqlite3.DatabaseError): self.conn.execute("UPDATE official_responses SET body='x' WHERE id=?", (rid,))

    def test_15_ai_never_asserts_guilt_and_metrics_caution(self):
        a = ai_mock.analyze("The officer is guilty and a criminal who was proven corrupt.")
        self.assertNotRegex(str(a), r"(?i)\bguilty\b|\bcriminal\b"); self.assertEqual(a["label"], "AI-assisted analysis \u2014 requires human review.")
        m = core.metrics(self.conn); self.assertIn("NOT evidence of misconduct", m["caution"]); self.assertGreater(m["complaint_volume"], 3)
        self.assertEqual(set(core.STAGES), {"allegation", "under_review", "investigation", "verified_finding", "rejected", "resolved", "appealed"})
        core.snapshot_metrics(self.conn)

    def test_16_draft_edits_and_soft_delete_preserve_audit_and_evidence(self):
        p = self.u("priya@demo.in"); wrong = self.u("ravi@demo.in")
        payload = {"title": "Water service request", "category_id": seed.cat(self.conn, "MUN-SANIT"), "jurisdiction": "Chennai Zone 5",
                   "description": "The water supply interruption near the community hall has continued for several days.", "location_text": "Old Road"}
        cid = core.create_draft(self.conn, p["id"], payload)
        updated = {**payload, "title": "Water supply interruption", "description": "The water supply interruption near the community hall has continued for several days without a repair update.", "location_text": "New Road"}
        core.update_draft(self.conn, cid, p["id"], updated)
        draft = core.get_complaint(self.conn, cid)
        self.assertEqual((draft["title"], draft["location_text"], draft["original_description"]), (updated["title"], "New Road", updated["description"]))
        self.assertEqual(draft["original_hash"], security.sha256_hex(updated["description"].encode()))
        with self.assertRaises(CoreError) as e: core.delete_draft(self.conn, cid, wrong["id"])
        self.assertEqual(e.exception.code, 403)
        core.register_evidence(self.conn, cid, p["id"], "draft-proof.png", seed._png())
        self.assertEqual(core.delete_draft(self.conn, cid, p["id"]), {"deleted": True})
        self.assertTrue(core.get_complaint(self.conn, cid)["deleted_at"])
        self.assertEqual(len(core.list_evidence(self.conn, cid)), 1)
        self.assertTrue(core.audit_verify(self.conn, cid)["valid"])
        kinds = [e["event_type"] for e in core.audit_list(self.conn, cid)]
        self.assertIn("draft_updated", kinds); self.assertIn("draft_deleted", kinds); self.assertIn("evidence_added", kinds)
        with self.assertRaises(CoreError) as e: core.delete_draft(self.conn, cid, p["id"])
        self.assertEqual(e.exception.code, 404)
        with self.assertRaises(CoreError) as e: core.delete_draft(self.conn, 1, p["id"])
        self.assertEqual(e.exception.code, 409)

class TestState: pothole = None
if __name__ == "__main__":
    unittest.main(verbosity=2)
