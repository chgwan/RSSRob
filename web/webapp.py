"""RSSRob preview web app.

A small Flask app that renders the items RSSRob would extract from a configured
site, using the real extraction machinery (``rssrob.config`` +
``rssrob.pipeline.obtain_items``). It fetches the page live and falls back to a
saved local copy when the network fails. This evolves the one-shot
``select_preview.py`` prototype into a served, browsable page.

Run (from the repo root):
    $CLAUDE_CODE_PYTHON web/webapp.py
then open http://127.0.0.1:5000/  (switch sites with ?site=<name>)

To reach content behind a firewall, route outbound fetches through a proxy:
    $CLAUDE_CODE_PYTHON web/webapp.py --proxy-port 7890     # http://127.0.0.1:7890
    $CLAUDE_CODE_PYTHON web/webapp.py --proxy socks5://127.0.0.1:1080
or set RSSROB_PROXY in the environment.
"""

import argparse
import io
import os
import re
import sys
import time
from pathlib import Path

# This file lives in web/; put the repo root on sys.path so `import rssrob`
# works, and anchor config/sample paths to the repo root (CWD-independent).
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import lxml.etree
import lxml.html
import requests
import yaml
from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   send_file, session, url_for)

from rssrob.article import fetch_article
from rssrob.backup import build_backup, restore_backup
from rssrob.config import ConfigError, load_config, normalize_proxy
from rssrob.extract import extract_items
from rssrob import admin_credential
from rssrob.pipeline import obtain_items
from rssrob.rss import parse_feed
from rssrob.scheduler import build_twitter_client, build_wechat_client
from rssrob.subscribers import Subscribers
from rssrob.twitter import X_LOGIN_URL, TwitterAuthError
from rssrob.twitter_credential import DEFAULT_PATH as TWITTER_CRED_PATH
from rssrob.twitter_credential import credential_from_cookie
from rssrob.twitter_credential import load as load_twitter_credential
from rssrob.twitter_credential import save as save_twitter_credential
from rssrob.wechat import MP_LOGIN_URL, WeChatAuthError
from rssrob.wechat_credential import DEFAULT_PATH as WECHAT_CRED_PATH
from rssrob.wechat_credential import credential_from_login
from rssrob.wechat_credential import load as load_credential
from rssrob.wechat_credential import save as save_credential

# Reads config.yaml when present, otherwise the bundled example; saves always go
# to config.yaml. Set RSSROB_CONFIG to override both read and write.
CONFIG_OVERRIDE = os.environ.get("RSSROB_CONFIG")


def _config_path():
    if CONFIG_OVERRIDE:
        return CONFIG_OVERRIDE
    cdir = REPO_ROOT / "configs"
    if cdir.is_dir():
        return str(cdir)
    cy = REPO_ROOT / "config.yaml"
    return str(cy if cy.exists() else REPO_ROOT / "config.example.yaml")


def _save_path():
    if CONFIG_OVERRIDE:
        return CONFIG_OVERRIDE
    cdir = REPO_ROOT / "configs"
    if cdir.is_dir():
        return str(cdir)
    return str(REPO_ROOT / "config.yaml")


# Live fetch falls back to these saved copies (keyed by url) on a network error,
# so the preview keeps working offline. (Saved pages live in samples/.)
FALLBACK_FILES = {
    "http://www.ipp.cas.cn/": str(REPO_ROOT / "samples" / "ipp_page.html"),
    "http://www.ipp.cas.cn/tzgg/tz_zhb/202606/t20260615_841876.html":
        str(REPO_ROOT / "samples" / "site.html"),
}

DESC_LEN = 160                 # short-description length
_ITEM_CACHE: dict = {}         # url -> (full_title, description) cached across requests

# Global fallback proxy, applied to any fetch whose feed has no per-feed proxy.
# Per-feed proxies live in each site's `proxy:` config. Set RSSROB_PROXY or pass
# --proxy / --proxy-port on the CLI (see __main__) for the global default.
PROXY_URL = os.environ.get("RSSROB_PROXY") or None

# Per-feed email subscriber list (gitignored; the notify job sends to these).
SUBS = Subscribers(str(REPO_ROOT / "var" / "subscribers.json"))


# Defaults for the selector playground (the IPP 通知公告 example).
PLAYGROUND_DEFAULTS = {
    "url": "http://www.ipp.cas.cn/",
    "item": ("xpath://h2[normalize-space()='通知公告']/ancestor::div"
             "[contains(@class,'ipp2020-item')][1]//div[@class='bd']//ul/li"),
    "title_sel": "xpath:.//a",
    "link_sel": "xpath:.//a/@href",
    "date_sel": "xpath:.//span",
    "proxy": "",
}

