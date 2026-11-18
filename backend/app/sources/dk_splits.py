"""DraftKings Network betting splits (public bets % and handle %), all six sports.

Page: https://dknetwork.draftkings.com/draftkings-sportsbook-betting-splits/
Layout (from the live page text): no <table>. Each game is a block of text lines:

    NE Patriots @ DET Lions
    Sun 11/15 · 9:30 AM ET
    Selection / Odds / Handle / Bets          <- column headers, in the order the page shows them
    Moneyline
    DET Lions  -148  54%  58%
    NE Patriots +124 46%  42%

Filters live in the query string: tb_eg (league id), itm_content (league name), tb_edate (n7days),
tb_emt (Moneyline|Spread|Total), tb_page (1,2,...). Only the NFL id (88808) is confirmed; for other
leagues the URL is built from the league name and may need a league id. Run `python -m app.diagnose`
and, if a league shows 0 games, open the page in your browser, click that league, copy the address
bar into DK_SPLITS_URL_<SPORT> (see .env.example).

The column ORDER (Handle then Bets, or the reverse) is read from the header lines of each block, and
can be forced with DK_SPLITS_ORDER=bets,handle. CHECK IT ONCE against the website.

Before launch: check DraftKings Network's terms about automated access to that page.
"""
from __future__ import annotations

import os
import re

import httpx
from bs4 import BeautifulSoup

from ..config import settings
from ..teams import is_known, norm_team

_PCT = re.compile(r"^(\d{1,3}(?:\.\d+)?)\s*%$")
_ODDS = re.compile(r"^[+\-−–]\s?\d{2,5}$|^EVEN$", re.I)
_GAME = re.compile(r"^(?P<away>.+?)\s+@\s+(?P<home>.+)$")
_LEAGUE = {"nfl": "NFL", "nba": "NBA", "nhl": "NHL", "mlb": "MLB", "ncaaf": "NCAAF", "ncaab": "NCAAB"}
_LEAGUE_ID = {"nfl": "88808", "nba": "42648", "nhl": "42133", "mlb": "84240", "ncaaf": "87637", "ncaab": "92483"}  # confirmed from the website address bar; add others via DK_SPLITS_ID_<SPORT>


def _lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    lines = [ln.strip() for ln in soup.get_text("\n").split("\n") if ln.strip()]
    out: list[str] = []
    i = 0
    while i < len(lines):  # DraftKings prints "SEA Seahawks @" and "LV Raiders" on two lines: join them
        if lines[i].endswith(" @") and i + 1 < len(lines):
            out.append(lines[i] + " " + lines[i + 1])
            i += 2
        else:
            out.append(lines[i])
            i += 1
    return out


def parse_html(html: str, sport: str = "nba", order: str | None = None) -> list[dict]:
    """Return [{away, home, bets:{team:%}, handle:{team:%}, columns:'handle,bets'}] for the moneyline."""
    lines = _lines(html)
    forced = [x.strip() for x in (order or os.getenv("DK_SPLITS_ORDER", "")).lower().split(",") if x.strip()]
    games, i = [], 0
    while i < len(lines):
        m = _GAME.match(lines[i])
        if not m or len(lines[i]) > 80 or "%" in lines[i]:
            i += 1
            continue
        # the block runs until the next game header
        j = i + 1
        while j < len(lines) and not (_GAME.match(lines[j]) and len(lines[j]) <= 80 and "%" not in lines[j]):
            j += 1
        block = lines[i + 1 : j]
        i = j
        cols = forced or [c for c in (b.lower() for b in block[:12]) if c in ("handle", "bets")] or ["handle", "bets"]
        if "moneyline" not in [b.lower() for b in block]:
            continue
        k = [b.lower() for b in block].index("moneyline") + 1
        rows = []
        while k < len(block) and len(rows) < 2:
            name = block[k]
            nxt = block[k + 1 : k + 4]
            if len(nxt) == 3 and _ODDS.match(nxt[0]) and _PCT.match(nxt[1]) and _PCT.match(nxt[2]):
                rows.append((name, float(_PCT.match(nxt[1]).group(1)), float(_PCT.match(nxt[2]).group(1))))
                k += 4
            else:
                if rows or name.lower() in ("spread", "total"):
                    break
                k += 1
        if len(rows) != 2:
            continue
        # keep only teams we recognise for this sport (stops an NFL page leaking into NBA, etc.)
        if not all(is_known(r[0], sport) for r in rows):
            continue
        a, h = norm_team(m["away"], sport), norm_team(m["home"], sport)
        by = {norm_team(r[0], sport): r for r in rows}
        if set(by) != {a, h}:
            continue
        first, second = cols[0], (cols[1] if len(cols) > 1 else "bets")
        g = {"away": a, "home": h, "columns": f"{first},{second}", "bets": {}, "handle": {}}
        for key, (_, v1, v2) in by.items():
            g[first][key], g[second][key] = v1, v2
        games.append(g)
    return games


def _url(sport: str, page: int) -> str:
    custom = os.getenv(f"DK_SPLITS_URL_{sport.upper()}")
    if custom:
        base = custom
    else:
        q = [f"itm_content={_LEAGUE[sport]}", "tb_edate=n7days", "tb_emt=Moneyline"]
        lid = os.getenv(f"DK_SPLITS_ID_{sport.upper()}") or _LEAGUE_ID.get(sport)
        if lid:
            q.insert(0, f"tb_eg={lid}")
        base = settings.DK_SPLITS_URL.rstrip("?") + "?" + "&".join(q)
    return base + (f"&tb_page={page}" if page > 1 else "")


async def fetch(sport: str) -> list[dict]:
    # DraftKings returns the NFL table no matter which league is in the address, so without a
    # confirmed league address we must NOT parse (college names would match NFL teams).
    if sport not in _LEAGUE_ID and not os.getenv(f"DK_SPLITS_URL_{sport.upper()}") and not os.getenv(f"DK_SPLITS_ID_{sport.upper()}"):
        raise RuntimeError(f"DraftKings league page for {sport.upper()} not set up yet (set DK_SPLITS_URL_{sport.upper()}); public splits skipped for this league")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
               "Accept": "text/html,application/xhtml+xml"}
    out: dict[tuple, dict] = {}
    async with httpx.AsyncClient(timeout=20, headers=headers, follow_redirects=True) as client:
        for page in range(1, 11):
            r = await client.get(_url(sport, page))
            r.raise_for_status()
            new = [g for g in parse_html(r.text, sport) if (g["away"], g["home"]) not in out]
            if not new:
                break
            out.update({(g["away"], g["home"]): g for g in new})
    return list(out.values())
