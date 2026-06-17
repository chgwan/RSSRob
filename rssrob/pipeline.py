import time
from dataclasses import replace
from typing import Optional, Tuple

from . import extract, rss
from .config import Site
from .feed import build_feed, write_feed
from .store import Store


def obtain_items(site: Site, fetcher,
                 wechat_client=None, twitter_client=None) -> Tuple[list, Optional[str], Optional[str]]:
    """Return (items, feed_title, feed_description).

    For ``html`` the meta is None; ``rss`` exposes the channel title/description;
    ``wechat``/``twitter`` return an account label as the title (so the feed can
    default to it). ``wechat`` needs a 公众号 client and ``twitter`` an X client
    instead of an HTTP fetcher."""
    if site.type == "wechat":
        if wechat_client is None:
            raise RuntimeError(f"wechat feed {site.name!r} requires a 公众号 client")
        raw = wechat_client.list_articles(site.account_id, site.max_items)
        return wechat_client.to_items(raw), site.account_name, None
    if site.type == "twitter":
        if twitter_client is None:
            raise RuntimeError(f"twitter feed {site.name!r} requires an X client")
        user_id = site.account_id or twitter_client.resolve_user(site.username).id
        raw = twitter_client.list_tweets(user_id, site.max_items)
        title = site.account_name or f"@{site.username}"
        return twitter_client.to_items(raw), title, None
    content = fetcher.get(site.url, site.timeout, site.user_agent)
    if site.type == "rss":
        parsed = rss.parse_feed(content, site.url)
        return parsed.items, parsed.title, parsed.description
    html = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    items = extract.extract_items(html, site.url, site.item, site.fields)
    return items, None, None


def run_cycle(site: Site, store: Store, fetcher, output_dir: str,
              now: Optional[float] = None, wechat_client=None, twitter_client=None) -> int:
    if now is None:
        now = time.time()
    items, feed_title, feed_desc = obtain_items(
        site, fetcher, wechat_client=wechat_client, twitter_client=twitter_client)
    if site.filter:
        items = [it for it in items if site.filter.keeps(it)]
    inserted = store.insert_new(site.name, items, now)
    if site.max_age_days:
        store.prune_old(site.name, site.max_age_days * 86400, now)

    effective = site
    if site.type in ("rss", "wechat", "twitter") and (site.title is None or site.description is None):
        effective = replace(
            site,
            title=site.title or feed_title,
            description=site.description or feed_desc,
        )

    recent = store.recent(site.name, site.max_items)
    xml = build_feed(effective, recent)
    write_feed(output_dir, site.name, xml)
    return inserted