app = Flask(__name__)

# Admin login credential (gitignored, under var/). When present it gates the
# whole UI; absent means "open mode" (today's behavior) with a setup banner.
ADMIN_CRED_PATH = admin_credential.DEFAULT_PATH

# Endpoints reachable without a session (the login/logout/first-run flow + static).
_PUBLIC_ENDPOINTS = {"login", "logout", "setup", "static"}


def _load_admin():
    """The current admin credential (read fresh each call), or None in open mode."""
    return admin_credential.load(ADMIN_CRED_PATH)


def _is_loopback():
    """True when the request comes from localhost (no X-Forwarded-For parsing)."""
    return request.remote_addr in ("127.0.0.1", "::1")


def _safe_next(target):
    """Only allow same-site relative redirect targets."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return None


# Sign session cookies with the persisted key when a credential exists at boot;
# otherwise an ephemeral key (no sessions are issued in open mode anyway).
_boot_cred = _load_admin()
app.secret_key = _boot_cred.secret_key if _boot_cred else os.urandom(32)


@app.before_request
def _require_login():
    cred = _load_admin()
    if cred is None:                                  # open mode: no auth
        return None
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    if session.get("user"):
        return None
    return redirect(url_for("login", next=request.path))


@app.context_processor
def _inject_admin():
    cred = _load_admin()
    return {
        "admin_required": cred is not None,
        "admin_user": session.get("user"),
        "admin_can_setup": cred is None and _is_loopback(),
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    cred = _load_admin()
    if cred is None:                                  # nothing to log into
        return redirect(url_for("index"))
    nxt = _safe_next(request.values.get("next"))
    if session.get("user"):
        return redirect(nxt or url_for("index"))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if admin_credential.verify(cred, username, password):
            app.secret_key = cred.secret_key          # match the persisted key (survives restart)
            session["user"] = cred.username
            session.permanent = True
            return redirect(nxt or url_for("index"))
        error = "incorrect username or password"      # same for bad user or pass
    return render_template("login.html", error=error, next=nxt or "")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if _load_admin() is not None:                     # first-run only
        return redirect(url_for("login"))
    if not _is_loopback():
        return render_template(
            "error.html",
            message="set the admin password locally: run "
                    "`rssrob set-admin-password` on the host, or open this "
                    "page from localhost."), 403
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip() or "admin"
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        if not password:
            error = "password must not be empty"
        elif password != confirm:
            error = "passwords do not match"
        else:
            cred = admin_credential.create(username, password, time.time())
            admin_credential.save(ADMIN_CRED_PATH, cred)
            app.secret_key = cred.secret_key          # sign sessions with the persisted key (survives restart)
            session["user"] = cred.username
            session.permanent = True
            return redirect(url_for("index"))
    return render_template("setup.html", error=error)


def _strip_html(s):
    if s and "<" in s:
        try:
            frag = lxml.html.fromstring(s)
            lxml.etree.strip_elements(frag, "script", "style", with_tail=False)
            return frag.text_content()
        except Exception:
            return s
    return s


# Leading CSS rule blocks (e.g. Word/WPS exports dump "@page{...} p{...}" as text).
_LEADING_CSS = re.compile(r"^(?:\s*[^{}<>]*\{[^{}]*\}\s*)+")


def _clean_desc(text):
    if not text:
        return text
    return _LEADING_CSS.sub("", text).strip()


def _shorten(text, n=DESC_LEN):
    if not text:
        return None
    text = re.sub(r"\s+", " ", _clean_desc(text)).strip()
    if not text:
        return None
    return text if len(text) <= n else text[:n].rstrip() + "…"


def _article_kwargs(article_sel):
    """Map a feed's `article` config block to fetch_article keyword selectors."""
    if not article_sel:
        return {}
    keys = {"title": "title_selector", "content": "content_selector",
            "date": "date_selector"}
    return {keys[k]: v for k, v in article_sel.items() if k in keys and v}


def enrich(item, fetcher, article_sel=None):
    """Return (display_title, description) for an item.

    For rss items the list title and summary are already full. For html items
    the list title is often truncated by the source page, so we follow the link
    and use the article's full title plus a body snippet, using the feed's own
    `article` selectors (IPP defaults otherwise). Results cached."""
    if item.summary:                       # rss: title + summary already complete
        return item.title, _shorten(_strip_html(item.summary))
    if not item.link:
        return item.title, None
    if item.link not in _ITEM_CACHE:
        try:
            art = fetch_article(item.link, fetcher, **_article_kwargs(article_sel))
            _ITEM_CACHE[item.link] = (art.title or item.title,
                                      _shorten(_strip_html(art.content_text)))
        except Exception:
            _ITEM_CACHE[item.link] = (item.title, None)
    full_title, desc = _ITEM_CACHE[item.link]
    return (full_title or item.title), desc


