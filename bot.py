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
import logging  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

import feedparser  # noqa: E402
import praw  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

# .env must be loaded before config is imported: config reads REDDIT_SUBREDDIT
# from the environment at import time.
load_dotenv()

import calendar_sync  # noqa: E402
import config  # noqa: E402
import events  # noqa: E402
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


def fetch_newry_ie_candidates():
    """(link, normalized_title) pairs for newry.ie's own most recent
    articles -- a direct, always-resolvable link source used to substitute
    for a Google News link that headline-matches the same story (see
    config.NEWRY_IE_LINK_MATCH_THRESHOLD). Parses the Newry.ie feed
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

    try:
        parsed = feedparser.parse(feed["url"])
    except Exception:
        log.exception("Failed to fetch Newry.ie feed for Google News link substitution")
        return []

    max_entries = feed.get("max_entries")
    entries = parsed.entries[:max_entries] if max_entries else parsed.entries

    candidates = []
    for entry in entries:
        title = getattr(entry, "title", "").strip()
        link = getattr(entry, "link", "").strip()
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

            resolved_url = linkclean.clean_url(link)

            # Still a Google News wrapper link (Tiers 1/2 didn't resolve
            # it): a lot of what this feed surfaces already ran on Newry.ie
            # too, under its own real, always-working link -- if a recent
            # Newry.ie article's headline is a close match, use that link
            # instead of the wrapper (stricter threshold than the general
            # fuzzy-dedup one: a wrong substitution is worse than a missed
            # one). See config.NEWRY_IE_LINK_MATCH_THRESHOLD.
            if "news.google.com" in resolved_url and newry_ie_candidates:
                substitute = find_newry_ie_substitute(normalized, newry_ie_candidates)
                if substitute:
                    newry_ie_link, score = substitute
                    log.info(
                        "Substituting Newry.ie link for unresolved Google News article "
                        "(headline %.2f similar) [%s] %s -> %s",
                        score, source_name, title, newry_ie_link,
                    )
                    resolved_url = newry_ie_link

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
    effect. Dedup is by the event's own link only -- unlike news, a single
    first-party feed doesn't have the "same story, different outlet,
    different wording" problem that motivates fuzzy-title dedup, so
    exact-URL dedup is enough. No MAX_POSTS_PER_RUN-style cap: the feed is
    naturally small (~10 items), and after the first run almost everything
    in it is already-seen and skipped instantly."""
    reddit = load_reddit()
    conn = state.connect()

    approved = moderation.approve_own_spam_posts(reddit, config.SUBREDDIT)
    if approved:
        log.info("Approved %d of the bot's own post(s) caught by the spam filter", approved)

    live_titles, live_urls, _ = moderation.fetch_recent_posts(reddit, config.SUBREDDIT)

    processed = 0
    for title, link in events.fetch_event_feed(config.EVENTS_FEED_URL):
        if state.url_already_posted(conn, link):
            continue

        if link in live_urls or title in live_titles:
            log.info("Skipping event (already live on r/%s): %s", config.SUBREDDIT, title)
            continue

        try:
            moderation.submit_post(reddit, config.SUBREDDIT, link, title[:300], force_category="Events")
            log.info("Posted event: %s -> %s", title, link)
        except Exception:
            log.exception("Failed to submit event post for %s", link)
            continue

        state.record_posted(conn, link, title, textutil.normalize(title))
        processed += 1
        time.sleep(config.SECONDS_BETWEEN_SUBMISSIONS)

    conn.close()
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
