"""Fetches events from an externally-hosted "What's On" guide for Newry
and syncs them to the Google Calendar -- see bot.sync_calendar(). Never
posted to Reddit, per the user's explicit instruction (2026-11-09).

The source (config.NEWRY_WHATS_ON_URL) is a browser-oriented JS file --
`window.NEWRY_GUIDE = { updatedAt, scope, events: [...] }`, followed by
trailing display logic this module never needs and ignores -- not an
API or RSS feed. fetch_events() extracts and parses just the data
object with a small hand-rolled parser (quote the object literal's bare
keys, then json.loads), rather than adding a JS-evaluation dependency
(no Node, no JSON5 library) for what turned out, once isolated from the
trailing logic, to be a well-defined, bounded parsing problem.

Each event's own `time` field is free text ("9pm", "8pm to late",
"Check with organiser", ...), not a structured field like newry.ie's
own "Event Date" row (see events.py) -- parse_event_times does a
best-effort extraction of the first (and, if present, second)
clock-time-shaped mention, defaulting to midnight when there's no
parseable time at all (confirmed live: a large fraction of entries have
none), same convention events.py already uses for its own date-only
case.
"""

import json
import logging
import re
from datetime import datetime

import requests

import config
import textutil

log = logging.getLogger("newry-bot")

_VAR_PREFIX = "window.NEWRY_GUIDE = "

_STRING_LITERAL = re.compile(r'"(?:[^"\\]|\\.)*"')
_BARE_KEY = re.compile(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)")
_CLOCK_TIME = re.compile(r"(\d{1,2})(?:[.:](\d{2}))?\s*(am|pm)|\bnoon\b", re.IGNORECASE)


def _extract_object_literal(js_text, prefix):
    """The raw '{...}' text assigned to `prefix` in `js_text` -- scans
    forward tracking brace depth, skipping over string-literal contents
    (so a brace inside a quoted description can't confuse the depth
    count), and stops at the matching close brace. Raises ValueError if
    `prefix` isn't found or the braces never balance -- fetch_events()
    catches that alongside every other failure mode."""
    start = js_text.index(prefix) + len(prefix)
    depth = 0
    in_string = False
    quote_char = ""
    i = start
    n = len(js_text)
    while i < n:
        ch = js_text[i]
        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == quote_char:
                in_string = False
            i += 1
            continue
        if ch in "\"'":
            in_string = True
            quote_char = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return js_text[start : i + 1]
        i += 1
    raise ValueError("unbalanced braces in object literal")


def _quote_bare_keys(js_object_text):
    """JS object-literal keys aren't quoted ('title: ...'), which isn't
    valid JSON ('"title": ...') -- quotes them, but only outside string
    literals, so a colon or comma that happens to appear inside a
    description's own text is never touched."""
    parts = []
    pos = 0
    for m in _STRING_LITERAL.finditer(js_object_text):
        parts.append(_BARE_KEY.sub(r'\1"\2"\3', js_object_text[pos : m.start()]))
        parts.append(m.group(0))
        pos = m.end()
    parts.append(_BARE_KEY.sub(r'\1"\2"\3', js_object_text[pos:]))
    return "".join(parts)


def _match_to_time(match):
    """(hour, minute) in 24h time from one _CLOCK_TIME regex match."""
    if match.group(0).lower() == "noon":
        return 12, 0
    hour = int(match.group(1)) % 12
    minute = int(match.group(2) or 0)
    if match.group(3).lower() == "pm":
        hour += 12
    return hour, minute


def parse_event_times(date_str, time_text):
    """(start, end) datetimes for a "YYYY-MM-DD" `date_str` and this
    source's free-text `time_text` field. start defaults to midnight if
    `time_text` has no clock-time-shaped mention at all (e.g. "Check
    with organiser", "Programme to be published") -- confirmed live, a
    large fraction of entries have none, and dropping them outright
    (calendar_sync.sync_event refuses a None start) would lose real
    events over missing presentation detail, not missing substance.
    end is the *second* clock-time mention if there is one (e.g. "9pm to
    11pm"), else None -- "8pm to late" has no second clock-time-shaped
    mention, so end is None there too, same as a bare "9pm" alone;
    sync_event's own DEFAULT_EVENT_DURATION_HOURS fallback covers both.
    (None, None) if `date_str` itself doesn't parse -- never raises."""
    try:
        base_date = datetime.strptime(date_str, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None, None

    matches = list(_CLOCK_TIME.finditer(time_text or ""))
    if not matches:
        return base_date, None

    start_hour, start_minute = _match_to_time(matches[0])
    start = base_date.replace(hour=start_hour, minute=start_minute)
    end = None
    if len(matches) > 1:
        end_hour, end_minute = _match_to_time(matches[1])
        end = base_date.replace(hour=end_hour, minute=end_minute)
    return start, end


def fetch_events():
    """Yields (title, link, start, end, venue, description) tuples for
    every guide entry with a usable title, link, and date -- an entry
    missing any of those is skipped, not fatal to the rest of the feed.
    Fails open (logs, yields nothing) if the fetch or parse fails
    entirely -- never raises."""
    try:
        response = requests.get(config.NEWRY_WHATS_ON_URL, timeout=15)
        response.raise_for_status()
        # The response's Content-Type carries no charset, so `requests`
        # falls back to the HTTP default (ISO-8859-1) even though the
        # page is actually UTF-8 -- same mangled-apostrophe class of bug
        # heating_oil.py hit on niliving.co.uk; set explicitly before
        # reading `.text`.
        response.encoding = "utf-8"
        obj_text = _extract_object_literal(response.text, _VAR_PREFIX)
        data = json.loads(_quote_bare_keys(obj_text))
    except Exception:
        log.exception("Failed to fetch/parse the Newry What's On guide")
        return

    for event in data.get("events", []):
        title = event.get("title")
        link = event.get("url")
        date_str = event.get("startDate")
        if not (title and link and date_str):
            continue
        start, end = parse_event_times(date_str, event.get("time", ""))
        if start is None:
            log.warning("Could not parse date %r for guide event %r", date_str, title)
            continue
        venue = event.get("venue") or event.get("location") or None
        yield title, link, start, end, venue, event.get("description", "")


def find_similar_existing_event(title, start, existing_events, threshold=None):
    """True if `existing_events` (a list of (summary, "YYYY-MM-DD")
    pairs from calendar_sync.list_upcoming_event_summaries) already has
    something on the same date as `start` whose title is similar enough
    (textutil.similarity) to plausibly be the same real-world event,
    just listed by a different source under a different title and link
    -- the cross-source "is something similar already in the calendar"
    check the user asked for, distinct from event_id_for's exact-link
    upsert (which only catches a repeat from *this* source). Defaults to
    config.CALENDAR_CROSS_SOURCE_DEDUP_THRESHOLD, deliberately stricter
    than the SIMILARITY_THRESHOLD news/Reddit-event dedup uses elsewhere
    in this project -- see that constant's own comment for why a false
    positive here (a real event silently never added to the calendar)
    is costlier than the false positives SIMILARITY_THRESHOLD tolerates.
    False if `start` is None (nothing to compare dates against)."""
    if start is None:
        return False
    if threshold is None:
        threshold = config.CALENDAR_CROSS_SOURCE_DEDUP_THRESHOLD
    date_str = start.strftime("%Y-%m-%d")
    normalized = textutil.normalize(title)
    return any(
        existing_date == date_str
        and textutil.similarity(normalized, textutil.normalize(existing_title)) >= threshold
        for existing_title, existing_date in existing_events
    )
