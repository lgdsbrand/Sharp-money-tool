from app.analysis import build_game_view, build_markets, side_label
from app.pipeline import Slate, attach_alts


def raw():
    return {"key": "k", "sport": "nfl", "away": "DET", "home": "NE", "start": None,
            "liquidity": {"kalshi": {"DET": 100000, "NE": 50000}},
            "alts": {
                "spread:-3.5": {"mtype": "spread", "line": -3.5, "liquidity": {"kalshi": {"NE": 700000, "DET": 100000}}},
                "spread:-2.5": {"mtype": "spread", "line": -2.5, "liquidity": {"kalshi": {"NE": 1000, "DET": 9000}}},
                "total:44.5": {"mtype": "total", "line": 44.5, "liquidity": {"novig": {"OVER": 200000, "UNDER": 300000}}},
            }}


def test_labels():
    assert side_label("spread", -3.5, "NE", "DET", "NE") == "NE -3.5"
    assert side_label("spread", -3.5, "DET", "DET", "NE") == "DET +3.5"
    assert side_label("total", 44.5, "OVER", "DET", "NE") == "Over 44.5"
    assert side_label("moneyline", None, "DET", "DET", "NE") == "DET ML"


def test_main_line_is_biggest():
    ms = {m["type"]: m for m in build_markets(raw(), raw()["liquidity"], "DET", "NE")}
    assert ms["spread"]["line"] == -3.5 and ms["spread"]["n_lines"] == 2
    assert ms["spread"]["side"] == "NE" and ms["spread"]["heavy"] and ms["spread"]["label"] == "NE -3.5"
    assert ms["total"]["side"] == "UNDER" and not ms["total"]["heavy"]
    assert ms["moneyline"]["side"] == "DET"


def test_view_top_market():
    v = build_game_view({**raw(), "key": "k"}, 0)
    assert v["heavy_market"] and v["top_market"]["type"] == "spread"


def test_attach_alts_orientation():
    sl = Slate("nfl")
    g = sl.get("DET", "NE")
    n = attach_alts(sl, [
        {"book": "kalshi", "away": "DET", "home": "NE", "mtype": "spread", "team_line": ("DET", 3.5), "liquidity": {"DET": 5, "NE": 7}},
        {"book": "novig", "away": "NE", "home": "DET", "mtype": "spread", "home_line": 3.5, "liquidity": {"NE": 1, "DET": 2}},
        {"book": "novig", "away": "DET", "home": "NE", "mtype": "total", "line": 44.5, "liquidity": {"OVER": 3, "UNDER": 4}},
    ])
    assert n == 3
    assert g["alts"]["spread:-3.5"]["liquidity"]["kalshi"] == {"DET": 5, "NE": 7}
    assert "total:44.5" in g["alts"]


def test_novig_spread_name_tail():
    from app.sources.novig import _LINE_TAIL
    assert _LINE_TAIL.sub("", "MEM -6.5") == "MEM" and _LINE_TAIL.sub("", "GSW +6.5") == "GSW"
    assert _LINE_TAIL.sub("", "Saint Bonaventure +12") == "Saint Bonaventure"


def test_keys_are_scrubbed():
    from app.pipeline import _status, scrub
    assert "SECRET123" not in scrub("401 for url 'https://x/odds?apiKey=SECRET123&markets=h2h'")
    assert "SECRET123" not in _status("odds_api", "live", False, 0, "url ?apiKey=SECRET123&x=1")["error"]


def test_top_play_rows_one_row_per_market():
    from app.analysis import top_play_rows
    v = build_game_view({**raw(), "alts": {**raw()["alts"], "total:44.5": {"mtype": "total", "line": 44.5, "liquidity": {"novig": {"OVER": 900000, "UNDER": 100000}}}}}, 0)
    rows = top_play_rows([v], 0)
    assert sorted(r["top_market"]["type"] for r in rows) == ["moneyline", "spread", "total"]
    assert all(r["key"] == "k" for r in rows)


def test_grade_won_markets():
    from app.pipeline import _grade_won
    f = {"scores": {"BOS": 110.0, "PHI": 100.0}}
    base = {"game_key": "BOS v PHI", "team": "BOS"}
    assert _grade_won({**base, "market": "moneyline"}, f, "nba") is True
    assert _grade_won({**base, "market": "spread", "line": -9.5}, f, "nba") is True
    assert _grade_won({**base, "market": "spread", "line": -10}, f, "nba") is None
    assert _grade_won({**base, "market": "spread", "line": -10.5}, f, "nba") is False
    assert _grade_won({**base, "team": "OVER", "market": "total", "line": 209.5}, f, "nba") is True
    assert _grade_won({**base, "team": "UNDER", "market": "total", "line": 209.5}, f, "nba") is False
    assert _grade_won({**base, "team": "OVER", "market": "total", "line": 210}, f, "nba") is None
    assert _grade_won({**base, "game_key": "LAL v MIA", "market": "moneyline"}, f, "nba") == "skip"


def test_market_stats_defaults_to_moneyline():
    from app.store import market_stats
    s = market_stats([{"status": "won", "stake": 25, "pnl": 20}, {"market": "total", "status": "lost", "stake": 25, "pnl": -25}])
    assert s["moneyline"]["won"] == 1 and s["total"]["lost"] == 1 and s["spread"]["won"] == 0
