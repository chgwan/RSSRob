"""Build and send a digest email of a feed's items to its subscribers.

Mirrors the web preview: each item shows date + full title (the item's link is
followed for the full title) + a short description. The full article body is
*not* included.

    python -m rssrob.digest --site <name>              # send to the feed's subscribers
    python -m rssrob.digest --site <name> --to me@x    # send to an override address
    python -m rssrob.digest --site <name> --dry-run     # print, don't send

SMTP config comes from the environment / .env (see rssrob.notify).
"""

import argparse
import html as _html
import re
import sys
from typing import List

import lxml.etree
import lxml.html

from .article import fetch_article
from .config import default_config_path, load_config
from .fetch import Fetcher
from .notify import EmailError, load_dotenv, send_email
from .pipeline import obtain_items
from .subscribers import Subscribers

DESC_LEN = 200                 # short-description length (like the web preview)
_ARTICLE_KEYS = {"title": "title_selector", "content": "content_selector",
                 "date": "date_selector"}
# Leading CSS rule blocks (Word/WPS exports dump "@page{...} p{...}" as text).
_LEADING_CSS = re.compile(r"^(?:\s*[^{}<>]*\{[^{}]*\}\s*)+")


def _article_kwargs(article_sel):
    if not article_sel:
        return {}
    return {_ARTICLE_KEYS[k]: v for k, v in article_sel.items()
            if k in _ARTICLE_KEYS and v}


def _text_from_html(html):
    if not html or "<" not in html:
        return html
    try:
        frag = lxml.html.fromstring(html)
        lxml.etree.strip_elements(frag, "script", "style", with_tail=False)
        return frag.text_content()
    except Exception:
        return html


def _shorten(text, n=DESC_LEN):
    if not text:
        return None
    text = re.sub(r"\s+", " ", _LEADING_CSS.sub("", text)).strip()
    if not text:
        return None
    return text if len(text) <= n else text[:n].rstrip() + "…"


def enrich_items(site, items, fetcher) -> List[dict]:
    """Follow each item's link for the full title + a short description.

    Returns entry dicts: {title, link, date, description}. rss items use their
    own summary; html items are fetched with the feed's `article` selectors."""
    kwargs = _article_kwargs(getattr(site, "article", None))
    entries = []
    for it in items:
        title, description = it.title, None
        if it.summary:                                   # rss: summary in the feed
            description = _shorten(_text_from_html(it.summary))
        elif it.link:                                    # html: follow the link
            try:
                art = fetch_article(it.link, fetcher, **kwargs)
                title = art.title or it.title
                description = _shorten(_text_from_html(art.content_text))
            except Exception:
                pass
        entries.append({"title": title, "link": it.link,
                        "date": it.date, "description": description})
    return entries


def build_digest(title: str, entries: List[dict]) -> tuple:
    """Return (subject, text_body, html_body): date + full title + short desc."""
    n = len(entries)
    plural = "" if n == 1 else "s"
    subject = f"[RSSRob] {title} — {n} update{plural}"

    text_lines = [f"{title} — {n} item{plural}", ""]
    for e in entries:
        date = (e["date"] or "").strip()
        text_lines.append(f"{('['+date+'] ') if date else ''}{e['title'] or '(untitled)'}")
        if e["description"]:
            text_lines.append(f"  {e['description']}")
        if e["link"]:
            text_lines.append(f"  {e['link']}")
        text_lines.append("")
    text = "\n".join(text_lines)

    rows = []
    for e in entries:
        date = _html.escape((e["date"] or "").strip())
        t = _html.escape(e["title"] or "(untitled)")
        link = _html.escape(e["link"] or "")
        head = (f'<a href="{link}" style="color:#0353a4;text-decoration:none">{t}</a>'
                if link else t)
        desc = (f'<div style="color:#555;font-size:90%;margin-top:.15rem">'
                f'{_html.escape(e["description"])}</div>' if e["description"] else "")
        rows.append(
            '<tr>'
            f'<td style="color:#1a7f37;white-space:nowrap;font-size:90%;'
            f'padding:.45rem 12px .45rem 0;vertical-align:top">{date}</td>'
            f'<td style="padding:.45rem 0;vertical-align:top">{head}{desc}</td>'
            '</tr>')
    html_body = (
        '<div style="max-width:680px;margin:0 auto;'
        'font-family:system-ui,-apple-system,Arial,sans-serif;color:#222">'
        f'<h2 style="font-size:1.2rem;margin:0 0 .2rem">{_html.escape(title)}</h2>'
        f'<p style="color:#666;margin:.1rem 0 .8rem">{n} update{plural}</p>'
        f'<table style="border-collapse:collapse;width:100%">{"".join(rows)}</table>'
        '<p style="color:#999;font-size:85%;margin-top:1rem">Sent by RSSRob.</p></div>')
    return subject, text, html_body


