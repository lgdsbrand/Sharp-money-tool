"""Exercises the LIVE path with fake feeds (no network): matching across spellings, and one feed failing."""
import asyncio

import pytest

from app import pipeline
from app.config import settings
from app.sources import dk_splits, kalshi, odds_api
from app.store import SqliteStore


@pytest.fixture()
def live(monkeypatch, tmp_path):
    for k, v in dict(DEMO_MODE=False, ODDS_API_KEY="x", DK_SPLITS_ENABLED=True, KALSHI_ENABLED=True,
                     POLYMARKET_ENABLED=False, PROPHETX_ENABLED=False, NOVIG_ENABLED=False, NIGHTLY_ENABLED=False).items():
        monkeypatch.setattr(settings, k, v)
    pipeline._odds_cache.clear()

    async def in_season(sport):
        return True

    monkeypatch.setattr(odds_api, "in_season", in_season)
    return SqliteStore(str(tmp_path / "live.db"))


def test_feeds_with_different_spellings_merge_into_one_game(live, monkeypatch):
    async def fake_odds(sport):
        return [{"away": "ohio state", "home": "michigan", "start": "2026-11-28T17:00:00Z",
                 "odds": {"pinnacle": {"ohio state": 1.60, "michigan": 2.40}, "draftkings": {"ohio state": 1.66, "michigan": 2.30}}}]

    async def fake_kalshi(sport):
        return [{"book": "kalshi", "away": "ohio state buckeyes", "home": "michigan wolverines", "start": None,
                 "liquidity": {"ohio state buckeyes": 400_000, "michigan wolverines": 100_000},
                 "prices": {"ohio state buckeyes": 0.62, "michigan wolverines": 0.38}}]

    async def fake_dk(sport):  # reversed orientation on purpose
        return [{"away": "michigan", "home": "ohio state", "bets": {"michigan": 30.0, "ohio state": 70.0},
                 "handle": {"michigan": 35.0, "ohio state": 65.0}}]

    monkeypatch.setattr(odds_api, "fetch", fake_odds)
    monkeypatch.setattr(kalshi, "fetch", fake_kalshi)
    monkeypatch.setattr(dk_splits, "fetch", fake_dk)
    snap = asyncio.run(pipeline.refresh("ncaaf", live))
    assert snap["mode"] == "live" and len(snap["games"]) == 1
    g = snap["games"][0]
    assert (g["away"], g["home"]) == ("ohio state", "michigan")  # orientation comes from the odds feed
    assert g["liquidity"]["kalshi"] == {"ohio state": 400_000, "michigan": 100_000}
    assert g["public"]["bets"] == {"michigan": 30.0, "ohio state": 70.0}
    assert set(g["odds"]["pinnacle"]) == {"ohio state", "michigan"}


def test_one_broken_feed_does_not_break_the_rest(live, monkeypatch):
    async def boom(sport):
        raise RuntimeError("kalshi is down")

    async def ok_odds(sport):
        return [{"away": "BOS", "home": "PHI", "start": None, "odds": {"pinnacle": {"BOS": 1.5, "PHI": 2.6}}}]

    async def ok_dk(sport):
        return []

    monkeypatch.setattr(kalshi, "fetch", boom)
    monkeypatch.setattr(odds_api, "fetch", ok_odds)
    monkeypatch.setattr(dk_splits, "fetch", ok_dk)
    snap = asyncio.run(pipeline.refresh("nba", live))
    assert snap["sources"]["kalshi"]["ok"] is False and "kalshi is down" in snap["sources"]["kalshi"]["error"]
    assert snap["sources"]["odds_api"]["ok"] is True
    assert snap["games"] == []  # no exchange money anywhere -> nothing to show, but no crash


def test_pushed_public_splits_are_used_when_scrape_returns_nothing(live, monkeypatch):
    async def fake_kalshi(sport):
        return [{"book": "kalshi", "away": "BOS", "home": "PHI", "start": None, "liquidity": {"BOS": 700_000, "PHI": 70_000}, "prices": {}}]

    async def empty(sport):
        return []

    monkeypatch.setattr(kalshi, "fetch", fake_kalshi)
    monkeypatch.setattr(odds_api, "fetch", empty)
    monkeypatch.setattr(dk_splits, "fetch", empty)
    asyncio.run(live.kv_set("public_splits:nba", [{"away": "Boston Celtics", "home": "Philadelphia 76ers",
                                                   "bets": {"Boston Celtics": 35, "Philadelphia 76ers": 65}, "handle": {}}]))
    snap = asyncio.run(pipeline.refresh("nba", live))
    assert snap["games"][0]["public"]["bets"] == {"BOS": 35, "PHI": 65}
    assert snap["sources"]["dk_splits"]["mode"] == "manual"
