import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from . import extract


class ConfigError(Exception):
    pass


def normalize_proxy(value):
    """Normalize a proxy setting into a requests-compatible URL, or None.

    Accepts full URLs (``socks5://ip:port``, ``http(s)://ip:port``), the
    ``socks5:ip:port`` shorthand (// inserted), a bare ``ip:port`` (assumed
    http), or a bare port (``7890`` -> ``http://127.0.0.1:7890``)."""
    if value is None or isinstance(value, bool):
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit():                                       # bare port -> localhost http
        return f"http://127.0.0.1:{s}"
    if re.match(r"^(socks5h?|https?)://", s, re.I):       # full url, keep as-is
        return s
    m = re.match(r"^(socks5h?|https?):(.+)$", s, re.I)    # "scheme:host:port" -> add //
    if m:
        return f"{m.group(1).lower()}://{m.group(2)}"
    return f"http://{s}"                                  # bare host:port -> http


@dataclass
class HttpConfig:
    host: str = "127.0.0.1"
    port: int = 8080


@dataclass
class Site:
    name: str
    url: str
    type: str = "html"
    title: Optional[str] = None
    description: Optional[str] = None
    item: Optional[str] = None
    fields: Dict[str, str] = field(default_factory=dict)
    interval: int = 3600
    max_items: int = 50
    timeout: int = 20
    user_agent: str = "RSSRob/0.1"
    proxy: Optional[str] = None      # per-feed proxy URL (or bare port)
    article: Dict[str, str] = field(default_factory=dict)  # follow-link selectors


@dataclass
class Config:
    output_dir: str
    state_db: str
    http: HttpConfig
    sites: List[Site]
    digest: Dict[str, int] = field(default_factory=dict)  # email digest sizing


def default_config_path() -> str:
    """Where to load config from by default: a `configs/` folder if present,
    else `config.yaml`, else the bundled `config.example.yaml`."""
    if Path("configs").is_dir():
        return "configs"
    if Path("config.yaml").exists():
        return "config.yaml"
    return "config.example.yaml"


def load_config(path: str) -> Config:
    """Load config from a single YAML file or a directory of YAML files.

    For a directory, every ``*.yaml``/``*.yml`` is loaded in filename order and
    merged: globals (output_dir/state_db/http/defaults) are merged by key (later
    files win), and ``sites`` are concatenated. A file may hold a single site
    directly (a top-level mapping with ``name`` and no ``sites``)."""
    p = Path(path)
    if p.is_dir():
        raw = _merge_dir(p)
    else:
        with open(p, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    return _build_config(raw)


def _merge_dir(directory: Path) -> dict:
    merged: dict = {}
    files = sorted(p for p in directory.iterdir()
                   if p.suffix in (".yaml", ".yml") and p.is_file())
    for f in files:
        with open(f, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if "name" in raw and "sites" not in raw:   # a single-site file
            raw = {"sites": [raw]}
        for key, value in raw.items():
            if key == "sites":
                merged.setdefault("sites", []).extend(value or [])
            elif key in ("defaults", "http") and isinstance(value, dict):
                merged.setdefault(key, {}).update(value)
            else:
                merged[key] = value
    return merged


def _build_config(raw: dict) -> Config:
    http_raw = raw.get("http", {}) or {}
    port = http_raw.get("port", 8080)
    if not isinstance(port, int) or not (0 < port < 65536):
        raise ConfigError(f"invalid http.port: {port!r}")
    http = HttpConfig(host=http_raw.get("host", "127.0.0.1"), port=port)

    defaults = raw.get("defaults", {}) or {}
    sites: List[Site] = []
    seen = set()
    for raw_site in raw.get("sites", []) or []:
        site = _build_site(raw_site, defaults)
        if site.name in seen:
            raise ConfigError(f"duplicate site name: {site.name!r}")
        seen.add(site.name)
        sites.append(site)

    return Config(
        output_dir=raw.get("output_dir", "./feeds"),
        state_db=raw.get("state_db", "./rssrob.db"),
        http=http,
        sites=sites,
        digest=raw.get("digest") or {},
    )


def _build_site(raw: dict, defaults: dict) -> Site:
    if "name" not in raw:
        raise ConfigError("site missing required key: name")
    name = raw["name"]
    if "url" not in raw:
        raise ConfigError(f"site {name!r} missing required key: url")

    stype = raw.get("type", "html")
    if stype not in ("html", "rss"):
        raise ConfigError(f"site {name!r} has unknown type: {stype!r}")

    if stype == "html":
        if "item" not in raw:
            raise ConfigError(f"site {name!r} (html) missing required key: item")
        if "fields" not in raw:
            raise ConfigError(f"site {name!r} (html) missing required key: fields")
        try:
            extract.validate_selector(raw["item"])
            for sel in (raw.get("fields") or {}).values():
                extract.validate_selector(sel)
        except ValueError as e:
            raise ConfigError(f"site {name!r}: {e}") from e

    return Site(
        name=name,
        url=raw["url"],
        type=stype,
        title=raw.get("title"),
        description=raw.get("description"),
        item=raw.get("item"),
        fields=raw.get("fields") or {},
        interval=raw.get("interval", defaults.get("interval", 3600)),
        max_items=raw.get("max_items", defaults.get("max_items", 50)),
        timeout=raw.get("timeout", defaults.get("timeout", 20)),
        user_agent=raw.get("user_agent", defaults.get("user_agent", "RSSRob/0.1")),
        proxy=normalize_proxy(raw.get("proxy", defaults.get("proxy"))),
        article=raw.get("article") or {},
    )
