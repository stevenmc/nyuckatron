"""URL and title cleanup for feed entries.

Google News RSS wraps every article behind a news.google.com/rss/articles/...
link. Resolving that to the real article URL has three tiers here, in order,
each progressively more expensive and used only when the cheaper ones fail:

1. Offline decode (`decode_google_news_url`): the path segment after
   /articles/ is base64url-encoded protobuf. Older-format links (seen in
   Google News RSS up to ~2023) store the real URL directly in it as a plain
   string field -- decodable locally, no network call, can't be rate
   limited or blocked. Current-format links (confirmed live, 2026-09-04)
   instead store an opaque token that requires a server round-trip, so this
   tier now returns None for most live links -- kept because it's free when
   it does work, and costs nothing when it doesn't.

2. Live decode (`resolve_google_news_url_live`): reverse-engineered from a
   maintained open-source decoder (github.com/SSujitX/google-news-url-decoder,
   which itself has gone through 7 rewrites chasing Google's changes -- this
   is an undocumented internal API, not a stable contract). Fetches the
   article's Google News interstitial page *following redirects* (the key
   detail: a single non-redirect-following request lands on Google's
   consent wall and gets nothing; the full redirect chain picks up a
   consent-acknowledged parameter along the way and lands on a 200 page
   carrying a signature+timestamp pair), then redeems those against
   Google's internal batchexecute RPC for the real URL. Verified end-to-end
   against a live production link once -- but confirmed failing 100% of
   the time in later testing (2026-09-05 and 2026-09-06), landing on the
   interactive consent page instead of the interstitial with signing
   params. Two extra HTTP requests when it does work, so callers should
   only invoke this for entries actually about to be posted, not every
   candidate fetched.

3. Browser resolve (`GoogleNewsBrowserResolver`): a real headless browser
   (Playwright + Chromium) does what a human would -- load the page, click
   "Reject all" on Google's cookie-consent interstitial, follow the
   resulting JS-driven redirect. Confirmed live (2026-09-07) to succeed
   where Tier 2 currently fails: 2/2 real links resolved correctly.
   Substantially heavier than the other tiers (a real browser process, not
   just an HTTP request) and entirely optional -- if the `playwright`
   package or its Chromium browser isn't installed, this fails closed
   (returns None) rather than raising or blocking anything else. See the
   class docstring for why it's designed to be launched once and reused
   across many links in a run, not once per link.

All three tiers return None on failure -- callers fall back to whatever
they had before, down to the original wrapper link if all three fail,
which still works fine for a human clicking it in a real browser (Google's
own JS completes the redirect there); it just shows news.google.com as the
link domain instead of the real outlet.
"""

import base64
import binascii
import json
import logging
import re
from urllib.parse import quote, urlparse, urlunparse

import requests

log = logging.getLogger("newry-bot")

_GOOGLE_NEWS_ARTICLE_PATH = re.compile(r"/articles/([^/?]+)")
_URL_IN_DECODED_BLOB = re.compile(r"https?://[\x21-\x7e]+")
_DATA_SIGNATURE = re.compile(r'data-n-a-sg="([^"]*)"')
_DATA_TIMESTAMP = re.compile(r'data-n-a-ts="([^"]*)"')

_BATCHEXECUTE_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)


def decode_google_news_url(url):
    """Tier 1: best-effort, network-free decode. Returns None if the link
    isn't a recognisable Google News article URL, or (commonly, for
    current-format links) doesn't decode to something URL-shaped."""
    match = _GOOGLE_NEWS_ARTICLE_PATH.search(url)
    if not match:
        return None

    blob = match.group(1)
    padded = blob + "=" * (-len(blob) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
    except (ValueError, binascii.Error):
        return None

    found = _URL_IN_DECODED_BLOB.search(raw.decode("latin-1"))
    return found.group(0) if found else None


def resolve_google_news_url_live(url, timeout=8):
    """Tier 2: best-effort resolve via Google's internal batchexecute RPC.
    Never raises -- returns None on any failure (network error, missing
    signing params, Google having changed the response format again) so
    callers can fall back to the wrapper link. The broad exception catch
    here is deliberate: this walks through two network calls and two layers
    of ad-hoc JSON parsing against an undocumented, unstable response
    format, where almost any part could break independently."""
    match = _GOOGLE_NEWS_ARTICLE_PATH.search(url)
    if not match:
        return None
    blob = match.group(1)

    try:
        page = requests.get(
            url, headers={"User-Agent": _USER_AGENT}, timeout=timeout, allow_redirects=True
        )
        page.raise_for_status()

        sig_match = _DATA_SIGNATURE.search(page.text)
        ts_match = _DATA_TIMESTAMP.search(page.text)
        if not sig_match or not ts_match:
            return None
        signature, timestamp = sig_match.group(1), ts_match.group(1)

        rpc_payload = [
            "Fbv4je",
            (
                '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,'
                "null,null,null,null,null,0,1],\"X\",\"X\",1,[1,1,1],1,1,null,0,0,null,0],"
                f'"{blob}",{timestamp},"{signature}"]'
            ),
        ]
        body = "f.req=" + quote(json.dumps([[rpc_payload]]))

        rpc_resp = requests.post(
            _BATCHEXECUTE_URL,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "User-Agent": _USER_AGENT,
            },
            timeout=timeout,
        )
        rpc_resp.raise_for_status()

        # Response body is `)]}'` (anti-JSON-hijacking prefix) + blank line
        # + a JSON array-of-arrays, with the real URL buried as a
        # JSON-encoded string inside a JSON-encoded string.
        outer = json.loads(rpc_resp.text.split("\n\n")[1])[:-2]
        inner = json.loads(outer[0][2])
        decoded_url = inner[1]

        return decoded_url if isinstance(decoded_url, str) and decoded_url.startswith("http") else None
    except Exception:
        return None


