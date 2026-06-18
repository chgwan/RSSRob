"""Build and send a digest email of a feed's items to its subscribers.

Mirrors the web preview: each item shows date + full title (the item's link is
followed for the full title) + a short description. The full article body is
*not* included.

By default the digest is *incremental*: only items not sent in a previous
digest are included (tracked by item id in a JSON state file). The first run
establishes the baseline; later runs send only what's new, and send nothing
when there are no new items. Use --all to ignore the state and resend
everything; --dry-run previews without sending or recording state.

    python -m rssrob.digest --site <name>              # send new items to subscribers
    python -m rssrob.digest --site <name> --to me@x    # send to an override address
    python -m rssrob.digest --site <name> --dry-run     # print, don't send/record
    python -m rssrob.digest --site <name> --all         # ignore state, resend all

SMTP config comes from the environment / .env (see rssrob.notify).
"""

import argparse
import html as _html
import json
import os
import re
import sys
import threading
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


def _item_key(it):
    return it.id or it.link


class SentStore:
    """Track which item ids have already been emailed per feed (JSON file).

    Shape on disk: {"<feed-name>": ["id1", "id2", ...]}. Bounded to the most
    recent ids per feed to avoid unbounded growth."""

    CAP = 1000
    _SUBS_KEY = "__by_subscriber__"   # namespace unlikely to collide with a feed name

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                return json.load(f) or {}
        return {}

    def _save(self, data):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def seen_ids(self, feed, subscriber=None):
        data = self._load()
        if subscriber is None:
            return set(data.get(feed, []))
        # per-subscriber set, unioned with the legacy global set as a baseline so
        # items already delivered before per-subscriber tracking count as seen.
        legacy = set(data.get(feed, []))
        per = data.get(self._SUBS_KEY, {}).get(subscriber, {}).get(feed, [])
        return legacy | set(per)

    def mark(self, feed, ids, subscriber=None):
        ids = [i for i in ids if i]
        if not ids:
            return
        with self._lock:
            data = self._load()
            if subscriber is None:
                lst = data.setdefault(feed, [])
            else:
                lst = (data.setdefault(self._SUBS_KEY, {})
                           .setdefault(subscriber, {})
                           .setdefault(feed, []))
            existing = set(lst)
            for i in ids:
                if i not in existing:
                    lst.append(i)
                    existing.add(i)
            if len(lst) > self.CAP:
                del lst[:-self.CAP]
            self._save(data)


def select_new_items(items, feed, state):
    """Return only items whose id has not already been emailed for `feed`."""
    seen = state.seen_ids(feed)
    return [it for it in items if _item_key(it) not in seen]


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


def _render_section(title: str, entries: List[dict]) -> tuple:
    """Render one feed's items as (text_block, html_block): a header plus a
    dated title / short-description / link list. Shared by single and combined
    digests so the markup stays identical."""
    n = len(entries)
    plural = "" if n == 1 else "s"

    text_lines = [f"{title} — {n} item{plural}", ""]
    for e in entries:
        date = (e["date"] or "").strip()
        text_lines.append(f"{('['+date+'] ') if date else ''}{e['title'] or '(untitled)'}")
        if e["description"]:
            text_lines.append(f"  {e['description']}")
        if e["link"]:
            text_lines.append(f"  {e['link']}")
        text_lines.append("")
    text_block = "\n".join(text_lines)

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
    html_block = (
        f'<h2 style="font-size:1.2rem;margin:0 0 .2rem">{_html.escape(title)}</h2>'
        f'<p style="color:#666;margin:.1rem 0 .8rem">{n} update{plural}</p>'
        f'<table style="border-collapse:collapse;width:100%">{"".join(rows)}</table>')
    return text_block, html_block


def _wrap_html(inner: str) -> str:
    """Wrap one or more rendered section blocks in the email shell + footer."""
    return (
        '<div style="max-width:680px;margin:0 auto;'
        'font-family:system-ui,-apple-system,Arial,sans-serif;color:#222">'
        f'{inner}'
        '<p style="color:#999;font-size:85%;margin-top:1rem">Sent by RSSRob.</p></div>')


def build_digest(title: str, entries: List[dict]) -> tuple:
    """Return (subject, text_body, html_body) for ONE feed: date + full title +
    short description."""
    n = len(entries)
    plural = "" if n == 1 else "s"
    subject = f"[RSSRob] {title} — {n} update{plural}"
    text_block, html_block = _render_section(title, entries)
    return subject, text_block, _wrap_html(html_block)


