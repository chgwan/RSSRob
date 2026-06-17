import json

import pytest

from rssrob.twitter import (Account, GraphQlTransport, RawTweet, TwitterAuthError,
                            TwitterClient, TwitterRateLimited, TwitterQueryError)
from rssrob.twitter_credential import Credential


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.user_result = None
        self.tweets_result = []
        self.tweets_raises = None

    def user_by_screen_name(self, handle, cred):
        self.calls.append(("user", handle))
        return self.user_result

    def user_tweets(self, user_id, limit, cred):
        self.calls.append(("tweets", user_id, limit))
        if self.tweets_raises:
            raise self.tweets_raises
        return self.tweets_result


def _cred():
    return Credential(auth_token="a", csrf_token="c", updated_at=1.0)


# --- client logic -----------------------------------------------------------

def test_resolve_user_maps_fields(monkeypatch):
    monkeypatch.setattr("rssrob.twitter.time.sleep", lambda s: None)
    t = FakeTransport()
    t.user_result = {"id": "44196397", "name": "Elon Musk",
                     "handle": "elonmusk", "desc": "bio"}
    c = TwitterClient(transport=t, credential=_cred())
    assert c.resolve_user("elonmusk") == Account(
        id="44196397", name="Elon Musk", handle="elonmusk", description="bio")


def test_list_tweets_maps(monkeypatch):
    monkeypatch.setattr("rssrob.twitter.time.sleep", lambda s: None)
    t = FakeTransport()
    t.tweets_result = [{"id": "1", "text": "hello",
                        "link": "https://x.com/elonmusk/status/1",
                        "created_at": 1718000000.0}]
    c = TwitterClient(transport=t, credential=_cred())
    assert c.list_tweets("44196397", limit=10) == [
        RawTweet("1", "hello", "https://x.com/elonmusk/status/1", 1718000000.0)]
    assert ("tweets", "44196397", 10) in t.calls


def test_to_items_maps_tweets():
    c = TwitterClient(transport=FakeTransport(), credential=_cred())
    raw = [RawTweet(id="1", text="a long tweet body here",
                    link="https://x.com/elonmusk/status/1",
                    created_at=1718000000.0)]
    items = c.to_items(raw)
    assert items[0].id == "1"
    assert items[0].link == "https://x.com/elonmusk/status/1"
    assert items[0].summary == "a long tweet body here"
    assert items[0].title == "a long tweet body here"      # short → used whole
    assert items[0].date == "2024-06-10T06:13:20+00:00"


def test_to_items_title_truncates_long_text():
    c = TwitterClient(transport=FakeTransport(), credential=_cred())
    long_text = "x" * 200
    items = c.to_items([RawTweet("1", long_text, "L", None)])
    assert items[0].title.endswith("…") and len(items[0].title) <= 81
    assert items[0].summary == long_text
    assert items[0].date is None


def test_no_credential_raises_auth_error():
    c = TwitterClient(transport=FakeTransport(), credential=None)
    with pytest.raises(TwitterAuthError):
        c.list_tweets("x", limit=5)


def test_auth_error_propagates(monkeypatch):
    monkeypatch.setattr("rssrob.twitter.time.sleep", lambda s: None)
    t = FakeTransport(); t.tweets_raises = TwitterAuthError("401")
    c = TwitterClient(transport=t, credential=_cred())
    with pytest.raises(TwitterAuthError):
        c.list_tweets("x", limit=5)


def test_rate_limited_propagates(monkeypatch):
    monkeypatch.setattr("rssrob.twitter.time.sleep", lambda s: None)
    t = FakeTransport(); t.tweets_raises = TwitterRateLimited("429")
    c = TwitterClient(transport=t, credential=_cred())
    with pytest.raises(TwitterRateLimited):
        c.list_tweets("x", limit=5)


# --- concrete GraphQL transport (parsing-level; network faked) --------------

class FakeSession:
    def __init__(self):
        self.requests = []
        self.queue = []

    def request(self, method, url, **kw):
        self.requests.append((method, url, kw))
        return self.queue.pop(0)