class GoogleNewsBrowserResolver:
    """Tier 3 Google News link resolver: a real headless browser
    (Playwright + Chromium) does what a human would -- load the page,
    click "Reject all" on Google's cookie-consent interstitial, follow the
    resulting JS-driven redirect to the real article. Confirmed live
    (2026-09-07) to succeed where Tier 2 (resolve_google_news_url_live)
    currently fails 100% of the time: 2/2 real links resolved correctly in
    testing -- and a browser context that's already rejected cookies once
    skips the consent click entirely on later resolves in the same
    session (the cookie persists), which is exactly why this class
    launches one browser and reuses it across many .resolve() calls
    rather than launching fresh per link -- that would repeat both the
    ~1-2s Chromium startup cost and the consent click every single time
    for no benefit.

    Fully optional and lazy: the browser only actually launches on the
    first .resolve() call, not at construction, so a run that never
    reaches Tier 3 (Tiers 1/2 already resolved everything that needed it,
    or there's nothing left to post) pays nothing for this at all. If the
    `playwright` package isn't installed, or its Chromium browser isn't
    installed (`playwright install chromium`), or the browser fails to
    launch for any other reason, .resolve() fails closed (returns None)
    after logging once -- and remembers that failure so it doesn't keep
    re-attempting a launch already known to fail for the rest of this
    run. Nothing else in the bot depends on this working; every caller
    already has a fallback for when it returns None, same as Tiers 1/2.

    Confirmed (2026-09-07) unusable on an EOL host: Playwright's bundled
    Node.js driver requires GLIBC_2.28+; Ubuntu 18.04 (Bionic) ships
    2.27, so `sync_playwright().start()` fails there every time with
    "version 'GLIBC_2.28' not found" -- not fixable by installing more
    packages, since glibc underpins the whole OS (upgrading it in place
    on a live box is a real risk to everything else running there, not
    something to attempt for this). This is exactly why .resolve()
    failing closed matters in practice, not just in theory: on a host
    like that, Tier 3 silently never activates, and the bot keeps working
    exactly as it did before this class existed. A modern Ubuntu
    (22.04/24.04 ship glibc 2.35+/2.39+) doesn't have this problem.

    Callers must call .close() when done -- a plain try/finally around
    whatever loop uses .resolve() is enough -- to release the browser
    process. Safe to call .close() even if .resolve() was never called or
    the browser never launched."""

    def __init__(self):
        self._page = None
        self._browser = None
        self._playwright = None
        self._unavailable = False

    def _ensure_launched(self):
        if self._page is not None or self._unavailable:
            return
        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._page = self._browser.new_page(user_agent=_USER_AGENT)
        except Exception:
            log.warning(
                "Could not launch headless browser for Google News link "
                "resolution -- skipping Tier 3 for the rest of this run. "
                "Is playwright installed ('pip install playwright') and "
                "its browser downloaded ('playwright install chromium')?"
            )
            self._unavailable = True
            self.close()

    def resolve(self, url, timeout=15000):
        """Best-effort resolve of one Google News wrapper link into its
        real article URL. Returns None on any failure (playwright/Chromium
        unavailable, navigation timeout, Google changing the consent flow
        again) -- never raises."""
        self._ensure_launched()
        if self._page is None:
            return None

        try:
            self._page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            try:
                # Only present if this browser context hasn't consented
                # yet this run -- a short timeout here is deliberate, so a
                # context that's already past the consent wall doesn't
                # stall waiting for a button that was never going to show.
                self._page.get_by_role("button", name="Reject all").first.click(timeout=3000)
                self._page.wait_for_load_state("domcontentloaded", timeout=timeout)
            except Exception:
                pass
            self._page.wait_for_load_state("networkidle", timeout=timeout)
            final_url = self._page.url
        except Exception:
            return None

        if (
            final_url
            and final_url.startswith("http")
            and "news.google.com" not in final_url
            and "consent.google.com" not in final_url
        ):
            return final_url
        return None

    def close(self):
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        self._page = None
        self._browser = None
        self._playwright = None


