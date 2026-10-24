"""Demo data: configuration (hierarchy, rules, categories, legal aid), accounts, and the 3 demo scenarios."""
import os, struct, zlib
from datetime import timedelta
from . import core, db
from .security import hash_password

DEMO_PASSWORD = os.environ.get("SEED_PASSWORD", "Demo@12345")  # demo accounts only; override via env
DEPTS = [("MUN", "Municipal Services", "Sanitation, certificates, local permits"),
         ("POL", "Police", "Policing conduct and FIR handling"),
         ("REV", "Revenue & Land Records", "Land records, mutation, revenue services")]
CATS = {"MUN": [("MUN-DELAY", "Delay or bribe demand for certificates/permits"), ("MUN-SANIT", "Sanitation and road maintenance neglect")],
        "POL": [("POL-FIR", "Refusal to register FIR / police inaction"), ("POL-CONDUCT", "Police misconduct or harassment")],
        "REV": [("REV-MUT", "Land mutation / records delay"), ("REV-BRIBE", "Bribe demand in revenue services")]}
LEVELS = [(1, "Local Office"), (2, "District Authority"), (3, "State Authority"), (4, "Oversight Authority")]
LEGAL_AID = [
    ("District Legal Services Authority (DLSA) Chennai", "Statutory legal services", "Free legal advice and representation for eligible persons", "*", "Chennai", "free", "Women, children, SC/ST, low-income (see NALSA criteria)", "Contact via district court complex (simulated)", "https://nalsa.gov.in"),
    ("Tamil Nadu State Legal Services Authority", "Statutory legal services", "Free legal aid, Lok Adalat, legal literacy", "*", "Tamil Nadu", "free", "As per Legal Services Authorities Act", "Simulated: 044-0000-0000", "https://tnlegalservices.tn.gov.in"),
    ("Citizens' Rights Law Clinic (University)", "Law school clinic", "Drafting RTI applications, writ petition assessments", "MUN-DELAY,REV-MUT,REV-BRIBE", "Chennai", "free", "Open to all, limited slots", "clinic@example.org (simulated)", "https://example.org/clinic"),
    ("Police Accountability Legal Network", "NGO", "Advice on police-conduct complaints and oversight bodies", "POL-FIR,POL-CONDUCT", "Tamil Nadu", "free", "Individuals facing police inaction or misconduct", "help@example.org (simulated)", "https://example.org/pal"),
    ("Chennai Public Interest Lawyers Collective", "Lawyer collective", "Subsidised representation for writ petitions", "*", "Chennai", "low_cost", "Sliding scale by income", "collective@example.org (simulated)", "https://example.org/cpil"),
    ("Private Advocate Referral Service", "Bar association referral", "Referral to private advocates", "*", "Tamil Nadu", "paid", "Fees apply", "referral@example.org (simulated)", "https://example.org/bar")]

