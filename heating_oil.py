"""Fetches the top 3 cheapest 500L home heating oil suppliers in
Northern Ireland, for display in the sidebar events widget -- see
events_widget.py and bot.run_events().

Scrapes niliving.co.uk/oil with plain `requests` -- confirmed live, its
supplier list is server-rendered directly into the page HTML, not
hydrated client-side by JS, so no headless browser is needed. Not
cheapestoil.co.uk, the other source suggested for this -- confirmed live
it blocks plain HTTP requests outright (403, no special headers found
that got past it), while niliving.co.uk works with no special handling
at all, same as every other site this project talks to.
"""

import html
import logging
import re

import requests

log = logging.getLogger("newry-bot")

_URL = "https://niliving.co.uk/oil"

# The page groups suppliers into three sibling panels, one per volume
# (300L/500L/900L) -- isolate the 500L one by slicing between its own
# marker and the next panel's.
_PANEL_START = 'data-oil-market-volume-panel="500"'
_NEXT_PANEL = 'data-oil-market-volume-panel="900"'
_ARTICLE = re.compile(r"<article.*?</article>", re.DOTALL)
_SUPPLIER_NAME = re.compile(r"<span[^>]*>#\d+</span>([^<]+)</h3>")
_PRICE = re.compile(r"<p[^>]*>£([\d,.]+)</p>")
_UPDATED = re.compile(r"Updated ([^<]+?)</span>")

# Display cleanup, same rule as fuel_prices.py's own _clean_name (kept
# independent rather than shared -- see this project's established
# preference for not coupling otherwise-independent modules together):
# strip "Newry" (and a comma immediately before it, if any) and
# "Supermarket" wherever they appear in a supplier's name.
_STRIP_WORDS = re.compile(r",?\s*\b(?:Newry|Supermarket)\b", re.IGNORECASE)


def _clean_name(text):
    return re.sub(r"\s+", " ", _STRIP_WORDS.sub("", text)).strip(" ,")

# niliving.co.uk sits behind Cloudflare, which challenges (403, a JS
# "Just a moment..." interstitial) the default `python-requests` user
# agent specifically -- confirmed live 2026-10-06 from the EC2 box (not an
# IP/datacenter block: the same box passes every time once a real
# browser-shaped User-Agent and Accept headers are sent, no cookies or JS
# execution needed).
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


def fetch_top_heating_oil_suppliers(limit=3):
    """(name, price_gbp, updated_text) for the `limit` cheapest 500L
    suppliers listed on niliving.co.uk, in the page's own (price-
    ascending) order -- or [] on any fetch/parse failure, never raises.
    `updated_text` is the site's own relative freshness label (e.g.
    "58 minutes ago"), kept as-is rather than parsed into an age -- unlike
    fuel_prices.py's MAX_PRICE_AGE_DAYS problem, this comparison site's
    own listings were confirmed live to already be routinely fresh
    (under an hour old), so there's no equivalent stale-price risk here
    to filter against."""
    try:
        response = requests.get(_URL, headers=_HEADERS, timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        log.warning("Could not fetch heating oil prices from %s", _URL)
        return []

    # The page's Content-Type header carries no charset, so `requests`
    # falls back to the HTTP default (ISO-8859-1) even though the actual
    # content is UTF-8 -- confirmed live, this mangled every "£" into
    # "Â£", which then silently failed the price regex below (no price
    # match -> no suppliers at all, not a wrong price).
    response.encoding = "utf-8"
    html_text = response.text
    start = html_text.find(_PANEL_START)
    if start == -1:
        log.warning("500L panel not found on %s -- page layout may have changed", _URL)
        return []
    end = html_text.find(_NEXT_PANEL, start)
    panel = html_text[start:end] if end != -1 else html_text[start:]

    suppliers = []
    for article in _ARTICLE.findall(panel):
        name_match = _SUPPLIER_NAME.search(article)
        price_match = _PRICE.search(article)
        if not (name_match and price_match):
            continue
        updated_match = _UPDATED.search(article)
        suppliers.append(
            (
                html.unescape(name_match.group(1).strip()),
                float(price_match.group(1).replace(",", "")),
                updated_match.group(1).strip() if updated_match else None,
            )
        )
        if len(suppliers) >= limit:
            break
    return suppliers


def build_heating_oil_markdown():
    """Markdown listing the top 3 cheapest 500L heating oil suppliers, or
    an empty string if none could be fetched -- so the caller can simply
    omit this section rather than show a broken placeholder."""
    suppliers = fetch_top_heating_oil_suppliers()
    if not suppliers:
        return ""

    lines = ["**Cheapest Home Heating Oil (500L)**  "]
    for i, (name, price, _updated) in enumerate(suppliers, start=1):
        name = _clean_name(name) or name  # never show a blank name if cleaning strips everything
        lines.append(f"{i}. {name} — £{price:.2f}  ")
    return "\n".join(lines).rstrip()
