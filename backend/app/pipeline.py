"""Refresh pipeline: pull every source, merge by game, attach model picks, save the snapshot,
auto-log tonight's heavy sharp plays and grade finished ones."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

from .analysis import best_prices, build_game_view
from .config import settings
from .sources import demo, dk_splits, kalshi, novig, odds_api, polymarket, prophetx
from .store import grade
from .teams import canon, match_pair, norm_team, same_team

_odds_cache: dict[str, tuple[float, list]] = {}


import re as _re
_SECRET = _re.compile(r"(api[_-]?key|token|apikey)=[^&\s'\"]+", _re.I)


def scrub(text):
    """Never show an API key in an error message (the URL of a failed request contains it)."""
    return _SECRET.sub(r"\1=***", text) if isinstance(text, str) else text


def _status(name, mode, ok, games=0, error=None, note=None):
    error, note = scrub(error), scrub(note)
    return {"name": name, "mode": mode, "ok": ok, "games": games, "error": error, "note": note}


_SRC_CACHE: dict = {}
# Slow-moving or throttled feeds are not re-fetched on every 2-minute refresh (avoids 429 / blocks).
_TTL = {"novig": 300, "dk_splits": 300, "prophetx": 120, "novig_alts": 300, "prophetx_alts": 120, "kalshi_alts": 120}
_TIMEOUT = {"novig": 90, "dk_splits": 90, "prophetx": 60, "novig_alts": 75, "prophetx_alts": 45, "kalshi_alts": 45}


async def _run(name: str, factory, statuses: dict, sport: str = ""):
    """factory: zero-argument function returning the coroutine (so a cached answer never starts a request)."""
    import time
    key = (name, sport)
    ttl = _TTL.get(name, 0)
    hit = _SRC_CACHE.get(key)
    if ttl and hit and time.time() - hit[0] < ttl:
        statuses[name] = _status(name, "live", True, len(hit[1]))
        return hit[1]
    try:
        res = await asyncio.wait_for(factory(), _TIMEOUT.get(name, settings.SOURCE_TIMEOUT))
        _SRC_CACHE[key] = (time.time(), res)
        statuses[name] = _status(name, "live", True, len(res))
        return res
    except asyncio.TimeoutError:
        err, mode = f"timed out after {_TIMEOUT.get(name, settings.SOURCE_TIMEOUT):.0f}s", "live"
    except (prophetx.NeedsSetup, novig.NeedsSetup) as e:
        statuses[name] = _status(name, "needs_setup", False, 0, str(e))
        return []
    except Exception as e:  # one broken source must never take the whole dashboard down
        err, mode = f"{type(e).__name__}: {e}", "live"
    if hit and time.time() - hit[0] < 1800:   # keep showing the last good data for up to 30 min
        statuses[name] = _status(name, mode, True, len(hit[1]), None, f"showing data from {int((time.time()-hit[0])/60)} min ago ({err})")
        return hit[1]
    statuses[name] = _status(name, mode, False, 0, err)
    return []


def attach_alts(slate, recs: list[dict]) -> int:
    """Merge spread / total records from any source into g["alts"]["spread:-3.5"] = {mtype, line (home handicap or
    points), liquidity: {book: {side: $}}}. Spread sides are team codes; total sides are OVER / UNDER."""
    n = 0
    for r in recs:
        g = slate.get(r["away"], r["home"], r.get("start"), create=False)
        if not g:
            continue
        if r["mtype"] == "spread":
            if r.get("home_line") is not None:
                line = float(r["home_line"])
            elif r.get("team_line"):
                team, tl_ = r["team_line"]
                line = float(tl_) if canon(team, g["away"], g["home"], slate.sport) == g["home"] else -float(tl_)
            else:
                continue
            liq = slate.relabel(g, r["liquidity"])
        else:
            line, liq = float(r["line"]), dict(r["liquidity"])
        e = g.setdefault("alts", {}).setdefault(f"{r['mtype']}:{line:g}", {"mtype": r["mtype"], "line": line, "liquidity": {}})
        slot = e["liquidity"].setdefault(r["book"], {})
        for k, v in liq.items():
            slot[k] = slot.get(k, 0) + v
        n += 1
    return n


class Slate:
    """Games for one sport, matched across feeds even when each feed spells teams differently."""

    def __init__(self, sport: str):
        self.sport = sport
        self.games: list[dict] = []

    def find(self, away: str, home: str) -> Optional[dict]:
        return next((g for g in self.games if match_pair(g["away"], g["home"], away, home, self.sport)), None)

    def get(self, away: str, home: str, start=None, create: bool = True) -> Optional[dict]:
        g = self.find(away, home)
        if g is None and create:
            g = {"key": f"{min(away, home)} v {max(away, home)}", "sport": self.sport, "away": away, "home": home,
                 "start": start, "liquidity": {}, "prices": {}, "public": {}, "odds": {}}
            self.games.append(g)
        if g is not None and start and not g.get("start"):
            g["start"] = start
        return g

    def relabel(self, g: dict, d: dict) -> dict:
        return {canon(k, g["away"], g["home"], self.sport): v for k, v in d.items()}


async def _cached_odds(sport: str, store, statuses: dict) -> list[dict]:
    """Odds cost API credits, so reuse them for ODDS_REFRESH_SECONDS and skip out-of-season leagues."""
    hit = _odds_cache.get(sport)
    if not hit:   # survive server restarts: every restart used to re-spend ~20 credits
        try:
            saved = await store.kv_get(f"odds_cache:{sport}")
            if saved and saved.get("res") is not None:
                hit = _odds_cache[sport] = (saved["ts"], saved["res"])
        except Exception:
            hit = None
    if hit and time.time() - hit[0] < settings.ODDS_REFRESH_SECONDS:
        statuses["odds_api"] = _status("odds_api", "cached", True, len(hit[1]), note=_credits_note())
        return hit[1]
    try:
        if not await odds_api.in_season(sport):
            statuses["odds_api"] = _status("odds_api", "live", True, 0, note=f"{sport.upper()} is out of season")
            _odds_cache[sport] = (time.time(), [])
            return []
    except Exception:
        pass  # if the free season check fails, still try the odds call
    res = await _run("odds_api", lambda: odds_api.fetch(sport), statuses, sport)
    if statuses["odds_api"]["ok"]:
        statuses["odds_api"]["note"] = _credits_note()
        _odds_cache[sport] = (time.time(), res)
        try:
            await store.kv_set(f"odds_cache:{sport}", {"ts": time.time(), "res": res})
        except Exception:
            pass
    return res


def _credits_note() -> Optional[str]:
    u = odds_api.last_usage
    return f"Odds API credits left: {u['remaining']}" if u.get("remaining") is not None else None


async def _model_picks(sport: str, store) -> list[dict]:
    picks = list(await store.kv_get(f"model_picks:{sport}") or [])
    if settings.MODEL_PICKS_URL:
        try:
            headers = {"Authorization": f"Bearer {settings.MODEL_PICKS_TOKEN}"} if settings.MODEL_PICKS_TOKEN else {}
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(settings.MODEL_PICKS_URL, params={"sport": sport}, headers=headers)
                r.raise_for_status()
                data = r.json()
                picks += data.get("picks", data) if isinstance(data, dict) else data
        except Exception:
            pass
    return picks


async def refresh(sport: str, store) -> dict:
    statuses: dict = {}
    slate = Slate(sport)

    if settings.DEMO_MODE:
        slate.games = demo.games(sport)
        mode = "demo"
        for n in ("kalshi", "novig", "prophetx", "odds_api", "dk_splits"):
            statuses[n] = _status(n, "demo", True, len(slate.games))
        for g in slate.games:
            g["alts"] = demo.alts(g)
        model = demo.model_picks(sport)
    else:
        mode = "live"
        tasks = {}
        if settings.ODDS_API_KEY:
            tasks["odds_api"] = _cached_odds(sport, store, statuses)
        if settings.DK_SPLITS_ENABLED:
            tasks["dk_splits"] = _run("dk_splits", lambda: dk_splits.fetch(sport), statuses, sport)
        if settings.KALSHI_ENABLED:
            tasks["kalshi"] = _run("kalshi", lambda: kalshi.fetch(sport), statuses, sport)
        if settings.POLYMARKET_ENABLED:
            tasks["polymarket"] = _run("polymarket", lambda: polymarket.fetch(sport), statuses, sport)
        if settings.PROPHETX_ENABLED:
            tasks["prophetx"] = _run("prophetx", lambda: prophetx.fetch(sport), statuses, sport)
        if settings.NOVIG_ENABLED:
            tasks["novig"] = _run("novig", lambda: novig.fetch(sport), statuses, sport)
        results = dict(zip(tasks, await asyncio.gather(*tasks.values())))

        # odds first: they define home/away orientation and kick-off time
        for o in results.get("odds_api", []):
            g = slate.get(o["away"], o["home"], o["start"])
            g["odds"] = {bk: slate.relabel(g, prices) for bk, prices in o["odds"].items()}
        for ex in ("kalshi", "polymarket", "prophetx", "novig"):
            for e in results.get(ex, []):
                g = slate.get(e["away"], e["home"], e.get("start"))
                g["liquidity"][ex] = slate.relabel(g, e["liquidity"])
                if e.get("prices"):
                    g["prices"][ex] = slate.relabel(g, e["prices"])
        splits = results.get("dk_splits") or await store.kv_get(f"public_splits:{sport}") or []
        if splits and not results.get("dk_splits"):
            statuses["dk_splits"] = _status("dk_splits", "manual", True, len(splits),
                                            note="using splits pushed through /api/public-splits")
        for s in splits:
            g = slate.get(norm_team(s["away"], sport), norm_team(s["home"], sport), create=False)
            if g:
                g["public"] = {"bets": slate.relabel(g, {norm_team(k, sport): v for k, v in s["bets"].items()}),
                               "handle": slate.relabel(g, {norm_team(k, sport): v for k, v in (s.get("handle") or {}).items()})}
        # spread / total markets (best effort: a failure here never touches the moneyline view)
        alt_tasks = {}
        for ex, mod, on in (("kalshi", kalshi, settings.KALSHI_ENABLED), ("novig", novig, settings.NOVIG_ENABLED),
                            ("prophetx", prophetx, settings.PROPHETX_ENABLED and bool(settings.PROPHETX_AFFILIATE_KEY))):
            if on:
                alt_tasks[ex] = _run(f"{ex}_alts", (lambda m=mod: m.fetch_alts(sport)), {}, sport)
        alt_res = dict(zip(alt_tasks, await asyncio.gather(*alt_tasks.values())))
        for ex, recs in alt_res.items():
            n = attach_alts(slate, recs or [])
            if ex in statuses:
                statuses[ex]["alts"] = n
        # drop games with no exchange data at all - nothing to show
        slate.games = [g for g in slate.games if g["liquidity"]]
        model = await _model_picks(sport, store)

    for m in model:
        g = slate.get(norm_team(m["away"], sport), norm_team(m["home"], sport), create=False)
        if g:
            g["model"] = {"pick": canon(norm_team(m["pick"], sport), g["away"], g["home"], sport), "prob": m.get("prob")}

    snap = {
        "sport": sport, "mode": mode, "updated_at": datetime.now(timezone.utc).isoformat(),
        "sources": statuses, "games": sorted(slate.games, key=lambda g: g.get("start") or ""),
    }
    await store.kv_set(f"snapshot:{sport}", snap)
    if settings.NIGHTLY_ENABLED and not settings.DEMO_MODE:
        await auto_log(snap, store)
    return snap


# --------------------------------------------------------------------------- nightly log
def slate_day(start: Optional[str]) -> str:
    """The 'night' a game belongs to, in DISPLAY_TZ (an 8pm ET game is still tonight, not tomorrow UTC)."""
    tz = ZoneInfo(settings.DISPLAY_TZ)
    try:
        dt = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else datetime.now(timezone.utc)
    except ValueError:
        dt = datetime.now(timezone.utc)
    return dt.astimezone(tz).date().isoformat()


async def auto_log(snap: dict, store) -> int:
    """Log every heavy-sharp play that has not started yet, once, with a flat unit stake."""
    existing = {p.get("log_key") for p in await store.plays_list() if p.get("log_key")}
    now = datetime.now(timezone.utc)
    added = 0
    for raw in snap["games"]:
        view = build_game_view(raw, settings.NIGHTLY_MIN_TOTAL)
        if not view["visible"] or not any(f["type"] == "heavy_sharp" for f in view["flags"]):
            continue
        if raw.get("start"):
            try:
                if datetime.fromisoformat(raw["start"].replace("Z", "+00:00")) <= now:
                    continue  # never log a play after the game started
            except ValueError:
                pass
        team = view["sharp_side"]
        day = slate_day(raw.get("start"))
        log_key = f"{day}:{raw['sport']}:{raw['key']}:{team}"
        if log_key in existing:
            continue
        bp = best_prices(raw.get("odds", {}), team) or (
            {"book": "pinnacle", "decimal": raw["odds"]["pinnacle"][team]} if team in raw.get("odds", {}).get("pinnacle", {}) else None
        )
        dec = bp["decimal"] if bp else 1.91  # no odds feed connected: assume -110 and say so in the notes
        await store.play_add({
            "sport": raw["sport"], "game_key": raw["key"], "team": team, "book": bp["book"] if bp else None,
            "description": f"{team} ML ({raw['away']} @ {raw['home']})", "odds_decimal": dec,
            "stake": settings.NIGHTLY_UNIT, "source": "sharp", "day": day, "log_key": log_key,
            "notes": f"auto-logged: {view['sharp_pct']:.0f}% of ${view['total']:,.0f}" + ("" if bp else " · assumed -110, no odds feed"),
        })
        added += 1
    return added


async def seed_demo_history(store) -> None:
    """In demo mode, add fake previous nights once so the Nightly log page has something to show."""
    if any(p.get("source") == "demo" for p in await store.plays_list()):
        return
    for row in demo.history(settings.NIGHTLY_UNIT):
        await store.play_add(row)


async def auto_grade(store) -> int:
    """Grade open moneyline plays from final scores (needs ODDS_API_KEY). Returns number graded."""
    if settings.DEMO_MODE or not settings.ODDS_API_KEY:
        return 0
    plays = [p for p in await store.plays_list() if p["status"] == "open" and p.get("team") and p.get("sport")]
    n = 0
    for sport in {p["sport"] for p in plays}:
        try:
            finals = await odds_api.fetch_scores(sport)
        except Exception:
            continue
        for p in plays:
            if p["sport"] != sport or p["status"] != "open":
                continue
            for f in finals:
                names = list(f["scores"])
                mine = next((t for t in names if same_team(t, p["team"], sport)), None)
                if not mine:
                    continue
                opp = next(t for t in names if t != mine)
                parts = [x.strip() for x in (p.get("game_key") or "").split(" v ")]
                if len(parts) == 2 and not any(same_team(opp, x, sport) for x in parts):
                    continue  # same team, different opponent (e.g. a play from another night)
                won = None if f["scores"][mine] == f["scores"][opp] else f["scores"][mine] > f["scores"][opp]
                await store.play_update(p["id"], grade(p, won))
                p["status"] = "done"
                n += 1
                break
    return n