def _png():
    def ch(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + ch(b"IEND", b"")

def seed_config(conn):
    now = core.iso(core.utcnow())
    for r, d in [("citizen", "Reports complaints"), ("official", "Government official"), ("supervisor", "Supervisor / oversight authority"),
                 ("legal_aid", "Legal aid provider"), ("admin", "Administrator")]:
        conn.execute("INSERT OR IGNORE INTO roles(name,description) VALUES(?,?)", (r, d))
    for code, name, desc in DEPTS:
        conn.execute("INSERT OR IGNORE INTO departments(code,name,description) VALUES(?,?,?)", (code, name, desc))
    for lvl, (ack, rev, res, app) in zip(range(1, 5), [(24, 72, 720, 168), (24, 72, 480, 168), (48, 120, 480, 168), (48, 120, 360, 168)]):
        conn.execute("INSERT OR IGNORE INTO escalation_rules(level,ack_hours,review_hours,resolution_hours,appeal_hours) VALUES(?,?,?,?,?)", (lvl, ack, rev, res, app))
    for code, name, _ in DEPTS:
        did = conn.execute("SELECT id FROM departments WHERE code=?", (code,)).fetchone()[0]
        for c, n in CATS[code]: conn.execute("INSERT OR IGNORE INTO categories(code,name,department_id) VALUES(?,?,?)", (c, n, did))
        ids = {}
        # hierarchy is data, not code: change parent_authority_id to reconfigure escalation
        ids[4] = conn.execute("INSERT INTO authorities(name,department_id,level,level_name,jurisdiction,parent_authority_id,is_oversight) VALUES(?,?,?,?,?,NULL,1)",
                              (f"{name} - State Oversight Commission", did, 4, "Oversight Authority", "Tamil Nadu Oversight")).lastrowid
        ids[3] = conn.execute("INSERT INTO authorities(name,department_id,level,level_name,jurisdiction,parent_authority_id) VALUES(?,?,?,?,?,?)",
                              (f"{name} - State Directorate", did, 3, "State Authority", "Tamil Nadu", ids[4])).lastrowid
        ids[2] = conn.execute("INSERT INTO authorities(name,department_id,level,level_name,jurisdiction,parent_authority_id) VALUES(?,?,?,?,?,?)",
                              (f"{name} - Chennai District Office", did, 2, "District Authority", "Chennai District", ids[3])).lastrowid
        for z in ("Zone 5", "Zone 9"):
            conn.execute("INSERT INTO authorities(name,department_id,level,level_name,jurisdiction,parent_authority_id) VALUES(?,?,?,?,?,?)",
                         (f"{name} - {z} Local Office", did, 1, "Local Office", f"Chennai {z}", ids[2]))
    for row in LEGAL_AID:
        conn.execute("INSERT INTO legal_aid(name,org_type,services,category_codes,region,cost,eligibility,contact,url) VALUES(?,?,?,?,?,?,?,?,?)", row)

def _user(conn, email, name, role, authority_id=None, phone=None, org=None):
    return conn.execute("INSERT INTO users(email,password_hash,full_name,phone,role,authority_id,organisation,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (email, hash_password(DEMO_PASSWORD), name, phone, role, authority_id, org, core.iso(core.utcnow()))).lastrowid

def seed_users(conn):
    _user(conn, "admin@demo.in", "Asha Admin", "admin")
    _user(conn, "priya@demo.in", "Priya Raman", "citizen", phone="9840011122")
    _user(conn, "ravi@demo.in", "Ravi Kumar", "citizen", phone="9840033344")
    _user(conn, "meena@demo.in", "Meena Subramanian", "citizen", phone="9840055566")
    _user(conn, "legalaid@demo.in", "Lakshmi Advocate (DLSA volunteer)", "legal_aid", org="DLSA Chennai")
    for a in conn.execute("SELECT a.*, d.code FROM authorities a JOIN departments d ON d.id=a.department_id ORDER BY a.id").fetchall():
        slug = {1: "official", 2: "district", 3: "state", 4: "oversight"}[a["level"]]
        z = ("z5" if "Zone 5" in a["jurisdiction"] else "z9") if a["level"] == 1 else ""
        email = f"{slug}.{a['code'].lower()}{('.' + z) if z else ''}@demo.gov"
        _user(conn, email, f"{a['level_name']} Officer ({a['code']}{' ' + z if z else ''})", "official" if a["level"] == 1 else "supervisor", a["id"])

def U(conn, email): return dict(conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone())
def cat(conn, code): return conn.execute("SELECT id FROM categories WHERE code=?", (code,)).fetchone()[0]

def _file(conn, cid, uid, name, data): return core.register_evidence(conn, cid, uid, name, data)

def seed_scenarios(conn):
    # ---- Scenario 1: successful resolution (Municipal, Zone 5)
    p = U(conn, "priya@demo.in"); off = U(conn, "official.mun.z5@demo.gov")
    c1 = core.create_draft(conn, p["id"], {"title": "Clerk demanded money for birth certificate", "category_id": cat(conn, "MUN-DELAY"), "jurisdiction": "Chennai Zone 5",
        "location_text": "Zone 5 Ward Office, Adyar", "incident_date": "2026-10-05",
        "description": "On 5 October 2026 at 11:00 am a clerk at the Zone 5 Ward Office near Adyar Bus Depot told me my daughter's birth certificate would be delayed unless I paid Rs 500. I refused and was told to come back next week. I have the receipt of my application."})
    _file(conn, c1, p["id"], "application_receipt.png", _png())
    core.store_analysis(conn, c1, ai_analysis(conn, c1))
    core.submit_complaint(conn, c1, p["id"], True, ai_analysis(conn, c1)["report"]); core.store_duplicate_flags(conn, c1, core.find_duplicates(conn, c1)); core.assign_authority(conn, c1)
    core.acknowledge(conn, c1, off, "Received. Inquiry has begun.")
    core.respond(conn, c1, off, "Counter staff were briefed; the certificate was issued the same day and a written reminder on fee rules was circulated.")
    core.decide(conn, c1, off, "resolved", "Certificate issued without payment; notice issued to counter staff. Matter closed.")
    # ---- Scenario 2: authority silent -> left OVERDUE so the n8n Deadline Monitor performs the escalation live
    r = U(conn, "ravi@demo.in")
    c2 = core.create_draft(conn, r["id"], {"title": "Police refused to register FIR for phone theft", "category_id": cat(conn, "POL-FIR"), "jurisdiction": "Chennai Zone 9",
        "location_text": "Anna Nagar Police Station", "incident_date": "2026-10-02",
        "description": "On 2 October 2026 at 6 pm my phone was stolen near Anna Nagar Tower. At the Anna Nagar Police Station an officer refused to register an FIR and asked me to come back later. I went three times. I have CCTV screenshots and my purchase invoice."})
    _file(conn, c2, r["id"], "cctv_screenshot.png", _png())
    core.store_analysis(conn, c2, ai_analysis(conn, c2))
    core.submit_complaint(conn, c2, r["id"], True, ai_analysis(conn, c2)["report"]); core.store_duplicate_flags(conn, c2, core.find_duplicates(conn, c2)); core.assign_authority(conn, c2)
    past = core.iso(core.utcnow() - timedelta(hours=30))
    conn.execute("UPDATE complaints SET ack_deadline=? WHERE id=?", (past, c2))  # simulate: 30h without acknowledgement
    # ---- Scenario 3: rejection -> appeal under review by district supervisor (decide live)
    m = U(conn, "meena@demo.in"); off3 = U(conn, "official.rev.z5@demo.gov")
    c3 = core.create_draft(conn, m["id"], {"title": "Mutation of land records refused without reason", "category_id": cat(conn, "REV-MUT"), "jurisdiction": "Chennai Zone 5",
        "location_text": "Zone 5 Revenue Office", "incident_date": "2026-09-20",
        "description": "On 20 September 2026 I applied for mutation of my late father's plot at the Zone 5 Revenue Office. The revenue inspector rejected it saying documents were incomplete but would not tell me which. I submitted the legal heir certificate and sale deed copies."})
    _file(conn, c3, m["id"], "legal_heir_certificate.pdf", b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF")
    core.store_analysis(conn, c3, ai_analysis(conn, c3))
    core.submit_complaint(conn, c3, m["id"], True, ai_analysis(conn, c3)["report"]); core.store_duplicate_flags(conn, c3, core.find_duplicates(conn, c3)); core.assign_authority(conn, c3)
    core.acknowledge(conn, c3, off3); core.decide(conn, c3, off3, "rejected", "Complaint closed: documents on file were treated as incomplete under checklist item 4.")
    core.create_appeal(conn, c3, m["id"], "The decision does not say which document is missing. I submitted the legal heir certificate and sale deed; please review the file.")
    return c1, c2, c3

def ai_analysis(conn, cid):
    """Seed-time stand-in for the n8n AI workflow result (same service the workflow calls)."""
    from . import ai_mock
    i = core.draft_analysis_input(conn, cid)
    return ai_mock.analyze(i["description"], i["evidence"], i["category"], i["location"])

def seed_all(conn):
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]: return False
    with_tx = db.transactional(lambda c: (seed_config(c), seed_users(c)))
    with_tx(conn); seed_scenarios(conn); return True

if __name__ == "__main__":
    c = db.connect(); db.init(c); print("seeded" if seed_all(c) else "already seeded")
