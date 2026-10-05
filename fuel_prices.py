"""Fetches the cheapest fresh (<=MAX_PRICE_AGE_DAYS old) petrol and diesel
price within the Newry area, for display in the sidebar events widget --
see events_widget.py and bot.run_events().

Uses fuelcosts.co.uk's free, unauthenticated API
(https://fuelcosts.co.uk/docs) -- itself built on the UK Government's
Fuel Finder open data scheme (The Motor Fuel Price (Open Data)
Regulations 2025), real per-forecourt prices reported by retailers
directly, not a scrape of a comparison site.

MAX_PRICE_AGE_DAYS exists because the API's own price-sort doesn't
account for freshness: confirmed live 2026-10-06, the cheapest-by-price
station for a given fuel type near Newry was routinely weeks stale (one
real example over 6 months old) while several genuinely fresh, only
slightly pricier stations sat further down the same price-sorted list.
Presenting the raw cheapest price without this filter would regularly
show a reader an outdated price as if it were current.
"""

import logging
from datetime import datetime, timezone

import requests

log = logging.getLogger("newry-bot")

_API_URL = "https://fuelcosts.co.uk/api/stations"

# Same coordinate pair already used for the (unrelated, externally
# managed) Weather sidebar widget -- see README's "Subreddit sidebar"
# section.
_NEWRY_LAT = 54.175102
_NEWRY_LON = -6.34023
_RADIUS_MILES = 10  # covers the catchment towns in config.CATCHMENT_PLACES without pulling in unrelated towns

MAX_PRICE_AGE_DAYS = 3

_FUEL_TYPES = (("Petrol", "E10"), ("Diesel", "B7_STANDARD"))


def _fetch_stations(fuel_code):
    response = requests.get(
        _API_URL,
        params={
            "lat": _NEWRY_LAT,
            "lon": _NEWRY_LON,
            "radius": _RADIUS_MILES,
            "fuel": fuel_code,
            "sort": "price",
            "perPage": 50,
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json().get("stations", [])


def fetch_cheapest_fuel_price(fuel_code):
    """(price_pence_per_litre, station_name, town, age_days) for the
    cheapest station within _RADIUS_MILES of Newry whose `fuel_code`
    price (e.g. "E10", "B7_STANDARD") was updated within
    MAX_PRICE_AGE_DAYS -- or None if the fetch fails, or nothing in range
    has a recent enough price for that fuel. Never raises -- same
    fail-open contract as exchange_rates.fetch_gbp_eur_rate."""
    try:
        stations = _fetch_stations(fuel_code)
    except Exception:
        log.exception("Failed to fetch fuel prices for %s", fuel_code)
        return None

    now = datetime.now(timezone.utc)
    best = None
    for station in stations:
        price_entry = next(
            (p for p in station.get("prices", []) if p.get("fuel_type") == fuel_code), None
        )
        if price_entry is None or price_entry.get("price") is None:
            continue
        try:
            updated = datetime.fromisoformat(price_entry["price_last_updated"].replace("Z", "+00:00"))
        except (KeyError, ValueError, TypeError):
            continue
        age_days = (now - updated).total_seconds() / 86400
        if age_days > MAX_PRICE_AGE_DAYS:
            continue
        price = price_entry["price"]
        if best is None or price < best[0]:
            town = (station.get("address") or {}).get("town", "")
            best = (price, station.get("name") or "Unknown", town, age_days)
    return best


def build_fuel_price_markdown():
    """Markdown for the cheapest fresh petrol and diesel price found in
    the Newry area -- a fuel type with no recent-enough price anywhere in
    range is simply omitted, not shown as a broken placeholder. Returns
    an empty string if neither fuel type has one."""
    lines = []
    for label, fuel_code in _FUEL_TYPES:
        result = fetch_cheapest_fuel_price(fuel_code)
        if result is None:
            continue
        price, station, town, _age_days = result
        location = f"{station}, {town}" if town else station
        lines.append(f"{label}: {price:.1f}p/L at {location}  ")

    if not lines:
        return ""
    return "**Cheapest Fuel (Newry area)**  \n" + "\n".join(lines).rstrip()
