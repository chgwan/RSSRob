import pytest

from rssrob.twitter_credential import (Credential, credential_from_cookie, load,
                                        save)

FULL_COOKIE = ("guest_id=v1%3A123; auth_token=abc123def; "
               "ct0=csrf456; lang=en; kdt=XYZ")


def test_save_load_roundtrip(tmp_path):
    p = str(tmp_path / "cred.json")
    c = Credential(auth_token="abc", csrf_token="ct0v", updated_at=1000.0,
                   proxy="http://127.0.0.1:7890")
    save(p, c)
    assert load(p) == c


def test_load_missing_returns_none(tmp_path):
    assert load(str(tmp_path / "nope.json")) is None


def test_load_empty_file_returns_none(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text("{}", encoding="utf-8")
    assert load(str(p)) is None


def test_from_cookie_extracts_auth_and_csrf():
    c = credential_from_cookie(FULL_COOKIE, now=5.0)
    assert c.auth_token == "abc123def"
    assert c.csrf_token == "csrf456"
    assert c.updated_at == 5.0
    assert c.proxy is None


def test_from_cookie_keeps_proxy():
    c = credential_from_cookie(FULL_COOKIE, now=0.0, proxy="7890")
    assert c.proxy == "7890"


def test_from_cookie_requires_both_cookies():
    with pytest.raises(ValueError):
        credential_from_cookie("auth_token=abc", now=0.0)        # no ct0
    with pytest.raises(ValueError):
        credential_from_cookie("ct0=csrf", now=0.0)              # no auth_token
    with pytest.raises(ValueError):
        credential_from_cookie("", now=0.0)


def test_save_is_atomic_overwrite(tmp_path):
    p = str(tmp_path / "cred.json")
    save(p, Credential("a1", "c1", 1.0))
    save(p, Credential("a2", "c2", 2.0))
    assert load(p).auth_token == "a2"
