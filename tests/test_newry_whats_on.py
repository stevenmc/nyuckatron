from datetime import datetime
from unittest.mock import MagicMock

import newry_whats_on


# --- _extract_object_literal ---------------------------------------------

def test_extract_object_literal_basic():
    js = 'window.NEWRY_GUIDE = {"a": 1};\nmore code here'
    assert newry_whats_on._extract_object_literal(js, "window.NEWRY_GUIDE = ") == '{"a": 1}'


def test_extract_object_literal_handles_nested_braces():
    js = 'window.NEWRY_GUIDE = {"a": {"b": 1}, "c": [{"d": 2}]};\ntrailing'
    result = newry_whats_on._extract_object_literal(js, "window.NEWRY_GUIDE = ")
    assert result == '{"a": {"b": 1}, "c": [{"d": 2}]}'


def test_extract_object_literal_ignores_braces_inside_string_literals():
    # A description containing "{" or "}" of its own must not confuse the
    # brace-depth count.
    js = 'window.NEWRY_GUIDE = {"a": "some {curly} text"};\ntrailing'
    result = newry_whats_on._extract_object_literal(js, "window.NEWRY_GUIDE = ")
    assert result == '{"a": "some {curly} text"}'


def test_extract_object_literal_raises_on_unbalanced_braces():
    js = 'window.NEWRY_GUIDE = {"a": 1'
    try:
        newry_whats_on._extract_object_literal(js, "window.NEWRY_GUIDE = ")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_extract_object_literal_raises_when_prefix_not_found():
    try:
        newry_whats_on._extract_object_literal("no prefix here", "window.NEWRY_GUIDE = ")
        assert False, "expected ValueError"
    except ValueError:
        pass


# --- _quote_bare_keys ------------------------------------------------------

def test_quote_bare_keys_quotes_simple_keys():
    assert newry_whats_on._quote_bare_keys('{title: "Event", venue: "Hall"}') == '{"title": "Event", "venue": "Hall"}'


def test_quote_bare_keys_does_not_touch_colons_or_commas_inside_strings():
    # A real description from the live source: colons and commas inside
    # the string value must survive untouched -- only the actual bare
    # key (title:) gets quoted.
    source = '{title: "Checked: 2 November 2026, confirmed"}'
    assert newry_whats_on._quote_bare_keys(source) == '{"title": "Checked: 2 November 2026, confirmed"}'


def test_quote_bare_keys_handles_nested_objects_and_arrays():
    source = '{events: [{id: "a", tags: ["music", "night"]}]}'
    assert (
        newry_whats_on._quote_bare_keys(source)
        == '{"events": [{"id": "a", "tags": ["music", "night"]}]}'
    )


# --- parse_event_times ------------------------------------------------------

def test_parse_event_times_single_time():
    start, end = newry_whats_on.parse_event_times("2026-11-07", "9pm")
    assert start == datetime(2026, 11, 7, 21, 0)
    assert end is None


def test_parse_event_times_range():
    start, end = newry_whats_on.parse_event_times("2026-11-08", "1pm to 3pm")
    assert start == datetime(2026, 11, 8, 13, 0)
    assert end == datetime(2026, 11, 8, 15, 0)


def test_parse_event_times_handles_minutes():
    start, end = newry_whats_on.parse_event_times("2026-11-08", "10.30am to 11.30am")
    assert start == datetime(2026, 11, 8, 10, 30)
    assert end == datetime(2026, 11, 8, 11, 30)


def test_parse_event_times_noon():
    start, _ = newry_whats_on.parse_event_times("2026-11-08", "Noon to 4pm")
    assert start == datetime(2026, 11, 8, 12, 0)


def test_parse_event_times_second_mention_not_a_clock_time_leaves_end_none():
    # "8pm to late" -- "late" has no clock-time shape, so there's no
    # second match to use as an end time, same as a bare "9pm" alone.
    start, end = newry_whats_on.parse_event_times("2026-10-31", "8pm to late")
    assert start == datetime(2026, 10, 31, 20, 0)
    assert end is None


def test_parse_event_times_defaults_to_midnight_when_no_time_found():
    # Confirmed live: a large fraction of this source's events have no
    # parseable time at all ("Check with organiser", etc.) -- default to
    # midnight rather than dropping the event, matching events.py's own
    # date-only convention.
    start, end = newry_whats_on.parse_event_times("2026-11-20", "Check with organiser")
    assert start == datetime(2026, 11, 20, 0, 0)
    assert end is None


def test_parse_event_times_returns_none_for_an_unparseable_date():
    assert newry_whats_on.parse_event_times("not-a-date", "9pm") == (None, None)


