"""Twitter/X client via X's internal web GraphQL API (x.com/i/api/graphql).

To follow an account without the paid API or a headless browser, RSSRob reuses a
logged-in browser session: it sends the user's ``auth_token``/``ct0`` cookies plus
the public web bearer token to the same GraphQL endpoints the x.com web app calls.
``UserByScreenName`` resolves a @handle to a numeric user id; ``UserTweets`` lists
that user's profile-timeline tweets.

The HTTP layer is isolated behind a ``transport`` so the mapping logic is testable
offline with canned JSON. ``TwitterClient`` holds the logic; ``GraphQlTransport``
is the one piece that talks to x.com. Note: X rotates GraphQL query-ids and
``features`` flags periodically — when a call 404s, ``TwitterQueryError`` says so.
"""

import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional

from .models import Item

# Where you log in to capture a credential.
X_LOGIN_URL = "https://x.com/"

# The x.com web app's public bearer token (a fixed, well-known constant).
PUBLIC_BEARER = ("Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4"
                 "puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA")

_TITLE_LEN = 80


class TwitterError(Exception):
    """Base for Twitter client errors."""


class TwitterAuthError(TwitterError):
    """Session is missing or expired; the user must log in again."""


class TwitterRateLimited(TwitterError):
    """X returned HTTP 429; back off and retry later."""


class TwitterQueryError(TwitterError):
    """A GraphQL query-id / features set is stale (X rotated it)."""


@dataclass
class Account:
    id: str                          # the numeric rest_id
    name: Optional[str] = None       # display name
    handle: Optional[str] = None     # screen name (no @)
    description: Optional[str] = None


@dataclass
class RawTweet:
    id: str                          # tweet rest_id
    text: Optional[str]
    link: Optional[str]
    created_at: Optional[float]      # epoch seconds


def _snippet(text, n=_TITLE_LEN):
    if not text:
        return None
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= n else one_line[:n].rstrip() + "…"


class TwitterClient:
    """Account operations on top of a swappable ``transport``.

    The transport exposes ``user_by_screen_name(handle, cred)`` and
    ``user_tweets(user_id, limit, cred)`` returning plain dicts. The client adds
    the credential guard, polite request spacing, and result mapping to
    ``Account`` / ``RawTweet`` / ``Item``."""

    def __init__(self, transport, credential=None, credential_path=None,
                 min_spacing=1.0):
        self.transport = transport
        self.credential = credential
        self.credential_path = credential_path
        self.min_spacing = min_spacing
        self._last_call = 0.0

    def _require(self):
        if self.credential is None:
            raise TwitterAuthError("not logged in; run `rssrob twitter-login`")

    def _space(self):
        wait = self.min_spacing - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.4))
        self._last_call = time.time()

    def resolve_user(self, handle) -> Account:
        self._require()
        self._space()
        r = self.transport.user_by_screen_name(handle, self.credential) or {}
        return Account(id=r.get("id"), name=r.get("name"),
                       handle=r.get("handle"), description=r.get("desc"))

    def list_tweets(self, user_id, limit) -> List[RawTweet]:
        self._require()
        self._space()
        rows = self.transport.user_tweets(user_id, limit, self.credential) or []
        return [RawTweet(
            id=r["id"], text=r.get("text"), link=r.get("link"),
            created_at=(float(r["created_at"])
                        if r.get("created_at") is not None else None))
            for r in rows]

    def to_items(self, raw: List[RawTweet]) -> List[Item]:
        """Map tweets to the shared ``Item`` shape (date as ISO8601)."""
        items = []
        for tw in raw:
            date = (datetime.fromtimestamp(tw.created_at, tz=timezone.utc).isoformat()
                    if tw.created_at is not None else None)
            items.append(Item(id=tw.id, title=_snippet(tw.text), link=tw.link,
                              summary=tw.text, date=date))
        return items


