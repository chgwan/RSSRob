"""Follow an item's link and extract the full article (title + content).

This is the "click the link" step: given an article page (like the IPP notice
pages), pull out the headline, publication date, and body. The body is returned
both as HTML (formatting preserved, links/images absolutised) and as plain text.

Selectors are parameters with IPP (www.ipp.cas.cn) defaults, so the same function
works for other sites by passing different selectors.
"""

import re
from dataclasses import dataclass
from typing import Optional

import lxml.etree
import lxml.html

from .extract import _select_nodes, parse_selector

# Defaults tuned for IPP article pages (www.ipp.cas.cn/.../tNNN.html).
DEFAULT_TITLE = "css:.ipp2020-article .hd h1"
DEFAULT_CONTENT = "css:#zoom"
DEFAULT_DATE = "css:.ipp2020-article .hd p.titBar"

DESC_LEN = 200                 # short-description length (feed <description> / digest)
# Leading CSS rule blocks (Word/WPS exports dump "@page{...} p{...}" as text).
_LEADING_CSS = re.compile(r"^(?:\s*[^{}<>]*\{[^{}]*\}\s*)+")


def text_from_html(html):
    """Plain text of an HTML fragment (scripts/styles stripped). Non-HTML
    input is returned unchanged; failures fall back to the raw input."""
    if not html or "<" not in html:
        return html
    try:
        frag = lxml.html.fromstring(html)
        lxml.etree.strip_elements(frag, "script", "style", with_tail=False)
        return frag.text_content()
    except Exception:
        return html


def shorten(text, n=DESC_LEN):
    """Collapse whitespace, drop leading CSS junk, and truncate to `n` chars
    with an ellipsis. Returns None for empty/whitespace-only input."""
    if not text:
        return None
    text = re.sub(r"\s+", " ", _LEADING_CSS.sub("", text)).strip()
    if not text:
        return None
    return text if len(text) <= n else text[:n].rstrip() + "…"


@dataclass
class Article:
    title: Optional[str]
    content_html: Optional[str]   # inner HTML of the body element
    content_text: Optional[str]   # plain text of the body
    date: Optional[str] = None
    url: Optional[str] = None


def _first_node(tree, selector):
    nodes = _select_nodes(tree, parse_selector(selector))
    return nodes[0] if nodes else None


def _inner_html(el) -> str:
    parts = []
    if el.text:
        parts.append(el.text)
    for child in el:
        parts.append(lxml.html.tostring(child, encoding="unicode"))
    return "".join(parts).strip()


def extract_article(html, base_url="", *, title_selector=DEFAULT_TITLE,
                    content_selector=DEFAULT_CONTENT,
                    date_selector=DEFAULT_DATE) -> Article:
    """Extract title/date/content from an already-fetched article page."""
    tree = lxml.html.fromstring(html)
    if base_url:
        tree.make_links_absolute(base_url)  # resolve relative links/images

    title_node = _first_node(tree, title_selector)
    title = title_node.text_content().strip() if title_node is not None else None

    date = None
    if date_selector:
        date_node = _first_node(tree, date_selector)
        if date_node is not None:
            raw = date_node.text_content().strip()
            # e.g. "2026-06-15|【大 中 小】" -> "2026-06-15"
            date = raw.split("|", 1)[0].strip() or None

    content_node = _first_node(tree, content_selector)
    content_html = _inner_html(content_node) if content_node is not None else None
    content_text = (content_node.text_content().strip()
                    if content_node is not None else None)

    return Article(title=title, content_html=content_html,
                   content_text=content_text, date=date, url=base_url or None)


def fetch_article(url, fetcher=None, *, timeout=20, user_agent="RSSRob/0.1",
                  **selectors) -> Article:
    """Follow ('click') a link and extract its article. Accepts an injectable
    fetcher (anything with a `get(url, timeout, user_agent)` method)."""
    if fetcher is None:
        from .fetch import Fetcher
        fetcher = Fetcher()
    content = fetcher.get(url, timeout, user_agent)
    html = (content.decode("utf-8", errors="replace")
            if isinstance(content, bytes) else content)
    return extract_article(html, base_url=url, **selectors)
