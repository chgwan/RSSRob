from rssrob import digest
from rssrob.config import Site
from rssrob.models import Item

ITEM = ("xpath://h2[normalize-space()='通知公告']/ancestor::div"
        "[contains(@class,'ipp2020-item')][1]//div[@class='bd']//ul/li")


def _site():
    return Site(name="ipp", url="http://www.ipp.cas.cn/", type="html", title="IPP",
                item=ITEM, fields={"title": "xpath:.//a", "link": "xpath:.//a/@href",
                                   "date": "xpath:.//span"})


def _record_sends(monkeypatch):
    """Patch send_email to record (to, bcc) and not actually send."""
    calls = []
    monkeypatch.setattr(digest, "send_email",
                        lambda to, subject, body, html=None, bcc=None: calls.append((to, bcc)))
    return calls


# --- formatting -------------------------------------------------------------

def test_build_digest_date_title_description():
    entries = [
        {"title": "Hello", "link": "http://x/1", "date": "06-15",
         "description": "a short summary"},
        {"title": "World", "link": "http://x/2", "date": "06-14", "description": None},
    ]
    subject, text, html = digest.build_digest("My Feed", entries)
    assert "My Feed" in subject and "2 updates" in subject
    assert "Hello" in text and "a short summary" in text and "http://x/1" in text
    assert '<a href="http://x/2"' in html and "06-14" in html


def test_build_combined_digest_multi_feed():
    sections = [
        {"title": "Feed A", "entries": [
            {"title": "A1", "link": "http://a/1", "date": "06-18", "description": "da"}]},
        {"title": "Feed B", "entries": [
            {"title": "B1", "link": "http://b/1", "date": "06-17", "description": None},
            {"title": "B2", "link": "http://b/2", "date": "06-16", "description": "db"}]},
    ]
    subject, text, html = digest.build_combined_digest(sections)
    assert subject == "[RSSRob] 3 updates across 2 feeds"
    assert "Feed A" in text and "Feed B" in text and "A1" in text and "B2" in text
    assert "Feed A" in html and "Feed B" in html and '<a href="http://a/1"' in html


def test_build_combined_digest_single_feed_uses_single_style():
    sections = [{"title": "Only", "entries": [
        {"title": "X", "link": "http://x", "date": "", "description": None}]}]
    subject, _text, _html = digest.build_combined_digest(sections)
    assert subject == "[RSSRob] Only — 1 update"     # delegates to single-feed style


def test_shorten_truncates_and_strips_leading_css():
    long = "@page{size:a4} p{m:0} " + ("word " * 100)
    out = digest._shorten(long, n=40)
    assert not out.startswith("@page") and out.endswith("…") and len(out) <= 41


# --- sent-state store -------------------------------------------------------

def test_sent_store_mark_and_dedupe(tmp_path):
    s = digest.SentStore(str(tmp_path / "s.json"))
    assert s.seen_ids("f") == set()
    s.mark("f", ["a", "b"])
    s.mark("f", ["b", "c", ""])
    assert s.seen_ids("f") == {"a", "b", "c"}


def test_sent_store_per_subscriber_isolation(tmp_path):
    s = digest.SentStore(str(tmp_path / "s.json"))
    s.mark("f", ["a"], subscriber="alice@x")
    assert s.seen_ids("f", "alice@x") == {"a"}
    assert s.seen_ids("f", "bob@x") == set()       # other subscriber isolated
    assert s.seen_ids("f") == set()                # global namespace untouched


def test_sent_store_subscriber_inherits_legacy_global_baseline(tmp_path):
    s = digest.SentStore(str(tmp_path / "s.json"))
    s.mark("f", ["g1", "g2"])                       # legacy/global send (subscriber=None)
    assert s.seen_ids("f", "alice@x") == {"g1", "g2"}   # baseline counts as seen
    s.mark("f", ["a1"], subscriber="alice@x")
    assert s.seen_ids("f", "alice@x") == {"g1", "g2", "a1"}
    assert s.seen_ids("f", "bob@x") == {"g1", "g2"}     # baseline shared, per-sub not


