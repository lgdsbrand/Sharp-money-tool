"""FastAPI app + dashboard.  Run locally:  python -m uvicorn app.main:app --reload --port 8000"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import contextlib
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import mimetypes
from pydantic import BaseModel

from . import auth, pipeline
from .pipeline import scrub
from .analysis import american_to_decimal, build_game_view, kelly_stake
from .config import settings
from .sources import odds_api
from .store import grade, make_store, nightly_groups, play_stats
from .teams import ALL_SPORTS, SPORT_LABELS, display

# Windows can map .js to text/plain in the registry, which some browsers refuse to run.
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")

store = make_store()
STATIC = Path(__file__).resolve().parent.parent / "static"


async def _background():
    while True:
        for sport in settings.SPORTS:
            with contextlib.suppress(Exception):
                await pipeline.refresh(sport, store)
        with contextlib.suppress(Exception):
            await pipeline.auto_grade(store)
        await asyncio.sleep(settings.REFRESH_SECONDS)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.DEMO_MODE:
        with contextlib.suppress(Exception):
            await pipeline.seed_demo_history(store)
    else:
        with contextlib.suppress(Exception):  # demo rows from an earlier DEMO_MODE=true run must not mix with real results
            for p in await store.plays_list():
                if p.get("source") == "demo":
                    await store.play_delete(p["id"])
    if not settings.DEMO_MODE and not settings.REQUIRE_LOGIN and not settings.ADMIN_TOKEN:
        print("WARNING: REQUIRE_LOGIN is off and ADMIN_TOKEN is empty - anyone who can reach this server can use it. Turn login on or set a token before deploying.")
    task = asyncio.create_task(_background()) if settings.REFRESH_SECONDS > 0 else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Sharp Money Tool API", version="1.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def optional_user(authorization: Optional[str] = Header(default=None)) -> Optional[dict]:
    return await auth.user_from_token(store, _bearer(authorization))


async def need_user(user: Optional[dict] = Depends(optional_user)) -> Optional[dict]:
    """Logged-in user. When REQUIRE_LOGIN=false the dashboard is open and this returns None."""
    if settings.REQUIRE_LOGIN and not user:
        raise HTTPException(401, "Please log in.")
    return user


async def admin(user: Optional[dict] = Depends(optional_user), x_admin_token: Optional[str] = Header(default=None)):
    """Admin = correct X-Admin-Token, or a logged-in user listed in ADMIN_EMAILS.
    With neither ADMIN_TOKEN nor ADMIN_EMAILS set: open locally, any logged-in user when login is required."""
    if settings.ADMIN_TOKEN and x_admin_token == settings.ADMIN_TOKEN:
        return
    if user and user["admin"]:
        return
    if not settings.ADMIN_TOKEN and not settings.ADMIN_EMAILS:
        if settings.REQUIRE_LOGIN and not user:
            raise HTTPException(401, "Please log in.")
        return
    raise HTTPException(401, "Admin only (log in as an ADMIN_EMAILS user or send X-Admin-Token).")


# ---- accounts -----------------------------------------------------------------
class Creds(BaseModel):
    email: str
    password: str
    name: str = ""


@app.post("/api/auth/signup")
async def signup(c: Creds):
    try:
        user, token = await auth.signup(store, c.email, c.password, c.name)
    except auth.AuthError as e:
        raise HTTPException(e.status, str(e))
    return {"user": user, "token": token}


@app.post("/api/auth/login")
async def login(c: Creds, request: Request):
    try:
        user, token = await auth.login(store, c.email, c.password, request.client.host if request.client else "")
    except auth.AuthError as e:
        raise HTTPException(e.status, str(e))
    return {"user": user, "token": token}


@app.post("/api/auth/logout")
async def logout(authorization: Optional[str] = Header(default=None)):
    await auth.logout(store, _bearer(authorization))
    return {"ok": True}


@app.get("/api/auth/me")
async def me(user: Optional[dict] = Depends(optional_user)):
    return {"user": user, "require_login": settings.REQUIRE_LOGIN, "allow_signup": settings.ALLOW_SIGNUP}


@app.get("/api/health")
async def health():
    return {"ok": True, "mode": "demo" if settings.DEMO_MODE else "live", "sports": settings.SPORTS,
            "write_protected": bool(settings.ADMIN_TOKEN), "require_login": settings.REQUIRE_LOGIN,
            "storage": "supabase" if settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY else "sqlite"}


_warming: dict[str, asyncio.Task] = {}


async def _snapshot(sport: str) -> Optional[dict]:
    """Latest snapshot. Demo mode builds it instantly. In live mode a missing snapshot is built in the
    background (a first live pull can take a while) and None is returned so the page shows 'warming up'."""
    snap = await store.kv_get(f"snapshot:{sport}")
    if snap:
        return snap
    if settings.DEMO_MODE or settings.REFRESH_SECONDS <= 0:
        return await pipeline.refresh(sport, store)
    t = _warming.get(sport)
    if not t or t.done():
        _warming[sport] = asyncio.create_task(_refresh_quiet(sport))
    return None


async def _refresh_quiet(sport: str):
    with contextlib.suppress(Exception):
        await pipeline.refresh(sport, store)


@app.get("/api/sharp")
async def sharp(
    sport: str = "all",
    min_liquidity: float = Query(default=settings.MIN_LIQUIDITY_DEFAULT, ge=0),
    bankroll: float = Query(default=0, ge=0),
    kelly_fraction: float = Query(default=0.25, gt=0, le=1),
    include_started: bool = False,
    _user: Optional[dict] = Depends(need_user),
):
    """Dashboard payload: top sharp plays, sharp-vs-public discrepancies, every game, metrics, source status."""
    sports = settings.SPORTS if sport == "all" else [sport]
    if any(s not in ALL_SPORTS for s in sports):
        raise HTTPException(422, f"sport must be one of: all, {', '.join(ALL_SPORTS)}")
    got = [await _snapshot(s) for s in sports]
    snaps = [x for x in got if x]
    warming = len(snaps) < len(got)
    views = []
    for snap in snaps:
        for g in snap["games"]:
            try:
                views.append(build_game_view(g, min_liquidity, bankroll, kelly_fraction))
            except Exception as e:  # one odd game must never blank the whole dashboard
                print(f"skipped game {g.get('key')}: {type(e).__name__}: {e}")
    for v in views:
        v["label"] = SPORT_LABELS[v["sport"]]
        v["away_name"], v["home_name"] = display(v["away"], v["sport"]), display(v["home"], v["sport"])
    now_iso = datetime.now(timezone.utc).isoformat()
    started = 0
    if not include_started and not settings.DEMO_MODE:
        keep = []
        for v in views:
            if v.get("start") and v["start"] <= now_iso:  # already under way: money is no longer pre-game
                started += 1
            else:
                keep.append(v)
        views = keep
    visible = sorted((v for v in views if v["visible"]), key=lambda v: v["total"], reverse=True)

    has = lambda v, *types: any(f["type"] in types for f in v["flags"])  # noqa: E731
    top_plays = [v for v in visible if has(v, "heavy_sharp", "big_money") or v.get("heavy_market")][:15]
    discrepancies = sorted((v for v in visible if v["conflict"] or has(v, "fade")),
                           key=lambda v: (not has(v, "fade"), -v["total"]))[:15]
    return {
        "sport": sport, "mode": "demo" if settings.DEMO_MODE else "live", "warming": warming,
        "updated_at": max((s["updated_at"] for s in snaps), default=None), "min_liquidity": min_liquidity,
        "metrics": {
            "games": len(visible), "hidden": len(views) - len(visible), "started": started,
            "liquidity": round(sum(v["total"] for v in visible)),
            "heavy_sharp": sum(1 for v in visible if has(v, "heavy_sharp")),
            "conflicts": sum(1 for v in visible if v["conflict"]),
            "plus_ev": sum(1 for v in visible if has(v, "plus_ev")),
            "model_agrees": sum(1 for v in visible if has(v, "model_agrees")),
        },
        "top_plays": top_plays, "discrepancies": discrepancies, "games": visible,
        "thresholds": {"heavy_sharp_pct": settings.HEAVY_SHARP_PCT, "fade_public_pct": settings.FADE_PUBLIC_PCT,
                       "big_money": settings.BIG_MONEY_TOTAL, "min_ev_pct": settings.MIN_EV_PCT,
                       "max_ev_pct": settings.MAX_EV_PCT, "nightly_unit": settings.NIGHTLY_UNIT,
                       "nightly_min_total": settings.NIGHTLY_MIN_TOTAL},
        "logos": {"team": settings.TEAM_LOGO_URL, "league": settings.LEAGUE_LOGO_URL},
    }


@app.get("/api/sources")
async def sources(_user: Optional[dict] = Depends(need_user)):
    """Which feeds are connected and healthy (used by the Profile page). Never returns key values."""
    merged: dict[str, dict] = {}
    for s in settings.SPORTS:
        snap = await store.kv_get(f"snapshot:{s}")
        for name, st in (snap or {}).get("sources", {}).items():
            m = merged.setdefault(name, {"name": name, "mode": st["mode"], "ok": True, "games": 0, "errors": [], "notes": []})
            m["games"] += st["games"]
            m["ok"] = m["ok"] and st["ok"]
            m["mode"] = st["mode"]
            if st.get("error"):
                m.setdefault("_err", {}).setdefault(scrub(st["error"]), []).append(s.upper())
            if st.get("note") and not st["note"].startswith("Odds API credits") and scrub(st["note"]) not in m["notes"]:
                m["notes"].append(scrub(st["note"]))
    for m in merged.values():   # one line per distinct error instead of one per sport
        for err, sps in m.pop("_err", {}).items():
            m["errors"].append(err if len(sps) == len(settings.SPORTS) else f"{', '.join(sps)}: {err}")
    keys = {
        "ODDS_API_KEY": bool(settings.ODDS_API_KEY), "KALSHI": settings.KALSHI_ENABLED,
        "PROPHETX_AFFILIATE_KEY": bool(settings.PROPHETX_AFFILIATE_KEY), "NOVIG": settings.NOVIG_ENABLED,
        "SUPABASE": bool(settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY), "MODEL_PICKS_URL": bool(settings.MODEL_PICKS_URL),
        "ADMIN_TOKEN": bool(settings.ADMIN_TOKEN),
    }
    return {"mode": "demo" if settings.DEMO_MODE else "live", "sources": list(merged.values()), "configured": keys,
            "storage": "supabase" if keys["SUPABASE"] else "sqlite", "odds_credits": odds_api.last_usage,
            "nightly": {"enabled": settings.NIGHTLY_ENABLED, "unit": settings.NIGHTLY_UNIT, "min_total": settings.NIGHTLY_MIN_TOTAL}}


@app.post("/api/refresh", dependencies=[Depends(need_user)])
async def refresh(sport: Optional[str] = None):
    sports = [sport] if sport else settings.SPORTS
    out = {}
    for s in sports:
        snap = await pipeline.refresh(s, store)
        out[s] = {"games": len(snap["games"]), "sources": snap["sources"]}
    return {"refreshed": out, "graded": await pipeline.auto_grade(store)}


# ---- bankroll ------------------------------------------------------------
class StakeIn(BaseModel):
    bankroll: float
    win_prob: float  # 0-1
    odds_american: Optional[int] = None
    odds_decimal: Optional[float] = None
    kelly_fraction: float = 0.25
    max_pct: float = 5.0


@app.post("/api/bankroll/stake")
async def stake(b: StakeIn):
    dec = b.odds_decimal or (american_to_decimal(b.odds_american) if b.odds_american else None)
    if not dec:
        raise HTTPException(422, "Provide odds_american or odds_decimal")
    return {"stake": kelly_stake(b.bankroll, b.win_prob, dec, b.kelly_fraction, b.max_pct), "decimal": dec}


# ---- plays tracker + nightly log ---------------------------------------------
class PlayIn(BaseModel):
    sport: str = "nba"
    game_key: Optional[str] = None
    description: str
    team: Optional[str] = None
    book: Optional[str] = None
    odds_decimal: float
    stake: float
    source: str = "manual"  # sharp | ev | model | manual
    notes: Optional[str] = None


class PlayPatch(BaseModel):
    result: Optional[str] = None  # won | lost | push | open
    stake: Optional[float] = None
    odds_decimal: Optional[float] = None
    notes: Optional[str] = None


def _uid(user: Optional[dict]) -> str:
    return user["id"] if user else ""


def _write_guard(user: Optional[dict] = Depends(optional_user), x_admin_token: Optional[str] = Header(default=None)):
    """Tracker writes: logged-in user; or, with login off, the old ADMIN_TOKEN rule."""
    if settings.REQUIRE_LOGIN:
        if not user:
            raise HTTPException(401, "Please log in.")
    elif settings.ADMIN_TOKEN and x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(401, "Invalid or missing X-Admin-Token")
    return user


@app.get("/api/plays")
async def plays(user: Optional[dict] = Depends(need_user)):
    rows = [p for p in await store.plays_list() if not p.get("log_key") and (p.get("user_id") or "") == _uid(user)]
    return {"plays": rows, "stats": play_stats(rows)}


@app.get("/api/nightly")
async def nightly(days: int = Query(default=14, ge=1, le=90), _user: Optional[dict] = Depends(need_user)):
    rows = [p for p in await store.plays_list() if p.get("log_key")]
    groups = nightly_groups(rows)
    return {"days": groups[:days], "overall": play_stats(rows), "unit": settings.NIGHTLY_UNIT,
            "demo": any(p.get("source") == "demo" for p in rows)}


@app.post("/api/plays")
async def add_play(p: PlayIn, user: Optional[dict] = Depends(_write_guard)):
    return await store.play_add({**p.model_dump(), "user_id": _uid(user)})


async def _own(pid: str, user: Optional[dict]) -> dict:
    cur = next((p for p in await store.plays_list() if p["id"] == pid), None)
    if not cur or (cur.get("user_id") or "") != _uid(user):
        raise HTTPException(404, "Play not found")
    return cur


@app.patch("/api/plays/{pid}")
async def update_play(pid: str, patch: PlayPatch, user: Optional[dict] = Depends(_write_guard)):
    cur = await _own(pid, user)
    data = {k: v for k, v in patch.model_dump().items() if v is not None and k != "result"}
    merged = {**cur, **data}
    if patch.result in ("won", "lost", "push"):
        data.update(grade(merged, {"won": True, "lost": False, "push": None}[patch.result]))
    elif patch.result == "open":
        data.update({"status": "open", "pnl": None})
    return await store.play_update(pid, data)


@app.delete("/api/plays/{pid}")
async def delete_play(pid: str, user: Optional[dict] = Depends(_write_guard)):
    await _own(pid, user)
    await store.play_delete(pid)
    return {"deleted": pid}


# ---- inputs you can push in (model picks, public splits) ---------------------
class ModelPick(BaseModel):
    away: str
    home: str
    pick: str
    prob: Optional[float] = None


class Splits(BaseModel):
    away: str
    home: str
    bets: dict[str, float]
    handle: dict[str, float] = {}


@app.post("/api/model-picks", dependencies=[Depends(admin)])
async def set_model_picks(picks: list[ModelPick], sport: str = "nba"):
    await store.kv_set(f"model_picks:{sport}", [p.model_dump() for p in picks])
    return {"stored": len(picks)}


@app.post("/api/public-splits", dependencies=[Depends(admin)])
async def set_public_splits(rows: list[Splits], sport: str = "nba"):
    await store.kv_set(f"public_splits:{sport}", [r.model_dump() for r in rows])
    return {"stored": len(rows)}


# ---- dashboard ---------------------------------------------------------------
@app.get("/api/logos")
async def logos():
    """Which sportsbook logo files you dropped into static/logos/ (kalshi.svg, draftkings.png, ...)."""
    d = STATIC / "logos"
    return {f.stem.lower(): f.name for f in d.iterdir() if f.suffix.lower() in (".svg", ".png", ".webp", ".jpg")} if d.exists() else {}



class NoCacheStatic(StaticFiles):
    """Always revalidate. Otherwise a browser that once ran ANOTHER project on this same address
    (e.g. localhost:8000) keeps serving that project's old app.js/style.css."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp


if STATIC.exists():
    app.mount("/static", NoCacheStatic(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    async def home():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache, must-revalidate"})
