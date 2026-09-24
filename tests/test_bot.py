import time
from types import SimpleNamespace

import bot
import config
import linkclean
import textutil


def test_is_excluded_matches_death_notice():
    assert bot.is_excluded("John Smith Death Notice - Newry", "Reposing at his home on Friday")


def test_known_local_outlets_restriction_is_enabled_only_where_it_makes_sense():
    # Re-enabled 2026-09-05 for the Google News feed specifically -- see
    # the long comment in config.py for the full history (disabled after
    # one production run dropped 0/6 genuinely on-topic stories, then
    # re-enabled once news.google.com itself was added to
    # KNOWN_LOCAL_OUTLETS so an unresolved wrapper link no longer gets
    # dropped by this check). BBC News NI and Newry.ie never needed it --
    # their own docstrings in config.py explain why -- and stay off.
    by_name = {feed["name"]: feed for feed in config.FEEDS}
    assert by_name["BBC News NI"]["restrict_to_known_local_outlets"] is False
    assert by_name["Newry.ie"]["restrict_to_known_local_outlets"] is False
    assert by_name["Google News - Newry area"]["restrict_to_known_local_outlets"] is True


def test_known_local_outlets_mechanism_still_correctly_rejects_hilltown_dundee():
    # The real incident that motivated this whole mechanism: a story from
    # Dundee (which has its own Hilltown district) passes the keyword
    # filter on "Hilltown" alone, but must still be identifiable as
    # non-local once its outlet is known -- confirmed here using the exact
    # three-argument call bot.py actually makes, including the
    # place-name-in-domain check, to make sure that broader check doesn't
    # accidentally let this exact case back in (thecourier.co.uk's
    # hostname doesn't contain "hilltown" -- only the URL path does, which
    # this check deliberately never looks at).
    feed = next(f for f in config.FEEDS if f["name"] == "Google News - Newry area")

    title = "New housing planned for Hilltown area"
    summary = ""
    dundee_url = "https://www.thecourier.co.uk/fp/news/dundee/12345/hilltown-housing/"

    haystack = (title + " " + summary).lower()
    passed_keyword_filter = any(kw.lower() in haystack for kw in feed["keyword_filter"])
    assert passed_keyword_filter  # confirms this is the exact failure mode -- keyword alone isn't enough

    assert not linkclean.is_allowed_domain(
        dundee_url, config.KNOWN_LOCAL_OUTLETS, config.KNOWN_LOCAL_OUTLET_SUFFIXES, config.CATCHMENT_PLACES
    )


def test_saoradh_is_not_in_known_local_outlets():
    # Deliberate exclusion, not an oversight -- see config.py's note on the
    # Irish political party additions. Checked against both its real
    # domain (saoradh.irish) and the .ie one it's sometimes mistakenly
    # assumed to use, so a future edit can't reintroduce it by guessing.
    assert not linkclean.is_allowed_domain("https://saoradh.irish/x", config.KNOWN_LOCAL_OUTLETS)
    assert not linkclean.is_allowed_domain("https://saoradh.ie/x", config.KNOWN_LOCAL_OUTLETS)


def test_is_excluded_matches_property_for_sale():
    assert bot.is_excluded("3-bed house for sale in Newry, guide price £180,000", "")


def test_is_excluded_matches_advertorial():
    assert bot.is_excluded("New cafe opens in Newry city centre", "Advertorial in association with Cafe Co")


def test_is_excluded_leaves_ordinary_news_alone():
    assert not bot.is_excluded(
        "Newry City slump to three-goal defeat at home to Glenavon", "Match report"
    )


def _fake_parsed(entries, bozo_exception=None):
    return SimpleNamespace(entries=entries, bozo_exception=bozo_exception)


def _struct_time_days_ago(days):
    return time.gmtime(time.time() - days * 86400)


# --- news-age limiter (_is_too_old / MAX_NEWS_AGE_DAYS) ------------------

