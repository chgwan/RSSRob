import pytest

from rssrob.wechat_credential import (Credential, credential_from_login, load,
                                       save)


def test_save_load_roundtrip(tmp_path):
    p = str(tmp_path / "cred.json")
    c = Credential(cookie="slave_sid=abc; slave_user=gh_x", token="123456789",
                   updated_at=1000.0)
    save(p, c)
    assert load(p) == c


def test_load_missing_returns_none(tmp_path):
    assert load(str(tmp_path / "nope.json")) is None


def test_load_empty_file_returns_none(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text("{}", encoding="utf-8")
    assert load(str(p)) is None


def test_credential_from_login_builds():
    c = credential_from_login("slave_sid=abc; x=y", "987654321", now=5.0)
    assert c.cookie == "slave_sid=abc; x=y" and c.token == "987654321"
    assert c.updated_at == 5.0


def test_credential_from_login_strips_whitespace():
    c = credential_from_login("  slave_sid=abc  ", "  42 ", now=0.0)
    assert c.cookie == "slave_sid=abc" and c.token == "42"


def test_credential_from_login_requires_cookie_and_token():
    with pytest.raises(ValueError):
        credential_from_login("", "123", now=0.0)
    with pytest.raises(ValueError):
        credential_from_login("slave_sid=abc", "", now=0.0)


def test_save_is_atomic_overwrite(tmp_path):
    p = str(tmp_path / "cred.json")
    save(p, Credential("c1", "t1", 1.0))
    save(p, Credential("c2", "t2", 2.0))
    assert load(p).token == "t2"
