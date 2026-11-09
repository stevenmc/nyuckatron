from datetime import datetime, timedelta
from unittest.mock import MagicMock

import calendar_sync
import config


def test_is_configured_false_when_env_vars_missing(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", None)
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", None)
    assert not calendar_sync.is_configured()


def test_is_configured_false_when_only_one_env_var_set(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", "/path/to/key.json")
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", None)
    assert not calendar_sync.is_configured()


def test_is_configured_true_when_both_set(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", "/path/to/key.json")
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", "abc@group.calendar.google.com")
    assert calendar_sync.is_configured()


def test_sync_event_noop_when_not_configured(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", None)
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", None)
    mock_put = MagicMock(side_effect=AssertionError("should never make an HTTP call"))
    monkeypatch.setattr(calendar_sync.requests, "put", mock_put)

    result = calendar_sync.sync_event("Some Event", "https://www.newry.ie/events/1", datetime.now(), None, "Venue")

    assert not result


def test_sync_event_returns_false_with_no_start_time(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", "/path/to/key.json")
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", "abc@group.calendar.google.com")
    mock_put = MagicMock(side_effect=AssertionError("should never make an HTTP call"))
    monkeypatch.setattr(calendar_sync.requests, "put", mock_put)

    result = calendar_sync.sync_event("Some Event", "https://www.newry.ie/events/1", None, None, "Venue")

    assert not result


def test_event_id_for_is_deterministic_and_calendar_id_shaped():
    id_a = calendar_sync.event_id_for("https://www.newry.ie/events/2392-newry-artisan-and-craft-market-2")
    id_b = calendar_sync.event_id_for("https://www.newry.ie/events/2392-newry-artisan-and-craft-market-2")
    id_c = calendar_sync.event_id_for("https://www.newry.ie/events/2057-nathan-carter-live-in-newry")

    assert id_a == id_b  # same link -> same id, every time (needed for upsert)
    assert id_a != id_c
    # Google Calendar event IDs: base32hex charset only -- lowercase a-v and
    # digits 0-9, 5-1024 chars. Anything outside a-v (e.g. w/x/y/z) 400s with
    # "Invalid resource id value." -- confirmed live against the real API,
    # which is what caught the previous "newryie" prefix containing w and y.
    import re
    assert re.fullmatch(r"[a-v0-9]{5,1024}", id_a)


def _mock_response(status_code, html_link="https://www.google.com/calendar/event?eid=fake"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"htmlLink": html_link})
    if status_code >= 400 and status_code != 404:
        import requests

        resp.raise_for_status.side_effect = requests.HTTPError(f"{status_code} error")
    return resp


def test_sync_event_updates_existing_event_when_put_succeeds(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", "/path/to/key.json")
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", "abc@group.calendar.google.com")
    monkeypatch.setattr(calendar_sync, "_access_token", lambda: "fake-token")

    mock_put = MagicMock(return_value=_mock_response(200))
    mock_post = MagicMock(side_effect=AssertionError("should not fall back to insert when update succeeds"))
    monkeypatch.setattr(calendar_sync.requests, "put", mock_put)
    monkeypatch.setattr(calendar_sync.requests, "post", mock_post)

    result = calendar_sync.sync_event(
        "Newry Artisan and Craft Market",
        "https://www.newry.ie/events/2392-newry-artisan-and-craft-market-2",
        datetime(2026, 9, 6, 11, 0),
        datetime(2026, 9, 6, 15, 0),
        "Newry Market",
    )

    assert result == "https://www.google.com/calendar/event?eid=fake"
    mock_put.assert_called_once()


def test_sync_event_falls_back_to_insert_when_update_404s(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", "/path/to/key.json")
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", "abc@group.calendar.google.com")
    monkeypatch.setattr(calendar_sync, "_access_token", lambda: "fake-token")

    mock_put = MagicMock(return_value=_mock_response(404))
    mock_post = MagicMock(return_value=_mock_response(200))
    monkeypatch.setattr(calendar_sync.requests, "put", mock_put)
    monkeypatch.setattr(calendar_sync.requests, "post", mock_post)

    result = calendar_sync.sync_event(
        "New Event", "https://www.newry.ie/events/9999-new-event", datetime(2026, 9, 6, 11, 0), None, "Some Venue"
    )

    assert result == "https://www.google.com/calendar/event?eid=fake"
    mock_put.assert_called_once()
    mock_post.assert_called_once()


def test_sync_event_uses_default_duration_when_end_time_missing(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", "/path/to/key.json")
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", "abc@group.calendar.google.com")
    monkeypatch.setattr(config, "DEFAULT_EVENT_DURATION_HOURS", 2)
    monkeypatch.setattr(calendar_sync, "_access_token", lambda: "fake-token")

    mock_put = MagicMock(return_value=_mock_response(200))
    monkeypatch.setattr(calendar_sync.requests, "put", mock_put)

    start = datetime(2026, 9, 6, 11, 0)
    calendar_sync.sync_event("Event", "https://www.newry.ie/events/1", start, None, "Venue")

    _, kwargs = mock_put.call_args
    body = kwargs["json"]
    assert body["end"]["dateTime"] == (start + timedelta(hours=2)).isoformat()


def test_sync_event_returns_false_and_does_not_raise_on_request_failure(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", "/path/to/key.json")
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", "abc@group.calendar.google.com")
    monkeypatch.setattr(calendar_sync, "_access_token", lambda: "fake-token")

    import requests

    mock_put = MagicMock(side_effect=requests.ConnectionError("network down"))
    monkeypatch.setattr(calendar_sync.requests, "put", mock_put)

    result = calendar_sync.sync_event(
        "Event", "https://www.newry.ie/events/1", datetime(2026, 9, 6, 11, 0), None, "Venue"
    )

    assert not result  # should not raise


def test_sync_event_defaults_source_name_to_newry_ie(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", "/path/to/key.json")
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", "abc@group.calendar.google.com")
    monkeypatch.setattr(calendar_sync, "_access_token", lambda: "fake-token")

    mock_put = MagicMock(return_value=_mock_response(200))
    monkeypatch.setattr(calendar_sync.requests, "put", mock_put)

    calendar_sync.sync_event("Event", "https://www.newry.ie/events/1", datetime(2026, 9, 6, 11, 0), None, "Venue")

    _, kwargs = mock_put.call_args
    assert kwargs["json"]["source"] == {"title": "newry.ie", "url": "https://www.newry.ie/events/1"}


def test_sync_event_uses_a_custom_source_name_when_given(monkeypatch):
    # A second events source (newry_whats_on.py) passes its own name so
    # the Calendar entry doesn't misattribute itself as "newry.ie".
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", "/path/to/key.json")
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", "abc@group.calendar.google.com")
    monkeypatch.setattr(calendar_sync, "_access_token", lambda: "fake-token")

    mock_put = MagicMock(return_value=_mock_response(200))
    monkeypatch.setattr(calendar_sync.requests, "put", mock_put)

    calendar_sync.sync_event(
        "Event", "https://example.com/1", datetime(2026, 9, 6, 11, 0), None, "Venue",
        source_name="Newry What's On Guide",
    )

    _, kwargs = mock_put.call_args
    assert kwargs["json"]["source"] == {"title": "Newry What's On Guide", "url": "https://example.com/1"}


# --- list_upcoming_event_summaries ------------------------------------------

def test_list_upcoming_event_summaries_returns_empty_when_not_configured(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", None)
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", None)

    assert calendar_sync.list_upcoming_event_summaries() == []


def test_list_upcoming_event_summaries_returns_summary_and_date_pairs(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", "/path/to/key.json")
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", "abc@group.calendar.google.com")
    monkeypatch.setattr(calendar_sync, "_access_token", lambda: "fake-token")

    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={
        "items": [
            {"summary": "Timed Event", "start": {"dateTime": "2026-11-14T19:00:00Z"}},
            {"summary": "All-Day Event", "start": {"date": "2026-11-20"}},
            {"summary": "No Start Info"},  # missing start -- skipped, not fatal
            {"start": {"date": "2026-11-21"}},  # missing summary -- skipped
        ]
    })
    monkeypatch.setattr(calendar_sync.requests, "get", MagicMock(return_value=resp))

    results = calendar_sync.list_upcoming_event_summaries()

    assert results == [("Timed Event", "2026-11-14"), ("All-Day Event", "2026-11-20")]


def test_list_upcoming_event_summaries_fails_open_on_request_exception(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", "/path/to/key.json")
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", "abc@group.calendar.google.com")
    monkeypatch.setattr(calendar_sync, "_access_token", lambda: "fake-token")

    import requests

    monkeypatch.setattr(calendar_sync.requests, "get", MagicMock(side_effect=requests.ConnectionError("down")))

    assert calendar_sync.list_upcoming_event_summaries() == []  # doesn't raise


# --- occurrence_key (distinct occurrences sharing one link) ----------------

def test_sync_event_uses_occurrence_key_instead_of_link_for_the_calendar_id_when_given(monkeypatch):
    # Regression test for a real incident (2026-11-09): the What's On
    # guide listed "Santa at the Castle" three times under one shared
    # link, with three different dates -- event_id_for(link) alone would
    # hash all three to the same ID and silently overwrite each other
    # down to a single surviving date.
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", "/path/to/key.json")
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", "abc@group.calendar.google.com")
    monkeypatch.setattr(calendar_sync, "_access_token", lambda: "fake-token")

    mock_put = MagicMock(return_value=_mock_response(200))
    monkeypatch.setattr(calendar_sync.requests, "put", mock_put)

    shared_link = "https://example.com/santa"
    calendar_sync.sync_event(
        "Santa at the Castle", shared_link, datetime(2026, 12, 6, 0, 0), None, "Narrow Water Castle",
        occurrence_key=f"{shared_link}|2026-12-06",
    )
    id_a = mock_put.call_args[0][0].rsplit("/", 1)[1]

    calendar_sync.sync_event(
        "Santa at the Castle", shared_link, datetime(2026, 12, 9, 0, 0), None, "Narrow Water Castle",
        occurrence_key=f"{shared_link}|2026-12-09",
    )
    id_b = mock_put.call_args[0][0].rsplit("/", 1)[1]

    assert id_a != id_b


def test_sync_event_falls_back_to_the_bare_link_when_occurrence_key_is_not_given(monkeypatch):
    # newry.ie's own calls never pass occurrence_key -- its event IDs
    # must stay exactly as they were before this feature existed, or
    # every already-synced newry.ie event would be orphaned and
    # duplicated on the next run.
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", "/path/to/key.json")
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", "abc@group.calendar.google.com")
    monkeypatch.setattr(calendar_sync, "_access_token", lambda: "fake-token")

    mock_put = MagicMock(return_value=_mock_response(200))
    monkeypatch.setattr(calendar_sync.requests, "put", mock_put)

    link = "https://www.newry.ie/events/2392-newry-artisan-and-craft-market-2"
    calendar_sync.sync_event("Event", link, datetime(2026, 12, 6, 0, 0), None, "Venue")

    used_id = mock_put.call_args[0][0].rsplit("/", 1)[1]
    assert used_id == calendar_sync.event_id_for(link)


# --- source.url must be a real http(s) link ---------------------------------

def test_sync_event_omits_source_field_for_a_non_http_link(monkeypatch):
    # Regression test for a real incident (2026-11-09): the What's On
    # guide has events with no real web page, just a phone contact
    # ("tel:+44...") -- the Calendar API 400s with "Invalid source url"
    # if that's passed as source.url. The real contact detail still
    # reaches the reader via the "More info: {link}" description line.
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", "/path/to/key.json")
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", "abc@group.calendar.google.com")
    monkeypatch.setattr(calendar_sync, "_access_token", lambda: "fake-token")

    mock_put = MagicMock(return_value=_mock_response(200))
    monkeypatch.setattr(calendar_sync.requests, "put", mock_put)

    calendar_sync.sync_event("Event", "tel:+447746148998", datetime(2026, 12, 5, 13, 0), None, "Venue")

    body = mock_put.call_args.kwargs["json"]
    assert "source" not in body
    assert "tel:+447746148998" in body["description"]


def test_sync_event_includes_source_field_for_a_real_http_link(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", "/path/to/key.json")
    monkeypatch.setattr(config, "GOOGLE_CALENDAR_ID", "abc@group.calendar.google.com")
    monkeypatch.setattr(calendar_sync, "_access_token", lambda: "fake-token")

    mock_put = MagicMock(return_value=_mock_response(200))
    monkeypatch.setattr(calendar_sync.requests, "put", mock_put)

    calendar_sync.sync_event("Event", "https://example.com/a", datetime(2026, 12, 5, 13, 0), None, "Venue")

    body = mock_put.call_args.kwargs["json"]
    assert body["source"] == {"title": "newry.ie", "url": "https://example.com/a"}
