PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS roles(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, description TEXT);
CREATE TABLE IF NOT EXISTS departments(id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, description TEXT);
CREATE TABLE IF NOT EXISTS authorities(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, department_id INTEGER NOT NULL REFERENCES departments(id),
  level INTEGER NOT NULL, level_name TEXT NOT NULL, jurisdiction TEXT NOT NULL,
  parent_authority_id INTEGER REFERENCES authorities(id), is_oversight INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, full_name TEXT NOT NULL, phone TEXT,
  role TEXT NOT NULL REFERENCES roles(name), authority_id INTEGER REFERENCES authorities(id), organisation TEXT,
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, department_id INTEGER NOT NULL REFERENCES departments(id));
CREATE TABLE IF NOT EXISTS escalation_rules(
  level INTEGER PRIMARY KEY, ack_hours REAL NOT NULL, review_hours REAL NOT NULL, resolution_hours REAL NOT NULL, appeal_hours REAL NOT NULL);
CREATE TABLE IF NOT EXISTS complaints(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE, citizen_id INTEGER NOT NULL REFERENCES users(id),
  category_id INTEGER NOT NULL REFERENCES categories(id), department_id INTEGER NOT NULL REFERENCES departments(id),
  title TEXT NOT NULL, original_description TEXT NOT NULL, original_hash TEXT NOT NULL,
  location_text TEXT, jurisdiction TEXT NOT NULL, incident_date TEXT,
  citizen_report TEXT, ai_analysis TEXT, ai_reviewed_at TEXT, duplicate_flags TEXT, triage TEXT,
  status TEXT NOT NULL DEFAULT 'draft', stage TEXT NOT NULL DEFAULT 'allegation',
  current_authority_id INTEGER REFERENCES authorities(id),
  ack_deadline TEXT, review_deadline TEXT, resolution_deadline TEXT, acknowledged_at TEXT,
  legal_share INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, submitted_at TEXT, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS evidence(
  id TEXT PRIMARY KEY, complaint_id INTEGER NOT NULL REFERENCES complaints(id), uploader_id INTEGER NOT NULL REFERENCES users(id),
  original_filename TEXT NOT NULL, stored_path TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL,
  sha256 TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_events(
  event_id TEXT PRIMARY KEY, complaint_id INTEGER NOT NULL, actor_id TEXT NOT NULL, timestamp TEXT NOT NULL,
  event_type TEXT NOT NULL, previous_hash TEXT NOT NULL, current_hash TEXT NOT NULL, metadata TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS status_history(
  id INTEGER PRIMARY KEY, complaint_id INTEGER NOT NULL REFERENCES complaints(id), from_status TEXT, to_status TEXT NOT NULL,
  stage TEXT, reason TEXT, actor TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS authority_transfers(
  id INTEGER PRIMARY KEY, complaint_id INTEGER NOT NULL REFERENCES complaints(id), from_authority_id INTEGER, to_authority_id INTEGER NOT NULL,
  reason TEXT NOT NULL, actor TEXT NOT NULL, audit_hash TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS escalations(
  id INTEGER PRIMARY KEY, complaint_id INTEGER NOT NULL REFERENCES complaints(id), previous_authority_id INTEGER NOT NULL,
  new_authority_id INTEGER, reason TEXT NOT NULL, deadline_type TEXT NOT NULL, trigger_deadline TEXT NOT NULL,
  actor TEXT NOT NULL, audit_hash TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS official_responses(
  id INTEGER PRIMARY KEY, complaint_id INTEGER NOT NULL REFERENCES complaints(id), authority_id INTEGER, author_id INTEGER NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL, body TEXT NOT NULL, corrects_id INTEGER REFERENCES official_responses(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions(
  id INTEGER PRIMARY KEY, complaint_id INTEGER NOT NULL REFERENCES complaints(id), authority_id INTEGER NOT NULL, author_id INTEGER NOT NULL REFERENCES users(id),
  outcome TEXT NOT NULL, reasoning TEXT NOT NULL, verified_finding INTEGER NOT NULL DEFAULT 0,
  supersedes_id INTEGER REFERENCES decisions(id), via_appeal_id INTEGER, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS appeals(
  id INTEGER PRIMARY KEY, complaint_id INTEGER NOT NULL REFERENCES complaints(id), decision_id INTEGER NOT NULL REFERENCES decisions(id),
  citizen_id INTEGER NOT NULL REFERENCES users(id), grounds TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'submitted',
  review_authority_id INTEGER REFERENCES authorities(id), deadline TEXT, outcome TEXT, outcome_reasoning TEXT, decided_by INTEGER,
  created_at TEXT NOT NULL, decided_at TEXT);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), complaint_id INTEGER, type TEXT NOT NULL, title TEXT NOT NULL,
  message TEXT NOT NULL, channel TEXT NOT NULL DEFAULT 'in_app', is_read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS legal_aid(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, org_type TEXT NOT NULL, services TEXT NOT NULL, category_codes TEXT NOT NULL DEFAULT '*',
  region TEXT NOT NULL, cost TEXT NOT NULL CHECK(cost IN ('free','low_cost','paid')), eligibility TEXT, contact TEXT, url TEXT);
CREATE TABLE IF NOT EXISTS legal_assistance(
  id INTEGER PRIMARY KEY, complaint_id INTEGER NOT NULL REFERENCES complaints(id), trigger_reason TEXT NOT NULL,
  summary TEXT NOT NULL, options TEXT NOT NULL, matches TEXT NOT NULL, checklist TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accountability_metrics(
  id INTEGER PRIMARY KEY, department_id INTEGER, total INTEGER, responded INTEGER, avg_response_hours REAL,
  resolved INTEGER, overdue INTEGER, verified_findings INTEGER, computed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workflow_runs(
  id INTEGER PRIMARY KEY, workflow TEXT NOT NULL, complaint_id INTEGER, status TEXT NOT NULL, request TEXT, response TEXT, error TEXT,
  attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE INDEX IF NOT EXISTS ix_complaints_citizen ON complaints(citizen_id);
CREATE INDEX IF NOT EXISTS ix_complaints_authority ON complaints(current_authority_id, status);
CREATE INDEX IF NOT EXISTS ix_complaints_deadlines ON complaints(status, ack_deadline, resolution_deadline);
CREATE INDEX IF NOT EXISTS ix_evidence_complaint ON evidence(complaint_id);
CREATE INDEX IF NOT EXISTS ix_audit_complaint ON audit_events(complaint_id);
CREATE INDEX IF NOT EXISTS ix_notif_user ON notifications(user_id, is_read);
CREATE INDEX IF NOT EXISTS ix_responses_complaint ON official_responses(complaint_id);
CREATE INDEX IF NOT EXISTS ix_appeals_review ON appeals(review_authority_id, status);
CREATE INDEX IF NOT EXISTS ix_wfruns_status ON workflow_runs(status);

-- Append-only / no-silent-removal guarantees enforced by the database itself.
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT,'audit_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT,'audit_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS complaint_no_delete BEFORE DELETE ON complaints BEGIN SELECT RAISE(ABORT,'complaints cannot be deleted'); END;
DROP TRIGGER IF EXISTS complaint_original_immutable;
CREATE TRIGGER complaint_original_immutable BEFORE UPDATE OF original_description, original_hash, citizen_id, code ON complaints
  WHEN (OLD.code IS NOT NULL AND NEW.code IS NOT OLD.code) OR NEW.citizen_id != OLD.citizen_id
    OR ((OLD.status != 'draft' OR NEW.status != 'draft') AND
      (NEW.original_description != OLD.original_description OR NEW.original_hash != OLD.original_hash))
  BEGIN SELECT RAISE(ABORT,'original complaint is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evidence_no_delete BEFORE DELETE ON evidence BEGIN SELECT RAISE(ABORT,'evidence cannot be removed'); END;
CREATE TRIGGER IF NOT EXISTS evidence_no_update BEFORE UPDATE ON evidence BEGIN SELECT RAISE(ABORT,'evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS decisions_no_update BEFORE UPDATE ON decisions BEGIN SELECT RAISE(ABORT,'decisions are immutable; add a superseding decision'); END;
CREATE TRIGGER IF NOT EXISTS decisions_no_delete BEFORE DELETE ON decisions BEGIN SELECT RAISE(ABORT,'decisions cannot be erased'); END;
CREATE TRIGGER IF NOT EXISTS responses_no_update BEFORE UPDATE ON official_responses BEGIN SELECT RAISE(ABORT,'responses are immutable; add a correction'); END;
CREATE TRIGGER IF NOT EXISTS responses_no_delete BEFORE DELETE ON official_responses BEGIN SELECT RAISE(ABORT,'responses cannot be erased'); END;
CREATE TRIGGER IF NOT EXISTS history_no_update BEFORE UPDATE ON status_history BEGIN SELECT RAISE(ABORT,'status_history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS history_no_delete BEFORE DELETE ON status_history BEGIN SELECT RAISE(ABORT,'status_history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS escalations_no_update BEFORE UPDATE ON escalations BEGIN SELECT RAISE(ABORT,'escalations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS escalations_no_delete BEFORE DELETE ON escalations BEGIN SELECT RAISE(ABORT,'escalations are append-only'); END;
