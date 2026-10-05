#!/usr/bin/env python3
"""Polls configured news feeds and posts new, on-topic, non-duplicate Newry
articles to r/<config.SUBREDDIT>. Meant to be run periodically via cron --
each run is a single, self-contained pass (fetch, filter, post, exit)."""

import sys

_MIN_PYTHON = (3, 10)  # praw 8+ (requirements.txt) dropped 3.9 support
if sys.version_info < _MIN_PYTHON:
    sys.exit(
        f"newry-bot requires Python {_MIN_PYTHON[0]}.{_MIN_PYTHON[1]}+, "
        f"found {sys.version.split()[0]}. See README.md for setup."
    )

import calendar as _calendar  # noqa: E402 -- stdlib, for timegm; distinct from calendar_sync below
import html  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from urllib.parse import urljoin  # noqa: E402

import feedparser  # noqa: E402
import praw  # noqa: E402
import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

# .env must be loaded before config is imported: config reads REDDIT_SUBREDDIT
# from the environment at import time.
load_dotenv()

import calendar_sync  # noqa: E402
import config  # noqa: E402
import events  # noqa: E402
import events_widget  # noqa: E402
import exchange_rates  # noqa: E402
import fuel_prices  # noqa: E402
import heating_oil  # noqa: E402
import linkclean  # noqa: E402
import moderation  # noqa: E402
import state  # noqa: E402
import textutil  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("newry-bot")


