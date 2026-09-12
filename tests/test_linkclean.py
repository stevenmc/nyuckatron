import sys
from unittest.mock import MagicMock, patch

import requests

import config
import linkclean

# A real (2023) Google News article link, used to verify the base64/protobuf
# decode against a known real-world value rather than a synthetic fixture.
REAL_GNEWS_URL = (
    "https://news.google.com/rss/articles/"
    "CBMie2h0dHBzOi8vd3d3LmlyaXNobmV3cy5jb20vbmV3cy9ub3J0aGVybmlyZWxhbmRuZXdzLzIwMjMvMDkvMDEvbmV3cy9"
    "uZXdyeV90ZWFjaGVyX2JlY29tZXNfeW91bmdlc3RfbmFzdXd0X3ByZXNpZGVudC0zNTc0MzMyL9IBAA"
    "?oc=5&hl=en-US&gl=US&ceid=US:en"
)
REAL_GNEWS_DECODED = (
    "https://www.irishnews.com/news/northernirelandnews/2023/09/01/news/"
    "newry_teacher_becomes_youngest_nasuwt_president-3574332/"
)

# A real (2026) current-format Google News link that does NOT decode
# offline (opaque token, not a plain embedded URL) -- used to test the live
# decode path. Captured from a real production run.
LIVE_FORMAT_GNEWS_URL = (
    "https://news.google.com/rss/articles/"
    "CBMisgFBVV95cUxNX0tsdFEtYkp2LVI4cm1YZEt5Um1odGhvZTc1VnhiR0lnRjBtdXF0czkybzdRWEhqNHJ4X2FOd2J3R2piY1ZP"
    "UDNKMEt5NDEyZE1ZcXRHRUxmaDJYRVZmTEN0UktUVUlqTVNwMU12c3VfUkhGMHlOeWtLXzRTVTdRNkpJMVB0eGNfSERGREhMS1hy"
    "OEQ5ZEtsS3poU3BULWVsQUtoal9LcDBTS2wwYTlncXl3"
    "?oc=5"
)
# Real signature+timestamp scraped from that live link's interstitial page,
# and the real batchexecute response body they redeem to -- both captured
# from an actual successful resolve (2026-09-04).
REAL_SIGNATURE = "Ae5Wzi9w4BAClBpbq2kP16vDcneV"
REAL_TIMESTAMP = "1788562093"
REAL_INTERSTITIAL_HTML = (
    f'<c-wiz><div jscontroller="x" data-n-a-sg="{REAL_SIGNATURE}" data-n-a-ts="{REAL_TIMESTAMP}"></div></c-wiz>'
)
REAL_BATCHEXECUTE_RESPONSE = (
    ")]}'\n\n"
    '[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://www.newry.ie/articles/news/'
    'team-mullen-to-compete-in-great-north-run-in-memory-of-their-late-mother-anne\\",1]"'
    ',null,null,null,""],["di",9],["af.httprm",8,"-8300123930729836481",37]]'
)
LIVE_FORMAT_DECODED = (
    "https://www.newry.ie/articles/news/"
    "team-mullen-to-compete-in-great-north-run-in-memory-of-their-late-mother-anne"
)


def test_decode_google_news_url_recovers_real_article_url():
    assert linkclean.decode_google_news_url(REAL_GNEWS_URL) == REAL_GNEWS_DECODED


def test_decode_google_news_url_returns_none_for_non_google_news_link():
    assert linkclean.decode_google_news_url("https://www.bbc.co.uk/news/uk-northern-ireland-123") is None


def test_decode_google_news_url_returns_none_for_garbage_blob():
    assert linkclean.decode_google_news_url("https://news.google.com/rss/articles/not-valid-base64!!!") is None


def _mock_response(text=None, json_data=None, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status} error")
    return resp


