"""Fetches events from newry.ie's events RSS feed and enriches them with
details scraped from each event's own page.

The RSS feed itself only gives a title, link, and a single `pubDate` --
verified 2026-09-05 against https://www.newry.ie/events?format=feed&type=rss.
No end time, venue, or price. Those live only on the individual event page,
rendered by Joomla's com_eventbooking component, which exposes them in a
consistent `eb-event-property-label` / `eb-event-property-value` table
(confirmed against multiple real, differently-shaped events) -- not a
documented API, so this is scraping a specific CMS component's output, not
integrating against a stable contract. Treat failures here the same way as
linkclean.py's Google News decode: best-effort, fall back to partial data,
never raise.
"""

import html
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import feedparser
import requests

log = logging.getLogger("newry-bot")

_EVENT_PROPERTY = re.compile(
    r'eb-event-property-label">\s*([^<]+?)\s*</td>\s*'
    r'<td class="eb-event-property-value[^"]*">\s*(.*?)\s*</td>',
    re.DOTALL,
)
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")

# Observed format on real event pages: "05-09-2026 7:00 pm" (day-month-year,
# no zero-padded hour, lowercase am/pm). Python's strptime tolerates both
# the missing zero-pad and lowercase am/pm here despite %I/%p normally
# implying otherwise -- verified against real captured examples.
_DATE_FORMAT = "%d-%m-%Y %I:%M %p"

# Event pages give naive local times with no timezone marker -- calendar_sync.py
# already assumes these are Europe/London wall-clock time when it submits them
# to Google Calendar (its _TIMEZONE constant), so is_in_the_past uses the same
# assumption for consistency, rather than the host machine's own local time
# (which won't always be Europe/London, e.g. a UTC EC2 box).
_LOCAL_TZ = ZoneInfo("Europe/London")


def is_in_the_past(start):
    """True if a parsed event start time has already passed. None (no start
    time could be scraped at all) is treated as NOT past -- consistent with
    this project's fail-open bias elsewhere (moderation.py, calendar_sync.py)
    -- calendar_sync.sync_event already separately refuses to sync an event
    with no start time at all, so this only needs to catch the case where a
    start time exists and is simply already behind us."""
    if start is None:
        return False
    now_local = datetime.now(_LOCAL_TZ).replace(tzinfo=None)
    return start < now_local


def fetch_event_feed(url):
    """Yields (title, link) for each entry in newry.ie's events RSS feed."""
    parsed = feedparser.parse(url)
    if not parsed.entries:
        reason = getattr(parsed, "bozo_exception", None) or "empty response"
        log.warning("Events feed returned no entries (%s)", reason)
        return

    for entry in parsed.entries:
        title = getattr(entry, "title", "").strip()
        link = getattr(entry, "link", "").strip()
        if title and link:
            yield title, link


def _clean_text(html_fragment):
    # Strip tags before unescaping entities, not after: an entity like
    # &lt; would otherwise decode to a literal "<" that the tag-stripping
    # regex could then misinterpret as a real tag delimiter.
    without_tags = _TAG.sub(" ", html_fragment)
    return _WHITESPACE.sub(" ", html.unescape(without_tags)).strip()


def _parse_event_datetime(text):
    try:
        return datetime.strptime(text, _DATE_FORMAT)
    except ValueError:
        return None


def fetch_event_details(link, timeout=8):
    """Best-effort scrape of one event's own page for its start/end time,
    venue, and price -- fields the RSS feed doesn't carry. Returns a dict
    with those four keys, any of which may be None if the page couldn't be
    fetched or didn't contain that field. Never raises."""
    details = {"start": None, "end": None, "venue": None, "price": None}
    try:
        response = requests.get(link, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException:
        log.warning("Could not fetch event page for details: %s", link)
        return details

    for label, value in _EVENT_PROPERTY.findall(response.text):
        label = label.strip()
        value = _clean_text(value)
        if label == "Event Date":
            details["start"] = _parse_event_datetime(value)
        elif label == "Event End Date":
            details["end"] = _parse_event_datetime(value)
        elif label == "Location":
            details["venue"] = value
        elif "Price" in label and details["price"] is None:
            details["price"] = value

    return details