def test_is_too_old_true_for_an_entry_older_than_the_limit():
    entry = SimpleNamespace(published_parsed=_struct_time_days_ago(config.MAX_NEWS_AGE_DAYS + 1))
    assert bot._is_too_old(entry)


def test_is_too_old_false_for_a_recent_entry():
    entry = SimpleNamespace(published_parsed=_struct_time_days_ago(0))
    assert not bot._is_too_old(entry)


def test_is_too_old_false_when_no_date_field_is_present():
    # Can't verify the age at all -- don't drop it over that, matching
    # this project's fail-open bias elsewhere.
    entry = SimpleNamespace()
    assert not bot._is_too_old(entry)


def test_is_too_old_falls_back_to_updated_parsed_when_published_parsed_is_missing():
    entry = SimpleNamespace(updated_parsed=_struct_time_days_ago(config.MAX_NEWS_AGE_DAYS + 1))
    assert bot._is_too_old(entry)


def test_fetch_entries_drops_an_entry_older_than_max_news_age_days(monkeypatch):
    # Regression test for a real, not just theoretical, case: newry.ie's own
    # feed has at least one entry whose pubDate is from 2022.
    monkeypatch.setattr(
        config,
        "FEEDS",
        [{"name": "Test Feed", "url": "http://example.com/rss", "keyword_filter": ["newry"]}],
    )
    entries = [
        SimpleNamespace(
            title="Newry story from 2022", link="http://example.com/1", summary="",
            published_parsed=time.gmtime(0),  # 1970 -- absurdly old, unambiguous
        ),
        SimpleNamespace(
            title="Newry story from today", link="http://example.com/2", summary="",
            published_parsed=_struct_time_days_ago(0),
        ),
    ]
    monkeypatch.setattr(bot.feedparser, "parse", lambda url: _fake_parsed(entries))

    results = [title for title, *_ in bot.fetch_entries()]

    assert results == ["Newry story from today"]


# --- max_entries (per-feed alternative to the age check) -----------------

def test_fetch_entries_max_entries_takes_only_the_first_n_in_feed_order(monkeypatch):
    monkeypatch.setattr(
        config,
        "FEEDS",
        [{"name": "Test Feed", "url": "http://example.com/rss", "keyword_filter": ["newry"], "max_entries": 2}],
    )
    entries = [
        SimpleNamespace(title=f"Newry story {i}", link=f"http://example.com/{i}", summary="")
        for i in range(5)
    ]
    monkeypatch.setattr(bot.feedparser, "parse", lambda url: _fake_parsed(entries))

    results = [title for title, *_ in bot.fetch_entries()]

    assert results == ["Newry story 0", "Newry story 1"]


def test_fetch_entries_max_entries_skips_the_age_check_entirely(monkeypatch):
    # The whole point of max_entries: for a feed whose own pubDate is
    # unreliable, an absurdly old date on an entry that's still within the
    # first N by feed position must NOT get it dropped -- that would just
    # reproduce the exact 0-survivors problem max_entries exists to fix
    # (confirmed live for Newry.ie and Newry Democrat).
    monkeypatch.setattr(
        config,
        "FEEDS",
        [{"name": "Test Feed", "url": "http://example.com/rss", "keyword_filter": ["newry"], "max_entries": 3}],
    )
    entries = [
        SimpleNamespace(
            title="Newry story with an absurdly old (unreliable) pubDate",
            link="http://example.com/1", summary="",
            published_parsed=time.gmtime(0),  # 1970
        ),
    ]
    monkeypatch.setattr(bot.feedparser, "parse", lambda url: _fake_parsed(entries))

    results = [title for title, *_ in bot.fetch_entries()]

    assert results == ["Newry story with an absurdly old (unreliable) pubDate"]


