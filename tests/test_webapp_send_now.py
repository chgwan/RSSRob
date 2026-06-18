"""Tests for the /send-now route — email a subscriber their feeds' new items now.

send_feed_digest is monkeypatched so no real email is ever sent."""
import importlib.util
import sys
from pathlib import Path


def _load_webapp():
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("webapp", root / "web" / "webapp.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["webapp"] = m
    spec.loader.exec_module(m)
    return m


def _app(tmp_path, monkeypatch):
    """Fresh webapp in open mode (no admin file), with two rss feeds a + b."""
    from rssrob.subscribers import Subscribers
    wa = _load_webapp()
    d = tmp_path / "configs"; d.mkdir()
    (d / "00.yaml").write_text("output_dir: ./var/feeds\nstate_db: ./var/x.db\n",
                               encoding="utf-8")
    (d / "a.yaml").write_text("name: a\ntype: rss\nurl: http://a/\n", encoding="utf-8")
    (d / "b.yaml").write_text("name: b\ntype: rss\nurl: http://b/\n", encoding="utf-8")
    wa.CONFIG_OVERRIDE = str(d)
    wa.REPO_ROOT = tmp_path
    wa.SUBS = Subscribers(str(tmp_path / "subs.json"))
    wa.ADMIN_CRED_PATH = str(tmp_path / "admin.json")   # absent -> open mode
    monkeypatch.setattr(wa, "load_dotenv", lambda *a, **k: None)  # don't read real .env
    return wa


def test_send_now_sends_each_followed_feed_to_that_subscriber_only(tmp_path, monkeypatch):
    wa = _app(tmp_path, monkeypatch)
    wa.SUBS.add("a", "x@e.com"); wa.SUBS.add("b", "x@e.com")
    wa.SUBS.add("a", "other@e.com")                 # must NOT receive anything
    calls = []

    def fake_send(site, recipients, **kw):
        calls.append((site.name, tuple(recipients), kw.get("only_new")))
        return {"sent": len(recipients), "items": 3, "no_new": False, "errors": []}

    monkeypatch.setattr(wa, "send_feed_digest", fake_send)
    r = wa.app.test_client().post("/send-now", data={"email": "x@e.com"})
    assert r.status_code == 302 and "/subscribers" in r.headers["Location"]
    assert set(calls) == {("a", ("x@e.com",), True), ("b", ("x@e.com",), True)}


def test_send_now_success_banner(tmp_path, monkeypatch):
    wa = _app(tmp_path, monkeypatch)
    wa.SUBS.add("a", "x@e.com")
    monkeypatch.setattr(wa, "send_feed_digest",
                        lambda site, recipients, **kw: {"sent": 1, "items": 2,
                                                        "no_new": False, "errors": []})
    r = wa.app.test_client().post("/send-now", data={"email": "x@e.com"},
                                  follow_redirects=True)
    assert r.status_code == 200
    assert b"Sent 1 digest" in r.data and b"x@e.com" in r.data


def test_send_now_reports_no_new_items(tmp_path, monkeypatch):
    wa = _app(tmp_path, monkeypatch)
    wa.SUBS.add("a", "x@e.com")
    monkeypatch.setattr(wa, "send_feed_digest",
                        lambda site, recipients, **kw: {"sent": 0, "items": 0,
                                                        "no_new": True, "errors": []})
    r = wa.app.test_client().post("/send-now", data={"email": "x@e.com"},
                                  follow_redirects=True)
    assert b"nothing sent" in r.data.lower()


def test_send_now_rejects_non_subscriber(tmp_path, monkeypatch):
    wa = _app(tmp_path, monkeypatch)
    called = []
    monkeypatch.setattr(wa, "send_feed_digest",
                        lambda *a, **k: called.append(1) or {})
    r = wa.app.test_client().post("/send-now", data={"email": "ghost@e.com"},
                                  follow_redirects=True)
    assert called == []                              # nothing sent
    assert b"not a subscriber" in r.data.lower()


def test_send_now_surfaces_send_errors(tmp_path, monkeypatch):
    wa = _app(tmp_path, monkeypatch)
    wa.SUBS.add("a", "x@e.com")
    monkeypatch.setattr(
        wa, "send_feed_digest",
        lambda site, recipients, **kw: {"sent": 0, "items": 2, "no_new": False,
                                        "errors": [("*", "EmailError: SMTP not configured")]})
    r = wa.app.test_client().post("/send-now", data={"email": "x@e.com"},
                                  follow_redirects=True)
    assert b"failed" in r.data.lower() and b"smtp" in r.data.lower()


def test_subscribers_page_has_send_now_button(tmp_path, monkeypatch):
    wa = _app(tmp_path, monkeypatch)
    wa.SUBS.add("a", "x@e.com")
    r = wa.app.test_client().get("/subscribers")
    assert r.status_code == 200
    assert b"Send now" in r.data and b"/send-now" in r.data
