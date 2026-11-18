import asyncio

import httpx

from app.sources import prophetx


def test_prophetx_parse(monkeypatch):
    monkeypatch.delenv("PROPHETX_QTY_DOLLARS", raising=False)
    monkeypatch.setattr(prophetx.settings, "PROPHETX_AFFILIATE_KEY", "k")
    monkeypatch.setattr(prophetx.settings, "PROPHETX_BASE_URL", "http://px.test/partner")

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "k"
        p = req.url.path
        if p.endswith("/affiliate/get_sport_events"):
            assert req.url.params["tournament_id"] == "132"
            return httpx.Response(200, json={"data": {"sport_events": [{
                "event_id": 101, "status": "scheduled", "scheduled": "2026-11-20T00:00:00Z",
                "competitors": [{"id": 1, "name": "Boston Celtics", "side": "home"},
                                {"id": 2, "name": "Philadelphia 76ers", "side": "away"}]}]}})
        if p.endswith("/v4/affiliate/get_multiple_markets"):
            return httpx.Response(200, json={"data": {"101": [{"id": 5, "name": "Moneyline", "type": "moneyline", "selections": [
                [{"name": "Boston Celtics", "competitor_id": 1, "price": -150, "quantity": 2000},
                 {"name": "Boston Celtics", "competitor_id": 1, "price": -155, "quantity": 500}],
                [{"name": "Philadelphia 76ers", "competitor_id": 2, "price": 130, "quantity": 900}]]}]}})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    g = asyncio.run(prophetx.fetch("nba", client))
    assert len(g) == 1 and g[0]["away"] == "PHI" and g[0]["home"] == "BOS"
    assert g[0]["liquidity"] == {"PHI": 900, "BOS": 2500}
    assert g[0]["prices"]["BOS"] == 0.608


def test_prophetx_needs_key(monkeypatch):
    import pytest
    monkeypatch.setattr(prophetx.settings, "PROPHETX_AFFILIATE_KEY", "")
    with pytest.raises(prophetx.NeedsSetup):
        asyncio.run(prophetx.fetch("nba"))