def test_resolve_google_news_url_live_full_success(monkeypatch):
    # Exercises the whole real pipeline (interstitial page -> signature +
    # timestamp -> batchexecute -> decoded URL) with real captured
    # responses, offline.
    get_resp = _mock_response(text=REAL_INTERSTITIAL_HTML)
    post_resp = _mock_response(text=REAL_BATCHEXECUTE_RESPONSE)
    monkeypatch.setattr(linkclean.requests, "get", MagicMock(return_value=get_resp))
    monkeypatch.setattr(linkclean.requests, "post", MagicMock(return_value=post_resp))

    result = linkclean.resolve_google_news_url_live(LIVE_FORMAT_GNEWS_URL)

    assert result == LIVE_FORMAT_DECODED


def test_resolve_google_news_url_live_sends_correct_batchexecute_payload(monkeypatch):
    get_resp = _mock_response(text=REAL_INTERSTITIAL_HTML)
    post_resp = _mock_response(text=REAL_BATCHEXECUTE_RESPONSE)
    mock_post = MagicMock(return_value=post_resp)
    monkeypatch.setattr(linkclean.requests, "get", MagicMock(return_value=get_resp))
    monkeypatch.setattr(linkclean.requests, "post", mock_post)

    linkclean.resolve_google_news_url_live(LIVE_FORMAT_GNEWS_URL)

    _, kwargs = mock_post.call_args
    assert REAL_SIGNATURE in kwargs["data"]
    assert REAL_TIMESTAMP in kwargs["data"]


def test_resolve_google_news_url_live_returns_none_when_signing_params_missing(monkeypatch):
    get_resp = _mock_response(text="<html>no signing params here</html>")
    monkeypatch.setattr(linkclean.requests, "get", MagicMock(return_value=get_resp))
    monkeypatch.setattr(linkclean.requests, "post", MagicMock())  # should never be called

    assert linkclean.resolve_google_news_url_live(LIVE_FORMAT_GNEWS_URL) is None


def test_resolve_google_news_url_live_returns_none_on_network_error(monkeypatch):
    monkeypatch.setattr(
        linkclean.requests, "get", MagicMock(side_effect=requests.ConnectionError("no route"))
    )
    assert linkclean.resolve_google_news_url_live(LIVE_FORMAT_GNEWS_URL) is None


def test_resolve_google_news_url_live_returns_none_on_consent_wall(monkeypatch):
    # Simulates the real, observed failure mode: landing on Google's consent
    # page instead of the interstitial with signing params.
    get_resp = _mock_response(text="<html>Before you continue to Google...</html>")
    monkeypatch.setattr(linkclean.requests, "get", MagicMock(return_value=get_resp))
    assert linkclean.resolve_google_news_url_live(LIVE_FORMAT_GNEWS_URL) is None


def test_resolve_google_news_url_live_returns_none_on_malformed_batchexecute_response(monkeypatch):
    get_resp = _mock_response(text=REAL_INTERSTITIAL_HTML)
    post_resp = _mock_response(text="not the expected format at all")
    monkeypatch.setattr(linkclean.requests, "get", MagicMock(return_value=get_resp))
    monkeypatch.setattr(linkclean.requests, "post", MagicMock(return_value=post_resp))

    assert linkclean.resolve_google_news_url_live(LIVE_FORMAT_GNEWS_URL) is None


def test_resolve_google_news_url_live_returns_none_for_non_google_news_link():
    assert linkclean.resolve_google_news_url_live("https://www.bbc.co.uk/news/123") is None


# --- clean_url's three-tier fallback chain --------------------------------

def test_clean_url_uses_offline_decode_without_touching_network(monkeypatch):
    monkeypatch.setattr(
        linkclean, "resolve_google_news_url_live", MagicMock(side_effect=AssertionError("should not be called"))
    )
    assert linkclean.clean_url(REAL_GNEWS_URL) == REAL_GNEWS_DECODED


def test_clean_url_falls_back_to_live_decode_when_offline_decode_fails(monkeypatch):
    monkeypatch.setattr(linkclean, "resolve_google_news_url_live", MagicMock(return_value=LIVE_FORMAT_DECODED))
    assert linkclean.clean_url(LIVE_FORMAT_GNEWS_URL) == LIVE_FORMAT_DECODED


