import pytest

from rssrob.notify import EmailError, SmtpConfig, build_message, send_email


class FakeSMTP:
    """Records the SMTP calls send_email makes (context-manager shaped)."""
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.calls.append(("close",))

    def starttls(self, context=None):
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        self.calls.append(("send", msg))


def test_build_message_plain_and_html():
    m = build_message("f@x", ["t@y"], "sub", "plain body", "<p>hi</p>")
    assert m["From"] == "f@x" and m["To"] == "t@y" and m["Subject"] == "sub"
    assert m.get_body(preferencelist=("plain",)).get_content().strip() == "plain body"
    assert m.get_body(preferencelist=("html",)).get_content().strip() == "<p>hi</p>"


def test_send_email_starttls_and_login():
    cfg = SmtpConfig(host="h", port=587, user="u", password="p", sender="u@x")
    fake = FakeSMTP()
    sent = send_email("a@b", "sub", "body", config=cfg, smtp_factory=lambda: fake)
    kinds = [c[0] for c in fake.calls]
    assert "starttls" in kinds
    assert ("login", "u", "p") in fake.calls
    assert sent == ["a@b"]
    msg = next(c[1] for c in fake.calls if c[0] == "send")
    assert msg["To"] == "a@b" and msg["From"] == "u@x"


def test_send_email_ssl_skips_starttls_and_no_auth():
    cfg = SmtpConfig(host="h", port=465, sender="x@y", use_ssl=True)
    fake = FakeSMTP()
    send_email(["a@b", "c@d"], "s", "b", config=cfg, smtp_factory=lambda: fake)
    kinds = [c[0] for c in fake.calls]
    assert "starttls" not in kinds and "login" not in kinds
    msg = next(c[1] for c in fake.calls if c[0] == "send")
    assert msg["To"] == "a@b, c@d"


def test_send_email_bcc_one_message_hides_recipients():
    cfg = SmtpConfig(host="h", sender="from@x")
    fake = FakeSMTP()
    env = send_email([], "s", "b", bcc=["a@b", "c@d"], config=cfg,
                     smtp_factory=lambda: fake)
    assert env == ["a@b", "c@d"]                     # all recipients returned
    sends = [c for c in fake.calls if c[0] == "send"]
    assert len(sends) == 1                           # a single message
    msg = sends[0][1]
    assert msg["Bcc"] == "a@b, c@d"                  # recipients in Bcc
    assert msg["To"] == "from@x"                     # To defaults to sender


def test_send_email_no_recipients():
    cfg = SmtpConfig(host="h", sender="x@y")
    with pytest.raises(EmailError):
        send_email([], "s", "b", config=cfg, smtp_factory=lambda: FakeSMTP())


def test_from_env_reads(monkeypatch):
    monkeypatch.setenv("RSSROB_SMTP_HOST", "smtp.x")
    monkeypatch.setenv("RSSROB_SMTP_USER", "me@x")
    monkeypatch.setenv("RSSROB_SMTP_PASSWORD", "pw")
    monkeypatch.delenv("RSSROB_SMTP_FROM", raising=False)
    cfg = SmtpConfig.from_env()
    assert cfg.host == "smtp.x" and cfg.user == "me@x"
    assert cfg.sender == "me@x" and cfg.port == 587 and cfg.use_starttls is True


def test_from_env_missing_host(monkeypatch):
    monkeypatch.delenv("RSSROB_SMTP_HOST", raising=False)
    with pytest.raises(EmailError):
        SmtpConfig.from_env()
