import sqlite3
from typing import List, Optional

from dateutil import parser as dateparser

from .models import StoredItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    feed       TEXT NOT NULL,
    id         TEXT NOT NULL,
    title      TEXT,
    link       TEXT,
    summary    TEXT,
    published  REAL,
    first_seen REAL NOT NULL,
    PRIMARY KEY (feed, id)
);
CREATE INDEX IF NOT EXISTS idx_items_order ON items (feed, published, first_seen);
"""


class Store:
    def __init__(self, db_path: str):
        # created on main thread, written from scheduler thread (single writer)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def insert_new(self, feed: str, items, now: float) -> int:
        cur = self.conn.cursor()
        inserted = 0
        for item in items:
            if not item.id:
                continue
            published = _parse_date(item.date)
            cur.execute(
                "INSERT OR IGNORE INTO items "
                "(feed, id, title, link, summary, published, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (feed, item.id, item.title, item.link, item.summary, published, now),
            )
            inserted += cur.rowcount
        self.conn.commit()
        return inserted

    def recent(self, feed: str, limit: int) -> List[StoredItem]:
        rows = self.conn.execute(
            "SELECT id, title, link, summary, published, first_seen "
            "FROM items WHERE feed = ? "
            "ORDER BY COALESCE(published, first_seen) DESC LIMIT ?",
            (feed, limit),
        ).fetchall()
        return [StoredItem(**dict(r)) for r in rows]

    def prune_old(self, feed: str, max_age_seconds: float, now: float) -> int:
        """Delete items older than now - max_age_seconds for one feed.

        Age uses COALESCE(published, first_seen) — the same fallback recent()
        orders by — so a freshly seen, undated item is not pruned immediately.
        Returns the number of rows deleted."""
        cutoff = now - max_age_seconds
        cur = self.conn.execute(
            "DELETE FROM items WHERE feed = ? "
            "AND COALESCE(published, first_seen) < ?",
            (feed, cutoff),
        )
        self.conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self.conn.close()


def _parse_date(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    try:
        return dateparser.parse(raw).timestamp()
    except (ValueError, OverflowError, TypeError):
        return None
