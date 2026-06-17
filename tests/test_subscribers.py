import json

import pytest

from rssrob.subscribers import Subscribers, is_valid_email, normalize_hours


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


def test_by_email_reverse_view(tmp_path):
    s = Subscribers(str(tmp_path / "subs.json"))
    s.add("feedA", "x@y.com", 6)
    s.add("feedB", "x@y.com")            # same email -> keeps its 6h
    s.add("feedA", "z@y.com", 12)
    assert s.by_email() == {
        "x@y.com": {"feeds": ["feedA", "feedB"], "hours": 6},
        "z@y.com": {"feeds": ["feedA"], "hours": 12},
    }


@pytest.mark.parametrize("value,expected", [
    (6, 6), ("12", 12), (None, 24), ("", 24), ("abc", 24), (0, 24), (-3, 24), (1.5, 1.5),
])
def test_normalize_hours(value, expected):
    assert normalize_hours(value) == expected


def test_frequency_is_email_level_set_once(tmp_path):
    s = Subscribers(str(tmp_path / "subs.json"))
    s.add("feedA", "a@b.com", 6)
    s.add("feedB", "a@b.com", 100)       # second subscribe does NOT change the email's freq
    assert s.freq("A@B.com") == 6        # email-level, case-insensitive
    assert s.items("feedA") == {"a@b.com": 6}
    assert s.items("feedB") == {"a@b.com": 6}


def test_set_freq_updates_existing_only(tmp_path):
    s = Subscribers(str(tmp_path / "subs.json"))
    assert s.set_freq("nobody@x.com", 8) is False     # not a subscriber
    s.add("f", "a@b.com", 6)
    assert s.set_freq("A@B.com", 12) is True
    assert s.freq("a@b.com") == 12


def test_remove_forgets_freq_when_fully_unsubscribed(tmp_path):
    s = Subscribers(str(tmp_path / "subs.json"))
    s.add("a", "x@y.com", 6)
    s.add("b", "x@y.com")
    s.remove("a", "x@y.com")
    assert s.freq("x@y.com") == 6        # still subscribed to b
    s.remove("b", "x@y.com")
    assert s.freq("x@y.com") == 24       # gone -> default


def test_legacy_list_format_read_as_default(tmp_path):
    p = tmp_path / "subs.json"
    p.write_text(json.dumps({"f": ["a@b.com", "c@d.com"]}), encoding="utf-8")
    s = Subscribers(str(p))
    assert s.items("f") == {"a@b.com": 24, "c@d.com": 24}
    assert s.list("f") == ["a@b.com", "c@d.com"]


def test_legacy_dict_format_derives_frequency(tmp_path):
    p = tmp_path / "subs.json"
    p.write_text(json.dumps({"f": {"a@b.com": 6}}), encoding="utf-8")
    s = Subscribers(str(p))
    assert s.freq("a@b.com") == 6 and s.list("f") == ["a@b.com"]