def test_fetch_entries_applies_keyword_filter(monkeypatch):
    monkeypatch.setattr(
        config,
        "FEEDS",
        [{"name": "Test Feed", "url": "http://example.com/rss", "keyword_filter": ["newry"]}],
    )
    entries = [
        SimpleNamespace(title="Newry council meets today", link="http://example.com/1", summary=""),
        SimpleNamespace(title="Unrelated national story", link="http://example.com/2", summary=""),
    ]
    monkeypatch.setattr(bot.feedparser, "parse", lambda url: _fake_parsed(entries))

    results = list(bot.fetch_entries())

    assert len(results) == 1
    assert results[0][0] == "Newry council meets today"


def test_fetch_entries_keyword_filter_matches_any_catchment_place(monkeypatch):
    # A story doesn't need to mention "Newry" itself -- any catchment-area
    # place name should be enough to pass the filter.
    monkeypatch.setattr(
        config,
        "FEEDS",
        [{"name": "BBC News NI", "url": "http://example.com/rss", "keyword_filter": config.CATCHMENT_PLACES}],
    )
    entries = [
        SimpleNamespace(title="New footbridge planned for Rostrevor", link="http://example.com/1", summary=""),
        SimpleNamespace(title="Crossmaglen GAA club hosts open day", link="http://example.com/2", summary=""),
        SimpleNamespace(title="Dundalk unveils new bus route", link="http://example.com/3", summary=""),
    ]
    monkeypatch.setattr(bot.feedparser, "parse", lambda url: _fake_parsed(entries))

    results = [title for title, *_ in bot.fetch_entries()]

    assert "New footbridge planned for Rostrevor" in results
    assert "Crossmaglen GAA club hosts open day" in results
    # Dundalk is deliberately not in the catchment list -- a Dundalk-only
    # story with no other catchment place mentioned should be filtered out.
    assert "Dundalk unveils new bus route" not in results


def test_fetch_entries_filters_relevance_even_for_google_news_feed(monkeypatch):
    # Regression test for a real incident (2026-09-04): with
    # keyword_filter=None on the Google News feed, a completely unrelated
    # crime story from Dundee, Scotland got posted, because nothing
    # independently checked relevance -- the code just trusted Google's
    # search results. The Google News feed must filter just like BBC does.
    monkeypatch.setattr(
        config,
        "FEEDS",
        [{"name": "Google News - Newry area", "url": "http://example.com/rss", "keyword_filter": config.CATCHMENT_PLACES}],
    )
    entries = [
        SimpleNamespace(
            title="Police investigate rape at Dundee multi - The Courier",
            link="http://example.com/1",
            summary="",
            source=SimpleNamespace(title="The Courier"),
        ),
        SimpleNamespace(
            title="Council approves new play park - Newry Times",
            link="http://example.com/2",
            summary="",
            source=SimpleNamespace(title="Newry Times"),
        ),
    ]
    monkeypatch.setattr(bot.feedparser, "parse", lambda url: _fake_parsed(entries))

    results = [title for title, *_ in bot.fetch_entries()]

    assert results == ["Council approves new play park"]


def test_fetch_entries_keyword_filter_checks_raw_title_before_stripping(monkeypatch):
    # A story can be genuinely local with no catchment place in the
    # headline text itself -- only in the outlet name that gets stripped
    # off afterwards (e.g. "... - Newry.ie"). Filtering on the
    # already-stripped title would wrongly drop it.
    monkeypatch.setattr(
        config,
        "FEEDS",
        [{"name": "Google News - Newry area", "url": "http://example.com/rss", "keyword_filter": config.CATCHMENT_PLACES}],
    )
    entry = SimpleNamespace(
        title="'Team Mullen' to compete in Great North Run in memory of their late mother Anne - Newry.ie",
        link="http://example.com/1",
        summary="",
        source=SimpleNamespace(title="Newry.ie"),
    )
    monkeypatch.setattr(bot.feedparser, "parse", lambda url: _fake_parsed([entry]))

    results = list(bot.fetch_entries())

    assert len(results) == 1
    assert results[0][0] == "'Team Mullen' to compete in Great North Run in memory of their late mother Anne"


