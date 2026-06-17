from rssrob.filters import parse_terms, FeedFilter, build_filter, apply_filter
from rssrob.models import Item


def test_parse_terms_from_string_and_list():
    assert parse_terms("python, rust\n go ") == ["python", "rust", "go"]
    assert parse_terms(["  a ", "", "b"]) == ["a", "b"]
    assert parse_terms(None) == []


def test_keeps_include_only():
    f = FeedFilter(include=["python"])
    assert f.keeps(Item(id="1", title="Python news")) is True
    assert f.keeps(Item(id="2", title="Rust news")) is False


def test_keeps_exclude_only():
    f = FeedFilter(exclude=["sponsored"])
    assert f.keeps(Item(id="1", title="Sponsored post")) is False
    assert f.keeps(Item(id="2", title="Real post")) is True


def test_keeps_include_and_exclude():
    f = FeedFilter(include=["python"], exclude=["sponsored"])
    assert f.keeps(Item(id="1", title="Python sponsored")) is False  # exclude wins
    assert f.keeps(Item(id="2", title="Python tips")) is True


def test_keeps_regex_and_field_name():
    f = FeedFilter(include=[r"v\d+\.\d+"], field_name="summary", regex=True)
    assert f.keeps(Item(id="1", title="x", summary="release v1.2")) is True
    assert f.keeps(Item(id="2", title="x", summary="no version")) is False


def test_keeps_bad_regex_is_skipped_not_fatal():
    f = FeedFilter(include=["(unclosed"], regex=True)
    assert f.keeps(Item(id="1", title="(unclosed here")) is False  # term skipped → no include match


def test_keeps_missing_field_is_empty_string():
    f = FeedFilter(exclude=["x"], field_name="summary")
    assert f.keeps(Item(id="1", title="t", summary=None)) is True  # None → "" → no match


def test_build_filter_none_when_empty():
    assert build_filter(None) is None
    assert build_filter({}) is None
    assert build_filter({"field": "summary", "regex": True}) is None  # no terms


def test_build_filter_from_config_dict():
    f = build_filter({"include": ["a", "b"], "exclude": "c", "field": "summary", "regex": True})
    assert f.include == ["a", "b"] and f.exclude == ["c"]
    assert f.field_name == "summary" and f.regex is True


def test_apply_filter_tags_kept_and_dropped():
    items = [Item(id="1", title="Python"), Item(id="2", title="Rust"),
             Item(id="3", title="Python sponsored")]
    results = apply_filter(items, include="python", exclude="sponsored", field="title", regex=False)
    assert [(r["item"].id, r["kept"], r["reason"]) for r in results] == [
        ("1", True, "kept"),
        ("2", False, "no include match"),
        ("3", False, "excluded"),
    ]