def send_feed_digest(site, recipients: List[str], limit: int = 10,
                     fetcher=None, dry_run: bool = False) -> dict:
    """Fetch a feed, follow links for full titles + short descriptions, and
    email a digest. `fetcher` is injectable for testing."""
    fetcher = fetcher or Fetcher(proxy=getattr(site, "proxy", None))
    items, feed_title, _ = obtain_items(site, fetcher)
    items = items[:limit]
    entries = enrich_items(site, items, fetcher)
    title = site.title or feed_title or site.name
    subject, text, html_body = build_digest(title, entries)

    if dry_run:
        return {"subject": subject, "text": text, "html": html_body,
                "items": len(entries), "recipients": recipients, "sent": 0,
                "errors": [], "dry_run": True}

    sent, errors = 0, []
    for r in recipients:
        try:
            send_email(r, subject, text, html=html_body)
            sent += 1
        except Exception as e:
            errors.append((r, f"{type(e).__name__}: {e}"))
    return {"subject": subject, "items": len(entries), "recipients": recipients,
            "sent": sent, "errors": errors, "dry_run": False}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="rssrob.digest",
        description="Email a feed's items (date + full title + short description) to subscribers.")
    p.add_argument("--site", required=True, help="feed name (from config)")
    p.add_argument("--config", help="config file or dir (default: ./configs/ etc.)")
    p.add_argument("--to", action="append", metavar="EMAIL",
                   help="override recipients (repeatable); default = the feed's subscribers")
    p.add_argument("--subscribers", default="subscribers.json",
                   help="subscriber store path (default: subscribers.json)")
    p.add_argument("--limit", type=int, default=10, help="max items in the digest")
    p.add_argument("--dry-run", action="store_true", help="print the digest, do not send")
    p.add_argument("--no-dotenv", action="store_true", help="don't load .env (use real env only)")
    args = p.parse_args(argv)

    try:
        config = load_config(args.config or default_config_path())
    except Exception as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    site = next((s for s in config.sites if s.name == args.site), None)
    if site is None:
        print(f"no such feed: {args.site!r}", file=sys.stderr)
        return 2

    recipients = args.to or Subscribers(args.subscribers).list(site.name)
    if not recipients:
        print(f"no subscribers for {site.name!r} (and no --to given)", file=sys.stderr)
        return 1

    if not args.dry_run and not args.no_dotenv:
        load_dotenv()

    try:
        result = send_feed_digest(site, recipients, limit=args.limit, dry_run=args.dry_run)
    except EmailError as e:
        print(f"email error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"[dry-run] subject: {result['subject']}")
        print(f"[dry-run] {result['items']} item(s) -> {len(recipients)} recipient(s): "
              f"{', '.join(recipients)}")
        print("-" * 60)
        print(result["text"])
        return 0

    print(f"sent '{result['subject']}' to {result['sent']}/{len(recipients)} recipient(s)")
    for r, err in result["errors"]:
        print(f"  FAILED {r}: {err}", file=sys.stderr)
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
