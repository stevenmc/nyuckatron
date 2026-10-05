"""Fetches the current GBP<->EUR exchange rate for display in the sidebar
events widget, alongside upcoming events -- see events_widget.py and
bot.run_events().

Uses Frankfurter (https://frankfurter.dev), a free API backed by the
European Central Bank's published reference rates -- no API key, no
rate-limit signup, a good fit for one lookup per run. Not the
`EURtoGBPRedditSidebar` Google Apps Script mentioned in README.md's
"Subreddit sidebar" section (that populates an entirely separate,
currently-unused Calendar widget on Steven's own Google account) --
this is a fresh, independent lookup, fetched live each run rather than
relying on that script's output.
"""

import logging

import requests

log = logging.getLogger("newry-bot")

_API_URL = "https://api.frankfurter.dev/v1/latest"


def fetch_gbp_eur_rate():
    """The current GBP->EUR rate (a float: how many EUR one GBP buys), or
    None on any failure -- never raises, same fail-open contract as the
    rest of this project's external-data fetches."""
    try:
        response = requests.get(_API_URL, params={"from": "GBP", "to": "EUR"}, timeout=10)
        response.raise_for_status()
        return response.json()["rates"]["EUR"]
    except Exception:
        log.exception("Failed to fetch GBP/EUR exchange rate")
        return None


def build_exchange_rate_markdown(gbp_to_eur):
    """gbp_to_eur: the GBP->EUR rate (float), or None if it couldn't be
    fetched. Returns Markdown text for both directions -- GBP->EUR and its
    arithmetic inverse, EUR->GBP, rather than a second API call -- or an
    empty string if the rate is unavailable, so the caller can simply omit
    this section instead of showing a broken placeholder."""
    if not gbp_to_eur:
        return ""
    eur_to_gbp = 1 / gbp_to_eur
    return (
        "**Exchange Rates**  \n"  # bold, not a heading (###) -- confirmed
        # live 2026-10-05 that Reddit's TextArea widget doesn't give
        # heading syntax any visual weight, the same way a lone newline
        # doesn't act as a real line break there either; **bold** does
        # work, since the event titles already rely on it.
        f"£1 = €{gbp_to_eur:.2f}  \n"
        f"€1 = £{eur_to_gbp:.2f}"
    )
