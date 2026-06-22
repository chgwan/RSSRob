"""Tests for the edit-feed flow: the edit button, playground prefill when opened
via edit, and /save updating an existing feed while preserving fields the form
doesn't show (max_items, interval, article, …)."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml


def _load_webapp():
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("webapp", root / "web" / "webapp.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["webapp"] = m
    spec.loader.exec_module(m)
    return m


HTML_FEED = """\
name: hfips-tzgg
type: html
url: http://example.com/l/
item: "css:li.x"
fields:
  title: "css:a"
  link: "css:a@href"
  date: "css:.date"
title: "Test Feed"
article:
  content: "css:.text"
filter:
  include:
    - alpha
    - beta
max_items: 7
interval: 99
"""

IPP_FEED = """\
name: ipp
type: html
url: http://www.ipp.cas.cn/
item: "xpath://h2[normalize-space()='通知公告']/ancestor::div[contains(@class,'ipp2020-item')][1]//div[@class='bd']//ul/li"
fields:
  title: "xpath:.//a"
  link: "xpath:.//a/@href"
  date: "xpath:.//span"
"""


RSS_FEED = """\
name: rss
type: rss
url: http://example.com/feed.xml
"""


def _app(wa, tmp_path, files):
    """Isolated app: configs/ with globals + the given {filename: yaml_text}."""
    from rssrob.subscribers import Subscribers
    d = tmp_path / "configs"
    d.mkdir()
    (d / "00-settings.yaml").write_text(
        "output_dir: ./var/feeds\nstate_db: ./var/rssrob.db\n", encoding="utf-8")
    for fn, txt in files.items():
        (d / fn).write_text(txt, encoding="utf-8")
    wa.CONFIG_OVERRIDE = str(d)
    wa.REPO_ROOT = tmp_path
    wa.SUBS = Subscribers(str(tmp_path / "subscribers.json"))
    wa.ADMIN_CRED_PATH = str(tmp_path / "admin.json")   # absent -> open mode
    return wa.app.test_client(), d


# --- edit prefill -----------------------------------------------------------

def test_edit_prefills_all_fields(tmp_path):
    wa = _load_webapp()
    client, _ = _app(wa, tmp_path, {"hfips-tzgg.yaml": HTML_FEED})
    html = client.get("/playground",
                      query_string={"site": "hfips-tzgg", "edit": "1"}).get_data(as_text=True)
    assert 'value="hfips-tzgg"' in html      # name
    assert "Test Feed" in html               # site_title
    assert "alpha, beta" in html             # include (joined)
    assert "css:.text" in html               # article selector
    assert "css:li.x" in html                # item selector


def test_edit_shows_delete_button_only_when_editing(tmp_path):
    wa = _load_webapp()
    client, _ = _app(wa, tmp_path, {"hfips-tzgg.yaml": HTML_FEED})
    editing = client.get("/playground",
                         query_string={"site": "hfips-tzgg", "edit": "1"}).get_data(as_text=True)
    assert "delete-feed-btn" in editing      # delete merged into edit page
    plain = client.get("/playground").get_data(as_text=True)
    assert "delete-feed-btn" not in plain    # not shown when creating new


# --- /save update: preserve + clear ----------------------------------------

def test_save_preserves_non_form_fields(tmp_path):
    wa = _load_webapp()
    client, d = _app(wa, tmp_path, {"hfips-tzgg.yaml": HTML_FEED})
    r = client.post("/save", data={
        "name": "hfips-tzgg", "type": "html", "url": "http://example.com/l/",
        "item": "css:li.CHANGED", "title_sel": "css:a", "link_sel": "css:a@href",
        "date_sel": "css:.date", "article_sel": "css:.text",
    })
    assert r.status_code == 302
    raw = yaml.safe_load((d / "hfips-tzgg.yaml").read_text(encoding="utf-8"))
    assert raw["item"] == "css:li.CHANGED"              # form value applied
    assert raw["max_items"] == 7 and raw["interval"] == 99   # non-form preserved
    assert raw["article"] == {"content": "css:.text"}   # article kept


def test_save_clearing_optional_fields(tmp_path):
    wa = _load_webapp()
    client, d = _app(wa, tmp_path, {"hfips-tzgg.yaml": HTML_FEED})
    client.post("/save", data={
        "name": "hfips-tzgg", "type": "html", "url": "http://example.com/l/",
        "item": "css:li.x", "title_sel": "css:a", "link_sel": "css:a@href",
        "date_sel": "css:.date",
        # site_title / proxy / include / exclude left empty -> should clear
    })
    raw = yaml.safe_load((d / "hfips-tzgg.yaml").read_text(encoding="utf-8"))
    assert "title" not in raw and "filter" not in raw and "proxy" not in raw


# --- /save per type (wechat / twitter) -------------------------------------

def test_save_wechat_writes_account(tmp_path):
    wa = _load_webapp()
    client, d = _app(wa, tmp_path, {})
    r = client.post("/save", data={
        "name": "w", "type": "wechat", "account_id": "MzAx==", "account_name": "某号",
    })
    assert r.status_code == 302
    raw = yaml.safe_load((d / "w.yaml").read_text(encoding="utf-8"))
    assert raw["type"] == "wechat" and raw["account_id"] == "MzAx=="
    assert raw["account_name"] == "某号"
    assert "url" not in raw and "item" not in raw


def test_save_wechat_requires_account_id(tmp_path):
    wa = _load_webapp()
    client, _ = _app(wa, tmp_path, {})
    r = client.post("/save", data={"name": "w", "type": "wechat"})
    assert r.status_code == 302 and "save_error" in r.headers["Location"]


def test_save_twitter_writes_username(tmp_path):
    wa = _load_webapp()
    client, d = _app(wa, tmp_path, {})
    client.post("/save", data={
        "name": "t", "type": "twitter", "username": "elonmusk", "account_name": "Elon",
    })
    raw = yaml.safe_load((d / "t.yaml").read_text(encoding="utf-8"))
    assert raw["type"] == "twitter" and raw["username"] == "elonmusk"
    assert raw["account_name"] == "Elon"


# --- preview page: edit button (not delete) --------------------------------

def test_preview_has_edit_button_not_delete(tmp_path):
    wa = _load_webapp()
    client, _ = _app(wa, tmp_path, {"ipp.yaml": IPP_FEED})
    # the index route renders without per-item article fetches (lazy enrichment),
    # so no stubbing is needed here
    html = client.get("/", query_string={"site": "ipp"}).get_data(as_text=True)
    assert "edit feed" in html               # edit entry point present
    assert "delete-feed-btn" not in html     # delete moved into the edit page


# --- index page: lazy enrichment (no article fetch on render) ---------------

def test_index_renders_without_article_fetch(tmp_path, monkeypatch):
    wa = _load_webapp()
    client, _ = _app(wa, tmp_path, {"ipp.yaml": IPP_FEED})
    calls = []
    monkeypatch.setattr(wa, "fetch_article",
                        lambda *a, **k: calls.append(1) or SimpleNamespace(title="x", content_text="y"))
    html = client.get("/", query_string={"site": "ipp"}).get_data(as_text=True)
    assert calls == []                      # no article fetch during page render
    assert "data-enrich=" in html           # html items flagged for lazy enrich


def test_index_shows_rss_summary_inline(tmp_path, monkeypatch):
    wa = _load_webapp()
    client, _ = _app(wa, tmp_path, {"rss.yaml": RSS_FEED})
    # stub the listing fetch so no network is needed; item carries a summary
    item = SimpleNamespace(title="T", link="http://x/1",
                           summary="<b>SUMMARY TEXT</b>", date=None)
    monkeypatch.setattr(wa, "obtain_items", lambda *a, **k: ([item], "feed", "d"))
    html = client.get("/", query_string={"site": "rss"}).get_data(as_text=True)
    assert "SUMMARY TEXT" in html           # RSS summary rendered inline
    assert "data-enrich=" not in html       # item with a summary is not flagged


# --- /enrich endpoint: lazy, cached, parallel, gated ------------------------

def _art(title, text):
    return SimpleNamespace(title=title, content_text=text)


def test_enrich_endpoint_returns_titles_and_descs(tmp_path, monkeypatch):
    wa = _load_webapp()
    client, _ = _app(wa, tmp_path, {"ipp.yaml": IPP_FEED})
    wa._ITEM_CACHE.clear()
    monkeypatch.setattr(wa, "fetch_article",
                        lambda link, fetcher, **kw: _art(f"FULL-{link}", "body text"))
    r = client.post("/enrich", json={"site": "ipp",
                                     "links": ["http://a", "http://b"]})
    assert r.status_code == 200
    data = r.get_json()
    assert data["http://a"] == {"title": "FULL-http://a", "desc": "body text"}
    assert data["http://b"] == {"title": "FULL-http://b", "desc": "body text"}


def test_enrich_endpoint_caches_across_calls(tmp_path, monkeypatch):
    wa = _load_webapp()
    client, _ = _app(wa, tmp_path, {"ipp.yaml": IPP_FEED})
    wa._ITEM_CACHE.clear()
    calls = []

    def fake(link, fetcher, **kw):
        calls.append(link)
        return _art(f"T-{link}", "x")

    monkeypatch.setattr(wa, "fetch_article", fake)
    client.post("/enrich", json={"site": "ipp", "links": ["http://a", "http://b"]})
    assert set(calls) == {"http://a", "http://b"}      # both fetched once
    calls.clear()
    client.post("/enrich", json={"site": "ipp", "links": ["http://a", "http://b"]})
    assert calls == []                                # served from _ITEM_CACHE


def test_enrich_endpoint_tolerates_failing_link(tmp_path, monkeypatch):
    wa = _load_webapp()
    client, _ = _app(wa, tmp_path, {"ipp.yaml": IPP_FEED})
    wa._ITEM_CACHE.clear()

    def fake(link, fetcher, **kw):
        if link == "http://bad":
            raise RuntimeError("boom")
        return _art("ok-title", "ok-body")

    monkeypatch.setattr(wa, "fetch_article", fake)
    r = client.post("/enrich", json={"site": "ipp",
                                     "links": ["http://bad", "http://good"]})
    data = r.get_json()
    assert data["http://bad"] == {"title": None, "desc": None}
    assert data["http://good"] == {"title": "ok-title", "desc": "ok-body"}


def test_enrich_endpoint_unknown_site_is_404(tmp_path):
    wa = _load_webapp()
    client, _ = _app(wa, tmp_path, {"ipp.yaml": IPP_FEED})
    r = client.post("/enrich", json={"site": "nope", "links": ["http://a"]})
    assert r.status_code == 404


def test_enrich_endpoint_respects_login_gate(tmp_path):
    wa = _load_webapp()
    client, _ = _app(wa, tmp_path, {"ipp.yaml": IPP_FEED})
    import rssrob.admin_credential as ac       # activate admin mode -> gate on
    ac.save(wa.ADMIN_CRED_PATH, ac.create("admin", "pw", 0))
    r = client.post("/enrich", json={"site": "ipp", "links": ["http://a"]})
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]
