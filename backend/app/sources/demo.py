"""Realistic FAKE data for all six sports so the whole tool runs and can be tested without API keys."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from ..teams import norm_team, pair_key

# sport -> rows of (away, home, kalshi $ (away, home), novig $ (away, home), public bets % on away, pinnacle ML (away, home))
_ROWS = {
    "nba": [
        ("BOS", "PHI", (717_000, 75_000), (17_000, 11_000), 38, (-210, 175)),
        ("ORL", "SAC", (487_000, 27_000), (21_000, 8_000), 44, (-185, 155)),
        ("LAC", "PHX", (141_000, 102_000), (10_000, 25_000), 47, (-120, 100)),
        ("MIA", "UTA", (181_000, 31_000), (24_000, 9_000), 31, (-165, 140)),
        ("OKC", "POR", (84_000, 122_000), (16_000, 11_000), 71, (-950, 650)),
        ("DEN", "MEM", (60_000, 38_000), (9_000, 6_000), 55, (-140, 118)),
    ],
    "nfl": [
        ("KC", "BUF", (1_450_000, 610_000), (88_000, 41_000), 41, (-125, 105)),
        ("PHI", "DAL", (930_000, 120_000), (60_000, 14_000), 57, (-190, 160)),
        ("SF", "SEA", (240_000, 260_000), (30_000, 33_000), 66, (-135, 115)),
        ("BAL", "PIT", (380_000, 95_000), (22_000, 12_000), 72, (-155, 130)),
        ("DET", "CHI", (505_000, 44_000), (35_000, 8_000), 49, (-320, 260)),
    ],
    "nhl": [
        ("WSH", "OTT", (212_000, 40_000), (14_000, 5_000), 45, (-120, 100)),
        ("FLA", "TOR", (98_000, 160_000), (7_000, 15_000), 68, (-110, -110)),
        ("COL", "VGK", (305_000, 88_000), (19_000, 9_000), 52, (-140, 118)),
        ("EDM", "CGY", (77_000, 61_000), (6_000, 4_000), 63, (-175, 150)),
    ],
    "mlb": [
        ("NYY", "BOS", (160_000, 52_000), (11_000, 5_000), 59, (-130, 110)),
        ("LAD", "SD", (405_000, 71_000), (25_000, 7_000), 74, (-185, 160)),
        ("HOU", "TEX", (90_000, 85_000), (8_000, 9_000), 50, (-105, -115)),
        ("ATL", "PHI", (130_000, 190_000), (10_000, 17_000), 46, (110, -130)),
    ],
    "ncaaf": [
        ("Ohio State", "Michigan", (420_000, 118_000), (26_000, 9_000), 58, (-165, 140)),
        ("Georgia", "Alabama", (255_000, 240_000), (17_000, 16_000), 52, (-110, -110)),
        ("Miami (OH)", "Ohio", (46_000, 105_000), (3_000, 8_000), 64, (-102, -118)),
        ("Texas", "Oklahoma", (310_000, 60_000), (20_000, 5_000), 69, (-210, 175)),
    ],
    "ncaab": [
        ("Duke", "North Carolina", (210_000, 90_000), (14_000, 6_000), 61, (-150, 128)),
        ("Kansas", "Kansas State", (150_000, 33_000), (9_000, 3_000), 48, (-260, 215)),
        ("Gonzaga", "Saint Mary's", (120_000, 72_000), (8_000, 5_000), 70, (-135, 115)),
        ("Houston", "Iowa State", (95_000, 101_000), (7_000, 8_000), 55, (-115, -105)),
    ],
}
_PICKS = {  # model picks: (away, home, pick, prob)
    "nba": [("BOS", "PHI", "BOS", 0.69), ("ORL", "SAC", "ORL", 0.66), ("MIA", "UTA", "UTA", 0.52), ("OKC", "POR", "POR", 0.21)],
    "nfl": [("KC", "BUF", "KC", 0.56), ("PHI", "DAL", "PHI", 0.71), ("BAL", "PIT", "PIT", 0.48)],
    "nhl": [("WSH", "OTT", "WSH", 0.58), ("FLA", "TOR", "TOR", 0.5)],
    "mlb": [("LAD", "SD", "LAD", 0.67), ("NYY", "BOS", "BOS", 0.49)],
    "ncaaf": [("Ohio State", "Michigan", "Ohio State", 0.62), ("Texas", "Oklahoma", "Texas", 0.7)],
    "ncaab": [("Duke", "North Carolina", "Duke", 0.6), ("Kansas", "Kansas State", "Kansas", 0.8)],
}


def _dec(american: int) -> float:
    return 1 + american / 100 if american > 0 else 1 + 100 / abs(american)


def games(sport: str = "nba") -> list[dict]:
    """Return raw snapshot games in the same shape the live pipeline produces."""
    rng = random.Random(int(datetime.now(timezone.utc).timestamp() // 60) + sum(map(ord, sport)))  # drifts every minute
    base = datetime.now(timezone.utc).replace(minute=5, second=0, microsecond=0) + timedelta(hours=3)
    out = []
    for i, (a_raw, h_raw, k, n, pub_a, (pa, ph)) in enumerate(_ROWS.get(sport, [])):
        a, h = norm_team(a_raw, sport), norm_team(h_raw, sport)
        jitter = lambda v: int(v * rng.uniform(0.97, 1.03))  # noqa: E731
        dec_a, dec_h = _dec(pa), _dec(ph)
        books = {
            "pinnacle": {a: dec_a, h: dec_h},
            "draftkings": {a: round(dec_a * rng.uniform(0.97, 1.08), 3), h: round(dec_h * rng.uniform(0.97, 1.08), 3)},
            "fanduel": {a: round(dec_a * rng.uniform(0.97, 1.08), 3), h: round(dec_h * rng.uniform(0.97, 1.08), 3)},
        }
        handle_a = max(5, min(95, pub_a + rng.randint(-8, 8)))
        out.append({
            "key": pair_key(a, h, sport), "sport": sport, "away": a, "home": h,
            "start": (base + timedelta(minutes=30 * i)).isoformat(),
            "liquidity": {"kalshi": {a: jitter(k[0]), h: jitter(k[1])}, "novig": {a: jitter(n[0]), h: jitter(n[1])}},
            "prices": {"kalshi": {a: round(1 / dec_a * 0.99, 3), h: round(1 / dec_h * 0.99, 3)}},
            "public": {"bets": {a: pub_a, h: 100 - pub_a}, "handle": {a: handle_a, h: 100 - handle_a}},
            "odds": books,
        })
    return out


def alts(g: dict) -> dict:
    """Demo spread + total markets for one game (two lines each, so the 'main line' choice is exercised)."""
    rng = random.Random(sum(map(ord, g["key"])))
    a, h = g["away"], g["home"]
    base = {"nba": 5.5, "nfl": 3.5, "nhl": 1.5, "mlb": 1.5, "ncaaf": 7.5, "ncaab": 6.5}.get(g["sport"], 3.5)
    tot = {"nba": 224.5, "nfl": 44.5, "nhl": 6.5, "mlb": 8.5, "ncaaf": 55.5, "ncaab": 142.5}.get(g["sport"], 44.5)
    out = {}
    for mtype, lines in (("spread", [-base, -base - 1]), ("total", [tot, tot + 1])):
        for j, line in enumerate(lines):
            scale = 1.0 if j == 0 else 0.25
            lean = rng.uniform(0.35, 0.85)
            sides = (a, h) if mtype == "spread" else ("OVER", "UNDER")
            liq = {}
            for bk, mult in (("kalshi", 1.0), ("novig", 0.6), ("prophetx", 0.4)):
                t = rng.randint(80_000, 700_000) * mult * scale
                liq[bk] = {sides[0]: round(t * lean), sides[1]: round(t * (1 - lean))}
            out[f"{mtype}:{line:g}"] = {"mtype": mtype, "line": line, "liquidity": liq}
    return out


def model_picks(sport: str = "nba") -> list[dict]:
    return [{"away": norm_team(a, sport), "home": norm_team(h, sport), "pick": norm_team(p, sport), "prob": pr}
            for a, h, p, pr in _PICKS.get(sport, [])]


def history(unit: float = 25.0) -> list[dict]:
    """Fake previous nights for the Nightly log page (mirrors the 'Day 1 returns' card Tyler showed)."""
    now = datetime.now(timezone.utc)
    nights = [
        (1, [("nhl", "WSH", "WSH @ OTT", 1.952, "won"), ("nba", "BOS", "BOS @ PHI", 1.84, "won"),
             ("nhl", "FLA", "FLA @ TOR", 1.847, "won"), ("nba", "ORL", "ORL @ SAC", 1.62, "won"),
             ("ncaaf", "miami oh", "Miami (OH) @ Ohio", 1.98, "lost")]),
        (2, [("nfl", "KC", "KC @ BUF", 1.91, "lost"), ("mlb", "LAD", "LAD @ SD", 1.54, "won"),
             ("nba", "MIA", "MIA @ UTA", 1.71, "won"), ("nhl", "COL", "COL @ VGK", 1.8, "lost")]),
    ]
    rows = []
    for days_ago, plays in nights:
        day = (now - timedelta(days=days_ago)).date().isoformat()
        for sport, team, desc, dec, res in plays:
            rows.append({
                "sport": sport, "game_key": desc, "description": f"{team.upper() if sport not in ('ncaaf', 'ncaab') else team.title()} ML ({desc})",
                "team": team, "book": "pinnacle", "odds_decimal": dec, "stake": unit, "source": "demo",
                "status": res, "pnl": round(unit * (dec - 1), 2) if res == "won" else -unit,
                "day": day, "log_key": f"demo:{day}:{desc}",
            })
    return rows
