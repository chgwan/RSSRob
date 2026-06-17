from rssrob.cli import main


HTML_CFG = """
output_dir: {out}
state_db: {db}
sites:
  - name: ipp
    type: html
    url: http://www.ipp.cas.cn/
    title: IPP
    item: "xpath://h2[normalize-space()='通知公告']/ancestor::div[contains(@class,'ipp2020-item')][1]//div[@class='bd']//ul/li"
    fields:
      title: "xpath:.//a"
      link: "xpath:.//a/@href"
"""


def _cfg(tmp_path, fixtures):
    text = HTML_CFG.format(out=tmp_path / "feeds", db=tmp_path / "db.sqlite")
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_run_once_prints_items(tmp_path, fixtures, make_fetcher, monkeypatch, capsys):
    html = (fixtures / "notices.html").read_bytes()
    monkeypatch.setattr("rssrob.cli.Fetcher",
                        lambda: make_fetcher({"http://www.ipp.cas.cn/": html}))
    rc = main(["--config", _cfg(tmp_path, fixtures), "run-once", "ipp"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "通知一" in out
    assert not (tmp_path / "feeds" / "ipp.xml").exists()   # no --write


def test_run_once_write_persists(tmp_path, fixtures, make_fetcher, monkeypatch):
    html = (fixtures / "notices.html").read_bytes()
    monkeypatch.setattr("rssrob.cli.Fetcher",
                        lambda: make_fetcher({"http://www.ipp.cas.cn/": html}))
    rc = main(["--config", _cfg(tmp_path, fixtures), "run-once", "ipp", "--write"])
    assert rc == 0
    assert (tmp_path / "feeds" / "ipp.xml").exists()


def test_unknown_site_returns_2(tmp_path, fixtures, monkeypatch):
    rc = main(["--config", _cfg(tmp_path, fixtures), "run-once", "nope"])
    assert rc == 2


def test_bad_config_returns_2(tmp_path):
    bad = tmp_path / "c.yaml"
    bad.write_text("sites:\n  - {name: x}\n", encoding="utf-8")  # missing url
    rc = main(["--config", str(bad), "run-once", "x"])
    assert rc == 2


def test_wechat_search_prints_matches(capsys, monkeypatch):
    from rssrob import cli
    from rssrob.wechat import Account

    class FakeClient:
        def search_accounts(self, name):
            return [Account(id="MzAx==", name="某号", description="d")]

    monkeypatch.setattr(cli, "build_wechat_client", lambda: FakeClient())
    rc = cli.main(["wechat-search", "某"])
    out = capsys.readouterr().out
    assert rc == 0 and "MzAx==" in out and "某号" in out


def test_wechat_search_no_credential_returns_2(capsys, monkeypatch):
    from rssrob import cli
    from rssrob.wechat import WeChatAuthError

    class FakeClient:
        def search_accounts(self, name):
            raise WeChatAuthError("no credential")

    monkeypatch.setattr(cli, "build_wechat_client", lambda: FakeClient())
    rc = cli.main(["wechat-search", "某"])
    assert rc == 2


def test_wechat_search_save_writes_feed(tmp_path, capsys, monkeypatch):
    from rssrob import cli
    from rssrob.wechat import Account

    class FakeClient:
        def search_accounts(self, name):
            return [Account(id="MzAx==", name="某号", description="一个测试公众号")]

    monkeypatch.setattr(cli, "build_wechat_client", lambda: FakeClient())
    monkeypatch.setattr("builtins.input", lambda *a: "1")
    cfgdir = tmp_path / "configs"
    cfgdir.mkdir()
    rc = cli.main(["--config", str(cfgdir), "wechat-search", "某", "--save", "myoa"])
    assert rc == 0
    written = (cfgdir / "myoa.yaml").read_text(encoding="utf-8")
    assert "type: wechat" in written and "MzAx==" in written and "某号" in written
    assert "description: 一个测试公众号" in written   # account intro carried over


def test_wechat_login_cookie_saves_credential(tmp_path, capsys, monkeypatch):
    from rssrob import cli
    from rssrob import wechat_credential
    cred_path = str(tmp_path / "cred.json")
    monkeypatch.setattr(cli, "WECHAT_CRED_PATH", cred_path)
    rc = cli.main(["wechat-login", "--cookie", "slave_sid=abc", "--token", "987654321"])
    assert rc == 0
    cred = wechat_credential.load(cred_path)
    assert cred is not None and cred.cookie == "slave_sid=abc" and cred.token == "987654321"


def test_wechat_login_interactive_prompts_and_saves(tmp_path, monkeypatch):
    from rssrob import cli
    from rssrob import wechat_credential
    cred_path = str(tmp_path / "cred.json")
    monkeypatch.setattr(cli, "WECHAT_CRED_PATH", cred_path)
    monkeypatch.setattr(cli, "_render_qr", lambda *a, **k: None)
    answers = iter(["123456789", "slave_sid=abc; slave_user=gh_x"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    rc = cli.main(["wechat-login"])
    assert rc == 0
    cred = wechat_credential.load(cred_path)
    assert cred.token == "123456789" and cred.cookie.startswith("slave_sid=abc")


def test_wechat_login_no_stdin_prints_help(capsys, monkeypatch):
    from rssrob import cli
    monkeypatch.setattr(cli, "_render_qr", lambda *a, **k: None)

    def no_stdin(*a):
        raise EOFError()
    monkeypatch.setattr("builtins.input", no_stdin)
    rc = cli.main(["wechat-login"])
    out = capsys.readouterr().out
    assert rc == 0 and "token" in out and "立即注册" in out


def test_wechat_login_missing_token_returns_2(capsys, monkeypatch):
    from rssrob import cli
    rc = cli.main(["wechat-login", "--cookie", "slave_sid=abc"])  # no --token
    assert rc == 2


def test_twitter_login_saves_credential(tmp_path, monkeypatch):
    from rssrob import cli
    cred_path = tmp_path / "tw.json"
    monkeypatch.setattr("rssrob.cli.TWITTER_CRED_PATH", str(cred_path))
    rc = cli.main(["twitter-login", "--cookie",
                   "auth_token=abc; ct0=xyz", "--proxy", "7890"])
    assert rc == 0
    import json
    saved = json.loads(cred_path.read_text(encoding="utf-8"))
    assert saved["auth_token"] == "abc" and saved["csrf_token"] == "xyz"
    assert saved["proxy"] == "7890"


def test_twitter_login_rejects_cookie_without_tokens(tmp_path, monkeypatch):
    from rssrob import cli
    monkeypatch.setattr("rssrob.cli.TWITTER_CRED_PATH", str(tmp_path / "tw.json"))
    rc = cli.main(["twitter-login", "--cookie", "lang=en"])
    assert rc == 2


def test_set_admin_password_writes_credential(tmp_path, monkeypatch):
    from rssrob import cli
    from rssrob import admin_credential
    cred_path = str(tmp_path / "admin.json")
    monkeypatch.setattr(cli, "ADMIN_CRED_PATH", cred_path)
    rc = cli.main(["set-admin-password", "--username", "admin",
                   "--password", "s3cret"])
    assert rc == 0
    cred = admin_credential.load(cred_path)
    assert cred is not None
    assert admin_credential.verify(cred, "admin", "s3cret") is True


def test_set_admin_password_preserves_secret_key(tmp_path, monkeypatch):
    from rssrob import cli
    from rssrob import admin_credential
    cred_path = str(tmp_path / "admin.json")
    monkeypatch.setattr(cli, "ADMIN_CRED_PATH", cred_path)
    cli.main(["set-admin-password", "--username", "admin", "--password", "one"])
    first = admin_credential.load(cred_path).secret_key
    cli.main(["set-admin-password", "--username", "admin", "--password", "two"])
    second = admin_credential.load(cred_path)
    assert second.secret_key == first                  # key preserved
    assert admin_credential.verify(second, "admin", "two") is True
