"""Email feed subscriptions + per-email digest frequency, in one JSON file.

Shape on disk:
    {
      "feeds": {"<feed>": ["a@x.com", ...], ...},   # which emails follow which feeds
      "frequencies": {"a@x.com": 24, ...}           # email-level digest cadence (hours)
    }

The frequency is per *email* (one value used for all of that email's feeds), set
on first subscribe and editable afterwards. Legacy shapes are still read: a
top-level {feed: [emails]} or {feed: {email: hours}} map (frequencies derived,
default 24). Kept out of git because it's user-submitted personal data.
"""

import json
import os
import re
import threading
from typing import List, Tuple

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

DEFAULT_FREQ_HOURS = 24


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match((email or "").strip()))


def normalize_hours(value, default: float = DEFAULT_FREQ_HOURS):
    """Coerce a frequency to a positive number of hours, else `default`."""
    try:
        h = float(value)
    except (TypeError, ValueError):
        return default
    if h <= 0:
        return default
    return int(h) if float(h).is_integer() else h


class Subscribers:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> Tuple[dict, dict]:
        """Return (feeds {feed: [emails]}, frequencies {email: hours})."""
        if not os.path.exists(self.path):
            return {}, {}
        with open(self.path, encoding="utf-8") as f:
            raw = json.load(f) or {}

        if isinstance(raw.get("feeds"), dict):                  # current format
            feeds = {fd: list(dict.fromkeys(es or []))
                     for fd, es in raw["feeds"].items()}
            freqs = {e: normalize_hours(h)
                     for e, h in (raw.get("frequencies") or {}).items()}
            return feeds, freqs

        feeds, freqs = {}, {}                                   # legacy top-level map
        for feed, val in raw.items():
            if isinstance(val, list):                           # {feed: [emails]}
                feeds[feed] = list(dict.fromkeys(val))
            elif isinstance(val, dict):                         # {feed: {email: hours}}
                feeds[feed] = list(val.keys())
                for e, h in val.items():
                    freqs[e] = normalize_hours(h)
            else:
                feeds[feed] = []
        return feeds, freqs

    def _save(self, feeds: dict, freqs: dict) -> None:
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"feeds": feeds, "frequencies": freqs}, f,
                      ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def list(self, feed: str) -> List[str]:
        """Subscriber emails for a feed (the digest recipient list)."""
        feeds, _ = self._load()
        return list(feeds.get(feed, []))

    def freq(self, email: str):
        """The email's digest frequency in hours (default if unknown)."""
        _, freqs = self._load()
        return freqs.get((email or "").strip().lower(), DEFAULT_FREQ_HOURS)

    def items(self, feed: str) -> dict:
        """{email: hours} for a feed, hours being each email's frequency."""
        feeds, freqs = self._load()
        return {e: freqs.get(e, DEFAULT_FREQ_HOURS) for e in feeds.get(feed, [])}

    def by_email(self) -> dict:
        """{email: {"feeds": [feeds...], "hours": N}}, sorted."""
        feeds, freqs = self._load()
        out: dict = {}
        for feed, emails in feeds.items():
            for e in emails:
                out.setdefault(e, []).append(feed)
        return {e: {"feeds": sorted(out[e]),
                    "hours": freqs.get(e, DEFAULT_FREQ_HOURS)}
                for e in sorted(out)}

    def add(self, feed: str, email: str, hours=DEFAULT_FREQ_HOURS) -> str:
        """Subscribe an email to a feed. The email's frequency is set on the
        first subscribe only (change it later via set_freq). Returns 'added',
        'exists', or 'invalid'."""
        email = (email or "").strip().lower()
        if not is_valid_email(email):
            return "invalid"
        with self._lock:
            feeds, freqs = self._load()
            lst = feeds.setdefault(feed, [])
            existed = email in lst
            if not existed:
                lst.append(email)
            if email not in freqs:                  # email-level: set once
                freqs[email] = normalize_hours(hours)
            self._save(feeds, freqs)
        return "exists" if existed else "added"

    def set_freq(self, email: str, hours) -> bool:
        """Update an existing subscriber's email-level frequency."""
        email = (email or "").strip().lower()
        with self._lock:
            feeds, freqs = self._load()
            if not any(email in es for es in feeds.values()):
                return False
            freqs[email] = normalize_hours(hours)
            self._save(feeds, freqs)
        return True

    def remove(self, feed: str, email: str) -> bool:
        email = (email or "").strip().lower()
        with self._lock:
            feeds, freqs = self._load()
            lst = feeds.get(feed, [])
            if email not in lst:
                return False
            lst.remove(email)
            if not any(email in es for es in feeds.values()):
                freqs.pop(email, None)              # forget freq when fully unsubscribed
            self._save(feeds, freqs)
        return True
