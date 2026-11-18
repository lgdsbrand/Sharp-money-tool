"""Run:  python -m app.diagnose   (from the backend folder, with your .env filled in)

Calls every live data source once per sport and prints what came back, so you can see exactly what
works on YOUR computer. Nothing is written except the optional Supabase check.
"""
from __future__ import annotations

import asyncio
import sys

from .config import settings
import httpx

from .sources import dk_splits, kalshi, novig, odds_api, polymarket, prophetx
from .store import make_store


async def _try(label: str, coro):
    try:
        res = await asyncio.wait_for(coro, settings.SOURCE_TIMEOUT)
        print(f"  [OK]    {label}: {len(res)} games")
        return res
    except (prophetx.NeedsSetup, novig.NeedsSetup) as e:
        print(f"  [SETUP] {label}: {e}")
    except Exception as e:
        print(f"  [FAIL]  {label}: {type(e).__name__}: {e}")
    return []


async def _dump_dk(sport: str) -> None:
    """When DraftKings gives 0 games, show what the server actually received."""
    url = dk_splits._url(sport, 1)
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}) as c:
            r = await c.get(url)
        lines = dk_splits._lines(r.text)
        name = f"diagnose_dk_{sport}.txt"
        open(name, "w", encoding="utf-8").write(f"URL: {url}\nSTATUS: {r.status_code}\nBYTES: {len(r.text)}\n\n" + "\n".join(lines[:400]))
        if sport == "nfl":
            import re
            open("diagnose_dk_raw.html", "w", encoding="utf-8").write(r.text)
            urls = sorted(set(re.findall(r"""(?:src|href|data-[\w-]+)=["']([^"']*(?:split|api|json|widget|iframe|odds|handle)[^"']*)["']""", r.text, re.I)))[:40]
            urls += sorted(set(re.findall(r"https?://[^\s\"'<>\\]+(?:api|json|split|handle)[^\s\"'<>\\]*", r.text, re.I)))[:40]
            open("diagnose_dk_urls.txt", "w", encoding="utf-8").write("\n".join(dict.fromkeys(urls)))
            print("          data-related URLs found in the page (saved to diagnose_dk_urls.txt):")
            for u in list(dict.fromkeys(urls))[:15]:
                print("            ", u[:160])
            print("          '%' signs in text:", sum(1 for l in lines if '%' in l), "| has 'Moneyline':", any('Moneyline' in l for l in lines))
        print(f"          url={url}\n          status={r.status_code} bytes={len(r.text)} text_lines={len(lines)} -> saved {name}")
        print("          first lines:", " | ".join(lines[:12])[:300])
    except Exception as e:
        print(f"          could not fetch {url}: {type(e).__name__}: {e}")


async def _dump_kalshi() -> None:
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{settings.KALSHI_BASE_URL}/markets", params={"series_ticker": kalshi.SERIES["nba"], "status": "open", "limit": 2})
        ms = r.json().get("markets", [])
        keep = ("ticker", "title", "yes_sub_title", "volume", "volume_fp", "volume_24h", "open_interest", "open_interest_fp", "liquidity", "liquidity_dollars", "last_price", "last_price_dollars", "yes_bid_dollars", "yes_ask_dollars", "close_time", "expected_expiration_time")
        import json
        open("diagnose_kalshi_sample.json", "w").write(json.dumps(ms, indent=1))
        for m in ms[:1]:
            print("  kalshi raw sample:", {k: m.get(k) for k in keep if k in m})
        print("  (full sample saved to diagnose_kalshi_sample.json)")
    except Exception as e:
        print(f"  kalshi raw sample failed: {e}")


