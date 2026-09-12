from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import events

# Real markup structure from a live newry.ie event page (Joomla's
# com_eventbooking component), verified 2026-09-05. Fields present vary
# per event -- this one has both a start and end date; see the second
# fixture below for one that's missing the end date, which is common.
REAL_EVENT_PAGE_WITH_END_DATE = """
<html><body>
<table>
<tr class="eb-event-property">
    <td class="eb-event-property-label">Event Date</td>
    <td class="eb-event-property-value">05-09-2026 7:00 pm</td>
</tr>
<tr class="eb-event-property">
    <td class="eb-event-property-label">Event End Date</td>
    <td class="eb-event-property-value">05-09-2026 9:30 pm</td>
</tr>
<tr class="eb-event-property">
    <td class="eb-event-property-label">Individual Price</td>
    <td class="eb-event-property-value">&#163;35.00</td>
</tr>
<tr class="eb-event-property">
    <td class="eb-event-property-label">Location</td>
    <td class="eb-event-property-value"><a href="/events/venues/1">Canal Court Hotel</a></td>
</tr>
</table>
</body></html>
"""

REAL_EVENT_PAGE_WITHOUT_END_DATE = """
<html><body>
<table>
<tr class="eb-event-property">
    <td class="eb-event-property-label">Event Date</td>
    <td class="eb-event-property-value">05-09-2026 9:00 am</td>
</tr>
<tr class="eb-event-property">
    <td class="eb-event-property-label">Individual Price</td>
    <td class="eb-event-property-value">Free</td>
</tr>
<tr class="eb-event-property">
    <td class="eb-event-property-label">Location</td>
    <td class="eb-event-property-value">Gaeláras Mhic Ardghail</td>
</tr>
</table>
</body></html>
"""


def _fake_feed(entries):
    return SimpleNamespace(entries=entries, bozo_exception=None)


def test_fetch_event_feed_yields_title_and_link(monkeypatch):
    entries = [
        SimpleNamespace(title="Nathan Carter - Live in Newry", link="https://www.newry.ie/events/2057-nathan-carter"),
        SimpleNamespace(title="Newry Artisan and Craft Market", link="https://www.newry.ie/events/2392-craft-market"),
    ]
    monkeypatch.setattr(events.feedparser, "parse", lambda url: _fake_feed(entries))

    results = list(events.fetch_event_feed("http://example.com/events.rss"))

    assert results == [
        ("Nathan Carter - Live in Newry", "https://www.newry.ie/events/2057-nathan-carter"),
        ("Newry Artisan and Craft Market", "https://www.newry.ie/events/2392-craft-market"),
    ]


def test_fetch_event_feed_skips_entries_missing_title_or_link(monkeypatch):
    entries = [
        SimpleNamespace(title="", link="https://www.newry.ie/events/1"),
        SimpleNamespace(title="Has no link", link=""),
        SimpleNamespace(title="Valid event", link="https://www.newry.ie/events/2"),
    ]
    monkeypatch.setattr(events.feedparser, "parse", lambda url: _fake_feed(entries))

    results = list(events.fetch_event_feed("http://example.com/events.rss"))

    assert results == [("Valid event", "https://www.newry.ie/events/2")]


def test_fetch_event_feed_handles_empty_feed_gracefully(monkeypatch):
    monkeypatch.setattr(events.feedparser, "parse", lambda url: _fake_feed([]))
    assert list(events.fetch_event_feed("http://example.com/events.rss")) == []


def _mock_get_response(text, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


def test_fetch_event_details_parses_full_page(monkeypatch):
    monkeypatch.setattr(
        events.requests, "get", MagicMock(return_value=_mock_get_response(REAL_EVENT_PAGE_WITH_END_DATE))
    )

    details = events.fetch_event_details("https://www.newry.ie/events/2057-nathan-carter")

    assert details["start"] == datetime(2026, 9, 5, 19, 0)
    assert details["end"] == datetime(2026, 9, 5, 21, 30)
    assert details["venue"] == "Canal Court Hotel"
    assert details["price"] == "£35.00"


def test_fetch_event_details_handles_missing_end_date(monkeypatch):
    monkeypatch.setattr(
        events.requests, "get", MagicMock(return_value=_mock_get_response(REAL_EVENT_PAGE_WITHOUT_END_DATE))
    )

    details = events.fetch_event_details("https://www.newry.ie/events/2389-workshop")

    assert details["start"] == datetime(2026, 9, 5, 9, 0)
    assert details["end"] is None
    assert details["venue"] == "Gaeláras Mhic Ardghail"
    assert details["price"] == "Free"


def test_fetch_event_details_returns_all_none_on_network_failure(monkeypatch):
    import requests

    monkeypatch.setattr(events.requests, "get", MagicMock(side_effect=requests.ConnectionError("down")))

    details = events.fetch_event_details("https://www.newry.ie/events/1")

    assert details == {"start": None, "end": None, "venue": None, "price": None}


def test_fetch_event_details_returns_all_none_for_page_with_no_property_table(monkeypatch):
    monkeypatch.setattr(events.requests, "get", MagicMock(return_value=_mock_get_response("<html>nothing here</html>")))

    details = events.fetch_event_details("https://www.newry.ie/events/1")

    assert details == {"start": None, "end": None, "venue": None, "price": None}


# --- is_in_the_past -------------------------------------------------------

def test_is_in_the_past_true_for_a_start_time_already_behind_us():
    yesterday = datetime.now() - timedelta(days=1)
    assert events.is_in_the_past(yesterday)


def test_is_in_the_past_false_for_a_future_start_time():
    tomorrow = datetime.now() + timedelta(days=1)
    assert not events.is_in_the_past(tomorrow)


def test_is_in_the_past_false_when_start_is_none():
    # No start time could be scraped at all -- don't drop it over that,
    # matching this project's fail-open bias elsewhere. calendar_sync.py's
    # own None-start check is what actually skips these, separately.
    assert not events.is_in_the_past(None)
