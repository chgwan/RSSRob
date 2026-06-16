"""Per-feed email subscriber list, stored as a small JSON file.

Shape on disk: {"<feed-name>": ["a@x.com", "b@y.com"], ...}. Kept out of the
config (and out of git) because it's user-submitted personal data. The email
notification job reads these recipients per feed.
"""

import json
import os
import re
import threading
from typing import List

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match((email or "").strip()))


class Subscribers:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                return json.load(f) or {}
        return {}

    def _save(self, data: dict) -> None:
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def list(self, feed: str) -> List[str]:
        return list(self._load().get(feed, []))

    def by_email(self) -> dict:
        """Reverse view: {email: [feeds...]}, emails and feeds sorted."""
        out: dict = {}
        for feed, emails in self._load().items():
            for e in emails:
                out.setdefault(e, []).append(feed)
        return {e: sorted(out[e]) for e in sorted(out)}

    def add(self, feed: str, email: str) -> str:
        """Add an email to a feed. Returns 'added', 'exists', or 'invalid'."""
        email = (email or "").strip().lower()
        if not is_valid_email(email):
            return "invalid"
        with self._lock:
            data = self._load()
            lst = data.setdefault(feed, [])
            if email in lst:
                return "exists"
            lst.append(email)
            self._save(data)
        return "added"

    def remove(self, feed: str, email: str) -> bool:
        email = (email or "").strip().lower()
        with self._lock:
            data = self._load()
            lst = data.get(feed, [])
            if email not in lst:
                return False
            lst.remove(email)
            self._save(data)
        return True
