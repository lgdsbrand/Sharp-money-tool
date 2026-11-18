"""Pure calculation helpers: sharp lean, flags, de-vig, +EV, Kelly staking, model agreement.

Nothing in here touches the network or the database, so it is fully unit-tested.
"""
from __future__ import annotations

from typing import Optional

from .config import settings

EXCHANGES = ["kalshi", "novig", "prophetx", "polymarket"]


# ---------------------------------------------------------------- odds maths
def american_to_decimal(american: float) -> float:
    return 1 + american / 100 if american > 0 else 1 + 100 / abs(american)


def decimal_to_american(dec: float) -> int:
    if not dec or dec <= 1.0001:  # no real price (0, 1.00): never divide by zero
        return 0
    return round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1))


def implied_prob(decimal_odds: float) -> float:
    return 1 / decimal_odds


def devig_two_way(dec_a: float, dec_b: float) -> tuple[float, float]:
    """Remove the bookmaker margin from a two-way market (proportional method)."""
    pa, pb = implied_prob(dec_a), implied_prob(dec_b)
    total = pa + pb
    return pa / total, pb / total


def ev_pct(fair_prob: float, decimal_odds: float) -> float:
    """Expected value as % of stake when you bet at `decimal_odds` and the true win prob is `fair_prob`."""
    return (fair_prob * decimal_odds - 1) * 100


def kelly_stake(
    bankroll: float,
    win_prob: float,
    decimal_odds: float,
    fraction: float = 0.25,
    max_pct: float = 5.0,
) -> float:
    """Fractional Kelly stake in currency, capped at `max_pct` % of bankroll. 0 when there is no edge."""
    b = decimal_odds - 1
    if b <= 0 or bankroll <= 0:
        return 0.0
    full = (b * win_prob - (1 - win_prob)) / b
    if full <= 0:
        return 0.0
    stake = bankroll * full * fraction
    return round(min(stake, bankroll * max_pct / 100), 2)


# ------------------------------------------------------------- sharp lean
def sharp_lean(liquidity: dict[str, dict[str, float]], teams: tuple[str, str]) -> dict:
    """Combine per-exchange dollars into a total + percentage split for the two teams."""
    totals = {t: 0.0 for t in teams}
    for book_liq in liquidity.values():
        for t in teams:
            totals[t] += float(book_liq.get(t, 0) or 0)
    total = sum(totals.values())
    if total <= 0:
        return {"total": 0.0, "pct": {t: 0.0 for t in teams}, "side": None, "side_pct": 0.0, "totals": totals}
    pct = {t: round(totals[t] / total * 100, 1) for t in teams}
    side = max(teams, key=lambda t: totals[t])
    return {"total": round(total, 2), "pct": pct, "side": side, "side_pct": pct[side], "totals": totals}


def best_prices(odds: dict[str, dict[str, float]], team: str, exclude: tuple[str, ...] = ("pinnacle",)) -> Optional[dict]:
    """Best (highest) decimal price available for `team` outside Pinnacle."""
    best = None
    for book, prices in odds.items():
        if book in exclude or team not in prices:
            continue
        if best is None or prices[team] > best["decimal"]:
            best = {"book": book, "decimal": prices[team]}
    return best


def ev_table(game: dict) -> dict:
    """For a game: fair probability from Pinnacle, and +EV for every other book / exchange price."""
    a, h = game["away"], game["home"]
    pin = game.get("odds", {}).get("pinnacle")
    if not pin or pin.get(a, 0) <= 1.0001 or pin.get(h, 0) <= 1.0001:
        return {"available": False, "rows": []}
    fa, fh = devig_two_way(pin[a], pin[h])
    fair = {a: fa, h: fh}
    rows = []
    for book, prices in game.get("odds", {}).items():
        if book == "pinnacle":
            continue
        for team in (a, h):
            if prices.get(team, 0) > 1.0001:
                rows.append({
                    "team": team, "book": book, "decimal": round(prices[team], 3),
                    "american": decimal_to_american(prices[team]),
                    "fair_prob": round(fair[team] * 100, 1),
                    "ev_pct": round(ev_pct(fair[team], prices[team]), 2),
                })
    # exchange prices (contract price = implied prob; ignores exchange fees)
    for book, prices in game.get("prices", {}).items():
        for team in (a, h):
            p = prices.get(team)
            if p and 0 < p < 1:
                dec = 1 / p
                rows.append({
                    "team": team, "book": book, "decimal": round(dec, 3),
                    "american": decimal_to_american(dec),
                    "fair_prob": round(fair[team] * 100, 1),
                    "ev_pct": round(ev_pct(fair[team], dec), 2),
                })
    rows = [r for r in rows if r["ev_pct"] <= settings.MAX_EV_PCT]  # +60% is a stale or in-play price, not an edge
    rows.sort(key=lambda r: r["ev_pct"], reverse=True)
    return {
        "available": True,
        "pinnacle": {t: {"decimal": pin[t], "american": decimal_to_american(pin[t]), "fair_prob": round(fair[t] * 100, 1)} for t in (a, h)},
        "rows": rows,
    }


