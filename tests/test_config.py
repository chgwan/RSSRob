import pytest

from rssrob.config import load_config, ConfigError


def _write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_digest_block_parsed(tmp_path):
    cfg = load_config(_write(tmp_path, "digest:\n  first_limit: 20\n  limit: 5\nsites: []\n"))
    assert cfg.digest == {"first_limit": 20, "limit": 5}


def test_digest_defaults_empty(tmp_path):
    cfg = load_config(_write(tmp_path, "sites: []\n"))
    assert cfg.digest == {}


VALID = """
output_dir: ./feeds
state_db: ./rssrob.db
http:
  host: 0.0.0.0
  port: 9000
defaults:
  interval: 1800
  max_items: 25
sites:
  - name: blog
    url: http://example.com/blog
    title: Blog
    item: "css:div.post"
    fields:
      title: "css:h2 a"
      link: "css:h2 a@href"
  - name: feedy
    type: rss
    url: http://example.com/feed.xml
"""


def test_load_valid(tmp_path):
    cfg = load_config(_write(tmp_path, VALID))
    assert cfg.output_dir == "./feeds"
    assert cfg.http.host == "0.0.0.0" and cfg.http.port == 9000
    blog = cfg.sites[0]
    assert blog.type == "html"
    assert blog.interval == 1800 and blog.max_items == 25   # defaults merged
    assert cfg.sites[1].type == "rss"


def test_missing_url_raises(tmp_path):
    bad = "sites:\n  - name: x\n    item: 'css:a'\n    fields: {title: 'css:a'}\n"
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


def test_duplicate_name_raises(tmp_path):
    dup = (
        "sites:\n"
        "  - {name: x, url: 'http://a/', type: rss}\n"
        "  - {name: x, url: 'http://b/', type: rss}\n"
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, dup))


def test_unknown_type_raises(tmp_path):
    bad = "sites:\n  - {name: x, url: 'http://a/', type: json}\n"
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


def test_html_missing_selectors_raises(tmp_path):
    bad = "sites:\n  - {name: x, url: 'http://a/'}\n"  # type defaults html, no item/fields
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


def test_invalid_selector_raises(tmp_path):
    bad = (
        "sites:\n  - name: x\n    url: 'http://a/'\n"
        "    item: 'xpath://[[[broken'\n    fields: {title: 'css:a'}\n"
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


def test_invalid_port_raises(tmp_path):
    bad = "http:\n  port: 99999\nsites: []\n"
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


PROXY_CFG = """
defaults:
  proxy: 8080
sites:
  - {name: bareport, url: 'http://a/', type: rss, proxy: 7890}
  - {name: socksfull, url: 'http://b/', type: rss, proxy: 'socks5://127.0.0.1:1080'}
  - {name: socksshort, url: 'http://c/', type: rss, proxy: 'socks5:10.0.0.2:1081'}
  - {name: httpfull, url: 'http://d/', type: rss, proxy: 'http://1.2.3.4:8888'}
  - {name: hostport, url: 'http://e/', type: rss, proxy: '1.2.3.4:9999'}
  - {name: inherits, url: 'http://f/', type: rss}
"""


def test_load_config_directory(tmp_path):
    d = tmp_path / "config"
    d.mkdir()
    (d / "00-settings.yaml").write_text(
        "output_dir: ./out\nhttp:\n  port: 9001\ndefaults:\n  interval: 99\n",
        encoding="utf-8")
    # single-site file (top-level mapping, no `sites:` wrapper)
    (d / "feed-a.yaml").write_text(
        "name: a\ntype: rss\nurl: http://a/\n", encoding="utf-8")
    # a file using the sites: list form
    (d / "feed-b.yaml").write_text(
        "sites:\n  - {name: b, type: rss, url: 'http://b/'}\n", encoding="utf-8")

    cfg = load_config(str(d))
    assert cfg.output_dir == "./out"
    assert cfg.http.port == 9001
    assert [s.name for s in cfg.sites] == ["a", "b"]      # filename order
    assert cfg.sites[0].interval == 99                    # globals merged in


def test_load_config_directory_duplicate_name(tmp_path):
    d = tmp_path / "config"
    d.mkdir()
    (d / "one.yaml").write_text("name: dup\ntype: rss\nurl: http://a/\n", encoding="utf-8")
    (d / "two.yaml").write_text("name: dup\ntype: rss\nurl: http://b/\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(str(d))


def test_wechat_site_valid_without_url(tmp_path):
    cfg = load_config(_write(tmp_path,
        "sites:\n  - {name: oa, type: wechat, account_id: 'MzAx==', account_name: '某号'}\n"))
    s = cfg.sites[0]
    assert s.type == "wechat" and s.account_id == "MzAx=="
    assert s.account_name == "某号" and s.url is None


def test_wechat_missing_account_id_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, "sites:\n  - {name: oa, type: wechat}\n"))


def test_wechat_title_defaults_to_account_name(tmp_path):
    cfg = load_config(_write(tmp_path,
        "sites:\n  - {name: oa, type: wechat, account_id: x, account_name: '某号'}\n"))
    assert cfg.sites[0].title == "某号"


def test_per_feed_proxy(tmp_path):
    cfg = load_config(_write(tmp_path, PROXY_CFG))
    by = {s.name: s for s in cfg.sites}
    assert by["bareport"].proxy == "http://127.0.0.1:7890"    # bare port
    assert by["socksfull"].proxy == "socks5://127.0.0.1:1080"  # full socks url
    assert by["socksshort"].proxy == "socks5://10.0.0.2:1081"  # socks5:host:port shorthand
    assert by["httpfull"].proxy == "http://1.2.3.4:8888"       # full http url
    assert by["hostport"].proxy == "http://1.2.3.4:9999"       # bare host:port -> http
    assert by["inherits"].proxy == "http://127.0.0.1:8080"     # inherits defaults.proxy


def test_twitter_site_requires_username():
    from rssrob.config import _build_config
    with pytest.raises(ConfigError):
        _build_config({"sites": [{"name": "t", "type": "twitter"}]})


def test_twitter_site_builds_with_username():
    from rssrob.config import _build_config
    cfg = _build_config({"sites": [
        {"name": "elon", "type": "twitter", "username": "elonmusk"}]})
    site = cfg.sites[0]
    assert site.type == "twitter" and site.username == "elonmusk"
    assert site.title == "@elonmusk"