def build_combined_digest(sections: List[dict]) -> tuple:
    """Return (subject, text_body, html_body) for one combined email across
    several feeds. `sections` is [{"title": str, "entries": [...]}, ...] for
    feeds that have items. A single section delegates to the single-feed style."""
    if len(sections) == 1:
        return build_digest(sections[0]["title"], sections[0]["entries"])
    total = sum(len(s["entries"]) for s in sections)
    plural = "" if total == 1 else "s"
    subject = f"[RSSRob] {total} update{plural} across {len(sections)} feeds"
    text_blocks, html_blocks = [], []
    for s in sections:
        tb, hb = _render_section(s["title"], s["entries"])
        text_blocks.append(tb.rstrip())
        html_blocks.append(hb)
    return subject, "\n\n".join(text_blocks), _wrap_html("".join(html_blocks))


def send_feed_digest(site, recipients: List[str], limit: int = 10,
                     first_limit: int = 20, fetcher=None, dry_run: bool = False,
                     state=None, only_new: bool = True) -> dict:
    """Fetch a feed, follow links for full titles + short descriptions, and
    email a single digest to all recipients (Bcc).

    With a `state` and `only_new=True`, only items not previously sent are
    included, and their ids are recorded after a successful send. The *first*
    send for a feed (no prior state) backfills up to `first_limit` items;
    later sends are capped at `limit`. `fetcher` is injectable for testing."""
    fetcher = fetcher or Fetcher(proxy=getattr(site, "proxy", None))
    items, feed_title, _ = obtain_items(site, fetcher)
    first_send = state is not None and not state.seen_ids(site.name)
    if only_new and state is not None:
        items = select_new_items(items, site.name, state)
    cap = first_limit if (only_new and first_send) else limit
    items = items[:cap]
    title = site.title or feed_title or site.name

    if not items:                                    # nothing new to send
        return {"subject": None, "items": 0, "recipients": recipients,
                "sent": 0, "errors": [], "dry_run": dry_run, "no_new": True}

    entries = enrich_items(site, items, fetcher)
    subject, text, html_body = build_digest(title, entries)

    if dry_run:
        return {"subject": subject, "text": text, "html": html_body,
                "items": len(entries), "recipients": recipients, "sent": 0,
                "errors": [], "dry_run": True, "no_new": False}

    # one email to everyone, Bcc so recipients don't see each other
    errors = []
    try:
        send_email([], subject, text, html=html_body, bcc=recipients)
        sent = len(recipients)
    except Exception as e:
        sent = 0
        errors.append(("*", f"{type(e).__name__}: {e}"))

    if sent and state is not None:
        state.mark(site.name, [_item_key(it) for it in items])

    return {"subject": subject, "items": len(entries), "recipients": recipients,
            "sent": sent, "errors": errors, "dry_run": False, "no_new": False}


