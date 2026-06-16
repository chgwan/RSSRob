import pytest

from rssrob.subscribers import Subscribers, is_valid_email


@pytest.mark.parametrize("email,ok", [
    ("a@b.com", True),
    ("first.last@sub.example.cn", True),
    ("nope", False),
    ("no@domain", False),
    ("@x.com", False),
    ("a b@x.com", False),
    ("", False),
])
def test_is_valid_email(email, ok):
    assert is_valid_email(email) is ok


def test_add_and_list(tmp_path):
    s = Subscribers(str(tmp_path / "subs.json"))
    assert s.list("feed1") == []
    assert s.add("feed1", "Me@Example.com") == "added"   # normalized to lowercase
    assert s.list("feed1") == ["me@example.com"]
    assert (tmp_path / "subs.json").exists()


def test_add_duplicate(tmp_path):
    s = Subscribers(str(tmp_path / "subs.json"))
    s.add("f", "a@b.com")
    assert s.add("f", "  A@B.com ") == "exists"          # case/space-insensitive dup
    assert s.list("f") == ["a@b.com"]


def test_add_invalid(tmp_path):
    s = Subscribers(str(tmp_path / "subs.json"))
    assert s.add("f", "not-an-email") == "invalid"
    assert s.list("f") == []


def test_feeds_are_independent(tmp_path):
    s = Subscribers(str(tmp_path / "subs.json"))
    s.add("a", "x@y.com")
    s.add("b", "z@y.com")
    assert s.list("a") == ["x@y.com"]
    assert s.list("b") == ["z@y.com"]


def test_remove(tmp_path):
    s = Subscribers(str(tmp_path / "subs.json"))
    s.add("f", "a@b.com")
    assert s.remove("f", "A@B.com") is True
    assert s.list("f") == []
    assert s.remove("f", "a@b.com") is False