class FallbackFetcher:
    """Fetch live; fall back to a saved local file on failure.

    Shares the ``get`` shape of ``rssrob.fetch.Fetcher`` so it drops straight
    into ``rssrob.pipeline.obtain_items``. Records which source was used so the
    page can show a live/offline badge.
    """

    def __init__(self, fallback_files, proxy=None):
        self.fallback_files = fallback_files
        self.proxy = proxy or PROXY_URL   # per-feed proxy, else the global default
        self.source = None   # "live" | "saved"
        self.error = None    # the live error message when we fell back

    def get(self, url, timeout=20, user_agent="RSSRob/0.1"):
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        try:
            resp = requests.get(url, timeout=timeout,
                                headers={"User-Agent": user_agent},
                                proxies=proxies)
            resp.raise_for_status()
            self.source = "live"
            return resp.content
        except Exception as e:
            path = self.fallback_files.get(url)
            if path and os.path.exists(path):
                self.source = "saved"
                self.error = str(e)
                with open(path, "rb") as f:
                    return f.read()
            raise


@app.route("/")
def index():
    try:
        config = load_config(_config_path())
    except (ConfigError, FileNotFoundError) as e:
        return render_template("error.html", message=f"config error: {e}"), 500

    if not config.sites:
        return render_template("error.html", message="no sites configured"), 500

    site_name = request.args.get("site", config.sites[0].name)
    site = next((s for s in config.sites if s.name == site_name), None)
    if site is None:
        abort(404, description=f"no such site: {site_name}")

    fetcher = FallbackFetcher(FALLBACK_FILES, proxy=site.proxy)
    # wechat/twitter feeds are fetched via their API clients, not an HTTP fetch.
    wechat_client = _wechat_client() if site.type == "wechat" else None
    twitter_client = _twitter_client() if site.type == "twitter" else None
    try:
        items, feed_title, feed_desc = obtain_items(
            site, fetcher, wechat_client=wechat_client,
            twitter_client=twitter_client)
    except WeChatAuthError:
        return render_template(
            "error.html",
            message="not logged in to mp.weixin.qq.com — open the "
                    "“wechat 订阅号” page and paste your cookie + token first.",
            sites=config.sites,
            active=site.name,
        ), 502
    except TwitterAuthError:
        return render_template(
            "error.html",
            message="not logged in to X — open the “twitter” page and "
                    "paste your cookie first.",
            sites=config.sites,
            active=site.name,
        ), 502
    except Exception as e:
        if site.type == "twitter":
            src = f"@{site.username}"
        else:
            src = site.url or f"公众号 {site.account_name or site.account_id}"
        return render_template(
            "error.html",
            message=f"could not load {src}: {e}",
            sites=config.sites,
            active=site.name,
        ), 502

    # remember how the page itself was loaded before article fetches reuse a fetcher
    main_source, main_error = fetcher.source, fetcher.error

    # full title + short description per item (separate fetcher so it doesn't
    # clobber the page's live/saved badge); same per-feed proxy
    article_fetcher = FallbackFetcher(FALLBACK_FILES, proxy=site.proxy)
    entries = []
    for it in items:
        title, desc = enrich(it, article_fetcher, site.article)
        entries.append({"item": it, "title": title, "desc": desc})

    subs = SUBS.items(site.name)          # {email: hours}
    return render_template(
        "preview.html",
        site=site,
        sites=config.sites,
        active=site.name,
        entries=entries,
        source=main_source,
        fetch_error=main_error,
        display_title=site.title or feed_title or site.name,
        subscriber_count=len(subs),
        subscribers=subs,
        show_subs=bool(request.args.get("show_subs")),
        subscribed=request.args.get("subscribed"),
        sub_error=request.args.get("sub_error"),
    )


@app.route("/subscribe", methods=["POST"])
def subscribe():
    """Add an email to a feed's subscriber list (for email update notifications)."""
    site = (request.form.get("site") or "").strip()
    email = (request.form.get("email") or "").strip()
    if not site:
        abort(400, description="missing feed")
    hours = request.form.get("hours") or 24
    status = SUBS.add(site, email, hours)
    if status == "added":
        return redirect(url_for("index", site=site, subscribed=email, show_subs=1))
    msg = "already subscribed" if status == "exists" else "please enter a valid email address"
    return redirect(url_for("index", site=site, sub_error=msg, show_subs=1))


