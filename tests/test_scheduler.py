import time

from rssrob.config import Config, HttpConfig, Site
from rssrob.scheduler import Scheduler
from rssrob.store import Store


def _html_site():
    return Site(
        name="ipp", url="http://www.ipp.cas.cn/", type="html", title="IPP",
        item=(
            "xpath://h2[normalize-space()='通知公告']"
            "/ancestor::div[contains(@class,'ipp2020-item')][1]//div[@class='bd']//ul/li"
        ),
        fields={"title": "xpath:.//a", "link": "xpath:.//a/@href"},
        interval=3600,
    )


def _config(tmp_path, site):
    return Config(output_dir=str(tmp_path / "feeds"),
                  state_db=str(tmp_path / "db.sqlite"),
                  http=HttpConfig(), sites=[site])


def test_run_site_scrapes_and_writes(tmp_path, fixtures, make_fetcher):
    html = (fixtures / "notices.html").read_bytes()
    fetcher = make_fetcher({"http://www.ipp.cas.cn/": html})
    site = _html_site()
    store = Store(str(tmp_path / "db.sqlite"))
    sched = Scheduler(_config(tmp_path, site), store, fetcher)
    sched._run_site(site, now=1000.0)
    assert (tmp_path / "feeds" / "ipp.xml").exists()
    assert len(store.recent("ipp", 10)) == 2


def test_run_site_isolates_errors(tmp_path):
    class Boom:
        def get(self, *a, **k):
            raise RuntimeError("network down")

    site = _html_site()
    store = Store(str(tmp_path / "db.sqlite"))
    sched = Scheduler(_config(tmp_path, site), store, Boom())
    # must not raise — the error is caught and logged
    sched._run_site(site, now=1000.0)
    assert store.recent("ipp", 10) == []


def _wechat_site():
    return Site(name="oa", url=None, type="wechat", account_id="x",
                account_name="某号", interval=7200)


def test_scheduler_builds_wechat_client_lazily(tmp_path, monkeypatch):
    import rssrob.scheduler as sch_mod
    site = _wechat_site()
    sentinel = object()
    monkeypatch.setattr(sch_mod, "build_wechat_client", lambda: sentinel)
    captured = {}
    monkeypatch.setattr(sch_mod, "run_cycle",
        lambda s, st, f, o, now, wechat_client=None, twitter_client=None:
            captured.update(wc=wechat_client) or 0)
    sched = Scheduler(_config(tmp_path, site), store=object(), fetcher=object())
    sched._run_site(site, now=0.0)
    assert captured["wc"] is sentinel


def test_scheduler_isolates_auth_error(tmp_path, monkeypatch):
    import rssrob.scheduler as sch_mod
    from rssrob.wechat import WeChatAuthError
    site = _wechat_site()
    monkeypatch.setattr(sch_mod, "build_wechat_client", lambda: object())

    def boom(*a, **k):
        raise WeChatAuthError("login expired")
    monkeypatch.setattr(sch_mod, "run_cycle", boom)
    sched = Scheduler(_config(tmp_path, site), store=object(), fetcher=object())
    sched._run_site(site, now=0.0)   # must not raise


def test_scheduler_backs_off_on_rate_limit(tmp_path, monkeypatch):
    import rssrob.scheduler as sch_mod
    from rssrob.wechat import WeChatRateLimited
    site = _wechat_site()
    monkeypatch.setattr(sch_mod, "build_wechat_client", lambda: object())

    def boom(*a, **k):
        raise WeChatRateLimited("freq control")
    monkeypatch.setattr(sch_mod, "run_cycle", boom)
    sched = Scheduler(_config(tmp_path, site), store=object(), fetcher=object())
    sched._run_site(site, now=1000.0)
    assert sched._backoff.get("oa", 0) >= 1000.0 + 1800   # pushed out


def test_start_stop_is_clean(tmp_path, fixtures, make_fetcher):
    html = (fixtures / "notices.html").read_bytes()
    fetcher = make_fetcher({"http://www.ipp.cas.cn/": html})
    site = _html_site()
    store = Store(str(tmp_path / "db.sqlite"))
    sched = Scheduler(_config(tmp_path, site), store, fetcher)
    sched.start()
    time.sleep(0.2)        # first cycle runs immediately (next_run starts at 0)
    sched.stop()
    assert (tmp_path / "feeds" / "ipp.xml").exists()


def test_build_twitter_client_uses_env_proxy(monkeypatch, tmp_path):
    import json
    from rssrob import scheduler
    cred = tmp_path / "tw.json"
    cred.write_text(json.dumps({"auth_token": "a", "csrf_token": "c",
                                "updated_at": 1.0, "proxy": None}), encoding="utf-8")
    monkeypatch.setenv("RSSROB_TWITTER_CREDENTIAL", str(cred))
    monkeypatch.setattr(scheduler, "TWITTER_CRED_PATH", str(cred))
    monkeypatch.setenv("RSSROB_PROXY", "7890")
    client = scheduler.build_twitter_client()
    assert client.transport.proxy == "http://127.0.0.1:7890"


def test_scheduler_passes_twitter_client(monkeypatch, tmp_path):
    from rssrob.config import Config, HttpConfig, Site
    from rssrob.scheduler import Scheduler

    captured = {}
    def fake_run_cycle(site, store, fetcher, output_dir, now, wechat_client=None,
                       twitter_client=None):
        captured["twitter_client"] = twitter_client
        return 0
    monkeypatch.setattr("rssrob.scheduler.run_cycle", fake_run_cycle)

    cfg = Config(output_dir=str(tmp_path), state_db=":memory:", http=HttpConfig(),
                 sites=[Site(name="elon", type="twitter", username="elonmusk")])
    sch = Scheduler(cfg, store=None, fetcher=object())
    sentinel = object()
    monkeypatch.setattr(sch, "_twitter", lambda: sentinel)
    sch._run_site(cfg.sites[0], now=0.0)
    assert captured["twitter_client"] is sentinel


def test_scheduler_uses_per_site_proxy_fetcher(monkeypatch, tmp_path):
    from rssrob.config import Config, HttpConfig, Site
    from rssrob.scheduler import Scheduler

    seen = {}
    def fake_run_cycle(site, store, fetcher, output_dir, now, wechat_client=None,
                       twitter_client=None):
        seen["proxy"] = getattr(fetcher, "proxy", "MISSING")
        return 0
    monkeypatch.setattr("rssrob.scheduler.run_cycle", fake_run_cycle)

    cfg = Config(output_dir=str(tmp_path), state_db=":memory:", http=HttpConfig(),
                 sites=[Site(name="s", type="rss", url="http://x", proxy="http://127.0.0.1:9")])
    sch = Scheduler(cfg, store=None, fetcher=object())
    sch._run_site(cfg.sites[0], now=0.0)
    assert seen["proxy"] == "http://127.0.0.1:9"
