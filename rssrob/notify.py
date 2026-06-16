"""Send email via SMTP using the standard library.

Credentials and server come from environment variables (keeps secrets out of
the committed config):

    RSSROB_SMTP_HOST       required, e.g. smtp.gmail.com
    RSSROB_SMTP_PORT       default 587
    RSSROB_SMTP_USER       username for auth (e.g. you@gmail.com)
    RSSROB_SMTP_PASSWORD   password / app password (Gmail needs an app password)
    RSSROB_SMTP_FROM       From address (default: RSSROB_SMTP_USER)
    RSSROB_SMTP_SSL        "1"/"true" -> implicit TLS (SMTPS, e.g. port 465)
    RSSROB_SMTP_STARTTLS   default true -> STARTTLS on a plain connection (587)

Quick test:
    RSSROB_SMTP_HOST=smtp.gmail.com RSSROB_SMTP_USER=you@gmail.com \\
    RSSROB_SMTP_PASSWORD=app_password \\
    python -m rssrob.notify --to you@gmail.com --subject "hi" --body "test"
"""

import argparse
import os
import smtplib
import ssl
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from typing import List, Optional, Sequence, Union


class EmailError(Exception):
    pass


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def load_dotenv(*paths: str) -> None:
    """Load KEY=VALUE lines from dotenv file(s) into os.environ, *without*
    overriding existing env vars (so real env always wins). Missing files are
    skipped. Defaults to `.env` (relative to CWD).

    This is a temporary convenience for local testing; in production set the
    RSSROB_SMTP_* variables in the real environment instead.
    """
    for path in (paths or (".env",)):
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


@dataclass
class SmtpConfig:
    host: str
    port: int = 587
    user: Optional[str] = None
    password: Optional[str] = None
    sender: Optional[str] = None
    use_ssl: bool = False        # implicit TLS (SMTPS)
    use_starttls: bool = True    # STARTTLS on a plain connection

    @classmethod
    def from_env(cls) -> "SmtpConfig":
        host = os.environ.get("RSSROB_SMTP_HOST")
        if not host:
            raise EmailError("RSSROB_SMTP_HOST is not set")
        user = os.environ.get("RSSROB_SMTP_USER")
        return cls(
            host=host,
            port=int(os.environ.get("RSSROB_SMTP_PORT", "587")),
            user=user,
            password=os.environ.get("RSSROB_SMTP_PASSWORD"),
            sender=os.environ.get("RSSROB_SMTP_FROM") or user,
            use_ssl=_env_bool("RSSROB_SMTP_SSL", False),
            use_starttls=_env_bool("RSSROB_SMTP_STARTTLS", True),
        )


def build_message(sender: str, recipients: Sequence[str], subject: str,
                  body: str, html: Optional[str] = None,
                  bcc: Optional[Sequence[str]] = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    if recipients:
        msg["To"] = ", ".join(recipients)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    msg.set_content(body or "")
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def send_email(to: Union[str, Sequence[str]], subject: str, body: str,
               html: Optional[str] = None, bcc: Union[str, Sequence[str], None] = None,
               config: Optional[SmtpConfig] = None, smtp_factory=None) -> List[str]:
    """Send one email to all recipients. `to` and `bcc` are an address or list.
    Use `bcc` to send a single email to many recipients without exposing the
    list. `config` defaults to SmtpConfig.from_env(); `smtp_factory` is
    injectable for testing. Returns the full recipient list on success."""
    cfg = config or SmtpConfig.from_env()
    if not cfg.sender:
        raise EmailError("no From address (set RSSROB_SMTP_FROM or RSSROB_SMTP_USER)")
    to_list = [to] if isinstance(to, str) else list(to or [])
    to_list = [r for r in to_list if r]
    bcc_list = [bcc] if isinstance(bcc, str) else list(bcc or [])
    bcc_list = [r for r in bcc_list if r]
    envelope = to_list + bcc_list
    if not envelope:
        raise EmailError("no recipients")

    display_to = to_list or [cfg.sender]   # ensure a valid To even when Bcc-only
    msg = build_message(cfg.sender, display_to, subject, body, html,
                        bcc=bcc_list or None)

    if smtp_factory is None:
        if cfg.use_ssl:
            ctx = ssl.create_default_context()
            smtp_factory = lambda: smtplib.SMTP_SSL(cfg.host, cfg.port, context=ctx)
        else:
            smtp_factory = lambda: smtplib.SMTP(cfg.host, cfg.port)

    with smtp_factory() as server:
        if not cfg.use_ssl and cfg.use_starttls:
            server.starttls(context=ssl.create_default_context())
        if cfg.user and cfg.password:
            server.login(cfg.user, cfg.password)
        server.send_message(msg)
    return envelope


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="rssrob.notify",
        description="Send a test email via SMTP (server/credentials from env vars).")
    p.add_argument("--to", required=True, action="append",
                   help="recipient address (repeat for multiple)")
    p.add_argument("--subject", default="RSSRob test email")
    p.add_argument("--body", default="This is a test email from RSSRob.")
    p.add_argument("--html", help="optional HTML body")
    p.add_argument("--no-dotenv", action="store_true",
                   help="do not load configs/.env / .env (use real env only)")
    args = p.parse_args(argv)

    if not args.no_dotenv:
        load_dotenv()

    try:
        sent = send_email(args.to, args.subject, args.body, html=args.html)
    except EmailError as e:
        print(f"email error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # network / auth / smtp errors
        print(f"send failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"sent to: {', '.join(sent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
