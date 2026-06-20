from rssrob.config import Site
from rssrob.pipeline import obtain_items, run_cycle
from rssrob.store import Store


def _html_site():
    return Site(
        name="ipp", url="http://www.ipp.cas.cn/", type="html",
        title="IPP", item=(
            "xpath://h2[normalize-space()='通知公告']"
            "/ancestor::div[contains(@class,'ipp2020-item')][1]//div[@class='bd']//ul/li"
        ),
        fields={"title": "xpath:.//a", "link": "xpath:.//a/@href",
                "date": "xpath:.//span"},
    )


def _rss_site():
    return Site(name="feedy", url="http://example.com/feed.xml", type="rss")


class _RecordingFetcher:
    """Serves a {url: bytes} mapping and records every requested URL, so tests
    can assert which links were (or were not) followed."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.requested = []

    def get(self, url, timeout=20, user_agent="RSSRob/0.1"):
        self.requested.append(url)
        if url not in self.mapping:
            raise RuntimeError(f"no fixture for {url}")
        return self.mapping[url]


def _tzgg_site(article=True):
    fields = {"title": "css:a", "link": "css:a@href", "date": "css:.date"}
    return Site(
        name="tzgg", url="http://example.com/list/", type="html", title="T",
        item="css:li.list-tit", fields=fields,
        article={"content": "css:.text"} if article else {},
    )


def test_obtain_items_html(fixtures, make_fetcher):
    html = (fixtures / "notices.html").read_bytes()
    fetcher = make_fetcher({"http://www.ipp.cas.cn/": html})
    items, title, desc = obtain_items(_html_site(), fetcher)
    assert len(items) == 2 and title is None and desc is None


def test_obtain_items_rss_exposes_channel_meta(fixtures, make_fetcher):
    xml = (fixtures / "sample_rss.xml").read_bytes()
    fetcher = make_fetcher({"http://example.com/feed.xml": xml})
    items, title, desc = obtain_items(_rss_site(), fetcher)
    assert len(items) == 2 and title == "Sample Feed" and desc == "A sample"


def test_obtain_items_wechat_uses_client():
    from rssrob.wechat import RawArticle
    from rssrob.models import Item
    site = Site(name="oa", url=None, type="wechat", account_id="MzAx==",
                account_name="某号", max_items=5)

    class FakeClient:
        def list_articles(self, account_id, limit):
            assert account_id == "MzAx==" and limit == 5
            return [RawArticle("L1", "t", "L1", "s", 1718000000.0)]

        def to_items(self, raw):
            return [Item(id=r.id, title=r.title, link=r.link, summary=r.summary,
                         date="2024-06-10T06:13:20+00:00") for r in raw]

    items, title, desc = obtain_items(site, fetcher=None, wechat_client=FakeClient())
    assert len(items) == 1 and items[0].id == "L1"
    assert title == "某号" and desc is None


def test_obtain_items_wechat_requires_client():
    site = Site(name="oa", url=None, type="wechat", account_id="x", account_name="n")
    import pytest
    with pytest.raises(RuntimeError):
        obtain_items(site, fetcher=None)


def test_run_cycle_wechat_defaults_title(tmp_path):
    from rssrob.wechat import RawArticle
    from rssrob.models import Item
    site = Site(name="oa", url=None, type="wechat", account_id="x",
                account_name="某号", max_items=5)

    class FakeClient:
        def list_articles(self, account_id, limit):
            return [RawArticle("https://mp.weixin.qq.com/s/AAA", "标题",
                               "https://mp.weixin.qq.com/s/AAA", "摘要", 1718000000.0)]

        def to_items(self, raw):
            return [Item(id=r.id, title=r.title, link=r.link, summary=r.summary,
                         date="2024-06-10T06:13:20+00:00") for r in raw]

    store = Store(str(tmp_path / "db.sqlite"))
    out = str(tmp_path / "feeds")
    inserted = run_cycle(site, store, None, out, now=1000.0, wechat_client=FakeClient())
    assert inserted == 1
    written = (tmp_path / "feeds" / "oa.xml").read_text(encoding="utf-8")
    assert "某号" in written and "标题" in written


def test_run_cycle_writes_feed_and_returns_inserted(tmp_path, fixtures, make_fetcher):
    html = (fixtures / "notices.html").read_bytes()
    fetcher = make_fetcher({"http://www.ipp.cas.cn/": html})
    store = Store(str(tmp_path / "db.sqlite"))
    out = str(tmp_path / "feeds")
    inserted = run_cycle(_html_site(), store, fetcher, out, now=1000.0)
    assert inserted == 2
    assert (tmp_path / "feeds" / "ipp.xml").exists()


def test_run_cycle_rss_defaults_feed_title(tmp_path, fixtures, make_fetcher):
    xml = (fixtures / "sample_rss.xml").read_bytes()
    fetcher = make_fetcher({"http://example.com/feed.xml": xml})
    store = Store(str(tmp_path / "db.sqlite"))
    out = str(tmp_path / "feeds")
    run_cycle(_rss_site(), store, fetcher, out, now=1000.0)
    written = (tmp_path / "feeds" / "feedy.xml").read_text(encoding="utf-8")
    assert "Sample Feed" in written     # title inherited from source feed


def test_obtain_items_twitter_branch():
    from rssrob.config import Site
    from rssrob.models import Item
    from rssrob.pipeline import obtain_items

    class FakeTw:
        def __init__(self):
            self.calls = []
        def resolve_user(self, handle):
            self.calls.append(("resolve", handle))
            class A: id = "44196397"
            return A()
        def list_tweets(self, user_id, limit):
            self.calls.append(("tweets", user_id, limit))
            return ["raw"]
        def to_items(self, raw):
            return [Item(id="1", title="hi", link="L", summary="hi", date=None)]

    site = Site(name="elon", type="twitter", username="elonmusk", max_items=20)
    tw = FakeTw()
    items, title, desc = obtain_items(site, fetcher=None, twitter_client=tw)
    assert items[0].id == "1"
    assert title == "@elonmusk"          # account_name unset → handle
    assert ("resolve", "elonmusk") in tw.calls
    assert ("tweets", "44196397", 20) in tw.calls


def test_obtain_items_twitter_uses_cached_account_id():
    from rssrob.config import Site
    from rssrob.pipeline import obtain_items

    class FakeTw:
        def __init__(self):
            self.calls = []
        def resolve_user(self, handle):
            raise AssertionError("should not resolve")
        def list_tweets(self, user_id, limit):
            self.calls.append(("tweets", user_id, limit)); return []
        def to_items(self, raw):
            return []

    site = Site(name="elon", type="twitter", username="elonmusk",
                account_id="44196397", max_items=10)
    tw = FakeTw()
    obtain_items(site, fetcher=None, twitter_client=tw)
    assert ("tweets", "44196397", 10) in tw.calls


def test_obtain_items_twitter_without_client_raises():
    import pytest
    from rssrob.config import Site
    from rssrob.pipeline import obtain_items
    site = Site(name="elon", type="twitter", username="elonmusk")
    with pytest.raises(RuntimeError):
        obtain_items(site, fetcher=None)


def test_run_cycle_prunes_old_items(tmp_path, fixtures, make_fetcher):
    xml = (fixtures / "sample_rss.xml").read_bytes()
    fetcher = make_fetcher({"http://example.com/feed.xml": xml})
    store = Store(str(tmp_path / "db.sqlite"))
    out = str(tmp_path / "feeds")
    # pre-seed an ancient item that the cycle should prune
    from rssrob.models import Item
    store.insert_new("feedy", [Item(id="ancient", title="ancient",
                                    date="Wed, 01 Jan 2010 00:00:00 GMT")], now=1.0)
    site = Site(name="feedy", url="http://example.com/feed.xml", type="rss",
                max_age_days=365)
    run_cycle(site, store, fetcher, out, now=1_750_000_000.0)
    ids = [r.id for r in store.recent("feedy", 50)]
    assert "ancient" not in ids          # pruned by recency


def test_run_cycle_keeps_all_when_max_age_zero(tmp_path, fixtures, make_fetcher):
    xml = (fixtures / "sample_rss.xml").read_bytes()
    fetcher = make_fetcher({"http://example.com/feed.xml": xml})
    store = Store(str(tmp_path / "db.sqlite"))
    out = str(tmp_path / "feeds")
    from rssrob.models import Item
    store.insert_new("feedy", [Item(id="ancient", title="ancient",
                                    date="Wed, 01 Jan 2010 00:00:00 GMT")], now=1.0)
    site = Site(name="feedy", url="http://example.com/feed.xml", type="rss",
                max_age_days=0)
    run_cycle(site, store, fetcher, out, now=1_750_000_000.0)
    assert "ancient" in [r.id for r in store.recent("feedy", 50)]


def test_run_cycle_drops_filtered_items_before_store(tmp_path, fixtures, make_fetcher):
    from rssrob.filters import FeedFilter
    xml = (fixtures / "sample_rss.xml").read_bytes()
    fetcher = make_fetcher({"http://example.com/feed.xml": xml})
    store = Store(str(tmp_path / "db.sqlite"))
    out = str(tmp_path / "feeds")
    # sample_rss.xml has 2 items titled "First" and "Second"
    site = Site(name="feedy", url="http://example.com/feed.xml", type="rss",
                filter=FeedFilter(exclude=["second"]))
    inserted = run_cycle(site, store, fetcher, out, now=1000.0)
    assert inserted == 1                              # only one item stored
    titles = [r.title for r in store.recent("feedy", 10)]
    assert all("Second" not in (t or "") for t in titles)


def test_run_cycle_enriches_summary_from_article_page(tmp_path):
    list_html = (
        '<ul class="list_grup">'
        '<li class="list-tit"><a href="http://example.com/a1">标题甲</a>'
        ' <span class="date">2026-06-18</span></li></ul>'
    )
    detail_html = (
        '<html><body><div class="content"><div class="title">标题甲</div>'
        '<div class="text"><p>这是详情正文内容，用于摘要提取测试。</p></div>'
        '</div></body></html>'
    )
    fetcher = _RecordingFetcher({
        "http://example.com/list/": list_html,
        "http://example.com/a1": detail_html,
    })
    store = Store(str(tmp_path / "db.sqlite"))
    out = str(tmp_path / "feeds")
    inserted = run_cycle(_tzgg_site(), store, fetcher, out, now=1000.0)
    assert inserted == 1
    # detail page was followed for the excerpt
    assert "http://example.com/a1" in fetcher.requested
    summary = store.recent("tzgg", 10)[0].summary
    assert summary and "详情正文" in summary
    # excerpt rendered into the feed XML as <description>
    xml = (tmp_path / "feeds" / "tzgg.xml").read_text(encoding="utf-8")
    assert "详情正文" in xml


def test_run_cycle_without_article_block_does_not_follow_links(tmp_path):
    list_html = (
        '<ul><li class="list-tit"><a href="http://example.com/detail.html">T</a>'
        ' <span class="date">2026-06-18</span></li></ul>'
    )
    fetcher = _RecordingFetcher({"http://example.com/list/": list_html})
    store = Store(str(tmp_path / "db.sqlite"))
    run_cycle(_tzgg_site(article=False), store, fetcher,
              str(tmp_path / "feeds"), now=1000.0)
    # only the list page was fetched — the item link was never followed
    assert fetcher.requested == ["http://example.com/list/"]
    assert store.recent("tzgg", 10)[0].summary is None


def test_enrich_does_not_refetch_already_stored_items(tmp_path):
    list_html = (
        '<ul class="list_grup">'
        '<li class="list-tit"><a href="http://example.com/a1">甲</a>'
        ' <span class="date">2026-06-18</span></li></ul>'
    )
    detail_html = '<html><body><div class="text"><p>详情正文内容。</p></div></body></html>'
    site = _tzgg_site()
    store = Store(str(tmp_path / "db.sqlite"))
    out = str(tmp_path / "feeds")
    # first cycle: detail fetched, summary stored
    run_cycle(site, store, _RecordingFetcher({
        "http://example.com/list/": list_html,
        "http://example.com/a1": detail_html,
    }), out, now=1000.0)
    assert "详情正文" in store.recent("tzgg", 1)[0].summary
    # second cycle: detail URL deliberately absent; must NOT be re-fetched
    fetcher2 = _RecordingFetcher({"http://example.com/list/": list_html})
    run_cycle(site, store, fetcher2, out, now=2000.0)
    assert fetcher2.requested == ["http://example.com/list/"]
    # stored summary persists (not overwritten by the un-enriched re-extraction)
    assert "详情正文" in store.recent("tzgg", 1)[0].summary


def test_enrich_paces_between_detail_fetches(tmp_path, monkeypatch):
    # two new items → two detail fetches, but the gap sleeps only once
    # (between fetches), never before the first, so single-item feeds are instant
    list_html = (
        '<ul class="list_grup">'
        '<li class="list-tit"><a href="http://example.com/a1">甲</a>'
        ' <span class="date">2026-06-18</span></li>'
        '<li class="list-tit"><a href="http://example.com/a2">乙</a>'
        ' <span class="date">2026-06-18</span></li></ul>'
    )
    detail = '<html><body><div class="text"><p>正文内容。</p></div></body></html>'
    sleeps = []
    monkeypatch.setattr("rssrob.pipeline.time.sleep", lambda s: sleeps.append(s))
    fetcher = _RecordingFetcher({
        "http://example.com/list/": list_html,
        "http://example.com/a1": detail,
        "http://example.com/a2": detail,
    })
    store = Store(str(tmp_path / "db.sqlite"))
    run_cycle(_tzgg_site(), store, fetcher, str(tmp_path / "feeds"), now=1000.0)
    assert fetcher.requested.count("http://example.com/a1") == 1
    assert fetcher.requested.count("http://example.com/a2") == 1
    assert len(sleeps) == 1                      # one gap between the two fetches
