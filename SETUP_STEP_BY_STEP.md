# Step by step: run, log in, test with REAL data, then switch to the client's database (v1.2)

All commands run inside the `backend` folder.

## A. First run (demo data) and LOG IN
1. `python -m venv .venv` then activate it (Windows: `.venv\Scripts\activate`), then `pip install -r requirements.txt`
2. `copy .env.example .env` (Mac/Linux: `cp`). Leave DEMO_MODE=true for now.
3. `python -m uvicorn app.main:app --port 8000` and open http://localhost:8000
4. **Press Ctrl+Shift+R** once (an old project cached on localhost:8000 made your page blank).
5. You now see a login screen: click **Create one**, sign up with any email + 8+ character password. Sign out = Profile page > Account > **Sign out**.
   Optional in `.env`: `ADMIN_EMAILS=you@mail.com` (admin), `ALLOW_SIGNUP=false` + `ALLOWED_EMAILS=a@x.com,b@x.com` (invite only), `REQUIRE_LOGIN=false` (no login screen).

## B. Real data
1. In `.env` set `DEMO_MODE=false`. Put your key as `ODDS_API_KEY=abc123` (no quotes, no "your_key_here" text) - Pinnacle odds, +EV, auto-grading.
2. Kalshi needs no key. DraftKings splits need no key.
3. Stop the server, run `python -m app.diagnose`. It prints [OK]/[FAIL] for every source and sport with a sample game. **Send me that output** if anything says FAIL or 0 games - the Kalshi and DraftKings adapters were written from public docs/page text and could not be tested from my sandbox (it has no internet to those sites).
4. Start the server again. The first live load runs in the background; the page shows "WARMING UP" and fills in within ~1 minute. Profile > Data connections shows each source LIVE/ERROR.
5. DraftKings: open the splits page in your browser, pick each league, copy the address-bar URL into `DK_SPLITS_URL_NBA=...` (etc.) if a league shows 0 games. Check once that Handle and Bets are not swapped (compare one game); if swapped set `DK_SPLITS_ORDER=bets,handle`.

## C. Your own Supabase first, client's later
1. supabase.com > New project (free). Wait until ready.
2. SQL Editor > New query > paste `supabase/schema.sql` > Run.
3. Project Settings > API: copy **Project URL** and the **service_role** key (not anon). Put in `.env`: `SUPABASE_URL=...` and `SUPABASE_SERVICE_KEY=...` (server only, never in website code).
4. Run `python -m app.diagnose`: Storage must say `[OK] Supabase`. Restart, sign up, add a play, and look at Table Editor > `plays` and `kv` (accounts and sessions are rows in `kv`, passwords hashed).
5. **Switching to the client's project:** he creates a Supabase project (or invites you), runs the same `schema.sql`, gives you the URL + service_role key. Change the two values in `.env` (or in Render's Environment tab), restart. Data is not copied between projects; the nightly log starts fresh and users sign up again. Keep `.env` out of git.

## D. Deploy (later)
Render (backend) env vars = your `.env` values + `CORS_ORIGINS=https://lgdsanalytics.vercel.app`; `ADMIN_EMAILS` set; Vercel site embeds the dashboard URL (iframe) or points `window.SHARP_API_BASE` at the Render URL.

## E. What is still not finished (be honest with the client)
- Novig and ProphetX: still stubs. Both need API keys from your own/ his accounts (Novig: Profile > Settings > Novig API, key pair, ID + location check; ProphetX: API Integration for trading keys, Market Data key by email) and the exact endpoint paths from docs.novig.com / docs.prophetx.co, which I could not open. Send me those two doc pages (or the OpenAPI file) and I fill them in.
- Pinnacle has no public liquidity: it appears as odds only.
- Sportsbook logos: drop official files in `backend/static/logos/` (see README there).
- Tyler's model picks: need his endpoint/format (MODEL_PICKS_URL).
- Show Novig/ProphetX data publicly only with their written permission.

## v1.3 notes
- Games that already started are hidden (pre-game money only). Add `?include_started=true` to /api/sharp to see them.
- EV edges above MAX_EV_PCT (25%) are treated as bad data and ignored.
- Menu = hamburger button (top left). Phones show tables as cards. Search box suggests teams/games as you type.
- Old demo plays are deleted automatically when DEMO_MODE=false.

## Novig (v1.4)
Novig's market data is PUBLIC - no key needed. In `.env`: `NOVIG_ENABLED=true`. Leave `NOVIG_BASE_URL` empty (real exchange, https://api.novig.com - confirm on docs.novig.com) or set `https://api.paper.novig.com` for play-money tests (not real money). Run `python -m app.diagnose`: it prints the address, the leagues Novig lists and sample games. Keys are only needed to place orders, which this tool never does.