def test_parse_event_times_returns_none_for_a_missing_date():
    assert newry_whats_on.parse_event_times(None, "9pm") == (None, None)


# --- fetch_events ------------------------------------------------------------

def _mock_response(text):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.text = text
    resp.encoding = "ISO-8859-1"  # the real site's actual (wrong) default -- see fetch_events' own fix
    return resp


_SAMPLE_JS = """window.NEWRY_GUIDE = {
  updatedAt: "2026-11-02",
  events: [
    {
      id: "a",
      startDate: "2026-11-07",
      title: "Nightshift",
      venue: "CWC Newry",
      time: "9pm",
      url: "https://example.com/a",
      description: "Live music."
    },
    {
      id: "b",
      startDate: "2026-11-08",
      title: "No Link Event",
      venue: "Somewhere",
      time: "9pm"
    }
  ]
};
(function() { /* trailing display logic, never parsed */ })();
"""


def test_fetch_events_yields_parsed_tuples(monkeypatch):
    monkeypatch.setattr(newry_whats_on.requests, "get", MagicMock(return_value=_mock_response(_SAMPLE_JS)))

    results = list(newry_whats_on.fetch_events())

    assert len(results) == 1  # the second entry has no "url", skipped
    title, link, start, end, venue, description = results[0]
    assert title == "Nightshift"
    assert link == "https://example.com/a"
    assert start == datetime(2026, 11, 7, 21, 0)
    assert end is None
    assert venue == "CWC Newry"
    assert description == "Live music."


def test_fetch_events_fails_open_on_request_exception(monkeypatch):
    import requests

    monkeypatch.setattr(newry_whats_on.requests, "get", MagicMock(side_effect=requests.ConnectionError("down")))

    assert list(newry_whats_on.fetch_events()) == []  # doesn't raise


def test_fetch_events_fails_open_when_the_page_layout_has_changed(monkeypatch):
    monkeypatch.setattr(
        newry_whats_on.requests, "get", MagicMock(return_value=_mock_response("<html>not the expected JS</html>"))
    )

    assert list(newry_whats_on.fetch_events()) == []  # doesn't raise


def test_fetch_events_sends_browser_like_defaults_and_sets_utf8_encoding(monkeypatch):
    response = _mock_response(_SAMPLE_JS)
    monkeypatch.setattr(newry_whats_on.requests, "get", MagicMock(return_value=response))

    list(newry_whats_on.fetch_events())

    assert response.encoding == "utf-8"


# --- find_similar_existing_event --------------------------------------------

def test_find_similar_existing_event_matches_same_date_and_similar_title():
    existing = [("Mike Denver & Guests", "2026-11-14")]
    start = datetime(2026, 11, 14, 19, 0)
    assert newry_whats_on.find_similar_existing_event("Mike Denver and Guests", start, existing)


def test_find_similar_existing_event_ignores_a_different_date():
    existing = [("Mike Denver & Guests", "2026-11-14")]
    start = datetime(2026, 11, 15, 19, 0)  # one day off
    assert not newry_whats_on.find_similar_existing_event("Mike Denver and Guests", start, existing)


def test_find_similar_existing_event_ignores_a_dissimilar_title_on_the_same_date():
    existing = [("Completely Unrelated Event", "2026-11-14")]
    start = datetime(2026, 11, 14, 19, 0)
    assert not newry_whats_on.find_similar_existing_event("Mike Denver and Guests", start, existing)


def test_find_similar_existing_event_false_when_start_is_none():
    existing = [("Mike Denver & Guests", "2026-11-14")]
    assert not newry_whats_on.find_similar_existing_event("Mike Denver and Guests", None, existing)


def test_find_similar_existing_event_false_for_empty_existing_list():
    assert not newry_whats_on.find_similar_existing_event("Anything", datetime(2026, 11, 14), [])


def test_find_similar_existing_event_ignores_a_coincidental_one_word_overlap():
    # Confirmed live 2026-11-09: "Santa at the Castle" and "Santa's
    # SuperShow -- Bellini's Newry" are unrelated events that happened to
    # land on the same date and share only the word "santa" -- scores 0.5
    # (SIMILARITY_THRESHOLD) but should not clear this check's stricter
    # CALENDAR_CROSS_SOURCE_DEDUP_THRESHOLD.
    existing = [("Santa's SuperShow — Bellini's Newry", "2026-12-06")]
    start = datetime(2026, 12, 6)
    assert not newry_whats_on.find_similar_existing_event("Santa at the Castle", start, existing)
