from unittest.mock import MagicMock

import heating_oil

# Real markup structure from niliving.co.uk/oil, verified live 2026-10-06
# (trimmed to the parts that matter -- the full page is ~210KB). The site
# groups suppliers into three sibling panels, one per volume; only the
# 500L one should ever be read.
def _article(rank, name, price, updated="5 minutes ago"):
    return (
        f'<article style="order:0"><div><div class="flex">'
        f'<span>verified</span><span>Updated {updated}</span></div>'
        f'<h3><span>#{rank}</span>{name}</h3>'
        f'<div><a>phone</a></div></div>'
        f'<div><p>£{price}</p><p>p/litre</p></div></article>'
    )


def _page(panel_500_articles, panel_300_articles=(), panel_900_articles=()):
    panel_300 = "".join(panel_300_articles)
    panel_500 = "".join(panel_500_articles)
    panel_900 = "".join(panel_900_articles)
    return (
        f'<div data-oil-market-volume-panel="300">{panel_300}</div>'
        f'<div data-oil-market-volume-panel="500">{panel_500}</div>'
        f'<div data-oil-market-volume-panel="900">{panel_900}</div>'
    )


def _mock_response(html_text):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.text = html_text
    resp.encoding = "ISO-8859-1"  # the real site's actual (wrong) default, see fetch's own fix
    return resp


def test_fetch_top_heating_oil_suppliers_reads_only_the_500l_panel(monkeypatch):
    page = _page(
        panel_300_articles=[_article(1, "Should Not Appear (300L)", 319.99)],
        panel_500_articles=[
            _article(1, "Portadown Oil Supplies", 525),
            _article(1, "New City Fuels", 525),
            _article(1, "Alfa Oils", 525),
        ],
        panel_900_articles=[_article(1, "Should Not Appear (900L)", 939)],
    )
    monkeypatch.setattr(heating_oil.requests, "get", MagicMock(return_value=_mock_response(page)))

    suppliers = heating_oil.fetch_top_heating_oil_suppliers()

    names = [name for name, price, updated in suppliers]
    assert names == ["Portadown Oil Supplies", "New City Fuels", "Alfa Oils"]
    assert "Should Not Appear (300L)" not in names
    assert "Should Not Appear (900L)" not in names


def test_fetch_top_heating_oil_suppliers_respects_the_limit(monkeypatch):
    page = _page(panel_500_articles=[_article(1, f"Supplier {i}", 500 + i) for i in range(6)])
    monkeypatch.setattr(heating_oil.requests, "get", MagicMock(return_value=_mock_response(page)))

    suppliers = heating_oil.fetch_top_heating_oil_suppliers(limit=3)

    assert len(suppliers) == 3


def test_fetch_top_heating_oil_suppliers_fixes_the_sites_own_encoding_mismatch(monkeypatch):
    # Regression test for a real bug (2026-10-06): niliving.co.uk's
    # Content-Type header carries no charset, so `requests` defaults to
    # ISO-8859-1 even though the page is actually UTF-8 -- every "£"
    # silently became "Â£", which matched no price at all (empty
    # results, not a wrong price -- the failure mode was total silence).
    page = _page(panel_500_articles=[_article(1, "Alfa Oils", 525)])
    response = _mock_response(page)
    monkeypatch.setattr(heating_oil.requests, "get", MagicMock(return_value=response))

    heating_oil.fetch_top_heating_oil_suppliers()

    assert response.encoding == "utf-8"  # set explicitly before reading .text


def test_fetch_top_heating_oil_suppliers_fails_open_on_request_exception(monkeypatch):
    import requests

    monkeypatch.setattr(heating_oil.requests, "get", MagicMock(side_effect=requests.ConnectionError("down")))

    assert heating_oil.fetch_top_heating_oil_suppliers() == []  # doesn't raise


def test_fetch_top_heating_oil_suppliers_fails_open_when_500l_panel_is_missing(monkeypatch):
    monkeypatch.setattr(
        heating_oil.requests, "get", MagicMock(return_value=_mock_response("<html>page layout changed</html>"))
    )

    assert heating_oil.fetch_top_heating_oil_suppliers() == []


def test_build_heating_oil_markdown_lists_suppliers_in_order(monkeypatch):
    page = _page(
        panel_500_articles=[
            _article(1, "Portadown Oil Supplies", 525),
            _article(1, "New City Fuels", 525),
        ]
    )
    monkeypatch.setattr(heating_oil.requests, "get", MagicMock(return_value=_mock_response(page)))

    markdown = heating_oil.build_heating_oil_markdown()

    assert "**Cheapest Home Heating Oil (500L)**" in markdown
    assert markdown.index("1. Portadown Oil Supplies — £525.00") < markdown.index("2. New City Fuels — £525.00")


def test_build_heating_oil_markdown_returns_empty_string_when_nothing_fetched(monkeypatch):
    monkeypatch.setattr(heating_oil.requests, "get", MagicMock(return_value=_mock_response("<html></html>")))

    assert heating_oil.build_heating_oil_markdown() == ""
