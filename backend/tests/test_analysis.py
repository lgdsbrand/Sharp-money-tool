import math

from app import analysis as an
from app.sources import demo
from app.teams import canon, is_known, match_pair, norm_team, pair_key, same_team


def test_american_decimal_roundtrip():
    assert math.isclose(an.american_to_decimal(150), 2.5)
    assert math.isclose(an.american_to_decimal(-200), 1.5)
    assert an.decimal_to_american(2.5) == 150
    assert an.decimal_to_american(1.5) == -200


def test_devig_sums_to_one():
    a, b = an.devig_two_way(an.american_to_decimal(-110), an.american_to_decimal(-110))
    assert math.isclose(a, 0.5) and math.isclose(b, 0.5)
    a, b = an.devig_two_way(1.5, 3.0)
    assert math.isclose(a + b, 1.0)
    assert a > b


def test_ev_and_kelly():
    # fair 55%, offered +100 (dec 2.0) -> EV +10%
    assert math.isclose(an.ev_pct(0.55, 2.0), 10.0)
    # full Kelly = (1*0.55-0.45)/1 = 10%; quarter Kelly of $1000 = $25
    assert an.kelly_stake(1000, 0.55, 2.0, fraction=0.25) == 25.0
    # no edge -> no stake
    assert an.kelly_stake(1000, 0.45, 2.0) == 0.0
    # cap at 5% of bankroll
    assert an.kelly_stake(1000, 0.9, 2.0, fraction=1.0, max_pct=5) == 50.0


def test_sharp_lean():
    liq = {"kalshi": {"BOS": 717_000, "PHI": 75_000}, "novig": {"BOS": 17_000, "PHI": 11_000}}
    lean = an.sharp_lean(liq, ("BOS", "PHI"))
    assert lean["side"] == "BOS"
    assert lean["total"] == 820_000
    assert 88 < lean["pct"]["BOS"] < 90  # screenshot shows 89%


def test_team_normalisation():
    assert norm_team("Boston Celtics") == "BOS"
    assert norm_team("Celtics") == "BOS"
    assert norm_team("Los Angeles C") == "LAC"
    assert norm_team("LA Clippers") == "LAC"
    assert norm_team("Los Angeles Lakers") == "LAL"
    assert pair_key("Philadelphia 76ers", "Boston Celtics") == "BOS v PHI"


def _view(a, h):
    games = {g["key"]: g for g in demo.games()}
    g = games[pair_key(a, h)]
    g["model"] = next((m for m in demo.model_picks() if {m["away"], m["home"]} == {a, h}), None)
    return an.build_game_view(g, min_liquidity=100_000, bankroll=1000)


def test_flags_heavy_sharp_and_model_agrees():
    v = _view("BOS", "PHI")
    types = {f["type"] for f in v["flags"]}
    assert "heavy_sharp" in types and "model_agrees" in types and "big_money" in types
    assert v["model_status"] == "agrees"


def test_fade_flag_when_public_on_other_side():
    v = _view("OKC", "POR")  # sharp on POR, 71% public bets on OKC
    assert v["sharp_side"] == "POR"
    assert any(f["type"] == "fade" for f in v["flags"])


def test_min_liquidity_filter_visibility():
    games = {g["key"]: g for g in demo.games()}
    small = an.build_game_view(games[pair_key("DEN", "MEM")], min_liquidity=1_000_000)
    assert small["visible"] is False


def test_ev_table_uses_pinnacle_fair_price():
    raw = {
        "away": "A", "home": "B", "prices": {},
        "odds": {"pinnacle": {"A": 1.91, "B": 1.91}, "dk": {"A": 2.10, "B": 1.70}},
    }
    t = an.ev_table(raw)
    assert t["available"]
    top = t["rows"][0]
    assert top["team"] == "A" and top["book"] == "dk" and top["ev_pct"] > 4
    assert an.ev_table({"away": "A", "home": "B", "odds": {}})["available"] is False


def test_suggestion_has_stake_when_bankroll_given():
    v = _view("BOS", "PHI")
    assert v["suggestion"]["team"]
    assert v["suggestion"]["stake"] is None or v["suggestion"]["stake"] >= 0


# ---------------------------------------------------------------- six sports
def test_pro_team_names_per_sport():
    assert norm_team("Kansas City Chiefs", "nfl") == "KC"
    assert norm_team("Los Angeles Rams", "nfl") == "LAR" and norm_team("Los Angeles Chargers", "nfl") == "LAC"
    assert norm_team("New York Giants", "nfl") == "NYG" and norm_team("NY Jets", "nfl") == "NYJ"
    assert norm_team("Vegas Golden Knights", "nhl") == "VGK" and norm_team("Utah Mammoth", "nhl") == "UTA"
    assert norm_team("Boston Red Sox", "mlb") == "BOS" and norm_team("Chicago White Sox", "mlb") == "CWS"
    assert norm_team("Chicago Cubs", "mlb") == "CHC"
    # same nickname/city, different league
    assert norm_team("Kings", "nhl") == "LAK" and norm_team("Kings", "nba") == "SAC"
    assert is_known("Dallas Cowboys", "nfl") and not is_known("Dallas Cowboys", "nba")


def test_college_matching_is_careful():
    assert norm_team("Ohio St", "ncaaf") == "ohio state"
    assert same_team("ohio state", "ohio state buckeyes", "ncaaf")
    assert not same_team("ohio", "ohio state", "ncaaf")  # different schools
    assert not same_team("michigan", "michigan state", "ncaab")
    assert norm_team("Miami (OH)", "ncaaf") == norm_team("Miami Ohio", "ncaaf") == "miami oh"
    assert match_pair("ohio state", "michigan", "michigan wolverines", "ohio state buckeyes", "ncaaf")
    assert canon("michigan wolverines", "ohio state", "michigan", "ncaaf") == "michigan"


def test_other_leagues_never_leak_into_a_sport():
    assert not is_known("Dallas Cowboys", "nba") and not is_known("Dallas Mavericks", "nfl")
    assert not is_known("Boston Bruins", "nba") and is_known("Boston Bruins", "nhl")


def test_college_display_and_apostrophes():
    from app.teams import display
    assert norm_team("Saint Mary's", "ncaab") == norm_team("St. Mary's", "ncaab") == "saint marys"
    assert display("miami oh", "ncaaf") == "Miami (OH)" and display("saint marys", "ncaab") == "Saint Mary's"
