"""ProphetX adapter - reads the read-only MARKET DATA API (docs.prophetx.co "Market Data Integration").

  Base URL (production): https://cash.api.prophetx.co/partner      (sandbox: https://api.sandbox.prophetx.dev/partner)
  Auth: the Market Data key, sent raw in the `Authorization` header (no "Bearer").
  GET /affiliate/get_tournaments?has_active_events=true
  GET /affiliate/get_sport_events?tournament_id=<id>      -> competitors [{id,name,side home/away,abbreviation}], scheduled, status
  GET /v4/affiliate/get_multiple_markets?event_ids=1&event_ids=2 ... (max 50)  -> {"data": {"<event_id>": [market,...]}}
Each market has `selections`: a list (one per side) of lists of {name, competitor_id, price (American), quantity}.

ASSUMPTION to confirm once on real data: `quantity` is dollars available at that price. If the numbers look
wrong in `python -m app.diagnose`, set PROPHETX_QTY_DOLLARS=false to treat quantity as contracts at $1 x price.

A Trading API key (made under account Settings -> API Integration) is a different key and does NOT work here;
the Market Data key has to be requested from hello@prophetexchange.com.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

import httpx

from ..config import settings
from ..teams import is_known, norm_team


class NeedsSetup(Exception):
    pass


# Tournament ids from the docs; college leagues are found by name from get_tournaments.
_PRO_IDS = {"nfl": 31, "nhl": 234, "nba": 132, "mlb": 109}
_NAME_PAT = {
    "nfl": r"^NFL$", "nba": r"^NBA$", "nhl": r"^NHL$", "mlb": r"^MLB$",
    "ncaaf": r"NCAAF|COLLEGE.?FOOTBALL|NCAA.?FOOTBALL",
    "ncaab": r"NCAAB|NCAAM|COLLEGE.?BASKETBALL|NCAA.?(MEN.?S?.?)?BASKETBALL",
}
_NOT_STARTED = {"scheduled", "not_started", "upcoming", "pre_match", "prematch", "open", ""}
last_stats: dict = {}


def _base() -> str:
    return (settings.PROPHETX_BASE_URL or "https://cash.api.prophetx.co/partner").rstrip("/")


def american_to_prob(p: float) -> float | None:
    if not p:
        return None
    return 100 / (p + 100) if p > 0 else -p / (-p + 100)


async def _get(client: httpx.AsyncClient, path: str, params=None):
    r = await client.get(_base() + path, params=params,
                         headers={"Authorization": settings.PROPHETX_AFFILIATE_KEY, "Accept": "application/json"})
    if r.status_code == 401:
        raise NeedsSetup("ProphetX says the key is not valid for the Market Data API (401). A Trading key will not work - "
                         "email hello@prophetexchange.com for a Market Data key")
    if r.status_code == 429:
        raise RuntimeError("ProphetX rate limit (429) - will retry next refresh")
    r.raise_for_status()
    return r.json().get("data", {})


async def _tournament_ids(client, sport: str) -> list[int]:
    forced = os.getenv(f"PROPHETX_TOURNAMENT_{sport.upper()}")
    if forced:
        return [int(x) for x in forced.split(",") if x.strip()]
    if sport in _PRO_IDS:
        return [_PRO_IDS[sport]]
    data = await _get(client, "/affiliate/get_tournaments", {"has_active_events": "true"})
    pat = re.compile(_NAME_PAT[sport], re.I)
    return [t["id"] for t in data.get("tournaments", []) if pat.search(t.get("name", "") or "")]


def _qty_dollars(qty: float, price: float) -> float:
    if os.getenv("PROPHETX_QTY_DOLLARS", "true").lower() in ("0", "false", "no"):
        prob = american_to_prob(price) or 0
        return qty * prob
    return qty


def _flatten(markets) -> list[dict]:
    """get_multiple_markets normally returns {event_id: [markets]}; a flat list is possible too."""
    if isinstance(markets, dict):
        return [dict(m, event_id=int(k)) for k, v in markets.items() for m in (v or []) if str(k).isdigit()]
    return [m for m in (markets or []) if isinstance(m, dict)]


async def fetch(sport: str, client: httpx.AsyncClient | None = None) -> list[dict]:
    if not settings.PROPHETX_AFFILIATE_KEY:
        raise NeedsSetup("PROPHETX_AFFILIATE_KEY is empty - put the ProphetX Market Data key in .env")
    own = client is None
    client = client or httpx.AsyncClient(timeout=20, headers={"User-Agent": "sharp-money-tool/1.7"}, follow_redirects=True)
    st = last_stats[sport] = {"tournaments": [], "events": 0, "markets": 0, "kept": 0, "name_miss": [], "sample": None}
    try:
        tids = await _tournament_ids(client, sport)
        st["tournaments"] = tids
        events: dict[int, dict] = {}
        for tid in tids:
            data = await _get(client, "/affiliate/get_sport_events", {"tournament_id": tid})
            for e in data.get("sport_events", []):
                if str(e.get("status", "")).lower() in _NOT_STARTED and len(e.get("competitors", [])) == 2:
                    events[e["event_id"]] = e
        st["events"] = len(events)
        if not events:
            return []
        ids = list(events)
        mkts: list[dict] = []
        for i in range(0, len(ids), 50):
            chunk = ids[i:i + 50]
            data = await _get(client, "/v4/affiliate/get_multiple_markets",
                              [("event_ids", str(x)) for x in chunk] + [("market_types", "moneyline")])
            mkts += _flatten(data)
        st["markets"] = len(mkts)

        out = []
        for m in mkts:
            if (m.get("type") or m.get("sub_type") or "moneyline") not in ("moneyline", "sup_moneyline") and m.get("name", "").lower() != "moneyline":
                continue
            ev = events.get(m.get("event_id"))
            if not ev:
                continue
            comps = {c["id"]: c for c in ev["competitors"]}
            by_side = {c.get("side"): c for c in ev["competitors"]}
            away, home = by_side.get("away"), by_side.get("home")
            if not away or not home:
                continue
            if not all(is_known(c["name"], sport) for c in (away, home)):
                st["name_miss"].append([away["name"], home["name"]])
                continue
            liq, prices = {}, {}
            for group in m.get("selections", []):
                for s in (group if isinstance(group, list) else [group]):
                    comp = comps.get(s.get("competitor_id"))
                    if not comp:
                        name = (s.get("name") or "").lower()
                        comp = next((c for c in ev["competitors"] if c["name"].lower() in name or name in c["name"].lower()), None)
                    if not comp or not s.get("price"):
                        continue
                    t = norm_team(comp["name"], sport)
                    liq[t] = liq.get(t, 0) + _qty_dollars(float(s.get("quantity") or 0), float(s["price"]))
                    p = american_to_prob(float(s["price"]))
                    if p and (t not in prices or p > prices[t]):
                        prices[t] = round(p, 3)
            a, h = norm_team(away["name"], sport), norm_team(home["name"], sport)
            if a not in liq or h not in liq:
                continue
            if st["sample"] is None:
                st["sample"] = m.get("selections")
            start = None
            if ev.get("scheduled"):
                try:
                    start = datetime.fromisoformat(ev["scheduled"].replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
                except ValueError:
                    pass
            out.append({"book": "prophetx", "away": a, "home": h, "start": start,
                        "liquidity": {a: round(liq[a]), h: round(liq[h])}, "prices": prices})
        st["kept"] = len(out)
        return out
    finally:
        if own:
            await client.aclose()
