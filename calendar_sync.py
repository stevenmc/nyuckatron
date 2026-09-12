"""Writes events to a Google Calendar via a service account.

Uses google-auth for service-account authentication (proper JWT signing,
not hand-rolled crypto) and plain REST calls via `requests` against the
Calendar API v3 directly, rather than the much heavier
google-api-python-client (a discovery-document-based client that also
pulls in httplib2, uritemplate, and more). The event-upsert surface needed
here is small enough that raw REST is simpler, and lighter for a
low-memory box -- unlike linkclean.py's Google News handling, this is a
documented, stable, versioned API, not reverse-engineered internals, so
raw REST against it is a reasonable, low-risk simplification rather than
something fragile.

Fully optional: if GOOGLE_SERVICE_ACCOUNT_FILE / GOOGLE_CALENDAR_ID aren't
set in .env, every function here is a no-op that logs once and returns --
Reddit posting works independently regardless of whether this has been
set up.

Live-tested end-to-end on 2026-09-05 against the real service account and
calendar: insert, upsert (update-in-place via PUT), and delete all
confirmed working. Two real issues turned up during that test and are
now fixed -- neither was visible from reading the code alone:

1. The Calendar API wasn't enabled yet on the GCP project (a one-time
   console step, unrelated to this code).
2. event_id_for()'s "newryie" prefix contained 'w' and 'y', which aren't
   in Calendar's allowed event-ID charset (a-v, 0-9) -- every insert
   400'd with "Invalid resource id value." until this was caught; see
   that function for the fix.
"""

import hashlib
import logging
from datetime import timedelta
from urllib.parse import quote

import requests

import config

log = logging.getLogger("newry-bot")

_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
_API_BASE = "https://www.googleapis.com/calendar/v3"
_TIMEZONE = "Europe/London"

_credentials = None
_warned_not_configured = False


def is_configured():
    return bool(config.GOOGLE_SERVICE_ACCOUNT_FILE and config.GOOGLE_CALENDAR_ID)


def _get_credentials():
    global _credentials
    if _credentials is None:
        from google.oauth2 import service_account

        _credentials = service_account.Credentials.from_service_account_file(
            config.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=[_CALENDAR_SCOPE]
        )
    return _credentials


def _access_token():
    from google.auth.transport.requests import Request

    creds = _get_credentials()
    if not creds.valid:
        creds.refresh(Request())
    return creds.token


def event_id_for(link):
    """Deterministic Google Calendar event ID derived from a newry.ie event
    link, so re-syncing the same event always targets the same Calendar
    entry (upsert) instead of creating a duplicate. Calendar event IDs are
    restricted to lowercase base32hex characters (a-v, 0-9) and hyphens,
    5-1024 characters -- hence hashing the link rather than reusing its
    text directly, which contains characters that aren't allowed. The
    prefix below must stick to that same a-v/0-9 charset too: "newryie"
    itself contains 'w' and 'y', which the Calendar API rejects outright
    (confirmed live -- every insert 400'd with "Invalid resource id
    value." until this was caught), hence "nerie" rather than the more
    obvious spelling."""
    digest = hashlib.sha1(link.encode("utf-8")).hexdigest()
    return "nerie" + digest[:24]


def sync_event(title, link, start, end, venue, description=""):
    """Upserts one event into the configured Google Calendar. Returns True
    on success, False otherwise (including when Calendar integration isn't
    configured, or the event has no usable start time) -- never raises, so
    a Calendar failure never blocks the independent Reddit-posting path."""
    if not is_configured():
        global _warned_not_configured
        if not _warned_not_configured:
            log.info(
                "Google Calendar not configured (set GOOGLE_SERVICE_ACCOUNT_FILE "
                "and GOOGLE_CALENDAR_ID in .env to enable) -- skipping calendar sync."
            )
            _warned_not_configured = True
        return False

    if start is None:
        log.warning("No start time for event '%s', skipping calendar sync: %s", title, link)
        return False

    if end is None:
        end = start + timedelta(hours=config.DEFAULT_EVENT_DURATION_HOURS)

    body = {
        "id": event_id_for(link),
        "summary": title,
        "location": venue or "",
        "description": (description + f"\n\nMore info: {link}").strip(),
        "start": {"dateTime": start.isoformat(), "timeZone": _TIMEZONE},
        "end": {"dateTime": end.isoformat(), "timeZone": _TIMEZONE},
        "source": {"title": "newry.ie", "url": link},
    }

    try:
        headers = {
            "Authorization": f"Bearer {_access_token()}",
            "Content-Type": "application/json",
        }
        calendar_id = quote(config.GOOGLE_CALENDAR_ID, safe="")

        # Update first (idempotent if this event was already synced before);
        # an event that doesn't exist yet 404s on update, so fall back to
        # insert in that case. Avoids a separate "does it exist" lookup.
        response = requests.put(
            f"{_API_BASE}/calendars/{calendar_id}/events/{body['id']}",
            headers=headers,
            json=body,
            timeout=10,
        )
        if response.status_code == 404:
            response = requests.post(
                f"{_API_BASE}/calendars/{calendar_id}/events",
                headers=headers,
                json=body,
                timeout=10,
            )
        response.raise_for_status()
        log.info("Synced event to calendar: %s", title)
        return True
    except Exception:
        log.exception("Failed to sync event '%s' to calendar", title)
        return False