class GraphQlTransport:
    """Concrete transport over x.com's internal GraphQL endpoints.

    Auth is the browser session: the ``auth_token`` + ``ct0`` cookies, the
    ``x-csrf-token`` header (= ``ct0``), and the public web bearer token. Query
    ids and ``features`` flags below are current as of writing; X rotates them, so
    a 404 surfaces as ``TwitterQueryError`` (update the constants when that
    happens)."""

    BASE = "https://x.com/i/api/graphql"

    # query-ids rotate; update if calls start 404ing (see TwitterQueryError).
    QUERY_IDS = {
        "UserByScreenName": "G3KGOASz96M-Qu0nwmGXNg",
        "UserTweets": "V7H0Ap3_Hh2FyS75OCDO3Q",
    }

    # Minimal-but-required GraphQL feature flags (X rejects calls without them).
    _USER_FEATURES = {
        "hidden_profile_subscriptions_enabled": True,
        "responsive_web_graphql_exclude_directive_enabled": True,
        "verified_phone_label_enabled": False,
        "subscriptions_verification_info_is_identity_verified_enabled": True,
        "subscriptions_verification_info_verified_since_enabled": True,
        "highlights_tweets_tab_ui_enabled": True,
        "responsive_web_twitter_article_notes_tab_enabled": True,
        "subscriptions_feature_can_gift_premium": True,
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
        "responsive_web_graphql_timeline_navigation_enabled": True,
    }
    _TWEET_FEATURES = {
        "responsive_web_graphql_exclude_directive_enabled": True,
        "verified_phone_label_enabled": False,
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "responsive_web_graphql_timeline_navigation_enabled": True,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
        "c9s_tweet_anatomy_moderator_badge_enabled": True,
        "tweetypie_unmention_optimization_enabled": True,
        "responsive_web_edit_tweet_api_enabled": True,
        "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
        "view_counts_everywhere_api_enabled": True,
        "longform_notetweets_consumption_enabled": True,
        "responsive_web_twitter_article_tweet_consumption_enabled": True,
        "tweet_awards_web_tipping_enabled": False,
        "freedom_of_speech_not_reach_fetch_enabled": True,
        "standardized_nudges_misinfo": True,
        "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
        "rweb_video_timestamps_enabled": True,
        "longform_notetweets_rich_text_read_enabled": True,
        "longform_notetweets_inline_media_enabled": True,
        "responsive_web_enhance_cards_enabled": False,
    }

    HEADERS = {
        "authorization": PUBLIC_BEARER,
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        "Accept": "*/*",
        "x-twitter-active-user": "yes",
        "x-twitter-client-language": "en",
    }

    def __init__(self, session=None, timeout=20, proxy=None):
        if session is None:
            import requests
            session = requests.Session()
        self.session = session
        self.timeout = timeout
        self.proxy = proxy

    def _call(self, op, variables, features, cred):
        qid = self.QUERY_IDS[op]
        url = f"{self.BASE}/{qid}/{op}"
        params = {"variables": json.dumps(variables, separators=(",", ":")),
                  "features": json.dumps(features, separators=(",", ":"))}
        headers = {**self.HEADERS, "x-csrf-token": cred.csrf_token,
                   "cookie": f"auth_token={cred.auth_token}; ct0={cred.csrf_token}"}
        proxies = ({"http": self.proxy, "https": self.proxy}
                   if self.proxy else None)
        resp = self.session.request("GET", url, params=params, headers=headers,
                                    timeout=self.timeout, proxies=proxies)
        status = getattr(resp, "status_code", 200)
        if status == 429:
            raise TwitterRateLimited("x.com rate limit (HTTP 429)")
        if status in (401, 403):
            raise TwitterAuthError(f"x.com session invalid (HTTP {status})")
        if status == 404:
            raise TwitterQueryError(
                f"x.com GraphQL {op} 404 — query-id likely rotated; "
                "update GraphQlTransport.QUERY_IDS")
        data = resp.json() or {}
        errors = data.get("errors")
        if errors and not data.get("data"):
            code = (errors[0] or {}).get("code")
            if code in (32, 64, 215):              # bad auth / suspended / bad token
                raise TwitterAuthError(f"x.com GraphQL auth error: {errors[0]}")
            raise TwitterError(f"x.com GraphQL error: {errors[0]}")
        return data

    def user_by_screen_name(self, handle, cred):
        variables = {"screen_name": handle, "withSafetyModeUserFields": True}
        d = self._call("UserByScreenName", variables, self._USER_FEATURES, cred)
        result = (((d.get("data") or {}).get("user") or {}).get("result") or {})
        legacy = result.get("legacy") or {}
        if not result.get("rest_id"):
            return {}
        return {"id": result.get("rest_id"), "name": legacy.get("name"),
                "handle": legacy.get("screen_name"),
                "desc": legacy.get("description")}

    def user_tweets(self, user_id, limit, cred):
        variables = {"userId": str(user_id), "count": min(int(limit), 40),
                     "includePromotedContent": False,
                     "withQuickPromoteEligibilityTweetFields": False,
                     "withVoice": True, "withV2Timeline": True}
        d = self._call("UserTweets", variables, self._TWEET_FEATURES, cred)
        timeline = ((((d.get("data") or {}).get("user") or {}).get("result") or {})
                    .get("timeline_v2") or {}).get("timeline") or {}
        return _parse_timeline(timeline.get("instructions") or [])


def _tweet_epoch(created_at):
    """Parse Twitter's 'Wed Oct 10 20:19:24 +0000 2018' format to epoch seconds."""
    if not created_at:
        return None
    try:
        return parsedate_to_datetime(created_at).timestamp()
    except (TypeError, ValueError):
        return None


def _parse_timeline(instructions):
    """Walk UserTweets instructions → a flat list of tweet dicts."""
    out = []
    for instr in instructions:
        for entry in instr.get("entries") or []:
            content = entry.get("content") or {}
            item = content.get("itemContent") or {}
            if item.get("itemType") != "TimelineTweet":
                continue
            result = ((item.get("tweet_results") or {}).get("result") or {})
            if result.get("__typename") == "TweetWithVisibilityResults":
                result = result.get("tweet") or result
            rest_id = result.get("rest_id")
            legacy = result.get("legacy") or {}
            if not rest_id or not legacy:
                continue
            screen = (((result.get("core") or {}).get("user_results") or {})
                      .get("result") or {}).get("legacy", {}).get("screen_name", "i")
            out.append({
                "id": rest_id,
                "text": legacy.get("full_text"),
                "link": f"https://x.com/{screen}/status/{rest_id}",
                "created_at": _tweet_epoch(legacy.get("created_at")),
            })
    return out
