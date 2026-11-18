# Sharp Money Tool

A dashboard that shows where the **sharp (exchange) money** is on tonight's games across **NBA, NFL, NHL, MLB, NCAAF and NCAAB**, compares it with the **public betting %**, and flags the plays worth looking at.

**What it does**
- **Top sharp plays**: games where 65%+ of exchange money is on one side (plus "big money" games).
- **Sharp vs public**: games where the public is on one side and the exchange money is on the other (FADE flag).
- **Minimum $ slider** and refresh button; sport tabs; sportsbook logo badges.
- **+EV vs Pinnacle**: removes Pinnacle's margin to get a fair price and compares other books and exchanges against it.
- **Model agrees** flag, **bankroll / Kelly** calculator, **suggested plays tracker**.
- **Nightly log**: before tip-off it logs every heavy sharp play at a flat unit, then grades it from the final score and shows record, profit and ROI per night.
- **Profile** page: your settings, plus a live status list of every data connection.

> **v1.2: start with SETUP_STEP_BY_STEP.md** (login, real data, Supabase switch).

## 1. Run it on your computer (demo data, no keys)

You need Python 3.10+. In PowerShell (VS Code terminal), from this folder:

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope Process Bypass
pip install -r requirements.txt
Copy-Item .env.example .env           # DEMO_MODE=true by default
python -m uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000**. A yellow banner says "Sample feed": every number is fake until you go live.
Run the tests with `python -m pytest -q`. (Use `python -m ...` rather than `uvicorn.exe`: some Windows security policies block unsigned `.exe` files.)

## 2. Get the keys and go live

Edit `backend/.env`, set `DEMO_MODE=false`, restart the server, then open the **Profile** page: it shows each connection as LIVE, SETUP or ERROR with the reason.

| What | Where to get it | Env variable | Status in this build |
|---|---|---|---|
| **Odds (Pinnacle + sportsbooks)** | Sign up at **the-odds-api.com**, the key is emailed to you. Free plan = 500 credits/month (not enough for six sports all day; a paid plan or Tyler's own odds feed is needed for launch). | `ODDS_API_KEY` | Coded from their docs. Not tested live. |
| **Kalshi** | Public market data, no key. | none | Coded from docs, **not tested live**. College series names are guesses: if NCAAF/NCAAB show 0 games, set `KALSHI_SERIES_NCAAF` / `_NCAAB` (find the ticker on kalshi.com). |
| **DraftKings public %** | No key. Scrapes the DK Network splits page. | `DK_SPLITS_URL` (+ `_NFL`, `_NBA`... if each league has its own page) | **Unverified parser.** If the page is JavaScript-rendered it returns nothing. Fallback below. Check DK's terms for automated access. |
| **Novig** | Novig has a self-serve API (docs.novig.com). Keys are created in your Novig account settings; it may need account verification, so Tyler may have to create them. Showing Novig data on a public site may need their written OK. | `NOVIG_ENABLED=true`, `NOVIG_BASE_URL`, `NOVIG_API_KEY_ID`, `NOVIG_PRIVATE_KEY_PATH` | **Not finished**: needs the endpoint paths + signing scheme from their docs filled in `app/sources/novig.py`. |
| **ProphetX** | Ask ProphetX for an affiliate / partner API key. | `PROPHETX_ENABLED=true`, `PROPHETX_AFFILIATE_KEY` | Built from the Market Data docs; needs a Market Data key (email hello@prophetexchange.com). Not yet run on real data. |
| **Pinnacle liquidity** | Pinnacle has no public API and doesn't expose liquidity. | none | Pinnacle is used as **odds** only (via the Odds API), as Tyler agreed. |
| **Supabase (storage)** | supabase.com -> your project -> **SQL Editor**: run `supabase/schema.sql`. Then **Project Settings -> API**: copy the *Project URL* and the **service_role** key. | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | Coded, not tested against a real project. Without it the tool uses a local SQLite file. |
| **His models** | Ask Tyler for the format. | `MODEL_PICKS_URL`, `MODEL_PICKS_TOKEN` | Expects `[{"away":"BOS","home":"PHI","pick":"BOS","prob":0.64}]`. You can also POST that JSON to `/api/model-picks`. |

**Security rules**
- Keys go only in `backend/.env` locally and in the Render dashboard online. Never in the website code, never in git, never in chat.
- The `service_role` key bypasses all database security. Server only.
- Set `ADMIN_TOKEN` in production; then paste the same token on the Profile page so you can add/delete plays.

**Public % fallback** (if the DK scrape fails): push the numbers yourself or from a script:
```
POST /api/public-splits?sport=nfl      header: X-Admin-Token: <token>
[{"away":"Kansas City Chiefs","home":"Buffalo Bills","bets":{"Kansas City Chiefs":41,"Buffalo Bills":59},"handle":{"Kansas City Chiefs":38,"Buffalo Bills":62}}]
```

## 3. Deploy on Tyler's stack
1. Push this folder to GitHub.
2. **Supabase**: run `supabase/schema.sql`.
3. **Render**: New + -> Blueprint -> select the repo (`render.yaml` is included). Add the secrets in the dashboard. Free instances sleep, so also add the repo secrets `API_URL` and `ADMIN_TOKEN` so `.github/workflows/refresh.yml` refreshes and grades every 15 minutes.
4. **Vercel / his site**: add a "Sharp Money Tool" tab. Quickest: an `<iframe src="https://YOUR-APP.onrender.com/">` (set `CORS_ORIGINS` to his domain). Better: port the screens in `backend/static/` into his React app and call the same `/api/...` endpoints; set `window.SHARP_API_BASE` to the Render URL.

## 4. How the numbers are decided
- **Sharp money** = the dollars on each side summed across the connected exchanges (Kalshi traded volume by default; `KALSHI_METRIC` changes it). **Sharp lean** = each side's share of that total.
- **Heavy sharp**: lean >= `HEAVY_SHARP_PCT` (65). **Big money**: total >= `BIG_MONEY_TOTAL`. **Fade**: sharp side >= 55% and public bets >= `FADE_PUBLIC_PCT` (55) on the other side.
- **+EV**: Pinnacle's two prices -> remove the margin -> fair probability; EV = fair prob x other price - 1. Exchange prices ignore exchange fees.
- **Kelly stake**: (b*p - q)/b x fraction (default quarter Kelly), capped at 5% of bankroll.
- **Nightly log**: only heavy-sharp games that have not started, once each, flat `NIGHTLY_UNIT`, at the best non-Pinnacle price (or -110 if no odds feed is connected, noted on the play). Graded from final scores (needs `ODDS_API_KEY`). Moneyline only.
- College team names are matched with a careful prefix rule ("Ohio State" != "Ohio"). If a game fails to match, add a line to `backend/team_aliases.json`.

## 5. Known limits (read before promising anything)
- Built and tested here against **fake feeds only**. The sandbox could not reach Kalshi, DraftKings, Novig, ProphetX or the Odds API, so the live adapters were written from their public docs and have never run against the real services. Expect to spend time on first live run fixing field names.
- Novig and ProphetX are built from their docs but not yet confirmed on real data.
- Moneyline only (DraftKings also publishes spread and total splits).
- Exchange "money" is not the same thing as sportsbook sharp action; it is the signal Tyler asked for.
- Not betting advice. Everything here is information.
