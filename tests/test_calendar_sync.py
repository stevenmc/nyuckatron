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
