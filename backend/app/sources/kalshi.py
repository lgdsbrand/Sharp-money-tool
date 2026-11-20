"""Kalshi: public market-data endpoints (no API key needed for reads).

NOTE: written from Kalshi's public API docs but NOT tested against the live API from the build
sandbox (outbound access was blocked). Field names are parsed defensively. If Kalshi changes a
field the adapter degrades to 0 instead of crashing - check the Profile page (data sources) after
your first live run. Series tickers for college sports are best guesses: override them with
KALSHI_SERIES_NCAAF / KALSHI_SERIES_NCAAB if the page shows 0 games.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict

import httpx

from ..config import settings
from ..teams import norm_team

SERIES = {
    "nba": "KXNBAGAME", "nhl": "KXNHLGAME", "nfl": "KXNFLGAME", "mlb": "KXMLBGAME",
    "ncaaf": "KXNCAAFGAME", "ncaab": "KXNCAAMBGAME",
}
ALT_SERIES = {
    "nba": ("KXNBASPREAD", "KXNBATOTAL"), "nhl": ("KXNHLSPREAD", "KXNHLTOTAL"), "nfl": ("KXNFLSPREAD", "KXNFLTOTAL"),
    "mlb": ("KXMLBSPREAD", "KXMLBTOTAL"), "ncaaf": ("KXNCAAFSPREAD", "KXNCAAFTOTAL"),
    "ncaab": ("KXNCAAMBSPREAD", "KXNCAAMBTOTAL"),
}
# event-ticker suffix (e.g. 26NOV19LALDEN) -> (away, home, start); filled by fetch() so spread/total events can be matched
_EVENTS: dict[str, dict] = {}
last_alt_stats: dict = {}
_SPREAD_RE = re.compile(r"^(?P<team>.+?)\s+wins by over\s+(?P<n>[\d.]+)", re.I)
_TOTAL_RE = re.compile(r"(?:over|more than)\s+(?P<n>[\d.]+)", re.I)
_TITLE = re.compile(r"^(?P<away>.+?)\s+(?:at|@|vs\.?)\s+(?P<home>.+?)(?:\s+Winner)?\??$", re.I)


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _price(m: dict) -> float:
    """Contract price in dollars (0-1), preferring the dollar fields."""
    for k in ("last_price_dollars", "yes_bid_dollars", "yes_ask_dollars"):
        if _f(m.get(k)) > 0:
            return _f(m[k])
    for k in ("last_price", "yes_bid", "yes_ask"):
        if _f(m.get(k)) > 0:
            return _f(m[k]) / 100
    return 0.0


def money(m: dict, metric: str) -> float:
    """Dollars on one side of a game. Contracts pay $1, so contracts x price ~= dollars."""
    if metric == "liquidity":
        if m.get("liquidity_dollars") is not None:
            return _f(m["liquidity_dollars"])
        return _f(m.get("liquidity")) / 100 if m.get("liquidity") is not None else 0.0
    metric = metric if metric in ("volume", "open_interest") else "volume"
    # Kalshi has been moving numeric fields to "<name>_fp" strings; accept either spelling.
    contracts = _f(m.get(metric)) or _f(m.get(metric + "_fp"))
    return contracts * (_price(m) or 0.5)


def _approx_start(m: dict):
    """Kalshi only gives when the market is expected to settle (about 3h after kick-off), so
    start ~= that minus 3 hours. The odds feed has the exact time and wins when both exist."""
    from datetime import datetime, timedelta
    t = m.get("expected_expiration_time") or m.get("close_time")
    try:
        return (datetime.fromisoformat(t.replace("Z", "+00:00")) - timedelta(hours=3)).isoformat()
    except Exception:
        return None


async def fetch(sport: str) -> list[dict]:
    series = os.getenv(f"KALSHI_SERIES_{sport.upper()}") or SERIES.get(sport)
    if not series:
        return []
    markets, cursor = [], None
    async with httpx.AsyncClient(timeout=20) as client:
        for _ in range(10):  # pagination guard
            params = {"series_ticker": series, "status": "open", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            r = await client.get(f"{settings.KALSHI_BASE_URL}/markets", params=params)
            r.raise_for_status()
            data = r.json()
            markets += data.get("markets", [])
            cursor = data.get("cursor")
            if not cursor:
                break

    by_event: dict[str, list[dict]] = defaultdict(list)
    for m in markets:
        by_event[m.get("event_ticker", m.get("ticker", ""))].append(m)

    games = []
    for event, ms in by_event.items():
        if len(ms) < 2:
            continue
        teams: dict[str, dict] = {}
        for m in ms:
            suffix = m.get("ticker", "").rsplit("-", 1)[-1]
            label = m.get("yes_sub_title") or suffix
            teams[norm_team(label, sport)] = m
        if len(teams) != 2:
            continue
        away, home = list(teams)
        t = _TITLE.match(ms[0].get("title", "") or "")
        if t and norm_team(t["away"], sport) in teams and norm_team(t["home"], sport) in teams:
            away, home = norm_team(t["away"], sport), norm_team(t["home"], sport)
        _EVENTS[event.split("-", 1)[-1]] = {"away": away, "home": home, "start": _approx_start(ms[0])}
        games.append({
            "book": "kalshi", "away": away, "home": home,
            "start": _approx_start(ms[0]),
            "liquidity": {c: round(money(m, settings.KALSHI_METRIC)) for c, m in teams.items()},
            "prices": {c: round(_price(m), 3) for c, m in teams.items() if _price(m)},
        })
    return games


def _yes_no_dollars(m: dict, metric: str) -> tuple[float, float]:
    """Dollars on YES and on NO for one contract market (YES costs p, NO costs 1-p per $1 contract)."""
    p = _price(m) or 0.5
    if metric == "liquidity":
        y = money(m, metric)
        return y, y
    contracts = money(m, metric) / p if p else 0.0
    return contracts * p, contracts * (1 - p)


async def fetch_alts(sport: str) -> list[dict]:
    """Spread and total markets. Same shape the pipeline uses for every source:
    {book, away, home, start, mtype, liquidity, and team_line=(team, handicap) for spreads / line for totals}.
    NOTE: series names and wording are best guesses - run `python -m app.diagnose` to see the raw samples."""
    pair = os.getenv(f"KALSHI_ALT_SERIES_{sport.upper()}")
    spread_s, total_s = (pair.split(",") if pair else ALT_SERIES.get(sport, (None, None)))
    st = last_alt_stats[sport] = {"spread_markets": 0, "total_markets": 0, "unmatched": 0, "kept": 0, "sample": []}
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=20) as client:
        for mtype, series in (("spread", spread_s), ("total", total_s)):
            if not series:
                continue
            markets, cursor = [], None
            for _ in range(10):
                params = {"series_ticker": series, "status": "open", "limit": 200}
                if cursor:
                    params["cursor"] = cursor
                r = await client.get(f"{settings.KALSHI_BASE_URL}/markets", params=params)
                if r.status_code == 404:
                    break
                r.raise_for_status()
                data = r.json()
                markets += data.get("markets", [])
                cursor = data.get("cursor")
                if not cursor:
                    break
            st[f"{mtype}_markets"] = len(markets)
            if markets and len(st["sample"]) < 4:
                m0 = markets[0]
                st["sample"].append({k: m0.get(k) for k in ("ticker", "title", "yes_sub_title", "floor_strike", "cap_strike", "strike_type")})
            for m in markets:
                ev = _EVENTS.get(m.get("event_ticker", "").split("-", 1)[-1])
                if not ev:
                    st["unmatched"] += 1
                    continue
                sub = m.get("yes_sub_title") or m.get("title") or ""
                y, n = _yes_no_dollars(m, settings.KALSHI_METRIC)
                rec = {"book": "kalshi", "away": ev["away"], "home": ev["home"], "start": ev["start"], "mtype": mtype}
                if mtype == "spread":
                    t = _SPREAD_RE.match(sub)
                    if not t:
                        continue
                    team = norm_team(t["team"], sport)
                    if team not in (ev["away"], ev["home"]):
                        continue
                    n_pts = _f(m.get("floor_strike")) or float(t["n"])
                    opp = ev["home"] if team == ev["away"] else ev["away"]
                    rec.update(team_line=(team, -n_pts), liquidity={team: round(y), opp: round(n)})
                else:
                    t = _TOTAL_RE.search(sub)
                    line = _f(m.get("floor_strike")) or (float(t["n"]) if t else 0.0)
                    if not line:
                        continue
                    rec.update(line=line, liquidity={"OVER": round(y), "UNDER": round(n)})
                out.append(rec)
    st["kept"] = len(out)
    return out