async def main() -> int:
    print("=== Sharp Money Tool diagnose ===")
    print(f"DEMO_MODE={settings.DEMO_MODE}  REQUIRE_LOGIN={settings.REQUIRE_LOGIN}  sports={','.join(settings.SPORTS)}")
    if settings.DEMO_MODE:
        print("NOTE: DEMO_MODE=true, so the dashboard shows sample data. Set DEMO_MODE=false in .env for real data.\n")

    print("\n-- Storage")
    store = make_store()
    kind = "Supabase" if settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY else "SQLite file"
    try:
        print(f"  [OK]    {kind}: {await store.ping()}")
    except Exception as e:
        print(f"  [FAIL]  {kind}: {e}\n          (did you run supabase/schema.sql in the SQL editor? is the key the service_role key?)")

    print("\n-- Odds feed (Pinnacle + books)")
    if not settings.ODDS_API_KEY:
        print("  [OFF]   ODDS_API_KEY is empty")
    elif "--odds" not in sys.argv:
        print("  [SKIP]  not tested, to save your monthly credits (each test costs ~5). Add --odds to test it: python -m app.diagnose --odds")
    else:
        for sp in settings.SPORTS:
            try:
                if not await odds_api.in_season(sp):
                    print(f"  [SKIP]  {sp}: out of season (no credits used)")
                    continue
            except Exception:
                pass
            res = await _try(sp, odds_api.fetch(sp))
            if res:
                g = res[0]
                print(f"          sample: {g['away']} @ {g['home']}  books={list(g['odds'])}")
        print(f"  credits: {odds_api.last_usage}")

    print("\n-- Kalshi (exchange money)")
    for sp in settings.SPORTS:
        res = await _try(sp, kalshi.fetch(sp))
        if res:
            g = res[0]
            print(f"          sample: {g['away']} @ {g['home']}  money={g['liquidity']}")

    await _dump_kalshi()

    print("\n-- DraftKings public splits")
    for sp in settings.SPORTS:
        res = await _try(sp, dk_splits.fetch(sp))
        if res:
            g = res[0]
            print(f"          sample: {g['away']} @ {g['home']}  bets={g['bets']} handle={g['handle']}  (columns read as: {g['columns']})")
            print("          CHECK once on the website that the Handle/Bets columns are not swapped; if they are, set DK_SPLITS_ORDER=bets,handle")
        else:
            await _dump_dk(sp)
            print(f"          {sp}: if 0 and no error, open the DraftKings splits page, pick the league, and paste the address-bar URL into DK_SPLITS_URL_{sp.upper()}")

    print("\n-- Novig / ProphetX / Polymarket")
    if settings.NOVIG_ENABLED:
        print(f"  novig address: {novig._base()}")
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                leagues = await novig._leagues(c)
            print(f"  novig leagues: {leagues}")
        except Exception as e:
            print(f"  [FAIL]  novig address/leagues: {type(e).__name__}: {e}  (set NOVIG_BASE_URL from docs.novig.com)")
        for sp in settings.SPORTS:
            res = await _try(f"novig {sp}", novig.fetch(sp))
            if res:
                g = res[0]
                print(f"          sample: {g['away']} v {g['home']}  resting$={g['liquidity']}")
            st = novig.last_stats.get(sp, {})
            print(f"          detail: leagues={st.get('leagues')} markets={st.get('markets')} open_2way={st.get('open_2way')} "
                  f"books={st.get('books')} kept={st.get('kept')} statuses={st.get('statuses')}")
            if st.get("sample_outcomes"):
                print(f"          outcome names seen: {st['sample_outcomes']}")
            if st.get("sample_desc"):
                print(f"          market descriptions: {st['sample_desc']}")
            if st.get("sample_event_desc") or st.get("event_error"):
                print(f"          event descriptions: {st.get('sample_event_desc')} {st.get('event_error', '')}")
            if st.get("cut_short"):
                print("          (stopped early to stay inside the time limit - rest loads next refresh)")
            if st.get("name_miss"):
                print(f"          names not recognised ({len(st['name_miss'])}): {st['name_miss'][:4]}")
            if st.get("book_error"):
                print(f"          book error: {st['book_error']}")
    else:
        print("  [OFF]   novig (NOVIG_ENABLED=false)")
    if settings.PROPHETX_ENABLED:
        print(f"  prophetx address: {prophetx._base()}")
        for sp in settings.SPORTS:
            res = await _try(f"prophetx {sp}", prophetx.fetch(sp))
            if res:
                g = res[0]
                print(f"          sample: {g['away']} @ {g['home']}  available={g['liquidity']}  prices={g['prices']}")
            st = prophetx.last_stats.get(sp, {})
            print(f"          detail: tournaments={st.get('tournaments')} events={st.get('events')} markets={st.get('markets')} kept={st.get('kept')}")
            if st.get("sample"):
                print(f"          raw selections sample: {str(st['sample'])[:400]}")
            if st.get("name_miss"):
                print(f"          names not recognised: {st['name_miss'][:3]}")
    else:
        print("  [OFF]   prophetx (PROPHETX_ENABLED=false)")
    await _try("polymarket", polymarket.fetch(settings.SPORTS[0])) if settings.POLYMARKET_ENABLED else print("  [OFF]   polymarket (optional)")
    print("\nCopy everything above into a message if something shows FAIL and I'll fix that adapter.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
