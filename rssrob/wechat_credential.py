"""WeChat 公众号平台 (mp.weixin.qq.com) session credential, stored as JSON.

The credential is what RSSRob needs to call the 公众号平台 article APIs
(searchbiz / appmsg): the browser ``cookie`` from a logged-in mp.weixin.qq.com
session plus the ``token`` shown in the post-login URL. It is captured by the
login flow (CLI ``wechat-login`` or the web ``/wechat/login`` page) and holds a
personal session secret, so the file is never committed (see ``.gitignore``).
"""

import json
import os
from dataclasses import asdict, dataclass

# Default location; overridable so tests and alternate deployments can relocate it.
DEFAULT_PATH = os.environ.get("RSSROB_WECHAT_CREDENTIAL", "./var/wechat_credential.json")


@dataclass
class Credential:
    cookie: str             # full Cookie header for mp.weixin.qq.com
    token: str              # mp-platform token (the token=... in the post-login URL)
    updated_at: float       # epoch seconds when captured


def load(path: str = DEFAULT_PATH):
    """Return the stored ``Credential``, or ``None`` if absent/empty."""
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        raw = json.load(f) or {}
    if not raw or "cookie" not in raw:
        return None
    return Credential(**{k: raw.get(k) for k in ("cookie", "token", "updated_at")})


def save(path: str, cred: Credential) -> None:
    """Atomically write the credential JSON (creating parent dirs as needed)."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(asdict(cred), f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def credential_from_login(cookie: str, token: str, now: float) -> Credential:
    """Build a ``Credential`` from a pasted mp.weixin.qq.com cookie + token.

    Get both after logging in to https://mp.weixin.qq.com/: the ``token`` is the
    number in the address bar (``...&token=123456789``), and the ``cookie`` comes
    from DevTools → Application → Cookies (or ``document.cookie``)."""
    cookie = (cookie or "").strip()
    token = (token or "").strip()
    if not cookie or not token:
        raise ValueError("both a mp.weixin.qq.com cookie and token are required")
    return Credential(cookie=cookie, token=token, updated_at=now)
