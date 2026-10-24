import os, secrets, logging
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = os.environ.get("CIVICSHIELD_DB", str(ROOT / "data" / "civicshield.db"))
EVIDENCE_DIR = Path(os.environ.get("CIVICSHIELD_EVIDENCE_DIR", str(ROOT / "data" / "evidence")))
N8N_WEBHOOK_BASE = os.environ.get("N8N_WEBHOOK_BASE", "http://localhost:5678/webhook").rstrip("/")
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "")
JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_TTL_MIN = int(os.environ.get("JWT_TTL_MIN", "480"))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "5")) * 1024 * 1024
ALLOW_DEMO_ENDPOINTS = os.environ.get("ALLOW_DEMO_ENDPOINTS", "true").lower() == "true"
if not JWT_SECRET:  # never hard-code secrets: fall back to an ephemeral random one
    JWT_SECRET = secrets.token_urlsafe(48)
    logging.warning("JWT_SECRET not set - using an ephemeral secret; sessions reset on restart.")
if not INTERNAL_API_KEY:
    INTERNAL_API_KEY = secrets.token_urlsafe(32)
    logging.warning("INTERNAL_API_KEY not set - n8n cannot call the backend until you set the same key in both.")