@app.route("/unsubscribe", methods=["POST"])
def unsubscribe():
    """Remove an email from a feed's subscriber list."""
    site = (request.form.get("site") or "").strip()
    email = (request.form.get("email") or "").strip()
    if not site:
        abort(400, description="missing feed")
    SUBS.remove(site, email)
    return redirect(url_for("index", site=site, show_subs=1))


@app.route("/set-frequency", methods=["POST"])
def set_frequency():
    """Update an email's digest frequency (hours), email-level."""
    email = (request.form.get("email") or "").strip()
    hours = request.form.get("hours") or 24
    SUBS.set_freq(email, hours)
    return redirect(url_for("email_list", updated=email))


@app.route("/subscribers")
def email_list():
    """List every subscriber email and which feeds it's subscribed to."""
    try:
        sites = load_config(_config_path()).sites
    except Exception:
        sites = []
    return render_template("subscribers.html", sites=sites, active=None,
                           by_email=SUBS.by_email(),
                           updated=request.args.get("updated"),
                           added=request.args.get("added"),
                           add_error=request.args.get("add_error"))


@app.route("/add-feed", methods=["POST"])
def add_feed():
    """Subscribe an email to a feed from the subscribers page."""
    email = (request.form.get("email") or "").strip()
    site = (request.form.get("site") or "").strip()
    hours = request.form.get("hours") or 24
    if not site:
        return redirect(url_for("email_list", add_error="choose a feed to add"))
    status = SUBS.add(site, email, hours)
    if status == "invalid":
        return redirect(url_for("email_list", add_error="enter a valid email address"))
    return redirect(url_for("email_list", added=email))


def _terms(s):
    """Split a comma/newline separated list into trimmed, non-empty terms."""
    return [t.strip() for t in re.split(r"[,\n]", s or "") if t.strip()]


def _matches_any(value, terms, regex):
    if regex:
        for p in terms:
            try:
                if re.search(p, value, re.I):
                    return True
            except re.error:
                continue
        return False
    low = value.lower()
    return any(t.lower() in low for t in terms)


def apply_filter(items, include, exclude, field, regex):
    """Tag each item kept/dropped by include/exclude terms on a chosen field.

    Keep rule: passes include (or none given) AND matches no exclude term."""
    inc, exc = _terms(include), _terms(exclude)
    results = []
    for it in items:
        value = getattr(it, field, None) or ""
        kept, reason = True, "kept"
        if inc and not _matches_any(value, inc, regex):
            kept, reason = False, "no include match"
        elif exc and _matches_any(value, exc, regex):
            kept, reason = False, "excluded"
        results.append({"item": it, "kept": kept, "reason": reason})
    return results


@app.route("/playground")
def playground():
    # Optional: load configured sites (for nav + ?site= prefill).
    try:
        config = load_config(_config_path())
        sites = config.sites
    except Exception:
        sites = []

    # Layer defaults: hardcoded -> selected site -> explicit query args.
    defaults = dict(PLAYGROUND_DEFAULTS)
    prefill_type = None
    site_name = request.args.get("site")
    if site_name:
        site = next((s for s in sites if s.name == site_name), None)
        if site:
            prefill_type = site.type
            defaults["url"] = site.url
            defaults["proxy"] = site.proxy or ""
            if site.type == "html":
                defaults.update(
                    item=site.item or defaults["item"],
                    title_sel=site.fields.get("title", ""),
                    link_sel=site.fields.get("link", ""),
                    date_sel=site.fields.get("date", ""),
                )
    form = {k: request.args.get(k, v) for k, v in defaults.items()}
    ptype = request.args.get("type") or prefill_type or "html"
    include = request.args.get("include", "")
    exclude = request.args.get("exclude", "")
    field = request.args.get("field", "title")
    regex = request.args.get("regex") == "on"

    results = error = source = None
    kept_n = total = 0
    try:
        fetcher = FallbackFetcher(FALLBACK_FILES, proxy=normalize_proxy(form.get("proxy")))
        content = fetcher.get(form["url"])
        source = fetcher.source
        if ptype == "rss":
            items = parse_feed(content, form["url"]).items
        else:
            html = content.decode("utf-8", errors="replace")
            fields = {name: sel for name, sel in
                      (("title", form["title_sel"]), ("link", form["link_sel"]),
                       ("date", form["date_sel"])) if sel.strip()}
            items = extract_items(html, form["url"], form["item"], fields)
        results = apply_filter(items, include, exclude, field, regex)
        total = len(results)
        kept_n = sum(1 for r in results if r["kept"])
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    return render_template(
        "playground.html",
        sites=sites, active=None,
        form=form, ptype=ptype, include=include, exclude=exclude, field=field, regex=regex,
        results=results, error=error, source=source, total=total, kept_n=kept_n,
        saved=request.args.get("saved"), save_error=request.args.get("save_error"),
    )