def test_clean_url_falls_back_to_wrapper_link_when_both_tiers_fail(monkeypatch):
    monkeypatch.setattr(linkclean, "resolve_google_news_url_live", MagicMock(return_value=None))
    assert linkclean.clean_url(LIVE_FORMAT_GNEWS_URL) == LIVE_FORMAT_GNEWS_URL


def test_clean_url_skips_live_decode_when_disabled(monkeypatch):
    monkeypatch.setattr(
        linkclean, "resolve_google_news_url_live", MagicMock(side_effect=AssertionError("should not be called"))
    )
    assert linkclean.clean_url(LIVE_FORMAT_GNEWS_URL, live_decode=False) == LIVE_FORMAT_GNEWS_URL


def test_strip_eventbrite_tracking_removes_query_string():
    url = "https://www.eventbrite.co.uk/e/newry-mela-2026-tickets-12345?aff=erelexpmlt&keep=no"
    cleaned = linkclean.strip_eventbrite_tracking(url)
    assert cleaned == "https://www.eventbrite.co.uk/e/newry-mela-2026-tickets-12345"


def test_strip_eventbrite_tracking_leaves_other_domains_untouched():
    url = "https://www.bbc.co.uk/news/uk-northern-ireland-123?utm_source=x"
    assert linkclean.strip_eventbrite_tracking(url) == url


def test_clean_url_prefers_decoded_google_news_link():
    assert linkclean.clean_url(REAL_GNEWS_URL) == REAL_GNEWS_DECODED


def test_clean_url_strips_mobile_subdomain_from_a_google_news_decoded_link():
    # Regression test: strip_mobile_subdomain was only ever applied to the
    # *input* URL, never to the result of decoding a Google News wrapper --
    # so the exact case that motivated building it (a Belfast Telegraph
    # article reached via Google News, decoding to an m.-prefixed URL)
    # would sail through with 'm.' intact. Blob below is the offline-decodable
    # (base64url, no padding) encoding of a plain m.-prefixed URL string,
    # built the same way REAL_GNEWS_URL's blob was.
    url = (
        "https://news.google.com/rss/articles/"
        "aHR0cHM6Ly9tLmJlbGZhc3R0ZWxlZ3JhcGguY28udWsvbmV3cy9tb2JpbGUtZXhhbXBsZQ"
    )
    assert linkclean.clean_url(url) == "https://belfasttelegraph.co.uk/news/mobile-example"


def test_clean_url_falls_back_to_original_when_undecodable(monkeypatch):
    # live_decode=False: this must stay offline. Without it, an
    # undecodable-but-Google-News-shaped URL would fall through to a real
    # network call via resolve_google_news_url_live.
    url = "https://news.google.com/rss/articles/not-valid-base64!!!"
    assert linkclean.clean_url(url, live_decode=False) == url


def test_clean_url_passes_through_ordinary_links():
    url = "https://www.bbc.co.uk/news/uk-northern-ireland-123"
    assert linkclean.clean_url(url) == url


def test_strip_vendor_suffix_dash_separator():
    assert linkclean.strip_vendor_suffix("New bins - Newry Times") == "New bins"


def test_strip_vendor_suffix_pipe_separator():
    title = "Seamus Mallon – 'A Shared Home Place' event | Newry News"
    assert linkclean.strip_vendor_suffix(title) == "Seamus Mallon – 'A Shared Home Place' event"


def test_strip_vendor_suffix_leaves_title_without_separator_untouched():
    assert linkclean.strip_vendor_suffix("Newry & Mourne") == "Newry & Mourne"


def test_strip_vendor_suffix_only_strips_last_dash_not_first():
    # A naive unlimited rsplit(' - ') strips everything after the FIRST
    # occurrence, mangling real headline content before the vendor suffix.
    title = "Council votes to fund new bridge - after weeks of debate - Newry Times"
    assert linkclean.strip_vendor_suffix(title) == "Council votes to fund new bridge - after weeks of debate"


def test_strip_vendor_suffix_en_dash_separator():
    assert linkclean.strip_vendor_suffix("Newry wins football – Irish News") == "Newry wins football"


