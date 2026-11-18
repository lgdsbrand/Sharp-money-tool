"""Polymarket (optional, disabled by default): public gamma + CLOB endpoints, no key needed.

EXPERIMENTAL and untested live from the build sandbox. Per-side money is the resting bid size
(size x price) on each outcome token's order book.
"""
from __future__ import annotations

import json

import httpx

from ..config import settings
from ..teams import norm_team

TAGS = {"nba": "nba", "nhl": "nhl", "nfl": "nfl", "mlb": "mlb", "ncaaf": "cfb", "ncaab": "cbb"}


async def _book_money(client: httpx.AsyncClient, token_id: str) -> float:
    r = await client.get(f"{settings.POLYMARKET_CLOB_URL}/book", params={"token_id": token_id})
    r.raise_for_status()
    bids = r.json().get("bids", [])
    return sum(float(b["price"]) * float(b["size"]) for b in bids)


async def fetch(sport: str) -> list[dict]:
    tag = TAGS.get(sport)
    if not tag:
        return []
    games = []
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{settings.POLYMARKET_GAMMA_URL}/events",
            params={"tag_slug": tag, "active": "true", "closed": "false", "limit": 100},
        )
        r.raise_for_status()
        for ev in r.json():
            for m in ev.get("markets", []):
                outcomes = m.get("outcomes")
                tokens = m.get("clobTokenIds")
                if isinstance(outcomes, str):
                    outcomes = json.loads(outcomes)
                if isinstance(tokens, str):
                    tokens = json.loads(tokens)
                if not outcomes or not tokens or len(outcomes) != 2 or m.get("sportsMarketType", "moneyline") != "moneyline":
                    continue
                if {o.lower() for o in outcomes} & {"yes", "no", "over", "under"}:
                    continue
                try:
                    liq = [await _book_money(client, t) for t in tokens]
                except httpx.HTTPError:
                    continue
                a, h = norm_team(outcomes[0], sport), norm_team(outcomes[1], sport)
                prices = json.loads(m["outcomePrices"]) if isinstance(m.get("outcomePrices"), str) else m.get("outcomePrices")
                games.append({
                    "book": "polymarket", "away": a, "home": h, "start": m.get("gameStartTime") or ev.get("startDate"),
                    "liquidity": {a: round(liq[0]), h: round(liq[1])},
                    "prices": {a: float(prices[0]), h: float(prices[1])} if prices else {},
                })
    return games
