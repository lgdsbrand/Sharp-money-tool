"""Persistence. Uses Supabase (REST/PostgREST) when SUPABASE_URL is set, otherwise a local SQLite file.

Two tables only (see supabase/schema.sql):
  kv(key, value)   - latest snapshot per sport, model picks, manual public splits
  plays(...)       - the suggested-plays tracker
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from .config import settings

PLAY_FIELDS = ["sport", "game_key", "description", "team", "book", "odds_decimal", "stake", "status", "pnl", "source", "notes", "day", "log_key", "user_id"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteStore:
    def __init__(self, path: str):
        self.path = path
        with self._c() as c:
            c.execute("create table if not exists kv(key text primary key, value text, updated_at text)")
            c.execute(
                "create table if not exists plays(id text primary key, created_at text, sport text, game_key text,"
                " description text, team text, book text, odds_decimal real, stake real, status text, pnl real,"
                " source text, notes text, day text, log_key text, user_id text)"
            )
            have = {r[1] for r in c.execute("pragma table_info(plays)")}
            for col in ("day", "log_key", "user_id"):  # upgrade older local databases
                if col not in have:
                    c.execute(f"alter table plays add column {col} text")

    def _c(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    async def kv_get(self, key: str) -> Optional[Any]:
        with self._c() as c:
            r = c.execute("select value from kv where key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else None

    async def kv_set(self, key: str, value: Any) -> None:
        with self._c() as c:
            c.execute("insert or replace into kv values(?,?,?)", (key, json.dumps(value), _now()))

    async def plays_list(self) -> list[dict]:
        with self._c() as c:
            return [dict(r) for r in c.execute("select * from plays order by created_at desc")]

    async def play_add(self, d: dict) -> dict:
        row = {"id": str(uuid.uuid4()), "created_at": _now(), "status": "open", "pnl": None, **d}
        cols = ["id", "created_at"] + PLAY_FIELDS
        with self._c() as c:
            c.execute(f"insert into plays({','.join(cols)}) values({','.join('?' * len(cols))})", [row.get(k) for k in cols])
        return {k: row.get(k) for k in cols}

    async def play_update(self, pid: str, patch: dict) -> Optional[dict]:
        patch = {k: v for k, v in patch.items() if k in PLAY_FIELDS}
        if patch:
            with self._c() as c:
                c.execute(f"update plays set {','.join(f'{k}=?' for k in patch)} where id=?", [*patch.values(), pid])
        with self._c() as c:
            r = c.execute("select * from plays where id=?", (pid,)).fetchone()
        return dict(r) if r else None

    async def play_delete(self, pid: str) -> None:
        with self._c() as c:
            c.execute("delete from plays where id=?", (pid,))

    async def ping(self) -> str:
        return "ok"


class SupabaseStore:
    def __init__(self, url: str, key: str, transport=None):
        self.transport = transport  # tests inject a fake PostgREST here
        self.base = f"{url}/rest/v1"
        # legacy service_role keys are JWTs (eyJ...) and go in both headers; the newer sb_secret_... keys are not JWTs
        # and must only be sent as `apikey`
        self.h = {"apikey": key, "Content-Type": "application/json"}
        if key.startswith("eyJ"):
            self.h["Authorization"] = f"Bearer {key}"

    async def _req(self, method: str, path: str, **kw):
        async with httpx.AsyncClient(timeout=20, transport=self.transport) as c:
            r = await c.request(method, f"{self.base}/{path}", headers={**self.h, **kw.pop("headers", {})}, **kw)
            if r.status_code >= 400:
                raise RuntimeError(f"Supabase {r.status_code} on {path.split('?')[0]}: {r.text[:200]}")
            return r.json() if r.content else None

    async def kv_get(self, key: str) -> Optional[Any]:
        rows = await self._req("GET", "kv", params={"key": f"eq.{key}", "select": "value"})
        return rows[0]["value"] if rows else None

    async def kv_set(self, key: str, value: Any) -> None:
        await self._req(
            "POST", "kv", json={"key": key, "value": value, "updated_at": _now()},
            headers={"Prefer": "resolution=merge-duplicates"},
        )

    async def plays_list(self) -> list[dict]:
        return await self._req("GET", "plays", params={"select": "*", "order": "created_at.desc"})

    async def play_add(self, d: dict) -> dict:
        rows = await self._req("POST", "plays", json={"status": "open", **d}, headers={"Prefer": "return=representation"})
        return rows[0]

    async def play_update(self, pid: str, patch: dict) -> Optional[dict]:
        patch = {k: v for k, v in patch.items() if k in PLAY_FIELDS}
        rows = await self._req("PATCH", "plays", params={"id": f"eq.{pid}"}, json=patch, headers={"Prefer": "return=representation"})
        return rows[0] if rows else None

    async def play_delete(self, pid: str) -> None:
        await self._req("DELETE", "plays", params={"id": f"eq.{pid}"})

    async def ping(self) -> str:
        """Used by `python -m app.diagnose`: proves the URL, key and tables are right."""
        await self._req("GET", "kv", params={"select": "key", "limit": 1})
        await self._req("GET", "plays", params={"select": "id,user_id", "limit": 1})
        return "ok"


def make_store():
    if settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY:
        return SupabaseStore(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    return SqliteStore(settings.SQLITE_PATH)


def grade(play: dict, won: Optional[bool]) -> dict:
    """Return {'status','pnl'} for a play. won=None means push."""
    stake, dec = float(play.get("stake") or 0), float(play.get("odds_decimal") or 0)
    if won is None:
        return {"status": "push", "pnl": 0.0}
    return {"status": "won", "pnl": round(stake * (dec - 1), 2)} if won else {"status": "lost", "pnl": round(-stake, 2)}


def play_stats(plays: list[dict]) -> dict:
    graded = [p for p in plays if p.get("status") in ("won", "lost", "push")]
    won = sum(1 for p in graded if p["status"] == "won")
    lost = sum(1 for p in graded if p["status"] == "lost")
    push = sum(1 for p in graded if p["status"] == "push")
    staked = sum(float(p.get("stake") or 0) for p in graded if p["status"] != "push")
    pnl = sum(float(p.get("pnl") or 0) for p in graded)
    return {
        "open": sum(1 for p in plays if p.get("status") == "open"),
        "won": won, "lost": lost, "push": push,
        "win_pct": round(won / (won + lost) * 100, 1) if won + lost else None,
        "staked": round(staked, 2), "pnl": round(pnl, 2),
        "roi_pct": round(pnl / staked * 100, 1) if staked else None,
    }


def nightly_groups(plays: list[dict]) -> list[dict]:
    """Group logged plays by slate day (newest first) with the same numbers as a 'Day 1 returns' card."""
    days: dict[str, list[dict]] = {}
    for p in plays:
        if p.get("log_key") and p.get("day"):
            days.setdefault(p["day"], []).append(p)
    out = []
    for day in sorted(days, reverse=True):
        ps = sorted(days[day], key=lambda p: (p.get("status") == "open", p.get("created_at") or ""))
        s = play_stats(ps)
        out.append({
            "day": day, "plays": ps, "record": f"{s['won']}-{s['lost']}" + (f"-{s['push']}" if s["push"] else ""),
            "profit": s["pnl"], "settled_stake": s["staked"], "roi_pct": s["roi_pct"], "open": s["open"],
            "final": s["open"] == 0,
        })
    return out