def test_strip_vendor_suffix_em_dash_separator():
    assert linkclean.strip_vendor_suffix("Newry wins football — Irish News") == "Newry wins football"


# --- domain exclusion (e.g. irishnews.com's metered paywall) -------------

def test_domain_of_strips_www():
    assert linkclean.domain_of("https://www.irishnews.com/news/x") == "irishnews.com"
    assert linkclean.domain_of("https://irishnews.com/news/x") == "irishnews.com"


def test_is_excluded_domain_matches_exact_and_subdomains():
    excluded = ["irishnews.com"]
    assert linkclean.is_excluded_domain("https://www.irishnews.com/news/x", excluded)
    assert linkclean.is_excluded_domain("https://irishnews.com/news/x", excluded)
    assert linkclean.is_excluded_domain("https://m.irishnews.com/news/x", excluded)


def test_is_excluded_domain_does_not_match_unrelated_domain_with_shared_substring():
    # "irishnews.com" must not match e.g. "not-irishnews.com" or
    # "irishnewsagency.com" -- only an exact host or a real subdomain of it.
    excluded = ["irishnews.com"]
    assert not linkclean.is_excluded_domain("https://not-irishnews.com/x", excluded)
    assert not linkclean.is_excluded_domain("https://irishnewsagency.com/x", excluded)


def test_is_allowed_domain_matches_exact_and_subdomains():
    allowed = ["newry.ie"]
    assert linkclean.is_allowed_domain("https://www.newry.ie/x", allowed)
    assert linkclean.is_allowed_domain("https://newry.ie/x", allowed)


def test_is_allowed_domain_rejects_anything_not_listed():
    # The real incident this exists for: a keyword match on "Hilltown"
    # isn't proof of location -- Dundee, Scotland also has a district
    # called Hilltown. thecourier.co.uk (a real Dundee-area outlet) isn't
    # a recognised Newry-area outlet, so it must be rejected regardless of
    # what the story's title says.
    allowed = config.KNOWN_LOCAL_OUTLETS
    assert not linkclean.is_allowed_domain("https://www.thecourier.co.uk/fp/news/dundee/x", allowed)


def test_is_allowed_domain_accepts_unresolved_google_news_wrapper_link():
    # Reversed 2026-09-05, deliberately: news.google.com is now IN
    # KNOWN_LOCAL_OUTLETS (see config.py's note on why) so an unresolved
    # link passes rather than being dropped -- favouring not losing real
    # coverage over catching the rare ambiguous-placename false positive.
    assert linkclean.is_allowed_domain(
        "https://news.google.com/rss/articles/abc123", config.KNOWN_LOCAL_OUTLETS
    )


# --- allowed_suffixes / place_names (extended matching) -----------------

def test_is_allowed_domain_matches_suffix_pattern():
    assert linkclean.is_allowed_domain(
        "https://www.communities-ni.gov.uk/news/x", [], allowed_suffixes=["-ni.gov.uk"]
    )
    assert not linkclean.is_allowed_domain(
        "https://www.gov.uk/news/x", [], allowed_suffixes=["-ni.gov.uk"]
    )


def test_is_allowed_domain_matches_place_name_substring():
    assert linkclean.is_allowed_domain(
        "https://www.newryobserver.com/x", [], place_names=["Newry"]
    )
    assert not linkclean.is_allowed_domain(
        "https://www.example.com/x", [], place_names=["Newry"]
    )


def test_is_allowed_domain_place_name_matching_is_case_insensitive():
    assert linkclean.is_allowed_domain(
        "https://www.NewryObserver.com/x", [], place_names=["newry"]
    )


def test_is_allowed_domain_combines_all_three_checks():
    allowed = ["newry.ie"]
    suffixes = ["-ni.gov.uk"]
    places = ["Warrenpoint"]
    assert linkclean.is_allowed_domain("https://newry.ie/x", allowed, suffixes, places)
    assert linkclean.is_allowed_domain("https://economy-ni.gov.uk/x", allowed, suffixes, places)
    assert linkclean.is_allowed_domain("https://warrenpointport.com/x", allowed, suffixes, places)
    assert not linkclean.is_allowed_domain("https://example.com/x", allowed, suffixes, places)


