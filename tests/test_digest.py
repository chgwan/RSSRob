from rssrob import digest
from rssrob.config import Site

ITEM = ("xpath://h2[normalize-space()='通知公告']/ancestor::div"
        "[contains(@class,'ipp2020-item')][1]//div[@class='bd']//ul/li")


def _site():
    return Site(name="ipp", url="http://www.ipp.cas.cn/", type="html", title="IPP",
                item=ITEM, fields={"title": "xpath:.//a", "link": "xpath:.//a/@href",
                                   "date": "xpath:.//span"})


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


def test_shorten_truncates_and_strips_leading_css():
    long = "@page{size:a4} p{m:0} " + ("word " * 100)
    out = digest._shorten(long, n=40)
    assert not out.startswith("@page") and out.endswith("…") and len(out) <= 41


def test_send_feed_digest_dry_run(fixtures, make_fetcher):
    html = (fixtures / "notices.html").read_bytes()
    # only the list page is mapped; article fetches fail -> fall back to list title
    fetcher = make_fetcher({"http://www.ipp.cas.cn/": html})
    res = digest.send_feed_digest(_site(), ["a@b.com"], fetcher=fetcher, dry_run=True)
    assert res["dry_run"] and res["sent"] == 0 and res["items"] == 2
    assert "通知一" in res["text"]


def test_send_feed_digest_sends_to_each(fixtures, make_fetcher, monkeypatch):
    html = (fixtures / "notices.html").read_bytes()
    fetcher = make_fetcher({"http://www.ipp.cas.cn/": html})
    sent = []
    monkeypatch.setattr(digest, "send_email",
                        lambda to, subject, body, html=None: sent.append(to))
    res = digest.send_feed_digest(_site(), ["a@b.com", "c@d.com"], fetcher=fetcher)
    assert res["sent"] == 2 and not res["errors"] and sent == ["a@b.com", "c@d.com"]


def test_send_feed_digest_limit(fixtures, make_fetcher):
    html = (fixtures / "notices.html").read_bytes()
    fetcher = make_fetcher({"http://www.ipp.cas.cn/": html})
    res = digest.send_feed_digest(_site(), ["a@b.com"], limit=1, fetcher=fetcher, dry_run=True)
    assert res["items"] == 1
