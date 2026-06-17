import web.webapp as webapp
from rssrob.twitter import TwitterAuthError


class _Account:
    id = "44196397"
    name = "Elon Musk"
    handle = "elonmusk"
    description = "Tech bio here"


class _Item:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeClient:
    def __init__(self, account=None, raises=None):
        self._account = account if account is not None else _Account()
        self._raises = raises

    def resolve_user(self, handle):
        if self._raises:
            raise self._raises
        return self._account

    def list_tweets(self, user_id, limit):
        return ["raw"]

    def to_items(self, raw):
        return [_Item(title="hello", summary="hello world tweet",
                      link="https://x.com/elonmusk/status/1",
                      date="2026-06-17T00:00:00+00:00")]


def _client():
    return webapp.app.test_client()


def test_lookup_returns_description_and_latest_posts(monkeypatch):
    monkeypatch.setattr(webapp, "_twitter_client", lambda: _FakeClient())
    r = _client().post("/twitter/lookup", data={"handle": "@elonmusk"})
    assert r.status_code == 200
    d = r.get_json()
    assert d["account"]["name"] == "Elon Musk"
    assert d["account"]["handle"] == "elonmusk"
    assert d["account"]["description"] == "Tech bio here"
    assert d["tweets"][0]["text"] == "hello world tweet"
    assert d["tweets"][0]["link"] == "https://x.com/elonmusk/status/1"
    assert d["tweets"][0]["date"] == "2026-06-17T00:00:00+00:00"


def test_lookup_blank_handle_errors(monkeypatch):
    monkeypatch.setattr(webapp, "_twitter_client", lambda: _FakeClient())
    d = _client().post("/twitter/lookup", data={"handle": ""}).get_json()
    assert "error" in d


def test_lookup_unknown_account_errors(monkeypatch):
    class _NoId:
        id = None
        name = handle = description = None
    monkeypatch.setattr(webapp, "_twitter_client",
                        lambda: _FakeClient(account=_NoId()))
    d = _client().post("/twitter/lookup", data={"handle": "ghost"}).get_json()
    assert "error" in d


def test_lookup_not_logged_in_errors(monkeypatch):
    monkeypatch.setattr(webapp, "_twitter_client",
                        lambda: _FakeClient(raises=TwitterAuthError("nope")))
    d = _client().post("/twitter/lookup", data={"handle": "elonmusk"}).get_json()
    assert "error" in d and "logged in" in d["error"]