def send_subscriber_digest(subscriber, sites, *, limit: int = 10,
                           first_limit: int = 20, fetcher=None,
                           dry_run: bool = False, state=None,
                           only_new: bool = True) -> dict:
    """Email one subscriber a single combined digest across all `sites` they
    follow. Incremental state is tracked per subscriber (only items not sent to
    THIS subscriber are included, then recorded after a successful send). A feed
    that fails to fetch is skipped and reported; the others are still sent."""
    sections = []
    to_mark = []                       # (feed_name, [item_keys]) to record on success
    errors = []
    for site in sites:
        f = fetcher or Fetcher(proxy=getattr(site, "proxy", None))
        try:
            items, feed_title, _ = obtain_items(site, f)
            seen = state.seen_ids(site.name, subscriber) if state is not None else set()
            first_send = state is not None and not seen
            if only_new and state is not None:
                items = [it for it in items if _item_key(it) not in seen]
            cap = first_limit if (only_new and first_send) else limit
            items = items[:cap]
            if not items:
                continue
            entries = enrich_items(site, items, f)
        except Exception as e:
            errors.append((site.name, f"{type(e).__name__}: {e}"))
            continue
        sections.append({"title": site.title or feed_title or site.name,
                         "entries": entries})
        to_mark.append((site.name, [_item_key(it) for it in items]))

    if not sections:
        return {"subject": None, "feeds": 0, "items": 0, "sent": 0,
                "errors": errors, "dry_run": dry_run, "no_new": True}

    subject, text, html_body = build_combined_digest(sections)
    total = sum(len(s["entries"]) for s in sections)

    if dry_run:
        return {"subject": subject, "text": text, "html": html_body,
                "feeds": len(sections), "items": total, "sent": 0,
                "errors": errors, "dry_run": True, "no_new": False}

    try:
        send_email([subscriber], subject, text, html=html_body)
    except Exception as e:
        return {"subject": subject, "feeds": len(sections), "items": total,
                "sent": 0, "errors": errors + [("*", f"{type(e).__name__}: {e}")],
                "dry_run": False, "no_new": False}

    if state is not None:
        for feed_name, keys in to_mark:
            state.mark(feed_name, keys, subscriber)

    return {"subject": subject, "feeds": len(sections), "items": total,
            "sent": 1, "errors": errors, "dry_run": False, "no_new": False}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="rssrob.digest",
        description="Email feed items to subscribers, one combined email per "
                    "subscriber (--subscriber/--all-subscribers) or one email "
                    "per feed (--site, legacy).")
    p.add_argument("--site", help="legacy: send ONE email per this feed to all its subscribers")
    p.add_argument("--subscriber", metavar="EMAIL",
                   help="send one combined digest to this subscriber across all their feeds")
    p.add_argument("--all-subscribers", action="store_true",
                   help="send every subscriber one combined digest across their feeds")
    p.add_argument("--config", help="config file or dir (default: ./configs/ etc.)")
    p.add_argument("--to", action="append", metavar="EMAIL",
                   help="(--site only) override recipients (repeatable)")
    p.add_argument("--subscribers", default="var/subscribers.json",
                   help="subscriber store path (default: var/subscribers.json)")
    p.add_argument("--limit", type=int, default=None,
                   help="max NEW items per incremental send (default: digest.limit or 10)")
    p.add_argument("--first-limit", type=int, default=None,
                   help="max items on a first send (default: digest.first_limit or 20)")
    p.add_argument("--state", default="var/digest_state.json",
                   help="sent-state file for incremental sends (default: var/digest_state.json)")
    p.add_argument("--all", action="store_true",
                   help="ignore state and (re)send all current items")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would be sent, do not send or record state")
    p.add_argument("--no-dotenv", action="store_true", help="don't load .env (use real env only)")
    args = p.parse_args(argv)

    if sum([bool(args.site), bool(args.subscriber), bool(args.all_subscribers)]) != 1:
        print("choose exactly one of --site, --subscriber, --all-subscribers", file=sys.stderr)
        return 2

    try:
        config = load_config(args.config or default_config_path())
    except Exception as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    if not args.dry_run and not args.no_dotenv:
        load_dotenv()

    # CLI flags override config's `digest:` block, which overrides built-in defaults.
    limit = args.limit if args.limit is not None else int(config.digest.get("limit", 10))
    first_limit = (args.first_limit if args.first_limit is not None
                   else int(config.digest.get("first_limit", 20)))
    state = SentStore(args.state)

    # --- legacy per-feed broadcast ---
    if args.site:
        site = next((s for s in config.sites if s.name == args.site), None)
        if site is None:
            print(f"no such feed: {args.site!r}", file=sys.stderr)
            return 2
        recipients = args.to or Subscribers(args.subscribers).list(site.name)
        if not recipients:
            print(f"no subscribers for {site.name!r} (and no --to given)", file=sys.stderr)
            return 1
        try:
            result = send_feed_digest(site, recipients, limit=limit,
                                      first_limit=first_limit, dry_run=args.dry_run,
                                      state=state, only_new=not args.all)
        except EmailError as e:
            print(f"email error: {e}", file=sys.stderr)
            return 2
        except Exception as e:
            print(f"failed: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        if result.get("no_new"):
            print(f"no new items for {site.name!r} since last send (use --all to resend)")
        elif args.dry_run:
            print(f"[dry-run] subject: {result['subject']}")
            print(f"[dry-run] {result['items']} new item(s) -> {len(recipients)} recipient(s): "
                  f"{', '.join(recipients)}")
        else:
            print(f"sent '{result['subject']}' ({result['items']} item(s)) to "
                  f"{result['sent']} recipient(s) in one email")
        return 0

    # --- combined per-subscriber send ---
    by_email = Subscribers(args.subscribers).by_email()
    if args.subscriber:
        email = args.subscriber.strip().lower()
        if email not in by_email:
            print(f"not a subscriber: {args.subscriber!r}", file=sys.stderr)
            return 1
        targets = {email: by_email[email]}
    else:
        targets = by_email
        if not targets:
            print("no subscribers", file=sys.stderr)
            return 1

    sites_by_name = {s.name: s for s in config.sites}
    for email, info in targets.items():
        sites = [sites_by_name[f] for f in info["feeds"] if f in sites_by_name]
        result = send_subscriber_digest(email, sites, limit=limit,
                                        first_limit=first_limit, dry_run=args.dry_run,
                                        state=state, only_new=not args.all)
        if result.get("no_new"):
            print(f"{email}: no new items")
        elif args.dry_run:
            print(f"[dry-run] {email}: {result['items']} item(s) across "
                  f"{result['feeds']} feed(s) — {result['subject']}")
        else:
            print(f"{email}: sent {result['items']} item(s) across {result['feeds']} feed(s)")
        for name, msg in result.get("errors", []):
            print(f"  {email} / {name}: {msg}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
