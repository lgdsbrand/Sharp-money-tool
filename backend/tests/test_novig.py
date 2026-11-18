import asyncio
import json

import httpx

from app.sources import novig


def _fake():
    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/v3/public/types/leagues":
            return httpx.Response(200, json=["NFL", "NBA", "COLLEGE_FOOTBALL"])
        if p == "/v3/public/catalog/markets":
            assert req.url.params["league"] == "NBA" and req.url.params["marketType"] == "MONEY"
            return httpx.Response(200, json={"items": [{
                "marketId": "m1", "eventId": "e1", "marketType": "MONEY", "status": "OPEN", "startsTs": 1795000000000,
                "outcomes": [{"outcomeId": "o1", "name": "Boston Celtics", "last": "0.700"},
                             {"outcomeId": "o2", "name": "Philadelphia 76ers", "bid": {"price": "0.300", "qty": 5}}]}]})
        if p == "/v3/public/catalog/markets/m1/book":
            return httpx.Response(200, json={"marketId": "m1", "seq": 1, "orders": {
                "o1": [{"orderId": "a", "price": "0.700", "qty": 100000}, {"orderId": "b", "price": "0.650", "qty": 20000}],
                "o2": [{"orderId": "c", "price": "0.300", "qty": 50000}]}})
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="")


def test_novig_public_parse(monkeypatch):
    monkeypatch.setattr(novig.settings, "NOVIG_BASE_URL", "http://novig.test")
    client = httpx.AsyncClient(transport=_fake()._transport)
    g = asyncio.run(novig.fetch("nba", client))
    assert len(g) == 1 and g[0]["book"] == "novig"
    assert g[0]["liquidity"] == {"BOS": 830, "PHI": 150}  # 100000*.7*.01 + 20000*.65*.01 ; 50000*.3*.01
    assert g[0]["prices"] == {"BOS": 0.7, "PHI": 0.3}


def test_novig_unknown_league_returns_empty(monkeypatch):
    monkeypatch.setattr(novig.settings, "NOVIG_BASE_URL", "http://novig.test")
    client = httpx.AsyncClient(transport=_fake()._transport)
    assert asyncio.run(novig.fetch("nhl", client)) == []
    assert novig.pick_leagues("ncaaf", ["NFL", "COLLEGE_FOOTBALL"]) == ["COLLEGE_FOOTBALL"]
