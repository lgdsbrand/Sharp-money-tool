"""The Odds API (the-odds-api.com): sportsbook prices incl. Pinnacle (bookmaker key `pinnacle`).

COST WARNING: the free plan is only 500 credits/month. Six sports refreshed all day will blow
through that, so the pipeline (a) only calls sports that are in season, (b) caches odds for
ODDS_REFRESH_SECONDS (default 1800), and (c) shows the remaining credits on the Profile page.
For a real launch use a paid plan or Tyler's own odds feed.
"""
from __future__ import annotations

import httpx

from ..config import settings
from ..teams import norm_team

SPORT_KEYS = {
    "nba": "basketball_nba", "nhl": "icehockey_nhl", "nfl": "americanfootball_nfl", "mlb": "baseball_mlb",
    "ncaaf": "americanfootball_ncaaf", "ncaab": "basketball_ncaab",
}
BASE = "https://api.the-odds-api.com/v4"
last_usage: dict = {}  # {"remaining": "...", "used": "..."} from response headers


def _track(r: httpx.Response) -> None:
    last_usage.update({
        "remaining": r.headers.get("x-requests-remaining"), "used": r.headers.get("x-requests-used"),
    })


async def in_season(sport: str) -> bool:
    """Free call (does not use credits): is this league currently in season?"""
    key = SPORT_KEYS.get(sport)
    if not key or not settings.ODDS_API_KEY:
        return False
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{BASE}/sports", params={"apiKey": settings.ODDS_API_KEY})
        r.raise_for_status()
    return any(s.get("key") == key and s.get("active") for s in r.json())


async def fetch(sport: str) -> list[dict]:
    key = SPORT_KEYS.get(sport)
    if not key or not settings.ODDS_API_KEY:
        return []
    # `bookmakers` instead of `regions`: a group of up to 10 bookmakers is billed like one region.
    params = {
        "apiKey": settings.ODDS_API_KEY, "markets": "h2h",
        "bookmakers": settings.ODDS_BOOKMAKERS, "oddsFormat": "decimal",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{BASE}/sports/{key}/odds", params=params)
        r.raise_for_status()
        _track(r)
        events = r.json()
    out = []
    for ev in events:
        away, home = norm_team(ev["away_team"], sport), norm_team(ev["home_team"], sport)
        odds = {}
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                if mk["key"] == "h2h":
                    odds[bk["key"]] = {norm_team(o["name"], sport): float(o["price"]) for o in mk["outcomes"] if o["name"] != "Draw" and float(o["price"]) > 1.0001}
        out.append({"away": away, "home": home, "start": ev.get("commence_time"), "odds": odds})
    return out


async def fetch_scores(sport: str) -> list[dict]:
    """Completed games (last 3 days) for auto-grading moneyline plays. Costs 2 credits."""
    key = SPORT_KEYS.get(sport)
    if not key or not settings.ODDS_API_KEY:
        return []
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{BASE}/sports/{key}/scores", params={"apiKey": settings.ODDS_API_KEY, "daysFrom": 3})
        r.raise_for_status()
        _track(r)
    out = []
    for ev in r.json():
        if not ev.get("completed") or not ev.get("scores"):
            continue
        scores = {norm_team(s["name"], sport): float(s["score"]) for s in ev["scores"]}
        if len(scores) == 2:
            out.append({"scores": scores})
    return out
