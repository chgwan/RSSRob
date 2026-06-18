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


# --- combined per-subscriber digest ----------------------------------------

def _orchestration(monkeypatch, per_feed):
    """Fake obtain_items/enrich_items so these tests exercise orchestration, not
    feed parsing. `per_feed` is {feed_name: [Item, ...]} and is read live, so
    callers may mutate it between sends. Returns the recorded send_email calls."""
    def fake_obtain(site, fetcher):
        return list(per_feed.get(site.name, [])), site.title or site.name, None

    def fake_enrich(site, items, fetcher):
        return [{"title": it.title, "link": it.link,
                 "date": it.date or "", "description": it.summary} for it in items]

    monkeypatch.setattr(digest, "obtain_items", fake_obtain)
    monkeypatch.setattr(digest, "enrich_items", fake_enrich)
    calls = []
    monkeypatch.setattr(digest, "send_email",
                        lambda to, subject, body, html=None, bcc=None:
                        calls.append({"to": to, "subject": subject, "body": body}))
    return calls


def _S(name, title):
    return Site(name=name, url=f"http://{name}/", type="rss", title=title)


def test_subscriber_digest_combines_feeds_into_one_email(monkeypatch, tmp_path):
    a, b = _S("a", "Feed A"), _S("b", "Feed B")
    calls = _orchestration(monkeypatch, {
        "a": [Item(id="a1", title="A one", link="http://a/1", summary="sa")],
        "b": [Item(id="b1", title="B one", link="http://b/1", summary="sb"),
              Item(id="b2", title="B two", link="http://b/2", summary="sb2")]})
    state = digest.SentStore(str(tmp_path / "s.json"))
    res = digest.send_subscriber_digest("alice@x", [a, b], state=state, fetcher=object())
    assert len(calls) == 1                           # ONE combined email
    assert calls[0]["to"] == ["alice@x"]             # To: subscriber, not Bcc
    assert res["feeds"] == 2 and res["items"] == 3 and res["sent"] == 1
    assert "Feed A" in calls[0]["body"] and "Feed B" in calls[0]["body"]
    assert "across 2 feeds" in calls[0]["subject"]


def test_subscriber_digest_marks_only_that_subscriber(monkeypatch, tmp_path):
    a = _S("a", "Feed A")
    _orchestration(monkeypatch, {"a": [Item(id="a1", title="A1", link="http://a/1", summary="s")]})
    state = digest.SentStore(str(tmp_path / "s.json"))
    digest.send_subscriber_digest("alice@x", [a], state=state, fetcher=object())
    assert state.seen_ids("a", "alice@x") == {"a1"}
    assert state.seen_ids("a", "bob@x") == set()     # bob unaffected


def test_subscriber_digest_no_new_sends_nothing(monkeypatch, tmp_path):
    a = _S("a", "Feed A")
    calls = _orchestration(monkeypatch, {"a": [Item(id="a1", title="A1", link="http://a/1", summary="s")]})
    state = digest.SentStore(str(tmp_path / "s.json"))
    digest.send_subscriber_digest("alice@x", [a], state=state, fetcher=object())  # marks a1
    res = digest.send_subscriber_digest("alice@x", [a], state=state, fetcher=object())
    assert res["no_new"] and res["sent"] == 0
    assert len(calls) == 1                           # only the first send went out


def test_subscriber_digest_partial_feed_error_still_sends_rest(monkeypatch, tmp_path):
    good, bad = _S("good", "Good"), _S("bad", "Bad")

    def fake_obtain(site, fetcher):
        if site.name == "bad":
            raise RuntimeError("boom")
        return [Item(id="g1", title="G1", link="http://g/1", summary="s")], "Good", None

    monkeypatch.setattr(digest, "obtain_items", fake_obtain)
    monkeypatch.setattr(digest, "enrich_items",
                        lambda site, items, fetcher: [{"title": it.title, "link": it.link,
                            "date": "", "description": it.summary} for it in items])
    calls = []
    monkeypatch.setattr(digest, "send_email",
                        lambda to, s, b, html=None, bcc=None: calls.append(to))
    state = digest.SentStore(str(tmp_path / "s.json"))
    res = digest.send_subscriber_digest("alice@x", [good, bad], state=state, fetcher=object())
    assert len(calls) == 1 and res["feeds"] == 1 and res["sent"] == 1
    assert any(name == "bad" for name, _ in res["errors"])


