# RSSRob

**English** | [中文](README.zh-CN.md)

**A light, configurable tool that generates RSS feeds from any website — even sites that don't offer one.**

For sites without a feed, you point RSSRob at a page and tell it (with CSS selectors or XPath) where the items and their fields live; for sites that already publish RSS/Atom, you just give it the feed URL. Either way, RSSRob runs on a schedule, builds a spec-correct RSS feed, and serves it over HTTP. It remembers everything it has seen, so your feed accumulates history and never shows the same item twice.

---

## Features

- **Two source types** — `html` sites are scraped with **CSS selectors or XPath**; `rss` sites already publish a feed, so RSSRob just parses and re-serves it (no selectors needed).
- **Built-in scheduler** — one process scrapes each site on its own interval; no external cron needed.
- **Built-in HTTP server** — serves each feed at a stable URL plus a simple index page.
- **Dedup + history** — a SQLite store tracks every item ever seen. Items are added once (deduped by id), and your feed keeps a rolling window of the most recent N — so history survives even after items scroll off the source page.
- **Spec-correct RSS** — feeds are generated with [`feedgen`](https://github.com/lkiesow/python-feedgen), not hand-rolled XML.
- **Debug-friendly** — `run-once` scrapes a single site and prints what it extracted, so you can dial in selectors before committing them.
- **Light footprint** — five dependencies, stdlib HTTP server and scheduler, single process.

---

## How it works

```
                         ┌──────────────────────────────────────┐
                         │             rssrob serve              │
                         │                                       │
   config.yaml ───────▶  │  ┌─────────────┐     ┌─────────────┐  │
                         │  │  scheduler  │     │ http.server │  │
                         │  │  (thread)   │     │ (main)      │  │
                         │  └──────┬──────┘     └──────┬──────┘  │
                         │         │ per-site          │ serves  │
                         │         ▼ interval          ▼         │
   site ────HTTP──▶ requests ─▶ extract (html, lxml) ─┐
                         │      parse   (rss,  feedparser) ─┴▶ store(SQLite) ─▶ feed.xml
                         │                                       (dedup)       (feedgen)
                         └──────────────────────────────────────┘
```

One scrape cycle for a site:

1. **Fetch** — `requests` downloads the page (or feed).
2. **Obtain items** — by source `type`:
   - `html`: `extract` applies the *item* selector to get rows, then per-row *field* selectors.
   - `rss`: `rss` parses the existing RSS/Atom feed with `feedparser`.
   - Both produce the same shape → `[{id, title, link, summary, date}, …]`.
3. **Store + dedup** — `store` inserts items whose `id` isn't already in SQLite, stamping `first_seen`. Items already known are skipped.
4. **Generate** — `feed` reads the most recent N items for that feed from SQLite and writes `feeds/<name>.xml`.

Meanwhile the HTTP server serves whatever XML files exist, independently of the scrape cycle.

---

## Requirements

- Python 3.11+
- Dependencies (5): `requests`, `lxml`, `feedgen`, `pyyaml`, `feedparser`
  - `feedgen` transitively brings in `python-dateutil` and `lxml`.

---

## Installation

```bash
git clone <your-repo-url> RSSRob
cd RSSRob
pip install -r requirements.txt
```

---

## Quick start

1. Create a `config.yaml` (see [Configuration](#configuration)):

   ```yaml
   output_dir: ./feeds
   state_db: ./rssrob.db
   http:
     host: 127.0.0.1
     port: 8080
   defaults:
     interval: 1800     # seconds between scrapes
     max_items: 50      # items kept per feed

   sites:
     - name: example-blog
       url: https://example.com/blog
       title: "Example Blog"
       item: "css:div.post"
       fields:
         title: "css:h2 a"
         link: "css:h2 a@href"
         summary: "css:p.excerpt"
         date: "css:time@datetime"
   ```

2. Test your selectors against the live page **without** writing any feed:

   ```bash
   rssrob run-once example-blog
   ```

   This prints the extracted items so you can confirm the selectors are right.

3. Start the scheduler + server:

   ```bash
   rssrob serve
   ```

4. Subscribe in your RSS reader to:

   ```
   http://127.0.0.1:8080/feeds/example-blog.xml
   ```

   Or open `http://127.0.0.1:8080/` for the index of all feeds.

---

## Configuration

RSSRob loads config from a **`configs/` folder** by default — every `*.yaml` file
in it is read in filename order and merged: global settings
(`output_dir`/`state_db`/`http`/`defaults`) are merged by key, and feeds are
collected from all files. Keep globals in one file (e.g. `configs/00-settings.yaml`)
and **one feed per file** (a top-level mapping with `name`, no `sites:` wrapper):

```
configs/
├── 00-settings.yaml      # output_dir, state_db, http, defaults
├── ipp-notices.yaml      # one feed
├── python-insider.yaml
└── …
```

A single `config.yaml` file still works too. Resolution order: `./configs/` if it
exists, else `config.yaml`, else `config.example.yaml`. Override with
`--config <file-or-dir>` (CLI) or `RSSROB_CONFIG` (web app).

### Global options

| Key | Default | Description |
|-----|---------|-------------|
| `output_dir` | `./feeds` | Where generated `<name>.xml` files are written and served from. |
| `state_db` | `./rssrob.db` | SQLite file holding item history / dedup state. |
| `http.host` | `127.0.0.1` | Host the server binds to. Use `0.0.0.0` to expose on your network. |
| `http.port` | `8080` | Port for the HTTP server. |
| `defaults.interval` | `3600` | Seconds between scrapes (per site, overridable). |
| `defaults.max_items` | `50` | Max items retained per feed (rolling window). |
| `defaults.timeout` | `20` | HTTP fetch timeout in seconds. |
| `defaults.user_agent` | `RSSRob/0.1` | User-Agent sent when fetching pages. |

### Per-site options

| Key | Required | Description |
|-----|----------|-------------|
| `name` | yes | Unique id; used as the feed filename (`<name>.xml`) and CLI argument. |
| `url` | yes | Page to scrape (`html`) or feed URL to parse (`rss`). |
| `type` | no | `html` (default) or `rss`. Selects the source handling. |
| `title` | html: yes / rss: no | RSS feed `<title>`. For `rss`, defaults to the source feed's title. |
| `description` | no | RSS feed `<description>`. Defaults to the title (or the source feed's description for `rss`). |
| `item` | html only | Selector matching each item/row on the page. Required for `html`; ignored for `rss`. |
| `fields` | html only | Map of field name → selector (see below). Required for `html`; ignored for `rss`. |
| `interval` | no | Overrides `defaults.interval` for this site. |
| `max_items` | no | Overrides `defaults.max_items` for this site. |
| `proxy` | no | Per-feed proxy; accepts `socks5://ip:port` or `http(s)://ip:port`. |
| `article` | no | "Go deeper" selectors (`title`/`content`): follow each item's link for the full title + body. |

#### `rss` source example

For a site that already publishes a feed, no selectors are needed:

```yaml
sites:
  - name: python-insider
    type: rss
    url: https://blog.python.org/feeds/posts/default
    # title/description optional — inherited from the source feed
    interval: 3600
    max_items: 30
```

RSSRob parses that feed, applies the same dedup/history, and re-serves it at
`/feeds/python-insider.xml`.

### Fields

Each entry under `fields` is a selector **evaluated relative to a single item element**:

- `title` (recommended) — the item title.
- `link` (recommended) — the item URL. Resolved to an absolute URL against the page URL.
- `summary` (optional) — description / excerpt.
- `date` (optional) — publication date. Parsed best-effort with `dateutil`; if missing or unparseable, the item's `first_seen` time is used instead.
- `id` (optional) — stable identity for dedup. **Defaults to `link`.** Set this if links contain volatile query params or if you prefer another unique field.

### Selector syntax

A selector is a string with an optional engine prefix and an optional attribute suffix:

```
[css:|xpath:] <selector> [@attribute]
```

- **Engine prefix** — `css:` for CSS selectors, `xpath:` for XPath. **No prefix means CSS.**
- **Attributes** — for **CSS**, append `@attr` (e.g. `css:h2 a@href`). For **XPath**, use the native attribute axis (e.g. `xpath:.//a/@href`) — the `@attr` suffix is not used there because XPath already uses `@` in predicates. With no attribute, the element's text content is returned.

Examples:

| Selector | Meaning |
|----------|---------|
| `css:h2 a` | text of the `<a>` inside an `<h2>` |
| `css:h2 a@href` | the `href` attribute of that link |
| `css:time@datetime` | the `datetime` attribute of a `<time>` tag |
| `xpath:.//h2/a` | text of the first matching link (XPath, relative to the item) |
| `xpath:.//h2/a/@href` | the `href` via native XPath attribute axis |

> CSS and XPath can be mixed freely across sites and even across fields within one site. Use whichever is clearer for a given page.

#### Selecting a section by its heading text

CSS can't match on text, so when several blocks share a class and only the
heading distinguishes them, anchor on the heading with XPath and walk to the
list. Worked example (the 通知公告 / "Announcements" block of an IPP homepage):

```yaml
sites:
  - name: ipp-notices
    type: html
    url: http://www.ipp.cas.cn/
    title: "IPP 通知公告"
    item: "xpath://h2[normalize-space()='通知公告']/ancestor::div[contains(@class,'ipp2020-item')][1]//div[@class='bd']//ul/li"
    fields:
      title: "xpath:.//a"
      link:  "xpath:.//a/@href"
      date:  "xpath:.//span"
```

This selects exactly the 6 announcement items (sibling sections sharing the same
class are excluded) and resolves each relative link to an absolute URL.

---

## Dedup & history model

The source page usually shows only *current* items, but a good feed should remember the past. RSSRob's SQLite store is the feed's **backing store**, not just a seen-set:

- Each scrape extracts the items currently on the page.
- Items with a **new `id`** are inserted with a `first_seen` timestamp.
- Items already in the store are **skipped** (this is the dedup).
- The written `.xml` is the most recent `max_items` for that feed, ordered by date (or `first_seen` when no date is available).

Result: items keep appearing in your feed even after they scroll off the source page, and nothing is ever duplicated.

---

## CLI

```bash
rssrob serve [--config config.yaml]
```
Loads the config, starts the background scheduler and the HTTP server, and runs until interrupted (Ctrl-C). This is the normal mode.

```bash
rssrob run-once <site-name> [--config config.yaml]
```
Scrapes a single site **once**, prints the extracted items to the terminal, and exits. Intended for debugging selectors before adding a site to your regular schedule. (By default it does not write to the store; pass `--write` to also persist + regenerate that feed.)

---

## Project structure

```
RSSRob/
├── README.md
├── requirements.txt          # core deps
├── requirements-web.txt      # extra deps for the preview web app (flask)
├── pyproject.toml            # pytest config (testpaths, import path)
├── config.example.yaml       # sample single-file config
├── configs/                  # default config folder — one file per feed
│   ├── 00-settings.yaml      # globals (output_dir, state_db, http, defaults)
│   └── <feed>.yaml           # one feed each
├── rssrob/                   # the package
│   ├── __init__.py
│   ├── __main__.py           # `python -m rssrob`
│   ├── cli.py                # argparse: serve / run-once
│   ├── config.py             # load + validate YAML → dataclasses (proxy, article, …)
│   ├── extract.py            # html: HTML + selectors → items (CSS/XPath, attrs, abs URLs)
│   ├── rss.py                # rss: parse existing RSS/Atom feed → items (feedparser)
│   ├── article.py            # follow a link → full title + content (feed enrichment)
│   ├── store.py              # SQLite: insert/dedup/fetch-recent
│   ├── feed.py               # items → RSS XML via feedgen
│   ├── scheduler.py          # background per-site interval loop
│   └── server.py             # stdlib http.server: /feeds/<name>.xml + index
├── web/                      # preview web app
│   ├── webapp.py             # Flask: feed preview + selector/filter playground
│   └── templates/
├── tools/                    # standalone helper scripts
│   ├── select_preview.py     # one-shot extraction → preview.html
│   └── request_url.py        # download a page's HTML to a file
├── samples/                  # saved pages for offline testing (ipp_page.html, …)
├── tests/                    # pytest suite + fixtures + conftest.py
└── docs/                     # specs and plans
```

---

## Development

```bash
pip install -r requirements.txt
pytest                 # unit tests: extract, rss, article, store, feed, config, …
```

Tests use saved HTML fixtures so extraction is verified offline (CSS + XPath + attribute + relative-URL cases), an in-memory/temp SQLite for dedup, and well-formedness checks on generated RSS. `pyproject.toml` puts the repo root on the import path and points pytest at `tests/`.

### Preview web app

A browser tool to preview feeds and dial in selectors/filters before committing them to config:

```bash
pip install -r requirements.txt -r requirements-web.txt
python web/webapp.py                       # open http://127.0.0.1:5000/
python web/webapp.py --proxy-port 7890     # default proxy for feeds that need one
```

- `/` — feed preview (full titles + descriptions, follows article links).
- `/playground` — live **selector & filter playground** for HTML *and* RSS sources; **Save** writes a tested site (selectors, `filter`, `proxy`) as one file per feed into `configs/` (or `config.yaml` in single-file mode).

---

## Roadmap

Deliberately left out to keep v1 light; easy to add later:

- Keyword / regex include–exclude filters.
- Date / recency filtering (drop items older than N days).
- Per-feed item templates and full-content fetching (follow each link).
- Cron-style schedules instead of fixed intervals.

---

## License

TBD.