# --- mobile subdomain stripping ------------------------------------------

def test_strip_mobile_subdomain_rewrites_m_prefix():
    assert (
        linkclean.strip_mobile_subdomain("http://m.belfasttelegraph.co.uk/news/x")
        == "http://belfasttelegraph.co.uk/news/x"
    )


def test_strip_mobile_subdomain_leaves_other_urls_untouched():
    assert (
        linkclean.strip_mobile_subdomain("https://www.belfasttelegraph.co.uk/news/x")
        == "https://www.belfasttelegraph.co.uk/news/x"
    )
    assert linkclean.strip_mobile_subdomain("https://example.com/x") == "https://example.com/x"


def test_clean_url_strips_mobile_subdomain():
    assert linkclean.clean_url("http://m.belfasttelegraph.co.uk/news/x") == (
        "http://belfasttelegraph.co.uk/news/x"
    )


def test_domain_of_strips_mobile_prefix():
    assert linkclean.domain_of("http://m.belfasttelegraph.co.uk/x") == "belfasttelegraph.co.uk"


def test_irishnews_is_excluded_by_default_config():
    assert linkclean.is_excluded_domain("https://www.irishnews.com/news/x", config.EXCLUDE_DOMAINS)


def test_clean_url_then_domain_check_catches_irish_news_wrapped_in_google_news_link():
    # The real-world case this exists for: Google News surfaces an Irish
    # News story, wrapped in a news.google.com link that doesn't show the
    # real domain until decoded.
    resolved = linkclean.clean_url(REAL_GNEWS_URL)
    assert linkclean.is_excluded_domain(resolved, config.EXCLUDE_DOMAINS)


# --- GoogleNewsBrowserResolver (Tier 3) -----------------------------------

def _fake_playwright_module(page):
    """A fake playwright.sync_api module whose sync_playwright().start()
    chain hands back the given fake page -- mirrors the real
    sync_playwright()/chromium.launch()/new_page() call chain closely
    enough for GoogleNewsBrowserResolver's own code to drive it."""
    fake_browser = MagicMock()
    fake_browser.new_page.return_value = page
    fake_pw = MagicMock()
    fake_pw.chromium.launch.return_value = fake_browser
    fake_sync_playwright_cm = MagicMock()
    fake_sync_playwright_cm.start.return_value = fake_pw
    module = MagicMock()
    module.sync_playwright.return_value = fake_sync_playwright_cm
    return module, fake_pw, fake_browser


def test_google_news_browser_resolver_returns_resolved_url_on_success(monkeypatch):
    page = MagicMock()
    page.url = "https://www.bbc.co.uk/news/articles/c9w4g4ypz70o"
    module, _, _ = _fake_playwright_module(page)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)

    resolver = linkclean.GoogleNewsBrowserResolver()
    try:
        result = resolver.resolve("https://news.google.com/rss/articles/abc123")
    finally:
        resolver.close()

    assert result == "https://www.bbc.co.uk/news/articles/c9w4g4ypz70o"
    page.goto.assert_called_once()


def test_google_news_browser_resolver_clicks_reject_all(monkeypatch):
    page = MagicMock()
    page.url = "https://example.com/real-article"
    module, _, _ = _fake_playwright_module(page)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)

    resolver = linkclean.GoogleNewsBrowserResolver()
    try:
        resolver.resolve("https://news.google.com/rss/articles/abc123")
    finally:
        resolver.close()

    page.get_by_role.assert_called_once_with("button", name="Reject all")