class FakeResp:
    def __init__(self, json_data, status=200):
        self._j = json_data
        self.status_code = status
        self.text = json.dumps(json_data)

    def json(self):
        return self._j


def test_transport_user_by_screen_name_maps():
    s = FakeSession()
    s.queue.append(FakeResp({"data": {"user": {"result": {
        "rest_id": "44196397",
        "legacy": {"name": "Elon Musk", "screen_name": "elonmusk",
                   "description": "bio"}}}}}))
    out = GraphQlTransport(session=s).user_by_screen_name("elonmusk", _cred())
    assert out == {"id": "44196397", "name": "Elon Musk",
                   "handle": "elonmusk", "desc": "bio"}


def _entry(tweet_id, full_text, created, screen_name="elonmusk"):
    return {"entryId": f"tweet-{tweet_id}", "content": {
        "entryType": "TimelineTimelineItem",
        "itemContent": {"itemType": "TimelineTweet", "tweet_results": {"result": {
            "rest_id": tweet_id,
            "core": {"user_results": {"result": {"legacy": {
                "screen_name": screen_name}}}},
            "legacy": {"full_text": full_text, "created_at": created}}}}}}


def test_transport_user_tweets_parses_timeline():
    s = FakeSession()
    instructions = [{"type": "TimelineAddEntries", "entries": [
        _entry("1", "hello world", "Wed Oct 10 20:19:24 +0000 2018"),
        {"entryId": "cursor-bottom-1", "content": {"entryType": "TimelineTimelineCursor"}},
    ]}]
    s.queue.append(FakeResp({"data": {"user": {"result": {"timeline_v2": {
        "timeline": {"instructions": instructions}}}}}}))
    out = GraphQlTransport(session=s).user_tweets("44196397", 10, _cred())
    assert out == [{"id": "1", "text": "hello world",
                    "link": "https://x.com/elonmusk/status/1",
                    "created_at": 1539202764.0}]


def test_transport_sends_auth_headers():
    s = FakeSession()
    s.queue.append(FakeResp({"data": {"user": {"result": {
        "rest_id": "1", "legacy": {"name": "n", "screen_name": "h",
                                   "description": ""}}}}}))
    GraphQlTransport(session=s).user_by_screen_name("h", _cred())
    _, url, kw = s.requests[0]
    assert "UserByScreenName" in url
    assert kw["headers"]["authorization"].startswith("Bearer ")
    assert kw["headers"]["x-csrf-token"] == "c"
    assert "auth_token=a" in kw["headers"]["cookie"]
    assert "ct0=c" in kw["headers"]["cookie"]


def test_transport_401_raises_auth():
    s = FakeSession(); s.queue.append(FakeResp({"errors": [{"code": 32}]}, status=401))
    with pytest.raises(TwitterAuthError):
        GraphQlTransport(session=s).user_by_screen_name("h", _cred())


def test_transport_429_raises_ratelimited():
    s = FakeSession(); s.queue.append(FakeResp({}, status=429))
    with pytest.raises(TwitterRateLimited):
        GraphQlTransport(session=s).user_tweets("1", 10, _cred())


def test_transport_404_raises_queryerror():
    s = FakeSession(); s.queue.append(FakeResp({}, status=404))
    with pytest.raises(TwitterQueryError):
        GraphQlTransport(session=s).user_by_screen_name("h", _cred())


def test_transport_passes_proxy():
    s = FakeSession()
    s.queue.append(FakeResp({"data": {"user": {"result": {
        "rest_id": "1", "legacy": {"name": "n", "screen_name": "h",
                                   "description": ""}}}}}))
    GraphQlTransport(session=s, proxy="http://127.0.0.1:7890").user_by_screen_name("h", _cred())
    _, _, kw = s.requests[0]
    assert kw["proxies"] == {"http": "http://127.0.0.1:7890",
                             "https": "http://127.0.0.1:7890"}
