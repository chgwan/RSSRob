"""Admin login credential for the management web UI, stored as JSON.

The webapp (web/webapp.py) is otherwise unauthenticated. When this credential
exists it gates the whole UI behind a username + password. Only a salted PBKDF2
hash of the password is stored — never the plaintext — plus a random key used to
sign the session cookie. The file holds a personal secret, so it lives under
var/ and is never committed. Hashing uses the standard library only (no Werkzeug)
so the core CLI, which doesn't install Flask, can write it too.
"""

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import asdict, dataclass
from typing import Optional

# Default location; overridable so tests and alternate deployments can relocate it.
DEFAULT_PATH = os.environ.get("RSSROB_ADMIN_CREDENTIAL", "./var/admin.json")

_FIELDS = ("username", "password_hash", "secret_key", "updated_at")
_ALGO = "pbkdf2_sha256"
_ITERATIONS = 240000


@dataclass
class AdminCredential:
    username: str
    password_hash: str        # "pbkdf2_sha256$<iters>$<salt_hex>$<hash_hex>"
    secret_key: str           # random hex; signs the Flask session cookie
    updated_at: float         # epoch seconds when set


def hash_password(password: str, *, iterations: int = _ITERATIONS,
                  salt: Optional[bytes] = None) -> str:
    """Return a self-describing PBKDF2-SHA256 hash string for ``password``."""
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGO}${iterations}${salt.hex()}${dk.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of ``password`` against an encoded hash string."""
    try:
        algo, iters, salt_hex, hash_hex = encoded.split("$")
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(iters))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


def create(username: str, password: str, now: float,
           secret_key: Optional[str] = None) -> AdminCredential:
    """Build a credential: hash the password, keep or generate the session key."""
    username = (username or "").strip()
    if not username:
        raise ValueError("username must not be empty")
    if not password:
        raise ValueError("password must not be empty")
    return AdminCredential(
        username=username,
        password_hash=hash_password(password),
        secret_key=secret_key or secrets.token_hex(32),
        updated_at=now,
    )


def verify(cred: Optional["AdminCredential"], username: str, password: str) -> bool:
    """True iff ``username`` matches and ``password`` checks against the hash."""
    if cred is None:
        return False
    user_ok = (username or "").strip() == cred.username
    pass_ok = _verify_password(password or "", cred.password_hash)
    return user_ok and pass_ok


def load(path: str = DEFAULT_PATH) -> Optional["AdminCredential"]:
    """Return the stored credential, or ``None`` if absent/empty/malformed."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f) or {}
    except (OSError, ValueError):
        return None
    if not raw or "password_hash" not in raw:
        return None
    return AdminCredential(**{k: raw.get(k) for k in _FIELDS})


def save(path: str, cred: "AdminCredential") -> None:
    """Atomically write the credential JSON (creating parent dirs as needed)."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(asdict(cred), f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
