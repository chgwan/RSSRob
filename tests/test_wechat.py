import pytest

from rssrob.wechat import (Account, MpPlatformTransport, RawArticle, WeChatAuthError,
                           WeChatClient, WeChatRateLimited)
from rssrob.wechat_credential import Credential


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.search_result = []
        self.articles_result = []
        self.articles_raises = None

    def search(self, name, cred):
        self.calls.append(("search", name))
        return self.search_result

    def articles(self, account_id, limit, cred):
        self.calls.append(("articles", account_id, limit))
        if self.articles_raises:
            raise self.articles_raises
        return self.articles_result


def _cred():
    return Credential(cookie="slave_sid=abc", token="123", updated_at=1.0)


# --- client logic -----------------------------------------------------------

def test_to_items_maps_articles():
    c = WeChatClient(transport=FakeTransport(), credential=_cred())
    raw = [RawArticle(id="https://mp.weixin.qq.com/s/AAA", title="t",
                      link="https://mp.weixin.qq.com/s/AAA", summary="s",
                      published_at=1718000000.0)]
    items = c.to_items(raw)
    assert items[0].id == items[0].link == "https://mp.weixin.qq.com/s/AAA"
    assert items[0].title == "t" and items[0].date == "2024-06-10T06:13:20+00:00"


def test_to_items_handles_missing_date():
    c = WeChatClient(transport=FakeTransport(), credential=_cred())
    items = c.to_items([RawArticle("L", "t", "L", "s", None)])
    assert items[0].date is None


def test_search_accounts_parses(monkeypatch):
    monkeypatch.setattr("rssrob.wechat.time.sleep", lambda s: None)
    t = FakeTransport()
    t.search_result = [{"id": "MzAx==", "name": "某号", "avatar": "u", "desc": "d"}]
    c = WeChatClient(transport=t, credential=_cred())
    assert c.search_accounts("某") == [
        Account(id="MzAx==", name="某号", avatar="u", description="d")]


def test_list_articles_parses(monkeypatch):
    monkeypatch.setattr("rssrob.wechat.time.sleep", lambda s: None)
    t = FakeTransport()
    t.articles_result = [{"id": "L1", "title": "t1", "link": "L1",
                          "summary": "s1", "published_at": 1718000000}]
    c = WeChatClient(transport=t, credential=_cred())
    assert c.list_articles("MzAx==", limit=10) == [
        RawArticle("L1", "t1", "L1", "s1", 1718000000.0)]
    assert ("articles", "MzAx==", 10) in t.calls


def test_no_credential_raises_auth_error():
    c = WeChatClient(transport=FakeTransport(), credential=None)
    with pytest.raises(WeChatAuthError):
        c.list_articles("x", limit=5)


def test_auth_error_propagates(monkeypatch):
    monkeypatch.setattr("rssrob.wechat.time.sleep", lambda s: None)
    t = FakeTransport(); t.articles_raises = WeChatAuthError("session expired")
    c = WeChatClient(transport=t, credential=_cred())
    with pytest.raises(WeChatAuthError):
        c.list_articles("x", limit=5)


def test_rate_limited_propagates(monkeypatch):
    monkeypatch.setattr("rssrob.wechat.time.sleep", lambda s: None)
    t = FakeTransport(); t.articles_raises = WeChatRateLimited("freq control")
    c = WeChatClient(transport=t, credential=_cred())
    with pytest.raises(WeChatRateLimited):
        c.list_articles("x", limit=5)


# --- concrete mp-platform transport (parsing-level; network faked) ----------

class FakeSession:
    def __init__(self):
        self.requests = []
        self.queue = []

    def request(self, method, url, **kw):
        self.requests.append((method, url, kw))
        return self.queue.pop(0)


class FakeResp:
    def __init__(self, json_data):
        self._j = json_data

    def json(self):
        return self._j

    def raise_for_status(self):
        pass


def test_transport_searchbiz_maps_fields():
    s = FakeSession()
    s.queue.append(FakeResp({"base_resp": {"ret": 0},
                             "list": [{"fakeid": "MzAx==", "nickname": "某号",
                                       "round_head_img": "u", "alias": "d"}]}))
    out = MpPlatformTransport(session=s).search("某", _cred())
    assert out == [{"id": "MzAx==", "name": "某号", "avatar": "u", "desc": "d"}]


def test_transport_appmsg_maps_and_unescapes_link():
    s = FakeSession()
    s.queue.append(FakeResp({"base_resp": {"ret": 0}, "app_msg_list": [
        {"title": "t", "link": "https://mp.weixin.qq.com/s?a=1&amp;b=2",
         "digest": "d", "create_time": 1718000000}]}))
    out = MpPlatformTransport(session=s).articles("MzAx==", 10, _cred())
    assert out == [{"id": "https://mp.weixin.qq.com/s?a=1&b=2", "title": "t",
                    "link": "https://mp.weixin.qq.com/s?a=1&b=2", "summary": "d",
                    "published_at": 1718000000}]


def test_transport_appmsg_handles_stringified_list():
    s = FakeSession()
    s.queue.append(FakeResp({"base_resp": {"ret": 0},
                             "app_msg_list": '[{"title":"t","link":"L","digest":"d","create_time":1}]'}))
    out = MpPlatformTransport(session=s).articles("x", 5, _cred())
    assert out[0]["title"] == "t" and out[0]["link"] == "L"


def test_transport_invalid_session_raises_auth():
    s = FakeSession(); s.queue.append(FakeResp({"base_resp": {"ret": 200003}}))
    with pytest.raises(WeChatAuthError):
        MpPlatformTransport(session=s).search("x", _cred())


def test_transport_freq_control_raises_ratelimited():
    s = FakeSession(); s.queue.append(FakeResp({"base_resp": {"ret": 200013}}))
    with pytest.raises(WeChatRateLimited):
        MpPlatformTransport(session=s).articles("x", 10, _cred())


def test_transport_sends_cookie_and_token():
    s = FakeSession(); s.queue.append(FakeResp({"base_resp": {"ret": 0}, "list": []}))
    MpPlatformTransport(session=s).search("x", _cred())
    _, _, kw = s.requests[0]
    assert kw["headers"]["Cookie"] == "slave_sid=abc"
    assert kw["params"]["token"] == "123" and kw["params"]["action"] == "search_biz"