def test_fetch_entries_strips_source_title_suffix(monkeypatch):
    monkeypatch.setattr(
        config,
        "FEEDS",
        [{"name": "Google News - Newry", "url": "http://example.com/rss", "keyword_filter": None}],
    )
    entry = SimpleNamespace(
        title="New bins scheme starts Monday - Newry Times",
        link="http://example.com/1",
        summary="",
        source=SimpleNamespace(title="Newry Times"),
    )
    monkeypatch.setattr(bot.feedparser, "parse", lambda url: _fake_parsed([entry]))

    (title, link, summary, source_name), = list(bot.fetch_entries())

    assert title == "New bins scheme starts Monday"


def test_fetch_entries_falls_back_to_generic_vendor_stripper_without_source(monkeypatch):
    monkeypatch.setattr(
        config,
        "FEEDS",
        [{"name": "BBC News NI", "url": "http://example.com/rss", "keyword_filter": None}],
    )
    entry = SimpleNamespace(
        title="Seamus Mallon event | Newry News",
        link="http://example.com/1",
        summary="",
    )
    monkeypatch.setattr(bot.feedparser, "parse", lambda url: _fake_parsed([entry]))

    (title, link, summary, source_name), = list(bot.fetch_entries())

    assert title == "Seamus Mallon event"


def test_fetch_entries_skips_entries_missing_title_or_link(monkeypatch):
    monkeypatch.setattr(
        config,
        "FEEDS",
        [{"name": "Test Feed", "url": "http://example.com/rss", "keyword_filter": None}],
    )
    entries = [
        SimpleNamespace(title="", link="http://example.com/1", summary=""),
        SimpleNamespace(title="Has no link", link="", summary=""),
        SimpleNamespace(title="Valid entry", link="http://example.com/3", summary=""),
    ]
    monkeypatch.setattr(bot.feedparser, "parse", lambda url: _fake_parsed(entries))

    results = list(bot.fetch_entries())

    assert len(results) == 1
    assert results[0][0] == "Valid entry"


def test_fetch_entries_handles_empty_feed_gracefully(monkeypatch):
    monkeypatch.setattr(
        config,
        "FEEDS",
        [{"name": "Test Feed", "url": "http://example.com/rss", "keyword_filter": None}],
    )
    monkeypatch.setattr(bot.feedparser, "parse", lambda url: _fake_parsed([]))

    assert list(bot.fetch_entries()) == []


# --- Newry.ie link substitution for unresolved Google News links --------
#
# Stopgap while Tier 3 (the headless-browser resolver) can't run on the
# current EC2 box -- see config.NEWRY_IE_LINK_MATCH_THRESHOLD.

# --- parsing newry.ie's homepage HTML (replaces its stale RSS feed, see
# config.py's Newry.ie FEEDS entry for the full incident) -----------------

def _raxo_article_html(slug, title, heading="h4"):
    return f'<{heading} class="raxo-title"><a href="/articles/news/{slug}">{title}</a></{heading}>'


def test_parse_newry_ie_homepage_articles_extracts_title_and_link_in_order():
    html_text = (
        _raxo_article_html("first-story", "First Story", heading="h3")
        + _raxo_article_html("second-story", "Second Story")
        + _raxo_article_html("third-story", "Third Story")
    )

    articles = bot._parse_newry_ie_homepage_articles(html_text)

    assert articles == [
        ("First Story", "https://www.newry.ie/articles/news/first-story"),
        ("Second Story", "https://www.newry.ie/articles/news/second-story"),
        ("Third Story", "https://www.newry.ie/articles/news/third-story"),
    ]  # h3 (featured/hero) and h4 (normal list item) both recognised


