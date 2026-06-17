"""Tests for the web app's remove-feed API."""
import importlib.util
import sys
from pathlib import Path

import yaml


def _load_webapp():
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("webapp", root / "web" / "webapp.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["webapp"] = m
    spec.loader.exec_module(m)
    return m


# --- _delete_site_files: config removal (folder + single-file) --------------

def test_delete_removes_single_site_file(tmp_path):
    wa = _load_webapp()
    d = tmp_path / "configs"; d.mkdir()
    (d / "00-settings.yaml").write_text("output_dir: ./var/feeds\n", encoding="utf-8")
    (d / "oa.yaml").write_text("name: oa\ntype: rss\nurl: http://a/\n", encoding="utf-8")
    assert wa._delete_site_files(str(d), "oa") is True
    assert not (d / "oa.yaml").exists()
    assert (d / "00-settings.yaml").exists()       # globals file untouched


def test_delete_from_sites_list_file(tmp_path):
    wa = _load_webapp()
    d = tmp_path / "configs"; d.mkdir()
    (d / "feeds.yaml").write_text(
        "sites:\n  - {name: a, type: rss, url: 'http://a/'}\n"
        "  - {name: b, type: rss, url: 'http://b/'}\n", encoding="utf-8")
    assert wa._delete_site_files(str(d), "a") is True
    raw = yaml.safe_load((d / "feeds.yaml").read_text(encoding="utf-8"))
    assert [s["name"] for s in raw["sites"]] == ["b"]


def test_delete_sites_file_removed_when_emptied(tmp_path):
    wa = _load_webapp()
    d = tmp_path / "configs"; d.mkdir()
    (d / "one.yaml").write_text(
        "sites:\n  - {name: only, type: rss, url: 'http://a/'}\n", encoding="utf-8")
    assert wa._delete_site_files(str(d), "only") is True
    assert not (d / "one.yaml").exists()           # held only that feed -> removed


def test_delete_single_file_mode(tmp_path):
    wa = _load_webapp()
    f = tmp_path / "config.yaml"
    f.write_text(
        "sites:\n  - {name: a, type: rss, url: 'http://a/'}\n"
        "  - {name: b, type: rss, url: 'http://b/'}\n", encoding="utf-8")
    assert wa._delete_site_files(str(f), "b") is True
    raw = yaml.safe_load(f.read_text(encoding="utf-8"))
    assert [s["name"] for s in raw["sites"]] == ["a"]


def test_delete_not_found_returns_false(tmp_path):
    wa = _load_webapp()
    d = tmp_path / "configs"; d.mkdir()
    (d / "oa.yaml").write_text("name: oa\ntype: rss\nurl: http://a/\n", encoding="utf-8")
    assert wa._delete_site_files(str(d), "missing") is False
    assert (d / "oa.yaml").exists()


# --- the /delete-feed route -------------------------------------------------

def _isolated_app(wa, tmp_path):
    from rssrob.subscribers import Subscribers
    d = tmp_path / "configs"; d.mkdir()
    (d / "00-settings.yaml").write_text(
        "output_dir: ./var/feeds\nstate_db: ./var/rssrob.db\n", encoding="utf-8")
    (d / "oa.yaml").write_text("name: oa\ntype: rss\nurl: http://a/\n", encoding="utf-8")
    wa.CONFIG_OVERRIDE = str(d)
    wa.REPO_ROOT = tmp_path
    wa.SUBS = Subscribers(str(tmp_path / "subscribers.json"))
    return wa.app.test_client(), d


def test_delete_feed_route_happy_path(tmp_path):
    wa = _load_webapp()
    client, d = _isolated_app(wa, tmp_path)
    # an existing generated feed file should be cleaned up too
    feeds = tmp_path / "var" / "feeds"; feeds.mkdir(parents=True)
    (feeds / "oa.xml").write_text("<rss/>", encoding="utf-8")
    r = client.post("/delete-feed", data={"name": "oa"})
    assert r.status_code == 200 and r.get_json()["deleted"] == "oa"
    assert not (d / "oa.yaml").exists()
    assert not (feeds / "oa.xml").exists()         # generated XML removed


def test_delete_feed_route_not_found(tmp_path):
    wa = _load_webapp()
    client, _ = _isolated_app(wa, tmp_path)
    r = client.post("/delete-feed", data={"name": "nope"})
    assert r.status_code == 404


def test_delete_feed_route_missing_name(tmp_path):
    wa = _load_webapp()
    client, _ = _isolated_app(wa, tmp_path)
    r = client.post("/delete-feed", data={})
    assert r.status_code == 400


# --- the /delete-feeds bulk route -------------------------------------------

def _multi_app(wa, tmp_path):
    from rssrob.subscribers import Subscribers
    d = tmp_path / "configs"; d.mkdir()
    (d / "00-settings.yaml").write_text(
        "output_dir: ./var/feeds\nstate_db: ./var/rssrob.db\n", encoding="utf-8")
    for n in ("a", "b", "c"):
        (d / f"{n}.yaml").write_text(
            f"name: {n}\ntype: rss\nurl: http://{n}/\n", encoding="utf-8")
    wa.CONFIG_OVERRIDE = str(d)
    wa.REPO_ROOT = tmp_path
    wa.SUBS = Subscribers(str(tmp_path / "subscribers.json"))
    return wa.app.test_client(), d


def test_delete_feeds_bulk(tmp_path):
    wa = _load_webapp()
    client, d = _multi_app(wa, tmp_path)
    r = client.post("/delete-feeds", data={"name": ["a", "c"]})
    assert r.status_code == 200
    body = r.get_json()
    assert set(body["deleted"]) == {"a", "c"} and body["not_found"] == []
    assert not (d / "a.yaml").exists() and not (d / "c.yaml").exists()
    assert (d / "b.yaml").exists()                 # untouched


def test_delete_feeds_mixed_existing_and_missing(tmp_path):
    wa = _load_webapp()
    client, _ = _multi_app(wa, tmp_path)
    r = client.post("/delete-feeds", data={"name": ["a", "zzz"]})
    body = r.get_json()
    assert body["deleted"] == ["a"] and body["not_found"] == ["zzz"]


def test_delete_feeds_comma_separated_names(tmp_path):
    wa = _load_webapp()
    client, _ = _multi_app(wa, tmp_path)
    r = client.post("/delete-feeds", data={"names": "a, b"})
    assert set(r.get_json()["deleted"]) == {"a", "b"}


def test_delete_feeds_empty_returns_400(tmp_path):
    wa = _load_webapp()
    client, _ = _multi_app(wa, tmp_path)
    r = client.post("/delete-feeds", data={})
    assert r.status_code == 400