def test_google_news_browser_resolver_tolerates_missing_consent_wall(monkeypatch):
    # A browser context that already consented earlier in the same run
    # (or Google just not showing the wall this time) means the "Reject
    # all" button never appears -- .click() raising must not abort the
    # whole resolve.
    page = MagicMock()
    page.url = "https://example.com/real-article"
    page.get_by_role.return_value.first.click.side_effect = Exception("no such element")
    module, _, _ = _fake_playwright_module(page)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)

    resolver = linkclean.GoogleNewsBrowserResolver()
    try:
        result = resolver.resolve("https://news.google.com/rss/articles/abc123")
    finally:
        resolver.close()

    assert result == "https://example.com/real-article"


def test_google_news_browser_resolver_returns_none_if_still_on_google(monkeypatch):
    # Navigation "succeeded" but never actually left Google (consent flow
    # changed again, or a genuine dead end) -- must not be reported as a
    # real resolution.
    page = MagicMock()
    page.url = "https://news.google.com/rss/articles/abc123"
    module, _, _ = _fake_playwright_module(page)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)

    resolver = linkclean.GoogleNewsBrowserResolver()
    try:
        result = resolver.resolve("https://news.google.com/rss/articles/abc123")
    finally:
        resolver.close()

    assert result is None


def test_google_news_browser_resolver_returns_none_on_consent_page_stuck(monkeypatch):
    page = MagicMock()
    page.url = "https://consent.google.com/ml?continue=..."
    module, _, _ = _fake_playwright_module(page)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)

    resolver = linkclean.GoogleNewsBrowserResolver()
    try:
        result = resolver.resolve("https://news.google.com/rss/articles/abc123")
    finally:
        resolver.close()

    assert result is None


def test_google_news_browser_resolver_returns_none_on_navigation_error(monkeypatch):
    page = MagicMock()
    page.goto.side_effect = Exception("net::ERR_TIMED_OUT")
    module, _, _ = _fake_playwright_module(page)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)

    resolver = linkclean.GoogleNewsBrowserResolver()
    try:
        result = resolver.resolve("https://news.google.com/rss/articles/abc123")
    finally:
        resolver.close()

    assert result is None


def test_google_news_browser_resolver_returns_none_when_playwright_not_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    resolver = linkclean.GoogleNewsBrowserResolver()
    try:
        result = resolver.resolve("https://news.google.com/rss/articles/abc123")
    finally:
        resolver.close()  # must not raise even though nothing ever launched

    assert result is None


def test_google_news_browser_resolver_returns_none_when_browser_fails_to_launch(monkeypatch):
    module = MagicMock()
    module.sync_playwright.side_effect = Exception("Executable doesn't exist")
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)

    resolver = linkclean.GoogleNewsBrowserResolver()
    try:
        result = resolver.resolve("https://news.google.com/rss/articles/abc123")
    finally:
        resolver.close()

    assert result is None


def test_google_news_browser_resolver_launches_browser_only_once_across_multiple_resolves(monkeypatch):
    page = MagicMock()
    page.url = "https://example.com/article"
    module, fake_pw, _ = _fake_playwright_module(page)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)

    resolver = linkclean.GoogleNewsBrowserResolver()
    try:
        resolver.resolve("https://news.google.com/rss/articles/one")
        resolver.resolve("https://news.google.com/rss/articles/two")
        resolver.resolve("https://news.google.com/rss/articles/three")
    finally:
        resolver.close()

    module.sync_playwright.assert_called_once()
    fake_pw.chromium.launch.assert_called_once()


def test_google_news_browser_resolver_does_not_retry_launch_after_failure(monkeypatch):
    module = MagicMock()
    module.sync_playwright.side_effect = Exception("Executable doesn't exist")
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)

    resolver = linkclean.GoogleNewsBrowserResolver()
    try:
        resolver.resolve("https://news.google.com/rss/articles/one")
        resolver.resolve("https://news.google.com/rss/articles/two")
    finally:
        resolver.close()

    # One failed attempt is enough to remember "unavailable" -- must not
    # keep re-attempting a launch already known to fail on every call.
    assert module.sync_playwright.call_count == 1


def test_google_news_browser_resolver_close_is_safe_when_nothing_ever_launched():
    resolver = linkclean.GoogleNewsBrowserResolver()
    resolver.close()  # must not raise