def test_parse_newry_ie_homepage_articles_dedupes_by_link_keeping_first_occurrence():
    # Real newry.ie pages repeat the same headline in a per-category
    # sidebar widget further down the page -- only the first (top of page,
    # most-recent-first) occurrence should survive.
    html_text = (
        _raxo_article_html("story-a", "Story A")
        + _raxo_article_html("story-b", "Story B")
        + _raxo_article_html("story-a", "Story A")  # repeated in a sidebar widget
    )

    articles = bot._parse_newry_ie_homepage_articles(html_text)

    assert articles == [
        ("Story A", "https://www.newry.ie/articles/news/story-a"),
        ("Story B", "https://www.newry.ie/articles/news/story-b"),
    ]


def test_parse_newry_ie_homepage_articles_unescapes_html_entities_in_titles():
    html_text = _raxo_article_html("cf-research", "&#163;20,000 raised for CF research &amp; more")

    articles = bot._parse_newry_ie_homepage_articles(html_text)

    assert articles == [("£20,000 raised for CF research & more", "https://www.newry.ie/articles/news/cf-research")]


def test_parse_newry_ie_homepage_articles_returns_empty_for_unrecognised_markup():
    assert bot._parse_newry_ie_homepage_articles("<html><body>no articles here</body></html>") == []


def test_scrape_newry_ie_homepage_returns_entries_from_a_successful_fetch(monkeypatch):
    html_text = _raxo_article_html("a-story", "A Story")
    monkeypatch.setattr(
        bot.requests, "get", lambda url, timeout: SimpleNamespace(
            text=html_text, raise_for_status=lambda: None
        )
    )

    parsed = bot._scrape_newry_ie_homepage()

    assert len(parsed.entries) == 1
    assert parsed.entries[0].title == "A Story"
    assert parsed.entries[0].link == "https://www.newry.ie/articles/news/a-story"


def test_scrape_newry_ie_homepage_fails_open_on_request_exception(monkeypatch):
    def _raise(url, timeout):
        raise bot.requests.RequestException("network is down")

    monkeypatch.setattr(bot.requests, "get", _raise)

    parsed = bot._scrape_newry_ie_homepage()

    assert parsed.entries == []  # doesn't raise


# --- fetch_newry_ie_candidates (uses the scrape above, not feedparser) --

def test_fetch_newry_ie_candidates_returns_link_and_normalized_title(monkeypatch):
    monkeypatch.setattr(config, "FEEDS", [{"name": "Newry.ie", "scrape_homepage": True, "max_entries": 3}])
    entries = [
        SimpleNamespace(title="Council approves new play park - Newry.ie", link="https://newry.ie/1"),
        SimpleNamespace(title="Second story", link="https://newry.ie/2"),
        SimpleNamespace(title="Third story", link="https://newry.ie/3"),
        SimpleNamespace(title="Fourth story (past max_entries)", link="https://newry.ie/4"),
    ]
    monkeypatch.setattr(bot, "_scrape_newry_ie_homepage", lambda: SimpleNamespace(entries=entries))

    candidates = bot.fetch_newry_ie_candidates()

    assert candidates == [
        ("https://newry.ie/1", bot.textutil.normalize("Council approves new play park")),
        ("https://newry.ie/2", bot.textutil.normalize("Second story")),
        ("https://newry.ie/3", bot.textutil.normalize("Third story")),
    ]  # respects max_entries, and strips the vendor suffix like fetch_entries does


def test_fetch_newry_ie_candidates_returns_empty_when_feed_not_configured(monkeypatch):
    monkeypatch.setattr(config, "FEEDS", [{"name": "BBC News NI", "url": "http://example.com/rss"}])
    assert bot.fetch_newry_ie_candidates() == []


def test_fetch_newry_ie_candidates_reflects_scrape_failing_open(monkeypatch):
    monkeypatch.setattr(config, "FEEDS", [{"name": "Newry.ie", "scrape_homepage": True}])
    monkeypatch.setattr(bot, "_scrape_newry_ie_homepage", lambda: SimpleNamespace(entries=[]))

    assert bot.fetch_newry_ie_candidates() == []  # doesn't raise


