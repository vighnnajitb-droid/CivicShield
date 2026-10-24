import hashlib, hmac, os, re, time
import jwt
from . import config

_ITER = 210_000

def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, _ITER)
    return f"pbkdf2_sha256${_ITER}${salt.hex()}${dk.hex()}"

def verify_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, dk = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(calc.hex(), dk)
    except Exception:
        return False

def make_token(user_id: int, role: str) -> str:
    now = int(time.time())
    return jwt.encode({"sub": str(user_id), "role": role, "iat": now, "exp": now + config.JWT_TTL_MIN * 60},
                      config.JWT_SECRET, algorithm="HS256")

def read_token(token: str) -> dict:
    return jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])

def password_ok(pw: str) -> bool:
    return len(pw) >= 8 and re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw)

def mask_phone(p):
    return None if not p else "*" * max(0, len(p) - 4) + p[-4:]

def mask_email(e):
    if not e or "@" not in e: return e
    n, d = e.split("@", 1)
    return n[0] + "***@" + d

def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