# ------------------------------------------------------- full game assembly
def build_game_view(raw: dict, min_liquidity: float, bankroll: float = 0.0, kelly_fraction: float = 0.25) -> dict:
    """Turn one raw snapshot game into what the dashboard shows (lean, flags, EV, model agreement)."""
    a, h = raw["away"], raw["home"]
    lean = sharp_lean(raw.get("liquidity", {}), (a, h))
    public_bets = (raw.get("public") or {}).get("bets") or {}
    public_handle = (raw.get("public") or {}).get("handle") or {}
    flags: list[dict] = []

    side = lean["side"]
    other = None
    if side:
        other = h if side == a else a
        if lean["side_pct"] >= settings.HEAVY_SHARP_PCT:
            flags.append({"type": "heavy_sharp", "team": side, "label": f"HEAVY SHARP {side}"})
        if lean["total"] >= settings.BIG_MONEY_TOTAL:
            flags.append({"type": "big_money", "team": side, "label": f"BIG MONEY ${lean['total'] / 1000:,.0f}K"})
        pub_other = public_bets.get(other)
        if (
            pub_other is not None
            and lean["side_pct"] >= 55
            and pub_other >= settings.FADE_PUBLIC_PCT
        ):
            flags.append({
                "type": "fade", "team": side,
                "label": f"FADE: sharp {side}, public {other} {pub_other:.0f}%",
            })

    public_side = max(public_bets, key=public_bets.get) if public_bets else None
    conflict = bool(side and public_side and public_side != side and public_bets.get(public_side, 0) >= 50)

    ev = ev_table(raw)
    best_ev = ev["rows"][0] if ev["available"] and ev["rows"] else None
    if best_ev and best_ev["ev_pct"] >= settings.MIN_EV_PCT:
        flags.append({"type": "plus_ev", "team": best_ev["team"], "label": f"+EV {best_ev['team']} {best_ev['ev_pct']:+.1f}% @ {best_ev['book']}"})

    model = raw.get("model")
    model_status = None
    if model and model.get("pick"):
        if side and model["pick"] == side:
            model_status = "agrees"
            flags.append({"type": "model_agrees", "team": side, "label": f"MODEL AGREES {side}"})
        elif side:
            model_status = "disagrees"

    view = {
        "key": raw["key"], "sport": raw.get("sport", "nba"), "away": a, "home": h,
        "start": raw.get("start"),
        "liquidity": raw.get("liquidity", {}),
        "total": lean["total"], "lean": lean["pct"], "sharp_side": side, "sharp_pct": lean["side_pct"],
        "public": {"bets": public_bets, "handle": public_handle}, "public_side": public_side, "conflict": conflict,
        "flags": flags, "model": model, "model_status": model_status,
        "ev": ev, "best_ev": best_ev,
        "visible": lean["total"] >= min_liquidity,
    }

    # suggested play: first flagged side we can price
    suggestion = suggest_play(view, raw, bankroll, kelly_fraction)
    if suggestion:
        view["suggestion"] = suggestion
    return view


def suggest_play(view: dict, raw: dict, bankroll: float, kelly_fraction: float) -> Optional[dict]:
    reasons = [f for f in view["flags"] if f["type"] in ("heavy_sharp", "fade", "plus_ev", "model_agrees")]
    if not reasons:
        return None
    best_ev = view.get("best_ev")
    # prefer a +EV pick, else follow the sharp side
    if best_ev and best_ev["ev_pct"] >= settings.MIN_EV_PCT:
        team, book, dec = best_ev["team"], best_ev["book"], best_ev["decimal"]
        win_prob = best_ev["fair_prob"] / 100
    elif view["sharp_side"]:
        team = view["sharp_side"]
        bp = best_prices(raw.get("odds", {}), team)
        if not bp:
            return {"team": team, "book": None, "decimal": None, "stake": None, "reasons": [r["label"] for r in reasons]}
        book, dec = bp["book"], bp["decimal"]
        pin = ev_table(raw)
        win_prob = (pin["pinnacle"][team]["fair_prob"] / 100) if pin["available"] else None
    else:
        return None
    stake = kelly_stake(bankroll, win_prob, dec, kelly_fraction) if (bankroll and win_prob) else None
    return {
        "team": team, "book": book, "decimal": dec, "american": decimal_to_american(dec) if dec else None,
        "win_prob": round(win_prob * 100, 1) if win_prob else None,
        "stake": stake, "reasons": [r["label"] for r in reasons],
    }