def test_fetch_newry_ie_candidates_skips_entries_missing_title_or_link(monkeypatch):
    monkeypatch.setattr(config, "FEEDS", [{"name": "Newry.ie", "scrape_homepage": True}])
    entries = [
        SimpleNamespace(title="", link="https://newry.ie/1"),
        SimpleNamespace(title="Has no link", link=""),
        SimpleNamespace(title="Valid entry", link="https://newry.ie/3"),
    ]
    monkeypatch.setattr(bot, "_scrape_newry_ie_homepage", lambda: SimpleNamespace(entries=entries))

    candidates = bot.fetch_newry_ie_candidates()

    assert len(candidates) == 1
    assert candidates[0][0] == "https://newry.ie/3"


def test_find_newry_ie_substitute_matches_a_close_headline():
    candidates = [
        ("https://newry.ie/1", bot.textutil.normalize("Newry's new £18.6m park is approved")),
        ("https://newry.ie/2", bot.textutil.normalize("Unrelated story about roadworks")),
    ]
    target = bot.textutil.normalize(
        "'Wonderful day' for Newry as £18.6m Albert Basin city park plans approved"
    )

    match = bot.find_newry_ie_substitute(target, candidates)

    assert match is not None
    assert match[0] == "https://newry.ie/1"


def test_find_newry_ie_substitute_is_stricter_than_the_general_dedup_threshold():
    # Two headlines similar enough to count as duplicates under
    # SIMILARITY_THRESHOLD, but not similar enough to trust for a URL
    # substitution -- config.NEWRY_IE_LINK_MATCH_THRESHOLD is deliberately
    # tighter, since a wrong substitution silently sends readers to the
    # wrong article under the original headline.
    a = textutil.normalize("SDLP credit Newry Community for making City Park vision a reality")
    b = textutil.normalize("SDLP Says Newry Community Made Newry Park A Reality")
    sim = textutil.similarity(a, b)
    assert config.SIMILARITY_THRESHOLD <= sim < config.NEWRY_IE_LINK_MATCH_THRESHOLD  # sanity-check the fixture

    assert textutil.is_duplicate_story(a, [b], config.SIMILARITY_THRESHOLD)
    assert bot.find_newry_ie_substitute(a, [("https://newry.ie/1", b)]) is None


def test_find_newry_ie_substitute_returns_none_when_nothing_matches():
    candidates = [("https://newry.ie/1", bot.textutil.normalize("Completely unrelated story"))]
    target = bot.textutil.normalize("Council approves new play park in Warrenpoint")

    assert bot.find_newry_ie_substitute(target, candidates) is None


def test_fetch_entries_handles_feed_fetch_exception(monkeypatch):
    monkeypatch.setattr(
        config,
        "FEEDS",
        [{"name": "Test Feed", "url": "http://example.com/rss", "keyword_filter": None}],
    )

    def _raise(url):
        raise ConnectionError("network is down")

    monkeypatch.setattr(bot.feedparser, "parse", _raise)

    assert list(bot.fetch_entries()) == []  # logged and skipped, not raised


def test_fetch_entries_routes_a_scrape_homepage_feed_through_the_scraper_not_feedparser(monkeypatch):
    monkeypatch.setattr(
        config,
        "FEEDS",
        [{"name": "Newry.ie", "scrape_homepage": True, "keyword_filter": ["newry"]}],
    )
    monkeypatch.setattr(
        bot, "_scrape_newry_ie_homepage",
        lambda: SimpleNamespace(entries=[SimpleNamespace(title="Newry story", link="https://newry.ie/1")]),
    )

    def _fail_if_called(url):
        raise AssertionError("scrape_homepage feeds must not go through feedparser.parse")

    monkeypatch.setattr(bot.feedparser, "parse", _fail_if_called)

    results = list(bot.fetch_entries())

    assert results == [("Newry story", "https://newry.ie/1", "", "Newry.ie")]