def strip_eventbrite_tracking(url):
    """Eventbrite links carry a long tracking query string that doesn't
    affect the destination -- strip it so the same event doesn't look like a
    new URL every time it's shared with different tracking params."""
    parsed = urlparse(url)
    if "eventbrite." not in parsed.netloc:
        return url
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def strip_mobile_subdomain(url):
    """Rewrites a 'm.' mobile-site subdomain (e.g. m.belfasttelegraph.co.uk)
    down to its canonical host before a link is ever posted. 'm.' is purely
    a mobile-rendering artifact of the same site, not a distinct domain --
    the canonical host works identically and looks cleaner in a Reddit
    post than a mobile-prefixed one."""
    parsed = urlparse(url)
    if parsed.netloc.lower().startswith("m."):
        parsed = parsed._replace(netloc=parsed.netloc[2:])
        return urlunparse(parsed)
    return url


def clean_url(url, live_decode=True):
    """Applies Eventbrite tracking-param stripping, mobile-subdomain
    stripping, then Google News resolution: free offline decode first,
    live decode as a fallback (only attempted when live_decode is True --
    callers on a hot path that shouldn't pay for two extra HTTP requests
    per candidate can pass False and get the offline-only result),
    original wrapper link as the last resort if both fail."""
    url = strip_eventbrite_tracking(url)
    url = strip_mobile_subdomain(url)
    decoded = decode_google_news_url(url)
    if decoded:
        return strip_mobile_subdomain(decoded)
    if live_decode:
        live = resolve_google_news_url_live(url)
        if live:
            return strip_mobile_subdomain(live)
    return url


def domain_of(url):
    """Hostname for matching against an exclude/allow list, with a leading
    'www.' or 'm.' stripped so 'irishnews.com' in config matches
    'irishnews.com', 'www.irishnews.com', and 'm.irishnews.com' links
    alike -- both are the same site, not a distinct domain. (clean_url
    already rewrites 'm.' out of URLs before they're posted; stripping it
    here too means matching still works correctly for a URL that reached
    this function some other way, e.g. directly in a test.)"""
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if netloc.startswith("m."):
        netloc = netloc[2:]
    return netloc


def is_excluded_domain(url, excluded_domains):
    """True if url's host is, or is a subdomain of, any entry in
    excluded_domains -- e.g. 'irishnews.com' in the list also catches
    'm.irishnews.com', not just an exact match, without matching an
    unrelated domain that merely contains the same substring."""
    netloc = domain_of(url)
    return any(netloc == d or netloc.endswith("." + d) for d in excluded_domains)


def is_allowed_domain(url, allowed_domains, allowed_suffixes=(), place_names=()):
    """True if url's host is allowed, by any of three independent checks:

    1. allowed_domains: exact match or subdomain of a specific, enumerated
       domain (same host/subdomain matching as is_excluded_domain,
       inverted) -- the precise, hand-verified case.
    2. allowed_suffixes: host ends with a shared naming-convention suffix,
       e.g. "-ni.gov.uk" for Northern Ireland executive departments --
       for a whole family of current and future sites that isn't a fixed
       list or a true subdomain relationship, just a pattern.
    3. place_names: one of these appears as a substring anywhere in the
       host -- broader/coverage-favoring, for an outlet whose own domain
       name already signals its location without needing to be
       enumerated by hand first (e.g. a hypothetical "newryobserver.com").
       This carries some of the same ambiguous-placename risk documented
       in CATCHMENT_PLACES itself -- "hilltown" in particular, since
       Hilltown, Dundee is a real place a business could equally name a
       domain after -- just at much lower likelihood here, since it's
       matching a registered domain name rather than arbitrary free-text
       article content.

    All three are optional and default to empty -- existing callers that
    only ever wanted the first, precise check are unaffected."""
    netloc = domain_of(url)
    if any(netloc == d or netloc.endswith("." + d) for d in allowed_domains):
        return True
    if any(netloc.endswith(suffix) for suffix in allowed_suffixes):
        return True
    if any(name.lower() in netloc for name in place_names):
        return True
    return False


# Matches a hyphen, en dash, em dash, or pipe surrounded by single spaces --
# the separators different outlets/CMSs use for "<headline> - Outlet".
_VENDOR_SEPARATOR = re.compile(r"\s[-–—|]\s")


def strip_vendor_suffix(title):
    """Generic fallback for '<headline> - Outlet' / '<headline> | Outlet'
    (or en dash / em dash variants) titles when the feed doesn't tell us the
    outlet name directly (see bot.py's use of entry.source.title for Google
    News, which is more precise when available). Cuts at the *last*
    separator found, regardless of which kind -- cutting at the first
    instead would mangle any real headline that happens to contain a
    ' - ' of its own before the vendor suffix."""
    matches = list(_VENDOR_SEPARATOR.finditer(title))
    if not matches:
        return title.strip()
    return title[: matches[-1].start()].strip()
