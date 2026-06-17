import argparse
import logging
import os
import re
import sys
import time

import yaml

from .config import ConfigError, default_config_path, load_config
from .fetch import Fetcher
from .pipeline import obtain_items, run_cycle
from .scheduler import Scheduler, build_twitter_client, build_wechat_client
from .server import make_server
from .store import Store
from .twitter import X_LOGIN_URL, TwitterAuthError
from .twitter_credential import DEFAULT_PATH as TWITTER_CRED_PATH
from .twitter_credential import credential_from_cookie
from .twitter_credential import save as save_twitter_credential
from .wechat import MP_LOGIN_URL, WeChatAuthError
from .wechat_credential import DEFAULT_PATH as WECHAT_CRED_PATH
from .wechat_credential import credential_from_login, save as save_credential
from . import admin_credential
from .admin_credential import DEFAULT_PATH as ADMIN_CRED_PATH


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="rssrob")
    parser.add_argument("--config", default=default_config_path(),
                        help="config file or directory (default: ./config/ if present, "
                             "else config.yaml, else config.example.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    p_once = sub.add_parser("run-once")
    p_once.add_argument("site")
    p_once.add_argument("--write", action="store_true")
    p_login = sub.add_parser("wechat-login")
    p_login.add_argument("--cookie", metavar="STR",
                         help="mp.weixin.qq.com cookie string to save")
    p_login.add_argument("--token", metavar="N",
                         help="mp.weixin.qq.com token (the token=.. in the URL)")
    p_login.add_argument("--qr-out", metavar="PATH",
                         help="also write the login QR to this SVG file")
    p_search = sub.add_parser("wechat-search")
    p_search.add_argument("name", help="account name to search for")
    p_search.add_argument("--save", metavar="FEED_NAME",
                          help="after picking a match, save it as a wechat feed")
    p_tw_login = sub.add_parser("twitter-login")
    p_tw_login.add_argument("--cookie", metavar="STR",
                            help="full x.com Cookie header to save")
    p_tw_login.add_argument("--proxy", metavar="P",
                            help="proxy for X fetches (e.g. 7890 or socks5://ip:port)")
    p_tw_add = sub.add_parser("twitter-add")
    p_tw_add.add_argument("handle", help="the @handle to follow (no @)")
    p_tw_add.add_argument("--save", metavar="FEED_NAME",
                          help="save it as a twitter feed with this name")
    p_admin = sub.add_parser("set-admin-password")
    p_admin.add_argument("--username", metavar="USER",
                         help="admin username (default: admin)")
    p_admin.add_argument("--password", metavar="PASS",
                         help="admin password (omit to be prompted)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    # WeChat commands don't need a loadable feed config (search --save only needs
    # a writable config path), so handle them before loading the config.
    if args.command == "wechat-login":
        return _wechat_login(args)
    if args.command == "wechat-search":
        return _wechat_search(args)
    if args.command == "twitter-login":
        return _twitter_login(args)
    if args.command == "twitter-add":
        return _twitter_add(args)
    if args.command == "set-admin-password":
        return _set_admin_password(args)

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"config not found: {args.config}", file=sys.stderr)
        return 2

    if args.command == "serve":
        return _serve(config)
    return _run_once(config, args.site, args.write)


def _serve(config) -> int:
    store = Store(config.state_db)
    fetcher = Fetcher()
    scheduler = Scheduler(config, store, fetcher)
    scheduler.start()
    server = make_server(config.output_dir, config.http.host, config.http.port)
    print(f"serving on http://{config.http.host}:{config.http.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down…")
    finally:
        scheduler.stop()
        server.server_close()
        store.close()
    return 0


def _run_once(config, site_name, write) -> int:
    site = next((s for s in config.sites if s.name == site_name), None)
    if site is None:
        print(f"no such site: {site_name}", file=sys.stderr)
        return 2

    fetcher = Fetcher(proxy=site.proxy) if site.proxy else Fetcher()
    wechat_client = build_wechat_client() if site.type == "wechat" else None
    twitter_client = build_twitter_client() if site.type == "twitter" else None
    if write:
        store = Store(config.state_db)
        try:
            inserted = run_cycle(site, store, fetcher, config.output_dir,
                                 wechat_client=wechat_client,
                                 twitter_client=twitter_client)
        finally:
            store.close()
        print(f"{inserted} new item(s); wrote {config.output_dir}/{site.name}.xml")
        return 0

    items, _, _ = obtain_items(site, fetcher, wechat_client=wechat_client,
                               twitter_client=twitter_client)
    print(f"{len(items)} item(s) from {site.name}:")
    for i, it in enumerate(items, 1):
        print(f"{i:>2}. [{it.date}] {it.title}")
        print(f"    {it.link}")
    return 0


def _render_qr(payload, out_path=None) -> None:
    """Print the login QR to the terminal; optionally also write it as SVG."""
    import qrcode

    qr = qrcode.QRCode(border=1)
    qr.add_data(payload)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    if out_path:
        import qrcode.image.svg
        qrcode.make(payload, image_factory=qrcode.image.svg.SvgImage).save(out_path)
        print(f"QR also written to {out_path}")


def _save_wechat_credential(cookie, token) -> int:
    """Build + persist a 公众号 credential from a cookie + token."""
    try:
        cred = credential_from_login(cookie, token, time.time())
    except ValueError as e:
        print(f"invalid login: {e}", file=sys.stderr)
        return 2
    save_credential(WECHAT_CRED_PATH, cred)
    print(f"saved 公众号 credential (token {cred.token}) to {WECHAT_CRED_PATH}")
    return 0


def _print_login_help() -> None:
    print("\nThis needs your OWN registered 公众号 (a free 个人订阅号 is enough; it's")
    print("only ever read). No account yet? Register at https://mp.weixin.qq.com/ →")
    print("立即注册 → 订阅号. RSSRob never posts to it.\n")
    print(f"1. Scan the QR (or open {MP_LOGIN_URL}) and log in to your 公众号.")
    print("2. Copy the token from the address bar (…&token=123456789).")
    print("3. Copy the cookie: DevTools (F12) → Network → click any mp.weixin.qq.com")
    print("   request → Headers → Request Headers → copy the whole `Cookie:` value.")
    print("   (Don't use document.cookie — the login cookies are HttpOnly.)")


def _wechat_login(args) -> int:
    # RSSRob captures the cookie + token from a logged-in mp.weixin.qq.com session
    # (your own 公众号 backend), which the searchbiz/appmsg APIs need.
    if args.cookie or args.token:                 # non-interactive (scriptable)
        return _save_wechat_credential(args.cookie or "", args.token or "")

    _render_qr(MP_LOGIN_URL, args.qr_out)
    _print_login_help()
    # Interactive paste — easier than quoting a long cookie on the command line.
    try:
        token = input("\nPaste the token (or Enter to skip): ").strip()
        cookie = input("Paste the full cookie (or Enter to skip): ").strip()
    except (EOFError, OSError):                    # no stdin (piped / captured)
        token = cookie = ""
    if token and cookie:
        return _save_wechat_credential(cookie, token)
    print("\nnothing saved — re-run `rssrob wechat-login --token <N> --cookie "
          "'<cookie>'`,\nor paste both on the web app's /wechat/login page.")
    return 0


def _wechat_search(args) -> int:
    client = build_wechat_client()
    try:
        accounts = client.search_accounts(args.name)
    except WeChatAuthError:
        print("not logged in — run `rssrob wechat-login`", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"search failed: {e}", file=sys.stderr)
        return 1

    if not accounts:
        print(f"no accounts found for {args.name!r}")
        return 0
    for i, a in enumerate(accounts, 1):
        desc = f" — {a.description}" if a.description else ""
        print(f"{i:>2}. {a.id}  {a.name}{desc}")

    if not args.save:
        print("\nadd `--save <feed-name>` to save one of these as a feed.")
        return 0

    try:
        choice = int(input(f"\nsave which # as feed {args.save!r}? ").strip())
        account = accounts[choice - 1]
    except (ValueError, IndexError):
        print("invalid selection; nothing saved", file=sys.stderr)
        return 1
    path = _write_wechat_feed(args.config, args.save, account)
    print(f"saved feed {args.save!r} → {path}")
    return 0


def _twitter_login(args) -> int:
    # Capture the x.com session cookie (auth_token + ct0) the GraphQL API needs.
    cookie = args.cookie
    if not cookie:
        print(f"Log in at {X_LOGIN_URL}, then copy your Cookie header:")
        print("  DevTools (F12) → Network → click any x.com request → Headers →")
        print("  Request Headers → copy the whole `Cookie:` value.")
        print("  (Don't use document.cookie — auth_token is HttpOnly.)")
        try:
            cookie = input("\nPaste the full cookie (or Enter to skip): ").strip()
        except (EOFError, OSError):
            cookie = ""
    if not cookie:
        print("nothing saved — re-run `rssrob twitter-login --cookie '<cookie>'`.")
        return 0
    try:
        cred = credential_from_cookie(cookie, time.time(), proxy=args.proxy)
    except ValueError as e:
        print(f"invalid cookie: {e}", file=sys.stderr)
        return 2
    save_twitter_credential(TWITTER_CRED_PATH, cred)
    print(f"saved X credential to {TWITTER_CRED_PATH}")
    return 0


def _twitter_add(args) -> int:
    client = build_twitter_client()
    handle = args.handle.lstrip("@")
    try:
        account = client.resolve_user(handle)
    except TwitterAuthError:
        print("not logged in — run `rssrob twitter-login`", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"lookup failed: {e}", file=sys.stderr)
        return 1
    if not account.id:
        print(f"no such account: @{handle}", file=sys.stderr)
        return 1
    print(f"@{account.handle}  id={account.id}  {account.name or ''}")
    if not args.save:
        print("\nadd `--save <feed-name>` to save it as a feed.")
        return 0
    path = _write_twitter_feed(args.config, args.save, account)
    print(f"saved feed {args.save!r} → {path}")
    return 0


def _set_admin_password(args) -> int:
    """Set or change the admin login for the management web UI."""
    username = args.username
    if username is None:
        try:
            username = input("Admin username [admin]: ").strip() or "admin"
        except (EOFError, OSError):
            username = "admin"
    password = args.password
    if password is None:
        import getpass
        try:
            password = getpass.getpass("Admin password: ")
            confirm = getpass.getpass("Confirm password: ")
        except (EOFError, OSError):
            print("no password provided; nothing saved", file=sys.stderr)
            return 2
        if password != confirm:
            print("passwords do not match; nothing saved", file=sys.stderr)
            return 2
    if not password:
        print("password must not be empty; nothing saved", file=sys.stderr)
        return 2
    existing = admin_credential.load(ADMIN_CRED_PATH)
    secret_key = existing.secret_key if existing else None
    cred = admin_credential.create(username, password, time.time(),
                                   secret_key=secret_key)
    admin_credential.save(ADMIN_CRED_PATH, cred)
    print(f"saved admin credential for {cred.username!r} to {ADMIN_CRED_PATH}")
    return 0


def _write_twitter_feed(config_path, feed_name, account) -> str:
    """Write (or upsert) a `type: twitter` feed into the config path."""
    site = {"name": feed_name, "type": "twitter",
            "username": account.handle, "account_id": account.id,
            "account_name": account.name}
    if account.description:
        site["description"] = account.description
    if os.path.isdir(config_path):
        fname = re.sub(r"[^A-Za-z0-9._-]", "-", feed_name) + ".yaml"
        out = os.path.join(config_path, fname)
        with open(out, "w", encoding="utf-8") as f:
            yaml.safe_dump(site, f, allow_unicode=True, sort_keys=False)
        return out
    raw = {}
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    sites = raw.setdefault("sites", [])
    for i, s in enumerate(sites):
        if s.get("name") == feed_name:
            sites[i] = site
            break
    else:
        sites.append(site)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
    return config_path


def _write_wechat_feed(config_path, feed_name, account) -> str:
    """Write (or upsert) a `type: wechat` feed into the config path."""
    site = {"name": feed_name, "type": "wechat",
            "account_id": account.id, "account_name": account.name}
    if account.description:                       # the 公众号's intro -> feed <description>
        site["description"] = account.description
    if os.path.isdir(config_path):
        fname = re.sub(r"[^A-Za-z0-9._-]", "-", feed_name) + ".yaml"
        out = os.path.join(config_path, fname)
        with open(out, "w", encoding="utf-8") as f:
            yaml.safe_dump(site, f, allow_unicode=True, sort_keys=False)
        return out
    # single-file mode: upsert into the one config file
    raw = {}
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    sites = raw.setdefault("sites", [])
    for i, s in enumerate(sites):
        if s.get("name") == feed_name:
            sites[i] = site
            break
    else:
        sites.append(site)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
    return config_path
