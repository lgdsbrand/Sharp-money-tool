import os

os.environ["DEMO_MODE"] = "true"
os.environ["REFRESH_SECONDS"] = "0"
os.environ["SQLITE_PATH"] = os.path.join(__import__("tempfile").gettempdir(), "sharp_test.db")
os.environ["ADMIN_TOKEN"] = ""

import pathlib

pathlib.Path(os.environ["SQLITE_PATH"]).unlink(missing_ok=True)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.sources.dk_splits import parse_html  # noqa: E402
from app.sources import demo  # noqa: E402


def test_dashboard_payload():
    with TestClient(app) as c:
        r = c.get("/api/sharp", params={"min_liquidity": 100000, "bankroll": 1000})
        assert r.status_code == 200
        d = r.json()
        assert d["mode"] == "demo"
        assert d["games"], "expected demo games"
        assert d["games"][0]["total"] >= d["games"][-1]["total"]  # sorted by money
        assert d["metrics"]["heavy_sharp"] and d["top_plays"] and d["discrepancies"]
        # slider filters rows out
        big = c.get("/api/sharp", params={"min_liquidity": 700000}).json()
        assert len(big["games"]) < len(d["games"]) and big["metrics"]["hidden"] > 0


def test_play_lifecycle_and_stats():
    with TestClient(app) as c:
        p = c.post("/api/plays", json={"description": "BOS ML", "team": "BOS", "game_key": "BOS-PHI",
                                       "odds_decimal": 1.9, "stake": 100}).json()
        assert p["status"] == "open"
        done = c.patch(f"/api/plays/{p['id']}", json={"result": "won"}).json()
        assert done["status"] == "won" and done["pnl"] == 90.0
        c.post("/api/plays", json={"description": "x", "odds_decimal": 2.0, "stake": 50})
        lost = [x for x in c.get("/api/plays").json()["plays"] if x["status"] == "open"][0]
        c.patch(f"/api/plays/{lost['id']}", json={"result": "lost"})
        s = c.get("/api/plays").json()["stats"]
        assert s["won"] == 1 and s["lost"] == 1 and s["pnl"] == 40.0
        assert s["roi_pct"] == round(40 / 150 * 100, 1)
        c.delete(f"/api/plays/{p['id']}")


def test_stake_endpoint():
    with TestClient(app) as c:
        r = c.post("/api/bankroll/stake", json={"bankroll": 1000, "win_prob": 0.55, "odds_american": 100})
        assert r.json()["stake"] == 25.0


def test_model_picks_roundtrip():
    with TestClient(app) as c:
        assert c.post("/api/model-picks", json=[{"away": "BOS", "home": "PHI", "pick": "BOS", "prob": 0.7}]).json()["stored"] == 1


def test_admin_token_enforced(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "ADMIN_TOKEN", "secret")
    with TestClient(app) as c:
        body = [{"away": "BOS", "home": "PHI", "pick": "BOS"}]
        assert c.post("/api/model-picks", json=body).status_code == 401
        assert c.post("/api/model-picks", json=body, headers={"X-Admin-Token": "secret"}).status_code == 200


def test_dk_parser_on_sample_html():
    html = """<div><h3>NE Patriots @ DET Lions</h3><span>Selection</span><span>Odds</span><span>Handle</span><span>Bets</span>
    <b>Moneyline</b><div>DET Lions</div><div>-148</div><div>54%</div><div>58%</div>
    <div>NE Patriots</div><div>+124</div><div>46%</div><div>42%</div><b>Spread</b><div>DET Lions</div><div>-3</div><div>50%</div><div>51%</div></div>"""
    g = parse_html(html, "nfl")
    assert len(g) == 1 and g[0]["away"] == "NE" and g[0]["home"] == "DET"
    assert g[0]["handle"] == {"DET": 54.0, "NE": 46.0} and g[0]["bets"] == {"DET": 58.0, "NE": 42.0}
    assert parse_html(html, "nfl", order="bets,handle")[0]["bets"]["DET"] == 54.0
    assert parse_html(html, "nba") == []  # an NFL block never leaks into another sport


def test_sports_filter_and_sections():
    with TestClient(app) as c:
        allg = c.get("/api/sharp", params={"min_liquidity": 0}).json()
        assert {g["label"] for g in allg["games"]} == {"NBA", "NFL", "NHL", "MLB", "NCAAF", "NCAAB"}
        nfl = c.get("/api/sharp", params={"sport": "nfl", "min_liquidity": 0}).json()
        assert nfl["games"] and all(g["sport"] == "nfl" for g in nfl["games"])
        assert allg["top_plays"] and all(any(f["type"] in ("heavy_sharp", "big_money") for f in g["flags"]) for g in allg["top_plays"])
        assert allg["discrepancies"] and all(g["conflict"] or any(f["type"] == "fade" for f in g["flags"]) for g in allg["discrepancies"])
        assert c.get("/api/sharp", params={"sport": "curling"}).status_code == 422


def test_nightly_log_seeded_and_grouped():
    with TestClient(app) as c:
        d = c.get("/api/nightly").json()
        assert d["demo"] and len(d["days"]) >= 2
        best = d["days"][0]
        assert best["record"] == "4-1" and best["settled_stake"] == 125.0 and best["final"] is True
        # auto-logged plays must not leak into the manual tracker list
        assert all(not p.get("log_key") for p in c.get("/api/plays").json()["plays"])


def test_sources_endpoint_never_leaks_keys():
    with TestClient(app) as c:
        d = c.get("/api/sources").json()
        assert d["mode"] == "demo" and d["storage"] in ("sqlite", "supabase")
        assert all(isinstance(v, bool) for v in d["configured"].values())


def test_auto_log_once_per_play_and_not_after_start():
    import asyncio
    from datetime import datetime, timedelta, timezone

    from app import pipeline
    from app.store import SqliteStore

    import tempfile

    db = os.path.join(tempfile.gettempdir(), "sharp_autolog.db")
    pathlib.Path(db).unlink(missing_ok=True)
    st = SqliteStore(db)
    games = demo.games("nba")
    future = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    for g in games:
        g["start"] = future
    games[-1]["start"] = past  # already started: must never be logged
    snap = {"games": games}
    first = asyncio.run(pipeline.auto_log(snap, st))
    second = asyncio.run(pipeline.auto_log(snap, st))
    rows = asyncio.run(st.plays_list())
    assert first > 0 and second == 0
    assert len({r["log_key"] for r in rows}) == len(rows)
    assert all(r["stake"] == 25 and r["status"] == "open" and r["day"] for r in rows)
    assert not any(games[-1]["key"] in r["log_key"] for r in rows)


def test_dk_parser_two_line_header_like_the_real_page():
    text = "SEA Seahawks @\nLV Raiders\nSun 11/15 · 4:05 PM ET\nSelection\nOdds\nHandle\nBets\nMoneyline\nLV Raiders\n+240\n11%\n8%\nSEA Seahawks\n−298\n89%\n92%\nSome article title\n"
    g = parse_html(text, "nfl")
    assert g == [{"away": "SEA", "home": "LV", "columns": "handle,bets", "bets": {"LV": 8.0, "SEA": 92.0}, "handle": {"LV": 11.0, "SEA": 89.0}}]
