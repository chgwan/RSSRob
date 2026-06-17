"""Twitter/X web session credential, stored as JSON.

RSSRob calls X's internal web GraphQL API with the same session a logged-in
browser uses. From a logged-in x.com session it needs two cookies: ``auth_token``
(the session) and ``ct0`` (the CSRF token, also sent as the ``x-csrf-token``
header). They are captured by the login flow (CLI ``twitter-login`` or the web
``/twitter/login`` page) by pasting the whole ``Cookie:`` header; this holds a
personal session secret, so the file is never committed (it lives under ``var/``).
"""

import json
import os
from dataclasses import asdict, dataclass
from typing import Optional

# Default location; overridable so tests and alternate deployments can relocate it.
DEFAULT_PATH = os.environ.get("RSSROB_TWITTER_CREDENTIAL", "./var/twitter_credential.json")

_FIELDS = ("auth_token", "csrf_token", "updated_at", "proxy")


@dataclass
class Credential:
    auth_token: str           # x.com 'auth_token' cookie (the session)
    csrf_token: str           # x.com 'ct0' cookie; also the x-csrf-token header
    updated_at: float         # epoch seconds when captured
    proxy: Optional[str] = None   # global proxy for the X transport (raw string)


def load(path: str = DEFAULT_PATH):
    """Return the stored ``Credential``, or ``None`` if absent/empty."""
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        raw = json.load(f) or {}
    if not raw or "auth_token" not in raw:
        return None
    return Credential(**{k: raw.get(k) for k in _FIELDS})


def save(path: str, cred: Credential) -> None:
    """Atomically write the credential JSON (creating parent dirs as needed)."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(asdict(cred), f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _parse_cookie_header(header: str) -> dict:
    """Parse a raw ``Cookie:`` header value into a {name: value} dict."""
    out = {}
    for part in (header or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def credential_from_cookie(cookie_header: str, now: float,
                           proxy: Optional[str] = None) -> Credential:
    """Build a ``Credential`` from a pasted x.com ``Cookie:`` header.

    Copy it from DevTools → Network → any x.com request → Request Headers →
    ``Cookie`` (``auth_token`` is HttpOnly, so ``document.cookie`` won't show it)."""
    cookies = _parse_cookie_header(cookie_header)
    auth_token = cookies.get("auth_token", "")
    csrf_token = cookies.get("ct0", "")
    if not auth_token or not csrf_token:
        raise ValueError("cookie must contain both auth_token and ct0 "
                         "(copy the full Cookie header from a logged-in x.com)")
    proxy = (proxy or "").strip() or None
    return Credential(auth_token=auth_token, csrf_token=csrf_token,
                      updated_at=now, proxy=proxy)
