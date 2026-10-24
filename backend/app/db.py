import sqlite3
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from . import config

def connect(path=None):
    p = path or config.DB_PATH
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, isolation_level=None, timeout=30)  # explicit transactions
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    return c

def init(conn):
    conn.executescript((Path(__file__).parent / "schema.sql").read_text())
    columns = {r[1] for r in conn.execute("PRAGMA table_info(complaints)")}
    if "deleted_at" not in columns:
        conn.execute("ALTER TABLE complaints ADD COLUMN deleted_at TEXT")

@contextmanager
def get_conn():
    c = connect()
    try:
        yield c
    finally:
        c.close()

def transactional(fn):
    """Run fn inside BEGIN IMMEDIATE unless a transaction is already open (nesting-safe)."""
    @wraps(fn)
    def wrapper(conn, *a, **k):
        if conn.in_transaction:
            return fn(conn, *a, **k)
        conn.execute("BEGIN IMMEDIATE")
        try:
            r = fn(conn, *a, **k)
            conn.execute("COMMIT")
            return r
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return wrapper

def rows(cur): return [dict(r) for r in cur.fetchall()]
def one(cur):
    r = cur.fetchone()
    return dict(r) if r else None
