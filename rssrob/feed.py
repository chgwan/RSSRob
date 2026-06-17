import os
import tempfile
from datetime import datetime, timezone

from feedgen.feed import FeedGenerator

from .config import Site


def build_feed(site: Site, items) -> bytes:
    # Some sources (wechat) have no public site URL; synthesize stable, valid
    # channel id/link values so feedgen's required fields are satisfied.
    channel_id = site.url or f"urn:rssrob:{site.name}"
    channel_link = site.url or "https://mp.weixin.qq.com/"
    fg = FeedGenerator()
    fg.id(channel_id)
    fg.title(site.title or site.name)
    fg.link(href=channel_link, rel="alternate")
    fg.description(site.description or site.title or site.name)
    for it in items:
        # append (not feedgen's default prepend) to preserve the store's
        # newest-first ordering in the output feed
        fe = fg.add_entry(order="append")
        fe.id(it.id)
        fe.guid(it.id, permalink=False)
        fe.title(it.title or "(untitled)")
        if it.link:
            fe.link(href=it.link)
        if it.summary:
            fe.description(it.summary)
        ts = it.published if it.published is not None else it.first_seen
        fe.published(datetime.fromtimestamp(ts, tz=timezone.utc))
    return fg.rss_str(pretty=True)


def write_feed(output_dir: str, name: str, xml: bytes) -> str:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"{name}.xml")
    fd, tmp = tempfile.mkstemp(dir=output_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(xml)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return path
