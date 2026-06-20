from rssrob.models import Item
from rssrob.store import Store


def _store(tmp_path):
    return Store(str(tmp_path / "test.db"))


def test_insert_new_dedups(tmp_path):
    s = _store(tmp_path)
    items = [Item(id="a", title="A"), Item(id="b", title="B")]
    assert s.insert_new("feed1", items, now=100.0) == 2
    # re-inserting the same ids inserts nothing
    assert s.insert_new("feed1", items, now=200.0) == 0
    assert len(s.recent("feed1", 10)) == 2


def test_known_ids_returns_stored_ids_scoped_to_feed(tmp_path):
    s = _store(tmp_path)
    assert s.known_ids("f") == set()
    s.insert_new("f", [Item(id="a", title="A"), Item(id="b", title="B")], now=1.0)
    s.insert_new("g", [Item(id="a", title="A in g")], now=1.0)
    assert s.known_ids("f") == {"a", "b"}
    assert s.known_ids("g") == {"a"}          # scoped per feed


def test_recent_orders_by_published_then_first_seen(tmp_path):
    s = _store(tmp_path)
    s.insert_new("f", [
        Item(id="old", title="old", date="Sun, 08 Jun 2026 10:00:00 GMT"),
        Item(id="new", title="new", date="Mon, 15 Jun 2026 10:00:00 GMT"),
    ], now=100.0)
    rows = s.recent("f", 10)
    assert [r.id for r in rows] == ["new", "old"]   # newest published first
    assert rows[0].published is not None


def test_undated_item_uses_first_seen_and_is_kept(tmp_path):
    s = _store(tmp_path)
    s.insert_new("f", [Item(id="x", title="x", date=None)], now=123.0)
    rows = s.recent("f", 10)
    assert rows[0].published is None
    assert rows[0].first_seen == 123.0


def test_max_items_window(tmp_path):
    s = _store(tmp_path)
    s.insert_new("f", [Item(id=str(i), title=str(i)) for i in range(5)], now=1.0)
    assert len(s.recent("f", 3)) == 3


def test_prune_old_deletes_items_past_cutoff(tmp_path):
    s = _store(tmp_path)
    # published far in the past (2020) vs recent (2026)
    s.insert_new("f", [
        Item(id="old", title="old", date="Wed, 01 Jan 2020 00:00:00 GMT"),
        Item(id="new", title="new", date="Mon, 15 Jun 2026 10:00:00 GMT"),
    ], now=1_700_000_000.0)
    now = 1_750_000_000.0           # ~2025-06
    deleted = s.prune_old("f", max_age_seconds=365 * 86400, now=now)
    assert deleted == 1
    assert [r.id for r in s.recent("f", 10)] == ["new"]


def test_prune_old_uses_first_seen_when_undated(tmp_path):
    s = _store(tmp_path)
    s.insert_new("f", [Item(id="x", title="x", date=None)], now=1000.0)
    # cutoff far after first_seen → pruned; long before → kept
    assert s.prune_old("f", max_age_seconds=10.0, now=1_000_000.0) == 1
    s.insert_new("f", [Item(id="y", title="y", date=None)], now=2000.0)
    assert s.prune_old("f", max_age_seconds=10_000.0, now=2001.0) == 0


def test_prune_old_is_scoped_to_one_feed(tmp_path):
    s = _store(tmp_path)
    s.insert_new("a", [Item(id="1", title="t", date="Wed, 01 Jan 2020 00:00:00 GMT")], now=1.0)
    s.insert_new("b", [Item(id="1", title="t", date="Wed, 01 Jan 2020 00:00:00 GMT")], now=1.0)
    s.prune_old("a", max_age_seconds=365 * 86400, now=1_750_000_000.0)
    assert len(s.recent("a", 10)) == 0
    assert len(s.recent("b", 10)) == 1