def test_select_new_items(tmp_path):
    items = [Item(id="1", title="a", link="l1"), Item(id="2", title="b", link="l2")]
    s = digest.SentStore(str(tmp_path / "s.json"))
    assert digest.select_new_items(items, "f", s) == items
    s.mark("f", ["1"])
    assert [i.id for i in digest.select_new_items(items, "f", s)] == ["2"]


# --- digest sending ---------------------------------------------------------

def test_sends_one_email_with_bcc(fixtures, make_fetcher, monkeypatch):
    html = (fixtures / "notices.html").read_bytes()
    fetcher = make_fetcher({"http://www.ipp.cas.cn/": html})
    calls = _record_sends(monkeypatch)
    # no state -> sends all current items in a single email, Bcc = all recipients
    res = digest.send_feed_digest(_site(), ["a@b.com", "c@d.com"], fetcher=fetcher)
    assert res["sent"] == 2 and res["items"] == 2
    assert len(calls) == 1                          # ONE email
    assert calls[0][1] == ["a@b.com", "c@d.com"]    # via Bcc


def test_first_send_uses_first_limit_then_incremental(fixtures, make_fetcher, monkeypatch, tmp_path):
    html = (fixtures / "notices.html").read_bytes()
    fetcher = make_fetcher({"http://www.ipp.cas.cn/": html})
    state = digest.SentStore(str(tmp_path / "state.json"))
    calls = _record_sends(monkeypatch)

    # first send: both items (first_limit >= 2), recorded
    r1 = digest.send_feed_digest(_site(), ["a@b.com"], fetcher=fetcher, state=state)
    assert r1["items"] == 2 and r1["sent"] == 1 and not r1.get("no_new")
    assert state.seen_ids("ipp") and len(calls) == 1

    # second send: nothing new -> no email
    r2 = digest.send_feed_digest(_site(), ["a@b.com"], fetcher=fetcher, state=state)
    assert r2.get("no_new") and r2["sent"] == 0 and len(calls) == 1


def test_first_limit_caps_first_send(fixtures, make_fetcher, tmp_path):
    html = (fixtures / "notices.html").read_bytes()
    fetcher = make_fetcher({"http://www.ipp.cas.cn/": html})
    state = digest.SentStore(str(tmp_path / "s.json"))
    res = digest.send_feed_digest(_site(), ["a@b.com"], first_limit=1,
                                  fetcher=fetcher, state=state, dry_run=True)
    assert res["items"] == 1                        # first send capped at first_limit


def test_dry_run_does_not_record_state(fixtures, make_fetcher, tmp_path):
    html = (fixtures / "notices.html").read_bytes()
    fetcher = make_fetcher({"http://www.ipp.cas.cn/": html})
    state = digest.SentStore(str(tmp_path / "s.json"))
    digest.send_feed_digest(_site(), ["a@b.com"], fetcher=fetcher, state=state, dry_run=True)
    assert state.seen_ids("ipp") == set()


def test_all_ignores_state(fixtures, make_fetcher, monkeypatch, tmp_path):
    html = (fixtures / "notices.html").read_bytes()
    fetcher = make_fetcher({"http://www.ipp.cas.cn/": html})
    state = digest.SentStore(str(tmp_path / "s.json"))
    calls = _record_sends(monkeypatch)

    digest.send_feed_digest(_site(), ["a@b.com"], fetcher=fetcher, state=state)   # populate
    assert digest.send_feed_digest(_site(), ["a@b.com"], fetcher=fetcher, state=state).get("no_new")
    r = digest.send_feed_digest(_site(), ["a@b.com"], fetcher=fetcher, state=state, only_new=False)
    assert not r.get("no_new") and r["sent"] == 1 and len(calls) == 2
