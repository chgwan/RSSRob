import requests


class Fetcher:
    """Thin requests wrapper. Injectable: anything with a matching `get` works.

    An optional `proxy` URL (http(s)://… or socks5://…) routes outbound
    requests, e.g. for a feed behind a firewall."""

    def __init__(self, proxy: str = None):
        self.proxy = proxy

    def get(self, url: str, timeout: int = 20, user_agent: str = "RSSRob/0.1") -> bytes:
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": user_agent},
                            proxies=proxies)
        resp.raise_for_status()
        return resp.content
