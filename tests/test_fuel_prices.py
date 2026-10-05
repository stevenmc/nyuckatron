from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import fuel_prices


def _station(name, fuel_type, price, age_days, town="Newry"):
    updated = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat().replace("+00:00", "Z")
    return {
        "name": name,
        "address": {"town": town},
        "prices": [{"fuel_type": fuel_type, "price": price, "price_last_updated": updated}],
    }


def _mock_response(stations):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"stations": stations})
    return resp


def test_fetch_cheapest_fuel_price_picks_the_cheapest_fresh_station(monkeypatch):
    stations = [
        _station("Cheap But Stale", "E10", 150.0, age_days=10),  # cheaper, but too old
        _station("Fresh And Reasonable", "E10", 163.9, age_days=0),
        _station("Also Fresh But Pricier", "E10", 170.0, age_days=1),
    ]
    monkeypatch.setattr(fuel_prices, "_fetch_stations", lambda fuel_code: stations)

    result = fuel_prices.fetch_cheapest_fuel_price("E10")

    price, name, town, age_days = result
    assert name == "Fresh And Reasonable"
    assert price == 163.9


def test_fetch_cheapest_fuel_price_ignores_prices_older_than_max_age(monkeypatch):
    # Regression case for a real finding (2026-10-06): the API's own
    # price-sort doesn't filter by freshness, so the cheapest-by-price
    # station is routinely weeks stale while fresher, pricier ones sit
    # further down the list.
    stations = [_station("Only Station", "E10", 150.0, age_days=fuel_prices.MAX_PRICE_AGE_DAYS + 1)]
    monkeypatch.setattr(fuel_prices, "_fetch_stations", lambda fuel_code: stations)

    assert fuel_prices.fetch_cheapest_fuel_price("E10") is None


def test_fetch_cheapest_fuel_price_accepts_a_price_just_inside_the_age_limit(monkeypatch):
    # Not exactly MAX_PRICE_AGE_DAYS -- real wall-clock time elapses
    # between building this fixture and fetch_cheapest_fuel_price's own
    # datetime.now() call, which would push an exact boundary value
    # fractionally over the limit and make this test flaky.
    stations = [_station("Just Fresh Enough", "E10", 160.0, age_days=fuel_prices.MAX_PRICE_AGE_DAYS - 0.01)]
    monkeypatch.setattr(fuel_prices, "_fetch_stations", lambda fuel_code: stations)

    result = fuel_prices.fetch_cheapest_fuel_price("E10")

    assert result is not None
    assert result[1] == "Just Fresh Enough"


def test_fetch_cheapest_fuel_price_ignores_stations_without_the_requested_fuel(monkeypatch):
    stations = [_station("Diesel Only Station", "B7_STANDARD", 190.0, age_days=0)]
    monkeypatch.setattr(fuel_prices, "_fetch_stations", lambda fuel_code: stations)

    assert fuel_prices.fetch_cheapest_fuel_price("E10") is None


def test_fetch_cheapest_fuel_price_returns_none_when_nothing_in_range(monkeypatch):
    monkeypatch.setattr(fuel_prices, "_fetch_stations", lambda fuel_code: [])

    assert fuel_prices.fetch_cheapest_fuel_price("E10") is None


def test_fetch_cheapest_fuel_price_fails_open_on_request_exception(monkeypatch):
    def _raise(fuel_code):
        raise Exception("network is down")

    monkeypatch.setattr(fuel_prices, "_fetch_stations", _raise)

    assert fuel_prices.fetch_cheapest_fuel_price("E10") is None  # doesn't raise


def test_build_fuel_price_markdown_includes_both_fuels(monkeypatch):
    def fake_fetch(fuel_code):
        if fuel_code == "E10":
            return (163.9, "Fiveways Supermarket", "Newry", 0.1)
        return (189.9, "Go Cloughoge", "Newry", 1.2)

    monkeypatch.setattr(fuel_prices, "fetch_cheapest_fuel_price", fake_fetch)

    markdown = fuel_prices.build_fuel_price_markdown()

    assert "**Cheapest Fuel (Newry area)**" in markdown
    assert "Petrol: 163.9p/L at Fiveways Supermarket, Newry" in markdown
    assert "Diesel: 189.9p/L at Go Cloughoge, Newry" in markdown


def test_build_fuel_price_markdown_omits_a_fuel_with_no_fresh_price(monkeypatch):
    def fake_fetch(fuel_code):
        return (163.9, "Fiveways Supermarket", "Newry", 0.1) if fuel_code == "E10" else None

    monkeypatch.setattr(fuel_prices, "fetch_cheapest_fuel_price", fake_fetch)

    markdown = fuel_prices.build_fuel_price_markdown()

    assert "Petrol" in markdown
    assert "Diesel" not in markdown


def test_build_fuel_price_markdown_returns_empty_string_when_neither_fuel_available(monkeypatch):
    monkeypatch.setattr(fuel_prices, "fetch_cheapest_fuel_price", lambda fuel_code: None)

    assert fuel_prices.build_fuel_price_markdown() == ""
