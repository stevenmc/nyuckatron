"""Keeps a single sidebar TextArea widget on the subreddit in sync with
newry.ie's upcoming events, rendered as Markdown links.

Exists because Reddit's native "Calendar" widget type (the one already on
r/newry, connected to the same Google Calendar calendar_sync.py writes to)
has no way to show a per-event link at all -- confirmed by reading praw's
widgets.py directly (its `configuration` schema is six display-toggle
fields, no URL field) and by inspecting the live widget's own data (no URL
of any kind per event). This is a second, bot-managed widget alongside it,
not a replacement -- see bot.run_events() and config.EVENTS_WIDGET_SHORT_NAME.
"""

import logging

import prawcore

import config

log = logging.getLogger("newry-bot")

_NO_EVENTS_TEXT = "*No upcoming events listed right now -- check back soon.*"


def build_events_widget_markdown(upcoming_events):
    """upcoming_events: a list of (title, link, start, end, venue,
    calendar_link) tuples. Pure -- no network or praw objects -- so it's
    directly unit-testable with fixture tuples, the same way
    bot._parse_newry_ie_homepage_articles and bot.find_newry_ie_substitute
    are.

    Per event, the link used is `link` (the real newry.ie event page) if
    present, else `calendar_link` (a Google Calendar event's own shareable
    link -- see calendar_sync.sync_event's return value) if present, else
    the title is rendered as plain bold text with no link at all rather
    than being dropped. In practice `link` is always present -- see
    events.fetch_event_feed, which only ever yields entries that already
    have both a title and a link -- so this fallback chain exists for
    robustness against a hypothetical future event source, not because
    it's reachable today.

    Sorted by start time, soonest first -- the feed's own order isn't
    guaranteed chronological. Returns a placeholder "no events" message
    for an empty list, rather than empty text, so the widget never renders
    blank."""
    if not upcoming_events:
        return _NO_EVENTS_TEXT

    lines = ["### Upcoming Events", ""]
    for title, link, start, end, venue, calendar_link in sorted(upcoming_events, key=lambda e: e[2]):
        url = link or calendar_link
        heading = f"**[{title}]({url})**" if url else f"**{title}**"

        detail = f"{start.strftime('%a')} {start.day} {start.strftime('%b')}, {_format_time(start)}"
        if end and end != start:
            detail += " – " + _format_time(end)
        if venue:
            detail += " · " + venue

        lines.append(f"- {heading}")
        lines.append(f"  {detail}")
        lines.append("")

    return "\n".join(lines).strip()


def _format_time(dt):
    """'7:00 pm' -- no leading zero on the hour, lowercase am/pm (Python's
    %I/%p alone give '07:00 PM')."""
    hour = dt.strftime("%I").lstrip("0") or "12"
    return f"{hour}:{dt.strftime('%M %p').lower()}"


def _find_widget(sidebar_widgets, short_name):
    """The widget in `sidebar_widgets` (e.g. subreddit.widgets.sidebar)
    whose .shortName matches `short_name`, or None if there isn't one."""
    for widget in sidebar_widgets:
        if getattr(widget, "shortName", None) == short_name:
            return widget
    return None


def sync_events_widget(reddit, subreddit_name, markdown_text):
    """Idempotent create-or-update of the config.EVENTS_WIDGET_SHORT_NAME
    sidebar TextArea widget: updates it in place if a widget with that
    exact shortName already exists, otherwise creates it once. Fails open
    (logs, returns False) on missing mod "config" permission or any other
    error -- same fail-open contract as moderation.fetch_spam_urls /
    approve_own_spam_posts, so a widget failure never blocks
    run_events()'s own posting flow. Returns True on success."""
    try:
        subreddit = reddit.subreddit(subreddit_name)
        existing = _find_widget(subreddit.widgets.sidebar, config.EVENTS_WIDGET_SHORT_NAME)
        if existing is not None:
            existing.mod.update(text=markdown_text)
        else:
            subreddit.widgets.mod.add_text_area(
                short_name=config.EVENTS_WIDGET_SHORT_NAME,
                text=markdown_text,
                styles=config.EVENTS_WIDGET_STYLES,
            )
        log.info("Synced sidebar events widget (%s)", config.EVENTS_WIDGET_SHORT_NAME)
        return True
    except prawcore.exceptions.Forbidden:
        log.warning(
            "Bot account lacks mod permission to manage widgets on r/%s -- "
            "skipping sidebar events widget sync. Grant it the 'config' mod "
            "permission to enable this.",
            subreddit_name,
        )
        return False
    except Exception:
        log.exception("Failed to sync sidebar events widget")
        return False