def test_subscriber_digest_first_limit_then_incremental_limit(monkeypatch, tmp_path):
    a = _S("a", "Feed A")
    pool = {"a": [Item(id=f"n{i}", title=f"t{i}", link=f"http://a/{i}", summary="s")
                  for i in range(3)]}
    _orchestration(monkeypatch, pool)
    state = digest.SentStore(str(tmp_path / "s.json"))
    r1 = digest.send_subscriber_digest("alice@x", [a], first_limit=2, limit=3,
                                       state=state, fetcher=object())
    assert r1["items"] == 2                           # first send capped at first_limit
    pool["a"] += [Item(id=f"m{i}", title=f"m{i}", link=f"http://a/m{i}", summary="s")
                  for i in range(5)]                  # 6 new now available
    r2 = digest.send_subscriber_digest("alice@x", [a], first_limit=2, limit=3,
                                       state=state, fetcher=object())
    assert r2["items"] == 3                           # not first send -> capped at limit


def test_subscriber_digest_dry_run_builds_without_send_or_state(monkeypatch, tmp_path):
    a = _S("a", "Feed A")
    calls = _orchestration(monkeypatch, {"a": [Item(id="a1", title="A1", link="http://a/1", summary="s")]})
    state = digest.SentStore(str(tmp_path / "s.json"))
    res = digest.send_subscriber_digest("alice@x", [a], state=state, dry_run=True, fetcher=object())
    assert res["dry_run"] and res["sent"] == 0 and res["subject"]
    assert calls == []                               # nothing sent
    assert state.seen_ids("a", "alice@x") == set()   # nothing marked


# --- digest CLI subscriber modes -------------------------------------------

def _write_two_feed_config(tmp_path):
    d = tmp_path / "configs"; d.mkdir()
    (d / "a.yaml").write_text("name: a\ntype: rss\nurl: http://a/\n", encoding="utf-8")
    (d / "b.yaml").write_text("name: b\ntype: rss\nurl: http://b/\n", encoding="utf-8")
    return str(d)


def test_cli_all_subscribers_sends_each_one_combined(monkeypatch, tmp_path):
    from rssrob.subscribers import Subscribers
    cfg = _write_two_feed_config(tmp_path)
    subs_path = str(tmp_path / "subs.json")
    s = Subscribers(subs_path)
    s.add("a", "x@e.com"); s.add("b", "x@e.com"); s.add("a", "y@e.com")
    seen = []
    monkeypatch.setattr(digest, "send_subscriber_digest",
                        lambda subscriber, sites, **kw: seen.append(
                            (subscriber, tuple(si.name for si in sites)))
                        or {"sent": 1, "items": 2, "feeds": len(sites),
                            "no_new": False, "errors": [], "subject": "x"})
    monkeypatch.setattr(digest, "load_dotenv", lambda *a, **k: None)
    rc = digest.main(["--all-subscribers", "--config", cfg, "--subscribers", subs_path])
    assert rc == 0
    assert ("x@e.com", ("a", "b")) in seen and ("y@e.com", ("a",)) in seen


def test_cli_subscriber_mode_unknown_email_returns_1(monkeypatch, tmp_path):
    cfg = _write_two_feed_config(tmp_path)
    subs_path = str(tmp_path / "subs.json")
    monkeypatch.setattr(digest, "load_dotenv", lambda *a, **k: None)
    rc = digest.main(["--subscriber", "ghost@e.com", "--config", cfg,
                      "--subscribers", subs_path])
    assert rc == 1


def test_cli_requires_exactly_one_mode(tmp_path):
    cfg = _write_two_feed_config(tmp_path)
    rc = digest.main(["--config", cfg])              # no mode chosen
    assert rc == 2


def test_cli_site_mode_returns_1_on_send_error(monkeypatch, tmp_path):
    from rssrob.subscribers import Subscribers
    cfg = _write_two_feed_config(tmp_path)
    subs_path = str(tmp_path / "subs.json")
    Subscribers(subs_path).add("a", "x@e.com")
    monkeypatch.setattr(digest, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(digest, "send_feed_digest",
                        lambda *a, **k: {"subject": "S", "items": 2, "sent": 0,
                                        "no_new": False,
                                        "errors": [("x@e.com", "EmailError: nope")]})
    rc = digest.main(["--site", "a", "--config", cfg, "--subscribers", subs_path])
    assert rc == 1


def test_cli_all_subscribers_returns_1_on_send_failure(monkeypatch, tmp_path):
    from rssrob.subscribers import Subscribers
    cfg = _write_two_feed_config(tmp_path)
    subs_path = str(tmp_path / "subs.json")
    Subscribers(subs_path).add("a", "x@e.com")
    monkeypatch.setattr(digest, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(digest, "send_subscriber_digest",
                        lambda subscriber, sites, **kw: {"subject": "S", "items": 2,
                            "feeds": 1, "sent": 0, "no_new": False,
                            "errors": [("*", "EmailError: nope")]})
    rc = digest.main(["--all-subscribers", "--config", cfg, "--subscribers", subs_path])
    assert rc == 1
