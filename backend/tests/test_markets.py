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
