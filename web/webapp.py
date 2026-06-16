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
import os
import re
import sys
from pathlib import Path

# This file lives in web/; put the repo root on sys.path so `import rssrob`
# works, and anchor config/sample paths to the repo root (CWD-independent).
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import lxml.etree
import lxml.html
import requests
import yaml
from flask import Flask, abort, redirect, render_template, request, url_for

from rssrob.article import fetch_article
from rssrob.config import ConfigError, load_config, normalize_proxy
from rssrob.extract import extract_items
from rssrob.pipeline import obtain_items
from rssrob.rss import parse_feed
from rssrob.subscribers import Subscribers

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
SUBS = Subscribers(str(REPO_ROOT / "subscribers.json"))


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
    try:
        items, feed_title, feed_desc = obtain_items(site, fetcher)
    except Exception as e:
        return render_template(
            "error.html",
            message=f"could not load {site.url}: {e}",
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

    return render_template(
        "preview.html",
        site=site,
        sites=config.sites,
        active=site.name,
        entries=entries,
        source=main_source,
        fetch_error=main_error,
        display_title=site.title or feed_title or site.name,
        subscriber_count=len(SUBS.list(site.name)),
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
    status = SUBS.add(site, email)
    if status == "added":
        return redirect(url_for("index", site=site, subscribed=email))
    msg = "already subscribed" if status == "exists" else "please enter a valid email address"
    return redirect(url_for("index", site=site, sub_error=msg))


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
            raw.setdefault("output_dir", "./feeds")
            raw.setdefault("state_db", "./rssrob.db")
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