def load_reddit():
    required = [
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
        "REDDIT_USERNAME",
        "REDDIT_PASSWORD",
        "REDDIT_USER_AGENT",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        log.error("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)
    return praw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        username=os.environ["REDDIT_USERNAME"],
        password=os.environ["REDDIT_PASSWORD"],
        user_agent=os.environ["REDDIT_USER_AGENT"],
    )


def _is_too_old(entry):
    """True if entry's feed-declared published/updated date is more than
    config.MAX_NEWS_AGE_DAYS old. A missing or unparseable date is treated
    as NOT too old -- we'd rather post something we can't verify the age
    of than silently drop it, matching this project's fail-open bias
    elsewhere (moderation.py, calendar_sync.py). This isn't just a
    theoretical guard: newry.ie's own feed has at least one entry whose
    pubDate is from 2022, for reasons unrelated to how recently it was
    actually published."""
    published = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if published is None:
        return False
    published_dt = datetime.fromtimestamp(_calendar.timegm(published), tz=timezone.utc)
    return published_dt < datetime.now(timezone.utc) - timedelta(days=config.MAX_NEWS_AGE_DAYS)


def fetch_entries():
    """Yield (title, link, summary, source_name) tuples from every
    configured feed, already filtered by each feed's keyword_filter and with
    known vendor suffixes stripped from the title."""
    for feed in config.FEEDS:
        try:
            # Newry.ie's own RSS feed is scraping's exception, not its rule
            # (see _scrape_newry_ie_homepage's docstring for why) -- every
            # other feed goes through feedparser as normal.
            if feed.get("scrape_homepage"):
                parsed = _scrape_newry_ie_homepage()
            else:
                parsed = feedparser.parse(feed["url"])
        except Exception:
            log.exception("Failed to fetch feed %s", feed["name"])
            continue

        if not parsed.entries:
            reason = getattr(parsed, "bozo_exception", None) or "empty response"
            log.warning("Feed %s returned no entries (%s)", feed["name"], reason)
            continue

        # max_entries and the date check (_is_too_old) are mutually
        # exclusive per feed: max_entries exists specifically for a feed
        # whose own pubDate is unreliable (see its comment in config.py),
        # so applying the date check as well would just reproduce the
        # exact problem it was added to fix. A feed with max_entries set
        # takes only the first N entries in feed order -- assumed
        # newest-first, the standard RSS convention, independent of
        # whether the pubDate *value* is trustworthy.
        max_entries = feed.get("max_entries")
        entries = parsed.entries[:max_entries] if max_entries else parsed.entries

        keyword = feed.get("keyword_filter")
        for entry in entries:
            title = getattr(entry, "title", "").strip()
            link = getattr(entry, "link", "").strip()
            summary = getattr(entry, "summary", "")
            if not title or not link:
                continue

            if max_entries is None and _is_too_old(entry):
                continue

            # Check relevance against the title as the feed gave it to us --
            # BEFORE stripping the outlet suffix. A human-interest story can
            # be genuinely local with no catchment place in the headline
            # itself, signalled only by a local outlet name in the suffix
            # (e.g. "... - Newry.ie"); stripping first and filtering after
            # would silently drop stories exactly like that.
            if keyword:
                haystack = (title + " " + summary).lower()
                if not any(kw.lower() in haystack for kw in keyword):
                    continue

            # Prefer the feed-supplied source name when we have it (Google
            # News gives us the exact outlet via entry.source.title, more
            # precise than guessing) then fall back to the generic
            # ' - Outlet' / ' | Outlet' stripper for anything left over.
            source = getattr(entry, "source", None)
            source_title = getattr(source, "title", None) if source else None
            if source_title and title.endswith(source_title):
                title = title[: -len(source_title)].rstrip(" -–—").strip()
            title = linkclean.strip_vendor_suffix(title)

            yield title, link, summary, feed["name"]


# Matches newry.ie's own article-headline links, e.g.:
#   <h3 class="raxo-title"><a href="/articles/news/some-slug">Some Title</a></h3>
# for a featured/hero item, or the same with <h4> for a normal list item --
# both variants seen live, confirmed 2026-09-24. Title text is taken as-is
# (may still contain HTML entities like &amp; or &hellip; -- unescaped by
# the caller) since a headline itself is never expected to contain nested
# markup.
_NEWRY_IE_ARTICLE_LINK = re.compile(r'<h[34] class="raxo-title"><a href="([^"]+)">(.*?)</a></h[34]>')


def _parse_newry_ie_homepage_articles(html_text):
    """(title, link) pairs for every article linked from newry.ie's
    homepage HTML, in the page's own order (confirmed newest-first, same as
    the dates newry.ie displays next to each one), deduplicated by link --
    the homepage repeats the same headline in more than one section (the
    main river of stories, then again in per-category sidebar widgets
    further down the page), and only the first, top-of-page occurrence
    should count. A pure function, deliberately, so it's testable without
    a real HTTP fetch."""
    seen_links = set()
    articles = []
    for href, raw_title in _NEWRY_IE_ARTICLE_LINK.findall(html_text):
        link = urljoin("https://www.newry.ie/", href)
        if link in seen_links:
            continue
        seen_links.add(link)
        title = html.unescape(raw_title).strip()
        if title:
            articles.append((title, link))
    return articles


def _scrape_newry_ie_homepage():
    """A feedparser-shaped object (an .entries list of title/link pairs) --
    scraped from newry.ie's own homepage HTML, standing in for that feed's
    RSS URL (https://www.newry.ie/?format=feed&type=rss), which was
    confirmed 2026-09-24 to have stopped updating around 2026-09-05: it
    returned the same 6 stale entries (dated 2022-2026, out of publish
    order) for nearly three weeks running, while the live site kept
    publishing new articles daily -- including one (the Evora Hospice
    "postcode lottery" story) that a reader confirmed seeing on newry.ie
    the same day it went unmatched by bot.find_newry_ie_substitute, because
    it was never in the feed's candidate list to begin with. See
    ai-instructions.md for the full incident.

    Fails open (logs, returns an empty .entries list) on any fetch error --
    same contract as feedparser.parse itself, which never raises on a
    network failure either; fetch_entries()'s per-feed try/except still
    wraps this call as a second line of defence."""
    try:
        response = requests.get("https://www.newry.ie/", timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        log.warning("Could not fetch newry.ie's homepage")
        return SimpleNamespace(entries=[], bozo_exception="fetch failed")

    articles = _parse_newry_ie_homepage_articles(response.text)
    entries = [SimpleNamespace(title=title, link=link) for title, link in articles]
    return SimpleNamespace(entries=entries, bozo_exception=None)


def fetch_newry_ie_candidates():
    """(link, normalized_title) pairs for newry.ie's own most recent
    articles -- a direct, always-resolvable link source used to substitute
    for a Google News link that headline-matches the same story (see
    config.NEWRY_IE_LINK_MATCH_THRESHOLD). Scrapes newry.ie's homepage
    independently of fetch_entries() so it isn't affected by FEEDS order or
    that feed's own keyword_filter -- a Newry.ie entry that got filtered out
    there (e.g. no catchment place mentioned in its own title/summary) can
    still be exactly what a Google News entry, which passed its own
    relevance check with different wording, is describing.

    Fails open (logs, returns []) on any error -- this is a nice-to-have
    substitution, never something that should block a run."""
    feed = next((f for f in config.FEEDS if f["name"] == "Newry.ie"), None)
    if feed is None:
        return []

    parsed = _scrape_newry_ie_homepage()
    max_entries = feed.get("max_entries")
    entries = parsed.entries[:max_entries] if max_entries else parsed.entries

    candidates = []
    for entry in entries:
        title = entry.title.strip()
        link = entry.link.strip()
        if not title or not link:
            continue
        candidates.append((link, textutil.normalize(linkclean.strip_vendor_suffix(title))))
    return candidates


def is_excluded(title, summary):
    haystack = (title + " " + summary).lower()
    return any(kw in haystack for kw in config.EXCLUDE_KEYWORDS)


def find_newry_ie_substitute(normalized_title, newry_ie_candidates):
    """(link, score) of the Newry.ie candidate whose headline is a close
    enough match for normalized_title to stand in for an unresolved Google
    News link, or None. See config.NEWRY_IE_LINK_MATCH_THRESHOLD."""
    return textutil.best_duplicate_match(
        normalized_title, newry_ie_candidates, config.NEWRY_IE_LINK_MATCH_THRESHOLD
    )


def main():
    reddit = load_reddit()
    conn = state.connect()
    state.prune_old(conn, config.DEDUP_WINDOW_DAYS)

    approved = moderation.approve_own_spam_posts(reddit, config.SUBREDDIT)
    if approved:
        log.info("Approved %d of the bot's own post(s) caught by the spam filter", approved)

    recent_titles = state.recent_titles(conn, config.DEDUP_WINDOW_DAYS)
    live_titles, live_urls, live_submissions = moderation.fetch_recent_posts(reddit, config.SUBREDDIT)
    # (submission, normalized_title) for every recent live post, so a fuzzy
    # headline match can be traced back to the specific post to comment on.
    live_submission_titles = [(sub, textutil.normalize(sub.title)) for sub in live_submissions]
    spam_urls = moderation.fetch_spam_urls(reddit, config.SUBREDDIT)
    feed_by_name = {feed["name"]: feed for feed in config.FEEDS}
    # Stopgap for Tier 3 (browser resolve) being unavailable on the current
    # EC2 box -- see config.NEWRY_IE_LINK_MATCH_THRESHOLD.
    newry_ie_candidates = fetch_newry_ie_candidates()

    posts_made = 0
    seen_urls_this_run = set()
    # Tier 3 (a real headless browser) is the expensive Google News resolve
    # -- lazily launched on first actual use, so a run that never reaches
    # it (Tiers 1/2 already resolved everything, or nothing survives to the
    # posting step) pays nothing. Only ever invoked below for an entry
    # that's passed every other check and is genuinely about to be posted,
    # bounding it to at most MAX_POSTS_PER_RUN browser resolves per run.
    browser_resolver = linkclean.GoogleNewsBrowserResolver()

    try:
        for title, link, summary, source_name in fetch_entries():
            if posts_made >= config.MAX_POSTS_PER_RUN:
                log.info("Reached MAX_POSTS_PER_RUN (%d), stopping", config.MAX_POSTS_PER_RUN)
                break

            if is_excluded(title, summary):
                log.info("Skipping (excluded keyword) [%s] %s", source_name, title)
                continue

            # Title-only checks first, deliberately, before the potentially
            # expensive URL resolve below (Google News links can cost two
            # extra HTTP requests to resolve) -- an article that's an exact
            # live-title match or a fuzzy duplicate of something already
            # posted gets rejected without ever paying for that resolve.
            if title in live_titles:
                log.info("Skipping (already live on r/%s) [%s] %s", config.SUBREDDIT, source_name, title)
                continue

            normalized = textutil.normalize(title)
            # A fuzzy headline match against a post that's still live on the
            # subreddit: probably the same story from another outlet (e.g.
            # two different write-ups of the same train disruption on the
            # same day). We can't be sure it's a duplicate -- different
            # outlets, different wording -- so rather than silently dropping
            # it, link it as a comment on that post further down (once the
            # URL is resolved and has passed the domain checks). dup_of is
            # (submission, score) or None.
            dup_of = textutil.best_duplicate_match(
                normalized, live_submission_titles, config.SIMILARITY_THRESHOLD
            )
            if dup_of is None and textutil.is_duplicate_story(
                normalized, recent_titles, config.SIMILARITY_THRESHOLD
            ):
                # Matches something in local state but nothing still live to
                # comment on -- keep the original silent-skip behaviour.
                log.info("Skipping (duplicate story) [%s] %s", source_name, title)
                continue

            # A Google News link: check for a matching Newry.ie article
            # BEFORE spending any effort resolving the wrapper link itself
            # -- a lot of what this feed surfaces already ran on Newry.ie
            # too, under its own real, always-working link, so there's no
            # reason to resolve Google's version at all once we know that.
            # Only once there's no confident match (stricter threshold than
            # the general fuzzy-dedup one -- a wrong substitution is worse
            # than a missed one; see config.NEWRY_IE_LINK_MATCH_THRESHOLD)
            # do we fall back to actually resolving the Google News link.
            resolved_url = None
            if "news.google.com" in link and newry_ie_candidates:
                substitute = find_newry_ie_substitute(normalized, newry_ie_candidates)
                if substitute:
                    newry_ie_link, score = substitute
                    log.info(
                        "Using Newry.ie's link for a Google News story with a matching "
                        "headline (%.2f similar) [%s] %s -> %s",
                        score, source_name, title, newry_ie_link,
                    )
                    resolved_url = newry_ie_link

            if resolved_url is None:
                resolved_url = linkclean.clean_url(link)
                if "news.google.com" in resolved_url:
                    log.warning("Could not resolve Google News link to a real URL (Tiers 1/2), posting wrapper link unless Tier 3 rescues it: %s", link)

            if linkclean.is_excluded_domain(resolved_url, config.EXCLUDE_DOMAINS):
                log.info("Skipping (excluded domain) [%s] %s -> %s", source_name, title, resolved_url)
                continue

            # A keyword match on a catchment place name isn't proof of
            # location -- see config.KNOWN_LOCAL_OUTLETS for why (Hilltown,
            # Co. Down vs. Hilltown, Dundee, an actual production
            # incident). Only feeds flagged for it get this extra check; an
            # unresolved wrapper link (still showing news.google.com) can't
            # be verified either way and is treated as not allowed --
            # unless Tier 3 below manages to resolve it after all.
            restrict_to_known = feed_by_name.get(source_name, {}).get("restrict_to_known_local_outlets")
            if restrict_to_known and not linkclean.is_allowed_domain(
                resolved_url, config.KNOWN_LOCAL_OUTLETS, config.KNOWN_LOCAL_OUTLET_SUFFIXES, config.CATCHMENT_PLACES
            ):
                log.info("Skipping (not a recognised local outlet) [%s] %s -> %s", source_name, title, resolved_url)
                continue

            if resolved_url in seen_urls_this_run or state.url_already_posted(conn, resolved_url):
                log.debug("Skipping (already posted, exact URL) %s", resolved_url)
                continue

            # Live-subreddit URL check: catches posts made outside this bot
            # (a human moderator, a different bot instance) that local
            # state can't see. The title-based half of this check already
            # ran above.
            if link in live_urls or resolved_url in live_urls:
                log.info("Skipping (already live on r/%s) [%s] %s", config.SUBREDDIT, source_name, title)
                continue

            if resolved_url in spam_urls or link in spam_urls:
                log.info("Skipping (in mod spam queue) [%s] %s", source_name, title)
                continue

            # Everything above has confirmed this entry is a genuine,
            # otherwise-ready-to-post candidate -- worth Tier 3's expense
            # if it's still an unresolved wrapper link. A resolve here can
            # still reject the post: an outlet that Tiers 1/2 couldn't
            # reveal (so slipped past the domain-exclusion and
            # known-outlet checks above) gets caught now instead of posted.
            if "news.google.com" in resolved_url:
                browser_resolved = browser_resolver.resolve(resolved_url)
                if browser_resolved:
                    if linkclean.is_excluded_domain(browser_resolved, config.EXCLUDE_DOMAINS):
                        log.info("Skipping (excluded domain, found via Tier 3) [%s] %s -> %s", source_name, title, browser_resolved)
                        continue
                    if restrict_to_known and not linkclean.is_allowed_domain(
                        browser_resolved, config.KNOWN_LOCAL_OUTLETS, config.KNOWN_LOCAL_OUTLET_SUFFIXES, config.CATCHMENT_PLACES
                    ):
                        log.info("Skipping (not a recognised local outlet, found via Tier 3) [%s] %s -> %s", source_name, title, browser_resolved)
                        continue
                    resolved_url = linkclean.strip_mobile_subdomain(browser_resolved)
                    log.info("Resolved via Tier 3 (browser): %s -> %s", link, resolved_url)

            # Probable same-story repost from another outlet (see dup_of
            # above): add it as a comment on the existing post instead of
            # making a second front-page submission. Only reached once the
            # link has resolved to a real URL and cleared the domain checks
            # -- we won't comment an unresolved wrapper link or a
            # non-local/excluded one. Record it as posted either way so it
            # doesn't get re-evaluated (and re-commented) next run.
            if dup_of is not None:
                dup_submission, score = dup_of
                if "news.google.com" in resolved_url:
                    log.info(
                        "Skipping (probable duplicate of a live post, but its link never resolved) [%s] %s",
                        source_name, title,
                    )
                    continue
                if moderation.comment_with_alternate_source(dup_submission, resolved_url):
                    log.info(
                        "Commented alternate source (headline %.2f similar to %r) [%s] %s -> %s",
                        score, dup_submission.title, source_name, title, resolved_url,
                    )
                else:
                    log.info(
                        "Alternate source already linked on %r, nothing to do [%s] %s",
                        dup_submission.title, source_name, title,
                    )
                state.record_posted(conn, resolved_url, title, normalized)
                seen_urls_this_run.add(resolved_url)
                time.sleep(config.SECONDS_BETWEEN_SUBMISSIONS)
                continue

            try:
                moderation.submit_post(reddit, config.SUBREDDIT, resolved_url, title[:300])
                log.info("Posted [%s] %s -> %s", source_name, title, resolved_url)
            except Exception:
                log.exception("Failed to submit post for %s", resolved_url)
                continue

            state.record_posted(conn, resolved_url, title, normalized)
            recent_titles.append(normalized)
            seen_urls_this_run.add(resolved_url)
            live_titles.add(title)
            live_urls.add(resolved_url)
            posts_made += 1

            if posts_made < config.MAX_POSTS_PER_RUN:
                time.sleep(config.SECONDS_BETWEEN_SUBMISSIONS)
    finally:
        browser_resolver.close()

    conn.close()
    log.info("Run complete. Posted %d new article(s).", posts_made)


def run_events():
    """Fetches newry.ie's events feed and posts new ones to Reddit (Events
    flair forced, not keyword-guessed -- an events feed doesn't need
    guessing). Google Calendar sync is handled separately by
    sync_calendar() below, not here -- this function is Reddit-only, so
    "run only the calendar work" doesn't also post to Reddit as a side
    effect.

    Dedup is by exact URL, exact live title, AND fuzzy title (same
    mechanism main() uses for news, see textutil.is_duplicate_story) --
    NOT by URL alone, despite an earlier version of this docstring
    claiming a single first-party feed couldn't have the "same story,
    different link" problem that motivates fuzzy dedup for news.
    Confirmed wrong 2026-09-28: newry.ie's own events system split one
    multi-night show ("Newry Youth Performing Arts presents Dear Evan
    Hanson") into four separate event listings, one per performance date,
    sharing one identical title but four different links/IDs -- exact-URL
    dedup alone posted all four as separate Reddit threads. Also fixes a
    related bug in the same incident: live_titles/live_urls were fetched
    once before the loop and never updated as the loop posted, so even the
    exact-title check missed repeats *within* a single run (all four
    postings happened 90 seconds apart in one run) -- both live_titles and
    recent_titles are now updated immediately after each post, same as
    main()'s loop already does.

    No MAX_POSTS_PER_RUN-style cap: the feed is naturally small (~10
    items), and after the first run almost everything in it is
    already-seen and skipped instantly.

    Also keeps a second sidebar widget (config.EVENTS_WIDGET_SHORT_NAME, a
    Markdown TextArea) in sync with every currently-upcoming event in the
    feed -- unlike the posting loop above, this is NOT gated by any of
    this function's own dedup: an event already posted, or skipped here
    as a probable duplicate, still belongs in the widget if it's
    genuinely upcoming. See events_widget.py for why this can't just be
    the native Reddit Calendar widget already on the sidebar. The same
    widget also carries, below the events list: a GBP/EUR exchange rate
    (exchange_rates.py), the cheapest fresh petrol/diesel price in the
    Newry area (fuel_prices.py), and the top 3 cheapest 500L heating oil
    suppliers in NI (heating_oil.py) -- each fetched fresh every run,
    each independently omitted (not shown as a broken placeholder) if
    its own fetch fails or has nothing current to show."""
    reddit = load_reddit()
    conn = state.connect()

    approved = moderation.approve_own_spam_posts(reddit, config.SUBREDDIT)
    if approved:
        log.info("Approved %d of the bot's own post(s) caught by the spam filter", approved)

    recent_titles = state.recent_titles(conn, config.DEDUP_WINDOW_DAYS)
    live_titles, live_urls, _ = moderation.fetch_recent_posts(reddit, config.SUBREDDIT)

    feed_events = list(events.fetch_event_feed(config.EVENTS_FEED_URL))

    processed = 0
    for title, link in feed_events:
        if state.url_already_posted(conn, link):
            continue

        if link in live_urls or title in live_titles:
            log.info("Skipping event (already live on r/%s): %s", config.SUBREDDIT, title)
            continue

        normalized = textutil.normalize(title)
        if textutil.is_duplicate_story(normalized, recent_titles, config.SIMILARITY_THRESHOLD):
            log.info(
                "Skipping event (duplicate title -- likely a repeat/multi-date "
                "Newry.ie listing of something already posted): %s",
                title,
            )
            continue

        try:
            moderation.submit_post(reddit, config.SUBREDDIT, link, title[:300], force_category="Events")
            log.info("Posted event: %s -> %s", title, link)
        except Exception:
            log.exception("Failed to submit event post for %s", link)
            continue

        state.record_posted(conn, link, title, normalized)
        recent_titles.append(normalized)
        live_titles.add(title)
        live_urls.add(link)
        processed += 1
        time.sleep(config.SECONDS_BETWEEN_SUBMISSIONS)

    conn.close()

    # A feed that returned zero raw entries is a probable fetch failure,
    # not "no events" (see ai-instructions.md's stale-RSS-feed incident
    # for why this distinction matters elsewhere in this project) --
    # leave the widget showing whatever it last had rather than
    # overwriting good content with a false "no events" message.
    if feed_events:
        upcoming = []
        for title, link in feed_events:
            details = events.fetch_event_details(link)
            if details["start"] is None or events.is_in_the_past(details["start"]):
                continue
            upcoming.append((title, link, details["start"], details["end"], details["venue"], None))

        markdown = events_widget.build_events_widget_markdown(upcoming)
        for section_markdown in (
            exchange_rates.build_exchange_rate_markdown(exchange_rates.fetch_gbp_eur_rate()),
            fuel_prices.build_fuel_price_markdown(),
            heating_oil.build_heating_oil_markdown(),
        ):
            if section_markdown:
                markdown += "\n\n" + section_markdown
        events_widget.sync_events_widget(reddit, config.SUBREDDIT, markdown)
    else:
        log.warning("Events feed returned no entries -- leaving sidebar events widget untouched")

    log.info("Events run complete. Processed %d new event(s).", processed)


def sync_calendar():
    """Google Calendar sync only -- no Reddit interaction at all (doesn't
    even need Reddit credentials to run). Iterates the whole events feed
    every run and unconditionally re-syncs each one, deliberately not
    gated by state.db's "posted to Reddit" history: calendar_sync.sync_event
    upserts by a deterministic event ID derived from the link (see
    calendar_sync.event_id_for), so re-syncing an already-synced event is a
    cheap, safe update rather than a duplicate -- and this way a calendar
    sync that failed on a prior run (Calendar API hiccup, say) still gets
    retried here regardless of whether the event was already posted to
    Reddit, rather than being silently skipped forever."""
    synced = 0
    for title, link in events.fetch_event_feed(config.EVENTS_FEED_URL):
        details = events.fetch_event_details(link)

        if events.is_in_the_past(details["start"]):
            log.info("Skipping event (already started/passed): %s", title)
            continue

        if calendar_sync.sync_event(title, link, details["start"], details["end"], details["venue"]):
            synced += 1
    log.info("Calendar sync complete. Synced %d event(s).", synced)


if __name__ == "__main__":
    # Optional mode argument -- default (no argument) runs everything,
    # unchanged from before this existed, so cron and any existing
    # invocation keep working as-is. "calendar" runs only sync_calendar --
    # Google Calendar sync with no Reddit interaction at all -- for testing
    # calendar_sync.py in isolation, or for splitting it onto its own cron
    # schedule later, without touching news or Reddit posting.
    _MODE = sys.argv[1] if len(sys.argv) > 1 else "all"
    if _MODE not in ("all", "news", "calendar"):
        sys.exit(f"Usage: {sys.argv[0]} [news|calendar]  (no argument runs everything)")

    if _MODE in ("all", "news"):
        main()
    if _MODE == "all":
        run_events()
    if _MODE in ("all", "calendar"):
        sync_calendar()
