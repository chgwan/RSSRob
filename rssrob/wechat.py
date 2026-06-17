"""WeChat 公众号 client via the Official Account platform (mp.weixin.qq.com).

To follow arbitrary 公众号, RSSRob logs in to *your own* registered 公众号 at
mp.weixin.qq.com and uses its editor APIs: ``searchbiz`` finds any account by
name (returning its ``fakeid``) and ``appmsg`` lists that account's published
articles. This is the standard self-hosted approach and far more reliable than
微信读书, whose API doesn't expose 公众号 at all.

The HTTP layer is isolated behind a ``transport`` so the credential/mapping logic
is testable offline with canned responses. ``WeChatClient`` holds the logic;
``MpPlatformTransport`` is the one piece that talks to mp.weixin.qq.com.

mp.weixin.qq.com rate-limits these endpoints aggressively ("频率限制"); the client
spaces requests and surfaces ``WeChatRateLimited`` so the scheduler can back off.
"""

import html
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from .models import Item

# Where you log in to capture a credential (your own 公众号 backend).
MP_LOGIN_URL = "https://mp.weixin.qq.com/"


class WeChatError(Exception):
    """Base for WeChat platform client errors."""


class WeChatAuthError(WeChatError):
    """Session is missing or expired; the user must log in again."""


class WeChatRateLimited(WeChatError):
    """mp.weixin.qq.com frequency control; back off and retry later."""


@dataclass
class Account:
    id: str                         # the account's fakeid
    name: Optional[str] = None
    avatar: Optional[str] = None
    description: Optional[str] = None


@dataclass
class RawArticle:
    id: str
    title: Optional[str]
    link: Optional[str]
    summary: Optional[str]
    published_at: Optional[float]   # epoch seconds


class WeChatClient:
    """公众号 operations on top of a swappable ``transport``.

    The transport exposes ``search(name, cred)`` and
    ``articles(fakeid, limit, cred)`` returning plain dicts (see
    ``MpPlatformTransport``). The client adds the credential guard, polite request
    spacing, and result mapping to ``Account`` / ``RawArticle`` / ``Item``."""

    def __init__(self, transport, credential=None, credential_path=None,
                 min_spacing=1.5):
        self.transport = transport
        self.credential = credential
        self.credential_path = credential_path
        self.min_spacing = min_spacing
        self._last_call = 0.0

    def _require(self):
        if self.credential is None:
            raise WeChatAuthError("not logged in; run `rssrob wechat-login`")

    def _space(self):
        """Keep at least ``min_spacing`` seconds (plus jitter) between calls."""
        wait = self.min_spacing - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.4))
        self._last_call = time.time()

    def search_accounts(self, name) -> List[Account]:
        self._require()
        self._space()
        return [Account(id=r["id"], name=r.get("name"),
                        avatar=r.get("avatar"), description=r.get("desc"))
                for r in (self.transport.search(name, self.credential) or [])]

    def list_articles(self, account_id, limit) -> List[RawArticle]:
        self._require()
        self._space()
        rows = self.transport.articles(account_id, limit, self.credential) or []
        return [RawArticle(
            id=r["id"], title=r.get("title"), link=r.get("link"),
            summary=r.get("summary"),
            published_at=(float(r["published_at"])
                          if r.get("published_at") is not None else None))
            for r in rows]

    def to_items(self, raw: List[RawArticle]) -> List[Item]:
        """Map articles to the shared ``Item`` shape (date as ISO8601)."""
        items = []
        for a in raw:
            date = (datetime.fromtimestamp(a.published_at, tz=timezone.utc).isoformat()
                    if a.published_at is not None else None)
            items.append(Item(id=a.id, title=a.title, link=a.link,
                              summary=a.summary, date=date))
        return items


class MpPlatformTransport:
    """Concrete transport over mp.weixin.qq.com's editor APIs.

    Auth is the logged-in session ``cookie`` plus the ``token`` query param.
    ``base_resp.ret`` is normalized: invalid-session -> ``WeChatAuthError``;
    frequency control -> ``WeChatRateLimited``."""

    BASE = "https://mp.weixin.qq.com"
    SEARCHBIZ = "/cgi-bin/searchbiz"
    APPMSG = "/cgi-bin/appmsg"

    _AUTH_RETS = {200003, -6}      # invalid session / not logged in
    _RATE_RETS = {200013}          # 频率限制 (frequency control)

    HEADERS = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        "Referer": "https://mp.weixin.qq.com/",
        "Accept": "application/json, text/plain, */*",
    }

    def __init__(self, session=None, timeout=20):
        if session is None:
            import requests
            session = requests.Session()
        self.session = session
        self.timeout = timeout

    def _call(self, path, cred, params):
        full = {"token": cred.token, "lang": "zh_CN", "f": "json", "ajax": 1,
                "random": round(random.random(), 8), **params}
        headers = {**self.HEADERS, "Cookie": cred.cookie}
        resp = self.session.request("GET", self.BASE + path, params=full,
                                    headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json() or {}
        ret = (data.get("base_resp") or {}).get("ret")
        if ret in self._RATE_RETS:
            raise WeChatRateLimited(f"mp.weixin.qq.com freq control: ret={ret}")
        if ret in self._AUTH_RETS:
            raise WeChatAuthError(f"mp.weixin.qq.com session invalid: ret={ret}")
        return data

    def search(self, name, cred):
        d = self._call(self.SEARCHBIZ, cred,
                       {"action": "search_biz", "begin": 0, "count": 5, "query": name})
        return [{"id": b.get("fakeid"), "name": b.get("nickname"),
                 "avatar": b.get("round_head_img"),
                 "desc": b.get("alias") or b.get("signature")}
                for b in (d.get("list") or [])]

    def articles(self, account_id, limit, cred):
        d = self._call(self.APPMSG, cred,
                       {"action": "list_ex", "begin": 0, "count": min(int(limit), 20),
                        "fakeid": account_id, "type": 9, "query": ""})
        msgs = d.get("app_msg_list") or []
        if isinstance(msgs, str):                      # some responses stringify it
            msgs = json.loads(msgs or "[]")
        out = []
        for a in msgs:
            link = html.unescape(a.get("link") or "")
            out.append({"id": link, "title": a.get("title"), "link": link,
                        "summary": a.get("digest"), "published_at": a.get("create_time")})
        return out
