"""Novig adapter - reads the PUBLIC (no key needed) market data from Novig's v3 API.

Built from Novig's own OpenAPI file (docs.novig.com, "Novig API 3.0.0"):
  GET /v3/public/types/leagues                      -> canonical league names
  GET /v3/public/catalog/markets?league=&marketType=MONEY&eventStatus=OPEN_PREGAME   (paged: items + next)
  GET /v3/public/catalog/markets/{id}/book?depth=N  -> {"orders": {outcomeId: [{price, qty}, ...]}}
Prices are probabilities (0-1). Dollars resting on an outcome = sum(qty x price x 1 cent).
These reads need no key, so NOVIG_API_KEY_ID / private key are NOT used here (they are only for trading).

NOVIG_BASE_URL: the spec only lists the PAPER (play-money) server, https://api.paper.novig.com.
The real exchange address is not in that file; the default below is a best guess - confirm it on
docs.novig.com (Quickstart) and set NOVIG_BASE_URL if different. Paper data is NOT real money.

Showing Novig data to OTHER people on a public site may need Novig's written permission.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

import httpx

from ..config import settings
from ..teams import is_known, norm_team


class NeedsSetup(Exception):
    pass


_LEAGUE_PATTERNS = {
    "nfl": r"^NFL$", "nba": r"^NBA$", "nhl": r"^NHL$", "mlb": r"^MLB$",
    "ncaaf": r"NCAAF|COLLEGE.?FOOTBALL|NCAA.?FOOTBALL|^CFB$",
    "ncaab": r"NCAAB|NCAAM|COLLEGE.?BASKETBALL|NCAA.?(MEN.?S?.?)?BASKETBALL|^CBB$",
}
_MAX_MARKETS = 80
_BUDGET = 24.0   # seconds; return what we have rather than hit the 30s source timeout


def _base() -> str:
    return (settings.NOVIG_BASE_URL or "https://api.novig.com").rstrip("/")


def pick_leagues(sport: str, available: list[str]) -> list[str]:
    import os
    forced = os.getenv(f"NOVIG_LEAGUE_{sport.upper()}")
    if forced:
        return [x.strip() for x in forced.split(",") if x.strip()]
    pat = re.compile(_LEAGUE_PATTERNS[sport], re.I)
    return [x for x in available if pat.search(x)]


_PACE = 0.25          # seconds between requests (Novig throttles the public API per IP)
_lock = None
_last = 0.0
_leagues_cache: tuple[float, list[str]] = (0.0, [])
last_stats: dict = {}


async def _get(client: httpx.AsyncClient, path: str, params: dict | None = None):
    """One paced request; waits and retries when Novig says 429 (honours Retry-After)."""
    global _lock, _last
    import time
    if _lock is None:
        _lock = asyncio.Lock()
    for attempt in range(4):
        async with _lock:
            wait = _PACE - (time.monotonic() - _last)
            if wait > 0:
                await asyncio.sleep(wait)
            r = await client.get(_base() + path, params=params)
            _last = time.monotonic()
        if r.status_code == 429:
            try:
                delay = float(r.headers.get("Retry-After", "2"))
            except ValueError:
                delay = 2.0
            if attempt == 3 or delay > 20:
                raise RuntimeError(f"Novig rate limit (429), asked to wait {delay:.0f}s - will retry next refresh")
            await asyncio.sleep(delay + 0.2)
            continue
        r.raise_for_status()
        return r.json()


async def _leagues(client) -> list[str]:
    import time
    global _leagues_cache
    ts, val = _leagues_cache
    if val and time.time() - ts < 3600:
        return val
    val = await _get(client, "/v3/public/types/leagues")
    _leagues_cache = (time.time(), val)
    return val


async def _paged(client, path: str, params: dict, limit_pages: int = 6) -> list[dict]:
    items, after = [], None
    for _ in range(limit_pages):
        data = await _get(client, path, {**params, **({"after": after} if after else {})})
        items += data.get("items", [])
        after = data.get("next")
        if not after:
            break
    return items


_SPLIT = re.compile(r"\s*@\s*|\s+(?:at|vs\.?|v|versus)\s+", re.I)


def _sides(desc: str | None) -> tuple[str, str] | None:
    parts = _SPLIT.split(desc or "", maxsplit=1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 and all(parts) else None


def _abbr_matches(abbr: str, name: str) -> bool:
    a = re.sub(r"[^a-z0-9]", "", abbr.lower())
    n = re.sub(r"[^a-z0-9 ]", "", name.lower())
    if not a:
        return False
    flat = n.replace(" ", "")
    initials = "".join(w[0] for w in n.split())
    if initials.startswith(a) or flat.startswith(a):
        return True
    it = iter(flat)           # letters of the abbreviation appear in order inside the name
    return flat[:1] == a[:1] and all(ch in it for ch in a)


def _college_names(outs: list[dict], desc: str | None):
    """Novig names college teams by code (ORST); the description holds the full names."""
    sides = _sides(desc)
    if not sides:
        return None
    a0, a1 = (o.get("name", "") for o in outs)
    m00, m01 = _abbr_matches(a0, sides[0]), _abbr_matches(a0, sides[1])
    m10, m11 = _abbr_matches(a1, sides[0]), _abbr_matches(a1, sides[1])
    if m00 and not m01 or m11 and not m10 or (m00 and m11 and not (m01 and m10)):
        return sides
    if m01 and not m00 or m10 and not m11:
        return sides[1], sides[0]
    return sides  # can't tell: assume the description order (odds feed corrects the rest)


def dollars_on_outcome(orders: list[dict]) -> float:
    """Resting dollars: qty x price x 1 cent for every resting order on one outcome."""
    return sum(float(o["price"]) * int(o["qty"]) * 0.01 for o in orders)


async def fetch(sport: str, client: httpx.AsyncClient | None = None) -> list[dict]:
    own = client is None
    client = client or httpx.AsyncClient(timeout=20, headers={"User-Agent": "sharp-money-tool/1.5"})
    st = last_stats[sport] = {"leagues": [], "markets": 0, "open_2way": 0, "books": 0,
                              "name_miss": [], "kept": 0, "sample_outcomes": []}
    try:
        leagues = pick_leagues(sport, await _leagues(client))
        st["leagues"] = leagues
        if not leagues:
            return []  # Novig doesn't list this league (yet)
        base = {"league": ",".join(leagues), "marketType": "MONEY", "limit": 500}
        markets = await _paged(client, "/v3/public/catalog/markets", {**base, "eventStatus": "OPEN_PREGAME"})
        if not markets:  # filter may be too strict; try without the status filter
            markets = await _paged(client, "/v3/public/catalog/markets", base)
        st["markets"] = len(markets)
        if markets:
            st["sample_outcomes"] = [[o.get("name") for o in m.get("outcomes", [])] for m in markets[:3]]
            st["statuses"] = sorted({str(m.get("status")) for m in markets})
        markets = [m for m in markets if m.get("status") in ("OPEN", None) and len(m.get("outcomes", [])) == 2][:_MAX_MARKETS]
        st["open_2way"] = len(markets)

        ev_desc: dict = {}
        if sport in ("ncaaf", "ncaab") and markets:
            try:   # market descriptions are only codes (ORST); the EVENT description should hold the full names
                evs = await _paged(client, "/v3/public/catalog/events", {"league": ",".join(leagues), "status": "OPEN_PREGAME", "limit": 500})
                ev_desc = {e["eventId"]: e.get("description") for e in evs}
                st["sample_event_desc"] = list(ev_desc.values())[:3]
            except Exception as e:
                st["event_error"] = str(e)[:120]
        out = []
        import time as _t
        t0 = _t.monotonic()
        if markets:
            st["sample_desc"] = [m.get("description") for m in markets[:3]]
        for m in markets:
            if _t.monotonic() - t0 > _BUDGET:
                st["cut_short"] = True
                break
            outs = m["outcomes"]
            if sport in ("ncaaf", "ncaab"):
                names = _college_names(outs, ev_desc.get(m.get("eventId")) or m.get("description"))
                if not names:
                    st["name_miss"].append([o.get("name") for o in outs])
                    continue
                teams = [norm_team(n, sport) for n in names]
            else:
                if not all(is_known(o.get("name", ""), sport) for o in outs):
                    st["name_miss"].append([o.get("name") for o in outs])
                    continue
                teams = [norm_team(o.get("name", ""), sport) for o in outs]
            if teams[0] == teams[1]:
                continue
            try:
                b = await _get(client, f"/v3/public/catalog/markets/{m['marketId']}/book", {"depth": 20})
            except Exception as e:
                st["book_error"] = str(e)[:120]
                if "429" in str(e):
                    break  # throttled hard: return what we have, try again next refresh
                continue
            st["books"] += 1
            liq, prices = {}, {}
            orders = b.get("orders", {})

            def best_bid(oid):
                ps = [float(x["price"]) for x in orders.get(oid, []) if x.get("price") is not None]
                return max(ps) if ps else None
            for i, (o, t) in enumerate(zip(outs, teams)):
                liq[t] = round(dollars_on_outcome(orders.get(o["outcomeId"], [])))
                # price you could actually BUY this side at = 1 - the best resting bid on the opposite side
                # (falls back to the last traded price when the other side has no orders)
                opp = best_bid(outs[1 - i]["outcomeId"])
                p = (1 - opp) if opp else (o.get("last") or (o.get("bid") or {}).get("price"))
                if p:
                    prices[t] = round(float(p), 3)
            start = None
            if m.get("startsTs"):
                start = datetime.fromtimestamp(m["startsTs"] / 1000, timezone.utc).isoformat()
            # Novig doesn't say who is away/home on a MONEY market; the odds feed fixes the orientation later
            out.append({"book": "novig", "away": teams[0], "home": teams[1], "start": start,
                        "liquidity": liq, "prices": prices})
        st["kept"] = len(out)
        return out
    finally:
        if own:
            await client.aclose()
