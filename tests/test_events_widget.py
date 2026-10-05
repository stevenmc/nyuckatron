from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import prawcore

import config
import events_widget


# --- build_events_widget_markdown (pure) ---------------------------------

def _event(title="Event", link="https://www.newry.ie/events/1", start=None, end=None, venue=None, calendar_link=None):
    return (title, link, start or datetime(2026, 10, 11, 19, 0), end, venue, calendar_link)


def test_build_events_widget_markdown_includes_title_as_link_text_and_link_as_target():
    markdown = events_widget.build_events_widget_markdown([_event(title="My Event", link="https://www.newry.ie/events/42")])
    assert "[My Event](https://www.newry.ie/events/42)" in markdown


def test_build_events_widget_markdown_includes_date_and_venue():
    markdown = events_widget.build_events_widget_markdown(
        [_event(start=datetime(2026, 10, 11, 19, 0), venue="Newry Town Hall")]
    )
    assert "Sun 11 Oct, 7:00 pm" in markdown
    assert "Newry Town Hall" in markdown


def test_build_events_widget_markdown_omits_venue_when_none():
    markdown = events_widget.build_events_widget_markdown([_event(venue=None)])
    assert "·" not in markdown


def test_build_events_widget_markdown_shows_end_time_range_when_present():
    markdown = events_widget.build_events_widget_markdown(
        [_event(start=datetime(2026, 10, 11, 19, 0), end=datetime(2026, 10, 11, 23, 0))]
    )
    assert "7:00 pm – 11:00 pm" in markdown


def test_build_events_widget_markdown_omits_end_time_when_same_as_start():
    markdown = events_widget.build_events_widget_markdown(
        [_event(start=datetime(2026, 10, 11, 19, 0), end=datetime(2026, 10, 11, 19, 0))]
    )
    assert "–" not in markdown


def test_build_events_widget_markdown_sorts_chronologically_regardless_of_input_order():
    later = _event(title="Later Event", start=datetime(2026, 10, 20, 9, 0))
    sooner = _event(title="Sooner Event", start=datetime(2026, 10, 11, 9, 0))

    markdown = events_widget.build_events_widget_markdown([later, sooner])

    assert markdown.index("Sooner Event") < markdown.index("Later Event")


def test_build_events_widget_markdown_falls_back_to_calendar_link_when_link_is_empty():
    markdown = events_widget.build_events_widget_markdown(
        [_event(title="Fallback Event", link="", calendar_link="https://calendar.google.com/event?eid=abc")]
    )
    assert "[Fallback Event](https://calendar.google.com/event?eid=abc)" in markdown


def test_build_events_widget_markdown_renders_plain_text_when_no_link_available_at_all():
    markdown = events_widget.build_events_widget_markdown(
        [_event(title="No Link Event", link="", calendar_link=None)]
    )
    assert "**No Link Event**" in markdown
    assert "[No Link Event]" not in markdown


def test_build_events_widget_markdown_returns_placeholder_text_for_empty_list():
    markdown = events_widget.build_events_widget_markdown([])
    assert "no upcoming events" in markdown.lower()


def test_build_events_widget_markdown_has_no_redundant_heading():
    # The widget's own shortName (config.EVENTS_WIDGET_SHORT_NAME,
    # "Upcoming Events") already says this -- a duplicate first line in
    # the body is just noise.
    markdown = events_widget.build_events_widget_markdown([_event(title="Some Event")])
    assert "upcoming events" not in markdown.lower()


def test_build_events_widget_markdown_uses_hard_line_breaks():
    # A single newline inside one Markdown list item is a soft break that
    # most renderers (Reddit's included) collapse back into one run-on
    # line -- each content line needs a trailing-double-space hard break
    # to actually render as two separate lines. Two events, so the first
    # event's lines aren't the very end of the whole string (where a
    # trailing hard break wouldn't matter and gets stripped anyway).
    markdown = events_widget.build_events_widget_markdown(
        [
            _event(title="First Event", start=datetime(2026, 10, 11, 19, 0), venue="A Venue"),
            _event(title="Second Event", start=datetime(2026, 10, 12, 19, 0), venue="Another Venue"),
        ]
    )
    lines = markdown.split("\n")
    assert lines[0].endswith("  ")  # the linked-title line
    assert lines[1].endswith("  ")  # the date/venue line


def test_build_events_widget_markdown_omits_the_time_for_a_date_only_event():
    # Some newry.ie events genuinely have no time field at all -- parsed
    # as midnight (see events._parse_event_date_row), which should read as
    # "no known time", not "starts at 12:00 am".
    markdown = events_widget.build_events_widget_markdown(
        [_event(title="Date Only Event", start=datetime(2026, 10, 12, 0, 0), venue="A Venue")]
    )
    assert "12:00 am" not in markdown.lower()
    assert "Mon 12 Oct · A Venue" in markdown


# --- sync_events_widget (mocked praw) -------------------------------------

def _fake_widget(short_name):
    return SimpleNamespace(shortName=short_name, mod=MagicMock())


def test_sync_events_widget_creates_when_no_matching_widget_exists():
    reddit = MagicMock()
    reddit.subreddit.return_value.widgets.sidebar = []

    result = events_widget.sync_events_widget(reddit, "newry", "some markdown")

    assert result is True
    reddit.subreddit.return_value.widgets.mod.add_text_area.assert_called_once_with(
        short_name=config.EVENTS_WIDGET_SHORT_NAME, text="some markdown", styles=config.EVENTS_WIDGET_STYLES
    )


def test_sync_events_widget_updates_existing_widget_in_place():
    existing = _fake_widget(config.EVENTS_WIDGET_SHORT_NAME)
    reddit = MagicMock()
    reddit.subreddit.return_value.widgets.sidebar = [existing]

    result = events_widget.sync_events_widget(reddit, "newry", "new markdown")

    assert result is True
    existing.mod.update.assert_called_once_with(text="new markdown")
    reddit.subreddit.return_value.widgets.mod.add_text_area.assert_not_called()


def test_sync_events_widget_ignores_unrelated_widgets_by_shortname():
    unrelated = _fake_widget("Rules")
    reddit = MagicMock()
    reddit.subreddit.return_value.widgets.sidebar = [unrelated]

    events_widget.sync_events_widget(reddit, "newry", "markdown")

    unrelated.mod.update.assert_not_called()
    reddit.subreddit.return_value.widgets.mod.add_text_area.assert_called_once()


def test_sync_events_widget_fails_open_on_forbidden():
    reddit = MagicMock()
    fake_response = MagicMock(status_code=403)
    reddit.subreddit.side_effect = prawcore.exceptions.Forbidden(fake_response)

    result = events_widget.sync_events_widget(reddit, "newry", "markdown")

    assert result is False  # doesn't raise, doesn't block run_events()


def test_sync_events_widget_fails_open_on_unexpected_error():
    reddit = MagicMock()
    reddit.subreddit.side_effect = Exception("reddit is down")

    result = events_widget.sync_events_widget(reddit, "newry", "markdown")

    assert result is False  # doesn't raise
