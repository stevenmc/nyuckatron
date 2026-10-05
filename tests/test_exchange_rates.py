from unittest.mock import MagicMock

import exchange_rates


def _mock_response(rate):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"amount": 1.0, "base": "GBP", "rates": {"EUR": rate}})
    return resp


def test_fetch_gbp_eur_rate_returns_the_rate_on_success(monkeypatch):
    monkeypatch.setattr(exchange_rates.requests, "get", MagicMock(return_value=_mock_response(1.18)))

    assert exchange_rates.fetch_gbp_eur_rate() == 1.18


def test_fetch_gbp_eur_rate_fails_open_on_request_exception(monkeypatch):
    import requests

    monkeypatch.setattr(exchange_rates.requests, "get", MagicMock(side_effect=requests.ConnectionError("down")))

    assert exchange_rates.fetch_gbp_eur_rate() is None  # doesn't raise


def test_fetch_gbp_eur_rate_fails_open_on_malformed_response(monkeypatch):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"unexpected": "shape"})
    monkeypatch.setattr(exchange_rates.requests, "get", MagicMock(return_value=resp))

    assert exchange_rates.fetch_gbp_eur_rate() is None  # doesn't raise


def test_build_exchange_rate_markdown_includes_both_directions_with_symbols():
    markdown = exchange_rates.build_exchange_rate_markdown(1.18)

    assert "£1 = €1.18" in markdown
    assert "€1 = £0.85" in markdown  # 1 / 1.18, rounded to 2dp


def test_build_exchange_rate_markdown_returns_empty_string_when_rate_is_none():
    assert exchange_rates.build_exchange_rate_markdown(None) == ""