def _load_raw(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _form_params(src):
    """All playground inputs from a form/args, for round-tripping in a redirect."""
    params = {k: src.get(k, "") for k in
              ("type", "url", "item", "title_sel", "link_sel", "date_sel", "proxy",
               "include", "exclude", "field", "name", "site_title")}
    if src.get("regex") == "on":
        params["regex"] = "on"
    return params


@app.route("/save", methods=["POST"])
def save():
    """Persist the tested selectors + filter from the playground as a config site."""
    name = (request.form.get("name") or "").strip()
    if not name:
        # keep everything the user entered so nothing is lost
        return redirect(url_for("playground", save_error="a site name is required to save",
                                **_form_params(request.form)))

    ptype = request.form.get("type", "html")
    title_sel = request.form.get("title_sel", "").strip()
    link_sel = request.form.get("link_sel", "").strip()
    date_sel = request.form.get("date_sel", "").strip()
    include = request.form.get("include", "")
    exclude = request.form.get("exclude", "")
    field = request.form.get("field", "title")
    regex = request.form.get("regex") == "on"

    site = {"name": name, "type": ptype, "url": request.form.get("url", "").strip()}
    if ptype == "html":
        site["item"] = request.form.get("item", "").strip()
        site["fields"] = {k: v for k, v in (("title", title_sel), ("link", link_sel),
                                            ("date", date_sel)) if v}
    site_title = request.form.get("site_title", "").strip()
    if site_title:
        site["title"] = site_title

    proxy = normalize_proxy(request.form.get("proxy"))
    if proxy:
        site["proxy"] = proxy

    flt = {}
    if _terms(include):
        flt["include"] = _terms(include)
    if _terms(exclude):
        flt["exclude"] = _terms(exclude)
    if field and field != "title":
        flt["field"] = field
    if regex:
        flt["regex"] = True
    if flt:
        site["filter"] = flt

    path = _save_path()
    try:
        if os.path.isdir(path):
            # folder mode: one file per feed (config/<name>.yaml)
            fname = re.sub(r"[^A-Za-z0-9._-]", "-", name) + ".yaml"
            with open(os.path.join(path, fname), "w", encoding="utf-8") as f:
                yaml.safe_dump(site, f, allow_unicode=True, sort_keys=False)
        else:
            # single-file mode: upsert into the one config file
            raw = _load_raw(path) or _load_raw(_config_path())
            raw.setdefault("output_dir", "./var/feeds")
            raw.setdefault("state_db", "./var/rssrob.db")
            raw.setdefault("http", {"host": "127.0.0.1", "port": 8080})
            sites = raw.setdefault("sites", [])
            for i, s in enumerate(sites):
                if s.get("name") == name:
                    sites[i] = site            # update existing
                    break
            else:
                sites.append(site)             # add new
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
    except OSError as e:
        return redirect(url_for("playground", save_error=f"could not write {path}: {e}",
                                **_form_params(request.form)))

    # round-trip back to the playground: selectors load from the saved site,
    # filter values come back via the query string, plus a success banner.
    params = {"site": name, "saved": name,
              "include": include, "exclude": exclude, "field": field}
    if regex:
        params["regex"] = "on"
    return redirect(url_for("playground", **params))


# --- WeChat 订阅号 (公众号平台) ---------------------------------------------
# One shared client across requests; rebuilt when the user logs in via
# /wechat/login, then reused by search.
_WECHAT_CLIENT = None


def _wechat_client():
    global _WECHAT_CLIENT
    if _WECHAT_CLIENT is None:
        _WECHAT_CLIENT = build_wechat_client()
    return _WECHAT_CLIENT


_TWITTER_CLIENT = None


def _twitter_client():
    global _TWITTER_CLIENT
    if _TWITTER_CLIENT is None:
        _TWITTER_CLIENT = build_twitter_client()
    return _TWITTER_CLIENT


def _qr_svg(payload):
    """Render a payload as an inline SVG QR code string."""
    import io

    import qrcode
    import qrcode.image.svg
    img = qrcode.make(payload, image_factory=qrcode.image.svg.SvgImage)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def _safe_sites():
    try:
        return load_config(_config_path()).sites
    except Exception:
        return []


def _write_site(site):
    """Persist one feed dict to the config (folder: one file; else upsert)."""
    name = site["name"]
    path = _save_path()
    if os.path.isdir(path):
        fname = re.sub(r"[^A-Za-z0-9._-]", "-", name) + ".yaml"
        out = os.path.join(path, fname)
        with open(out, "w", encoding="utf-8") as f:
            yaml.safe_dump(site, f, allow_unicode=True, sort_keys=False)
        return out
    raw = _load_raw(path) or _load_raw(_config_path())
    raw.setdefault("output_dir", "./var/feeds")
    raw.setdefault("state_db", "./var/rssrob.db")
    raw.setdefault("http", {"host": "127.0.0.1", "port": 8080})
    sites = raw.setdefault("sites", [])
    for i, s in enumerate(sites):
        if s.get("name") == name:
            sites[i] = site
            break
    else:
        sites.append(site)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
    return path


def _delete_site_files(save_path, name) -> bool:
    """Remove feed ``name`` from the config (folder: drop it from / delete the
    owning file; single file: drop it from ``sites``). Returns True if found.

    The inverse of ``_write_site``: a single-site file (top-level ``name``) is
    deleted outright; a ``sites:`` file has the entry removed (and the file
    deleted if it then holds nothing but that one feed)."""
    if os.path.isdir(save_path):
        removed = False
        files = list(Path(save_path).glob("*.yaml")) + list(Path(save_path).glob("*.yml"))
        for fp in files:
            raw = _load_raw(str(fp))
            if not raw:
                continue
            if "sites" in raw:
                sites = raw.get("sites") or []
                kept = [s for s in sites if s.get("name") != name]
                if len(kept) == len(sites):
                    continue
                removed = True
                if kept or (set(raw) - {"sites"}):       # keep file for its globals
                    raw["sites"] = kept
                    with open(fp, "w", encoding="utf-8") as f:
                        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
                else:
                    fp.unlink()
            elif raw.get("name") == name:                # single-site file
                fp.unlink()
                removed = True
        return removed

    raw = _load_raw(save_path)
    sites = raw.get("sites") or []
    kept = [s for s in sites if s.get("name") != name]
    if len(kept) == len(sites):
        return False
    raw["sites"] = kept
    with open(save_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
    return True


def _delete_feed_artifacts(name) -> None:
    """Best-effort cleanup after removing a feed: its generated XML + subscribers."""
    try:
        output_dir = load_config(_config_path()).output_dir
    except Exception:
        output_dir = "./var/feeds"
    out = Path(output_dir)
    if not out.is_absolute():
        out = REPO_ROOT / out
    try:
        (out / f"{name}.xml").unlink()
    except OSError:
        pass
    try:
        for email in SUBS.list(name):
            SUBS.remove(name, email)
    except Exception:
        pass


@app.route("/wechat/login")
def wechat_login():
    """Show a QR to open the 公众号 backend, plus a cookie+token capture.

    The working flow is: log in at mp.weixin.qq.com (your own 公众号), then paste
    the session cookie and the token from the URL here."""
    cred = load_credential(WECHAT_CRED_PATH)
    return render_template(
        "wechat_login.html", active=None, sites=_safe_sites(),
        login_url=MP_LOGIN_URL, qr_svg=_qr_svg(MP_LOGIN_URL),
        logged_in_token=(cred.token if cred else None),
        pasted=request.args.get("pasted"), paste_error=request.args.get("paste_error"))


@app.route("/wechat/paste", methods=["POST"])
def wechat_paste():
    """Save a 公众号 credential from a pasted cookie + token."""
    global _WECHAT_CLIENT
    cookie = (request.form.get("cookie") or "").strip()
    token = (request.form.get("token") or "").strip()
    try:
        cred = credential_from_login(cookie, token, time.time())
    except ValueError as e:
        return redirect(url_for("wechat_login", paste_error=str(e)))
    save_credential(WECHAT_CRED_PATH, cred)
    _WECHAT_CLIENT = None        # rebuild the shared client with the new credential
    return redirect(url_for("wechat_login", pasted=cred.token))


@app.route("/wechat/search", methods=["POST"])
def wechat_search():
    name = (request.form.get("name") or "").strip()
    if not name:
        return jsonify({"accounts": []})
    try:
        accounts = _wechat_client().search_accounts(name)
    except WeChatAuthError:
        return jsonify({"error": "not logged in — log in above first"})
    except Exception as e:
        return jsonify({"error": str(e)})
    return jsonify({"accounts": [{"id": a.id, "name": a.name, "avatar": a.avatar,
                                  "desc": a.description} for a in accounts]})


@app.route("/wechat/save", methods=["POST"])
def wechat_save():
    name = (request.form.get("name") or "").strip()
    account_id = (request.form.get("account_id") or "").strip()
    account_name = (request.form.get("account_name") or "").strip()
    account_description = (request.form.get("account_description") or "").strip()
    if not name or not account_id:
        return jsonify({"error": "feed name and account_id are required"})
    site = {"name": name, "type": "wechat", "account_id": account_id,
            "account_name": account_name}
    if account_description:                       # the 公众号's intro -> feed <description>
        site["description"] = account_description
    interval = request.form.get("interval")
    if interval:
        try:
            site["interval"] = int(interval)
        except ValueError:
            pass
    try:
        path = _write_site(site)
    except OSError as e:
        return jsonify({"error": f"could not write config: {e}"})
    return jsonify({"saved": name, "path": path})


@app.route("/delete-feed", methods=["POST"])
def delete_feed():
    """Remove a feed: drop it from the config and clean up its generated XML +
    subscribers. (Item history in the SQLite store is left intact.)"""
    name = (request.form.get("name") or "").strip()
    if not name:
        return jsonify({"error": "feed name required"}), 400
    try:
        removed = _delete_site_files(_save_path(), name)
    except OSError as e:
        return jsonify({"error": f"could not update config: {e}"}), 500
    if not removed:
        return jsonify({"error": f"no such feed: {name}"}), 404
    _delete_feed_artifacts(name)
    return jsonify({"deleted": name})


@app.route("/delete-feeds", methods=["POST"])
def delete_feeds():
    """Remove several feeds at once. Names come from repeated ``name`` fields
    and/or a comma/newline-separated ``names`` field. Returns which were deleted
    and which weren't found."""
    names = request.form.getlist("name") + _terms(request.form.get("names", ""))
    names = list(dict.fromkeys(n.strip() for n in names if n.strip()))  # de-dup, ordered
    if not names:
        return jsonify({"error": "no feed names given"}), 400
    deleted, not_found = [], []
    for name in names:
        try:
            removed = _delete_site_files(_save_path(), name)
        except OSError as e:
            return jsonify({"error": f"could not update config: {e}",
                            "deleted": deleted}), 500
        if removed:
            _delete_feed_artifacts(name)
            deleted.append(name)
        else:
            not_found.append(name)
    return jsonify({"deleted": deleted, "not_found": not_found})


# --- Twitter / X ------------------------------------------------------------

@app.route("/twitter/login")
def twitter_login():
    """Paste an x.com session cookie (auth_token + ct0) to read tweets."""
    cred = load_twitter_credential(TWITTER_CRED_PATH)
    return render_template(
        "twitter_login.html", active=None, sites=_safe_sites(),
        login_url=X_LOGIN_URL, logged_in=bool(cred),
        pasted=request.args.get("pasted"),
        paste_error=request.args.get("paste_error"))


@app.route("/twitter/paste", methods=["POST"])
def twitter_paste():
    """Save an X credential from a pasted cookie (+ optional proxy)."""
    global _TWITTER_CLIENT
    cookie = (request.form.get("cookie") or "").strip()
    proxy = (request.form.get("proxy") or "").strip()
    try:
        cred = credential_from_cookie(cookie, time.time(), proxy=proxy or None)
    except ValueError as e:
        return redirect(url_for("twitter_login", paste_error=str(e)))
    save_twitter_credential(TWITTER_CRED_PATH, cred)
    _TWITTER_CLIENT = None        # rebuild the shared client with the new credential
    return redirect(url_for("twitter_login", pasted="1"))


@app.route("/twitter/lookup", methods=["POST"])
def twitter_lookup():
    """Preview an account before saving: its bio + a few latest posts."""
    handle = (request.form.get("handle") or "").strip().lstrip("@")
    if not handle:
        return jsonify({"error": "enter a @handle"})
    try:
        client = _twitter_client()
        account = client.resolve_user(handle)
        if not account.id:
            return jsonify({"error": f"no such account: @{handle}"})
        tweets = [{"text": it.summary or it.title, "link": it.link, "date": it.date}
                  for it in client.to_items(client.list_tweets(account.id, 3))]
    except TwitterAuthError:
        return jsonify({"error": "not logged in — paste your cookie above first"})
    except Exception as e:
        return jsonify({"error": str(e)})
    return jsonify({"account": {"id": account.id, "name": account.name,
                                "handle": account.handle,
                                "description": account.description},
                    "tweets": tweets})


@app.route("/twitter/save", methods=["POST"])
def twitter_save():
    handle = (request.form.get("handle") or "").strip().lstrip("@")
    name = (request.form.get("name") or "").strip()
    if not handle or not name:
        return jsonify({"error": "feed name and @handle are required"})
    try:
        account = _twitter_client().resolve_user(handle)
    except TwitterAuthError:
        return jsonify({"error": "not logged in — paste your cookie above first"})
    except Exception as e:
        return jsonify({"error": str(e)})
    if not account.id:
        return jsonify({"error": f"no such account: @{handle}"})
    site = {"name": name, "type": "twitter", "username": account.handle,
            "account_id": account.id, "account_name": account.name}
    if account.description:
        site["description"] = account.description
    try:
        path = _write_site(site)
    except OSError as e:
        return jsonify({"error": f"could not write config: {e}"})
    return jsonify({"saved": name, "path": path})


# --- Backup / restore -------------------------------------------------------
# A backup bundles the active config + the whole var/ tree (SQLite item history,
# generated feeds, subscribers, digest state, 公众号 credential) into one zip, so
# the instance can be moved or rebuilt. Restore extracts it back into place.


def _backup_sources():
    """Paths (under REPO_ROOT) to include in a backup: active config + var/."""
    sources = []
    cfg = Path(_config_path())
    if cfg.name != "config.example.yaml" and cfg.exists():
        sources.append(cfg)                       # configs/ dir or config.yaml
    var_dir = REPO_ROOT / "var"
    if var_dir.exists():
        sources.append(var_dir)
    return sources


@app.route("/about")
def about():
    return render_template("about.html", active=None, sites=_safe_sites())


@app.route("/backup")
def backup_page():
    return render_template("backup.html", active=None, sites=_safe_sites(),
                           restored=request.args.get("restored"),
                           restore_error=request.args.get("restore_error"))


@app.route("/backup/download")
def backup_download():
    data = build_backup(REPO_ROOT, _backup_sources())
    fname = "rssrob-backup-" + time.strftime("%Y%m%d-%H%M%S") + ".zip"
    return send_file(io.BytesIO(data), mimetype="application/zip",
                     as_attachment=True, download_name=fname)


@app.route("/backup/restore", methods=["POST"])
def backup_restore():
    f = request.files.get("backup")
    if not f or not f.filename:
        return redirect(url_for("backup_page", restore_error="choose a backup .zip file"))
    try:
        names = restore_backup(REPO_ROOT, f.read())
    except ValueError as e:
        return redirect(url_for("backup_page", restore_error=str(e)))
    return redirect(url_for("backup_page", restored=len(names)))


def resolve_proxy(proxy, proxy_port, proxy_host="127.0.0.1", proxy_scheme="http"):
    """Build a proxy URL: an explicit --proxy wins, else <scheme>://<host>:<port>."""
    if proxy:
        return proxy
    if proxy_port:
        return f"{proxy_scheme}://{proxy_host}:{proxy_port}"
    return None


def _build_arg_parser():
    p = argparse.ArgumentParser(description="RSSRob preview web app")
    p.add_argument("--host", default="127.0.0.1", help="webapp bind host")
    p.add_argument("--port", type=int, default=5000, help="webapp port (default 5000)")
    p.add_argument("--proxy", metavar="URL",
                   help="full proxy URL for outbound fetches, e.g. "
                        "http://127.0.0.1:7890 or socks5://127.0.0.1:1080")
    p.add_argument("--proxy-port", type=int, metavar="N",
                   help="shorthand for a proxy at <proxy-host>:N")
    p.add_argument("--proxy-host", default="127.0.0.1",
                   help="proxy host used with --proxy-port (default 127.0.0.1)")
    p.add_argument("--proxy-scheme", default="http",
                   choices=["http", "https", "socks5", "socks5h"],
                   help="proxy scheme used with --proxy-port (default http)")
    return p


if __name__ == "__main__":
    args = _build_arg_parser().parse_args()

    PROXY_URL = normalize_proxy(resolve_proxy(args.proxy, args.proxy_port,
                                              args.proxy_host, args.proxy_scheme))
    if PROXY_URL:
        print(f"default proxy for feeds without their own: {PROXY_URL}")
        if PROXY_URL.startswith("socks"):
            try:
                import socks  # noqa: F401  (PySocks)
            except ImportError:
                print("  note: SOCKS proxies need PySocks → pip install 'requests[socks]'")

    app.run(host=args.host, port=args.port, debug=True)
