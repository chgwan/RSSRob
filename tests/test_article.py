from rssrob.article import DESC_LEN, extract_article, fetch_article, shorten, text_from_html


def test_extract_article(fixtures):
    html = (fixtures / "article.html").read_text(encoding="utf-8")
    art = extract_article(html, base_url="http://www.ipp.cas.cn/tzgg/x.html")
    assert art.title == "测试文章标题"
    assert art.date == "2026-06-15"                  # split off the "|【大 中 小】" tail
    assert "第一段内容" in art.content_text
    assert "第二段内容" in art.content_text
    assert "<p>" in art.content_html                 # html formatting preserved
    # a relative link inside the body is absolutised against base_url
    assert "http://www.ipp.cas.cn/rel/link.html" in art.content_html


def test_extract_article_missing_returns_none():
    art = extract_article("<html><body><p>nothing here</p></body></html>")
    assert art.title is None
    assert art.content_html is None
    assert art.content_text is None


def test_fetch_article_uses_injected_fetcher(fixtures, make_fetcher):
    html = (fixtures / "article.html").read_bytes()
    url = "http://www.ipp.cas.cn/tzgg/x.html"
    fetcher = make_fetcher({url: html})
    art = fetch_article(url, fetcher)
    assert art.title == "测试文章标题"
    assert art.url == url


def test_shorten_truncates_and_passes_through():
    assert shorten("a" * 300, n=10) == "a" * 10 + "…"
    assert shorten("short") == "short"            # under DESC_LEN → unchanged
    assert shorten(None) is None
    assert shorten("   ") is None


def test_shorten_strips_leading_css_junk():
    # Word/WPS exports prepend "@page{...} p{...}" as text — dropped before truncation
    assert shorten("@page{size:8.5in} p{margin:0} hello world", n=40) == "hello world"


def test_text_from_html_strips_scripts_and_tags():
    out = text_from_html("<p>hi <script>bad()</script>there</p>")
    assert "hi" in out and "there" in out and "bad" not in out
    assert text_from_html("plain") == "plain"     # no tags → unchanged
    assert text_from_html(None) is None


def test_desc_len_default():
    assert DESC_LEN == 200
