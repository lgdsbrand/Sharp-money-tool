/* Sharp Money Tool dashboard (no build step). Set window.SHARP_API_BASE before this script to point at a remote API. */
(() => {
const API = (window.SHARP_API_BASE || '').replace(/\/$/, '');
const SPORTS = [['all', 'All sports'], ['nba', 'NBA'], ['nfl', 'NFL'], ['nhl', 'NHL'], ['mlb', 'MLB'], ['ncaaf', 'NCAAF'], ['ncaab', 'NCAAB']];
const BOOKS = { kalshi: 'Kalshi', novig: 'Novig', prophetx: 'ProphetX', polymarket: 'Polymarket', pinnacle: 'Pinnacle', draftkings: 'DraftKings', fanduel: 'FanDuel', betmgm: 'BetMGM', caesars: 'Caesars', dk_splits: 'DraftKings', odds_api: 'Odds feed' };
const SHORT = { kalshi: 'K', novig: 'N', prophetx: 'PX', polymarket: 'P', pinnacle: 'PN', draftkings: 'DK', fanduel: 'FD', betmgm: 'MG', caesars: 'CZ', dk_splits: 'DK', odds_api: 'OD' };
const TITLES = { overview: 'Market overview', board: 'Full market board', plays: 'Suggested plays', nightly: 'Nightly log', bankroll: 'Bankroll & risk', profile: 'Profile & settings' };
const DEFAULTS = { name: '', email: '', sport: 'all', minLiq: 100000, bankroll: 1000, risk: 1, kelly: 0.25, unit: 25, token: '' };

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = (n, d = 0) => (n < 0 ? '-' : '') + '$' + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const signed = (n, d = 2) => (n >= 0 ? '+' : '-') + '$' + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const k = (n) => (n >= 1e6 ? '$' + (n / 1e6).toFixed(2) + 'M' : n >= 1e3 ? '$' + Math.round(n / 1e3) + 'K' : '$' + Math.round(n || 0));
const american = (dec) => (!dec ? '—' : dec >= 2 ? '+' + Math.round((dec - 1) * 100) : String(Math.round(-100 / (dec - 1))));
const toDecimal = (a) => (a > 0 ? 1 + a / 100 : 1 + 100 / Math.abs(a));
const pct = (n) => (n == null ? '—' : Math.round(n) + '%');
const num = (v, f) => (Number.isFinite(parseFloat(v)) ? parseFloat(v) : f);

/* ---------- settings (localStorage, always wrapped) ---------- */
function loadSettings() { try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem('sharp_settings') || '{}') }; } catch { return { ...DEFAULTS }; } }
function saveSettings(s) { try { localStorage.setItem('sharp_settings', JSON.stringify(s)); } catch { /* private mode: keep in memory only */ } }
const state = { q: '', auth: null, authMode: 'login', logos: {}, s: loadSettings(), view: 'overview', sport: null, day: 'all', minLiq: null, data: null, sources: null, byId: new Map(), timer: null };
state.sport = state.s.sport; state.minLiq = state.s.minLiq;

/* ---------- accounts ---------- */
function getToken() { try { return localStorage.getItem('sharp_auth') || sessionStorage.getItem('sharp_auth') || ''; } catch { return ''; } }
function setToken(t, remember = true) {
  try {
    localStorage.removeItem('sharp_auth'); sessionStorage.removeItem('sharp_auth');
    if (t) (remember ? localStorage : sessionStorage).setItem('sharp_auth', t);
  } catch { /* ignore */ }
}
function showAuth(mode) {
  if (mode) state.authMode = mode;
  const signup = state.authMode === 'signup';
  $('auth-screen').hidden = false;
  $('auth-title').textContent = signup ? 'Create your account' : 'Log in';
  $('auth-name-row').hidden = !signup; $('auth-submit').textContent = signup ? 'Create account' : 'Log in';
  $('auth-switch-text').textContent = signup ? 'Already have an account?' : 'No account yet?';
  $('auth-switch').textContent = signup ? 'Log in' : 'Create one';
  $('auth-pass').autocomplete = signup ? 'new-password' : 'current-password';
  $('auth-switch').style.display = (signup || !state.auth || state.auth.allow_signup !== false) ? '' : 'none';
}
function paintAccount() {
  const u = state.auth && state.auth.user;
  $('acct-name').textContent = u ? u.name : 'Not signed in'; $('acct-email').textContent = u ? u.email : '';
  $('acct-avatar').textContent = initials(u ? u.name : '');
  $('signout').style.display = u ? '' : 'none';
  $('acct-note').textContent = u ? 'Your tracked plays are private to your account.' : 'Login is switched off on this server (REQUIRE_LOGIN=false).';
  if (u && !state.s.name) { state.s.name = u.name; saveSettings(state.s); paintUser(); }
}
async function signOut() { try { await api('/api/auth/logout', { method: 'POST' }); } catch { /* ignore */ } setToken(''); state.auth.user = null; state.data = null; state.sources = null; showAuth('login'); }
async function boot() {
  try { state.auth = await api('/api/auth/me'); } catch { state.auth = { user: null, require_login: false }; }
  paintAccount();
  if (state.auth.require_login && !state.auth.user) { showAuth('login'); return; }
  $('auth-screen').hidden = true; loadPlays(); load();
}

/* ---------- helpers ---------- */
async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (opts.body) headers['Content-Type'] = 'application/json';
  if (opts.method && opts.method !== 'GET' && state.s.token) headers['X-Admin-Token'] = state.s.token;
  const tok = getToken(); if (tok) headers.Authorization = 'Bearer ' + tok;
  const r = await fetch(API + path, { ...opts, headers });
  if (r.status === 401 && !path.startsWith('/api/auth/') && state.auth && state.auth.require_login) { setToken(''); state.auth.user = null; showAuth(); throw new Error('Please log in.'); }
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = j.detail || msg; } catch { /* ignore */ }
    throw new Error(r.status === 401 ? 'Admin token missing or wrong. Add it on the Profile page.' : msg);
  }
  return r.json();
}
let toastTimer;
function toast(msg, bad = false) {
  const t = $('toast'); t.textContent = msg; t.className = 'toast' + (bad ? ' bad' : ''); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
}
function badge(book) {
  const key = String(book || '').toLowerCase(); const t = SHORT[key] || key.slice(0, 2).toUpperCase();
  const file = state.logos[key];
  const inner = file ? `<img src="${API}/static/logos/${esc(file)}" alt="" onerror="this.parentNode.textContent=this.parentNode.dataset.t">` : esc(t);
  return `<span class="bk ${esc(key)}" data-t="${esc(t)}" title="${esc(BOOKS[key] || key)}">${inner}</span>`;
}
function whenLabel(iso) {
  if (!iso) return '';
  const d = new Date(iso); if (isNaN(d)) return '';
  const today = new Date().toDateString() === d.toDateString();
  const t = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  return today ? t : d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + t;
}
const SLUG_X = { nba: { GSW: 'gs', NOP: 'no', NYK: 'ny', SAS: 'sa', UTA: 'utah', WAS: 'wsh' }, nfl: { WAS: 'wsh' },
  nhl: { LAK: 'la', NJD: 'nj', SJS: 'sj', TBL: 'tb', UTA: 'utah' }, mlb: { CHW: 'chw', ATH: 'oak', SDP: 'sd', SFG: 'sf', TBR: 'tb', KCR: 'kc' } };
const LEAGUE_SLUG = { nba: 'nba', nfl: 'nfl', nhl: 'nhl', mlb: 'mlb', ncaaf: 'college-football', ncaab: 'mens-college-basketball' };
const fill = (tpl, o) => tpl.replace(/\{(\w+)\}/g, (_, x) => o[x] || '');
function tl(v, team) {   // team logo (pro leagues; colleges fall back to just the name)
  const L = state.data && state.data.logos; if (!L || !L.team || !SLUG_X[v.sport]) return '';
  const slug = SLUG_X[v.sport][team] || String(team).toLowerCase();
  return `<img class="tlogo" loading="lazy" alt="" src="${esc(fill(L.team, { sport: v.sport, slug }))}" onerror="this.remove()">`;
}
function chip(v) {
  const L = state.data && state.data.logos;
  const img = L && L.league ? `<img class="llogo" alt="" src="${esc(fill(L.league, { slug: LEAGUE_SLUG[v.sport] }))}" onerror="this.remove()">` : '';
  return `<span class="sport-chip">${img}${esc(v.label)}</span>`;
}
const nm = (v, team) => (team === v.away ? v.away_name : team === v.home ? v.home_name : team);
const other = (v, team) => (team === v.away ? v.home : v.away);
function flagPills(v) {
  const out = [];
  for (const f of v.flags) {
    if (f.type === 'heavy_sharp') out.push(`<span class="signal-pill">HEAVY SHARP · ${esc(nm(v, f.team))}</span>`);
    else if (f.type === 'big_money') out.push(`<span class="signal-pill blue">BIG MONEY ${k(v.total)}</span>`);
    else if (f.type === 'fade') out.push(`<span class="signal-pill amber">FADE PUBLIC</span>`);
    else if (f.type === 'plus_ev') out.push(`<span class="signal-pill">+EV ${esc(nm(v, f.team))}</span>`);
    else if (f.type === 'model_agrees') out.push(`<span class="signal-pill blue">MODEL AGREES</span>`);
  }
  if (v.model_status === 'disagrees') out.push(`<span class="signal-pill red">MODEL DIFFERS</span>`);
  return `<div class="flags">${out.join('') || '<span class="muted-cell">—</span>'}</div>`;
}
const gameCell = (v) => `<div class="event-name">${chip(v)}<span class="tm">${tl(v, v.away)}${esc(v.away_name)}</span> @ <span class="tm">${tl(v, v.home)}${esc(v.home_name)}</span></div><div class="event-meta">${esc(whenLabel(v.start))}</div>`;
const MKT = { moneyline: 'Moneyline', spread: 'Spread', total: 'Total' };
const sideName = (v, mk, side) => (mk.type === 'total' ? (side === 'OVER' ? 'Over' : 'Under') + ' ' + mk.line : mk.type === 'spread' ? nm(v, side) + ' ' + ((side === v.home ? mk.line : -mk.line) > 0 ? '+' : '') + (side === v.home ? mk.line : -mk.line) : nm(v, side));
const sideLogo = (v, mk, side) => (mk.type === 'total' ? '' : tl(v, side));
const mktTag = (mk) => `<span class="mkt-tag ${mk.type}">${MKT[mk.type]}</span>`;
function marketRows(v) {   // Moneyline / Spread / Total, $ on each side and the split per exchange
  return (v.markets || []).map((mk) => {
    const [s0, s1] = mk.sides;
    const per = Object.entries(mk.liquidity).map(([bk, l]) => `<span class="ex-chip">${badge(bk)}${k(l[s0] || 0)}<small>/ ${k(l[s1] || 0)}</small></span>`).join('');
    return `<div class="mkt-row${mk.heavy ? ' heavy' : ''}"><div class="mkt-head">${mktTag(mk)}<b>${sideLogo(v, mk, mk.side)}${esc(sideName(v, mk, mk.side))}</b><span class="mkt-pct">${mk.side_pct}% of ${k(mk.total)}</span></div>
      <div class="mkt-split"><span>${sideLogo(v, mk, s0)}${esc(sideName(v, mk, s0))} <b>${k(mk.totals[s0] || 0)}</b></span><div class="source-bar split"><span style="width:${mk.pct[s0]}%"></span></div><span><b>${k(mk.totals[s1] || 0)}</b> ${sideLogo(v, mk, s1)}${esc(sideName(v, mk, s1))}</span></div>
      <div class="ex-list">${per}</div></div>`;
  }).join('');
}
const register = (v) => { const id = v.sport + '|' + v.key; state.byId.set(id, v); return id; };

/* ---------- overview ---------- */
function metricCard(head, icon, value, sub) { return `<article class="metric-card"><div class="metric-head">${head} <span>${icon}</span></div><strong>${value}</strong><small>${sub}</small></article>`; }

const matches = (v) => { const q = state.q.trim().toLowerCase(); return !q || `${v.away_name} ${v.home_name} ${v.away} ${v.home} ${v.label}`.toLowerCase().includes(q); };
function labelTables() {
  document.querySelectorAll('.table-wrap table').forEach((t) => {
    const heads = [...t.querySelectorAll('thead th')].map((h) => h.textContent.trim());
    t.querySelectorAll('tbody tr').forEach((tr) => [...tr.children].forEach((td, i) => { if (heads[i]) td.dataset.label = heads[i]; }));
  });
}
function renderOverview() {
  const d = state.data; if (!d) return;
  paintRules();
  const topPlays = d.top_plays.filter(matches), discs = d.discrepancies.filter(matches);
  state.byId.clear();
  const m = d.metrics;
  $('metrics').innerHTML =
    metricCard('GAMES ABOVE MINIMUM', '▦', m.games, `${m.hidden} hidden below ${k(state.minLiq)}`) +
    metricCard('EXCHANGE MONEY', '◉', k(m.liquidity), 'Total across the games shown') +
    metricCard('HEAVY SHARP PLAYS', '↗', m.heavy_sharp, `${d.thresholds.heavy_sharp_pct}%+ of the money on one side`) +
    metricCard('SHARP VS PUBLIC', '⇄', m.conflicts, 'Public is on the other side');

  $('top-count').textContent = `${topPlays.length} PLAYS`;
  $('top-rows').innerHTML = topPlays.length ? topPlays.map((v) => {
    const mk = v.top_market || { type: 'moneyline', side: v.sharp_side, side_pct: v.sharp_pct, total: v.total, liquidity: v.liquidity, sides: [v.away, v.home] };
    const isML = mk.type === 'moneyline', side = isML ? v.sharp_side : mk.side, id = register(v), p = isML ? v.public.bets[side] : null, h = isML ? v.public.handle[side] : null;
    const opp = mk.sides[0] === mk.side ? mk.sides[1] : mk.sides[0];
    const ex = Object.entries(mk.liquidity).map(([bk, l]) => `<span class="ex-chip">${badge(bk)}${k(l[mk.side] || 0)}<small>/ ${k(l[opp] || 0)}</small></span>`).join('');
    const ev = v.best_ev ? `<span class="${v.best_ev.ev_pct >= d.thresholds.min_ev_pct ? 'positive' : 'neutral'}">${v.best_ev.ev_pct > 0 ? '+' : ''}${v.best_ev.ev_pct}%</span> <small class="muted-cell">${esc(nm(v, v.best_ev.team))} ${esc(BOOKS[v.best_ev.book] || v.best_ev.book)}</small>` : '<span class="muted-cell">—</span>';
    return `<tr><td>${gameCell(v)}</td>
      <td>${mktTag(mk)}<div class="team-big">${sideLogo(v, mk, mk.side)}${esc(sideName(v, mk, mk.side))}</div><div class="split-cell"><b>${mk.side_pct}%</b> of ${k(mk.total)}</div><div class="lean-bar"><span style="width:${mk.side_pct}%"></span></div></td>
      <td><div class="ex-list">${ex}</div></td>
      <td class="amount">${p == null ? '<span class="muted-cell">—</span>' : `${pct(p)} <small class="muted-cell">bets</small> · ${pct(h)} <small class="muted-cell">handle</small>`}</td>
      <td>${isML ? ev : '<span class="muted-cell">—</span>'}</td><td>${flagPills(v)}</td>
      <td>${isML ? `<button class="action-btn" data-track="${esc(id)}">+ Track</button>` : '<span class="muted-cell">ML only</span>'}</td></tr>`;
  }).join('') : `<tr class="empty-row"><td colspan="7">No heavy sharp plays above ${k(state.minLiq)} right now. Lower the minimum with the slider.</td></tr>`;

  $('disc-count').textContent = `${discs.length} GAMES`;
  $('disc-rows').innerHTML = discs.length ? discs.map((v) => {
    const side = v.sharp_side, ps = v.public_side, id = register(v);
    return `<tr><td>${gameCell(v)}</td>
      <td><div class="team-big">${tl(v, side)}${esc(nm(v, side))}</div><div class="split-cell"><b>${v.sharp_pct}%</b> of exchange money</div><div class="lean-bar"><span style="width:${v.sharp_pct}%"></span></div></td>
      <td><div class="team-big">${tl(v, ps)}${esc(nm(v, ps))}</div><div class="split-cell"><b>${pct(v.public.bets[ps])}</b> bets · <b>${pct(v.public.handle[ps])}</b> handle</div><div class="lean-bar pub"><span style="width:${v.public.bets[ps] || 0}%"></span></div></td>
      <td class="amount">${k(v.total)}</td><td>${flagPills(v)}</td>
      <td><button class="action-btn" data-track="${esc(id)}">+ Track sharp side</button></td></tr>`;
  }).join('') : `<tr class="empty-row"><td colspan="6">No game above ${k(state.minLiq)} has both exchange money and DraftKings public splits yet. Lower the minimum liquidity, or check back closer to game time.</td></tr>`;

  labelTables();
  const t = d.thresholds;
  $('thresholds').textContent = `FLAGS · HEAVY SHARP = ${t.heavy_sharp_pct}%+ OF EXCHANGE MONEY ON ONE SIDE · FADE = SHARP SIDE ≥55% AND PUBLIC ≥${t.fade_public_pct}% ON THE OTHER · BIG MONEY = ${k(t.big_money)}+ · +EV = ${t.min_ev_pct}%+ VS DE-VIGGED PINNACLE`;
}

/* ---------- market board ---------- */
const dayKey = (iso) => { const d = new Date(iso); return isNaN(d) ? 'tbd' : d.toLocaleDateString('en-CA'); };
function dayLabel(key) {
  if (key === 'tbd') return 'TBD';
  const t = new Date(), tk = t.toLocaleDateString('en-CA'); t.setDate(t.getDate() + 1);
  if (key === tk) return 'Today'; if (key === t.toLocaleDateString('en-CA')) return 'Tomorrow';
  return new Date(key + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}
function renderBoard() {
  const d = state.data; if (!d) return;
  const days = [...new Set(d.games.map((g) => dayKey(g.start)))].sort();
  if (state.day !== 'all' && !days.includes(state.day)) state.day = 'all';
  $('board-days').innerHTML = [['all', 'All days'], ...days.map((x) => [x, dayLabel(x)])].map(([v, l]) => `<button class="sport-tab ${state.day === v ? 'active' : ''}" data-day="${v}">${l}</button>`).join('');
  const games = d.games.filter(matches).filter((v) => state.day === 'all' || dayKey(v.start) === state.day);
  $('board-count').textContent = `${games.length} GAMES`;
  $('board-cards').innerHTML = games.length ? games.map((v) => {
    const id = register(v), a = v.away, h = v.home;
    const rows = Object.entries(v.liquidity).map(([bk, l]) => {
      const tot = (l[a] || 0) + (l[h] || 0) || 1, pa = ((l[a] || 0) / tot) * 100;
      return `<div class="source-row">${badge(bk)}<div class="source-bar split"><span style="width:${pa}%"></span></div><b>${k(l[a] || 0)} / ${k(l[h] || 0)}</b></div>`;
    }).join('');
    const chips = [];
    if (v.public.bets[a] != null) chips.push(`<span class="signal-pill dim">BETS ${pct(v.public.bets[a])} / ${pct(v.public.bets[h])}</span>`, `<span class="signal-pill dim">HANDLE ${pct(v.public.handle[a])} / ${pct(v.public.handle[h])}</span>`);
    if (v.model) chips.push(`<span class="signal-pill blue">MODEL ${esc(nm(v, v.model.pick))}${v.model.prob ? ' ' + Math.round(v.model.prob * 100) + '%' : ''}</span>`);
    if (v.ev.available) chips.push(`<span class="signal-pill ${v.best_ev && v.best_ev.ev_pct >= d.thresholds.min_ev_pct ? '' : 'dim'}">PINNACLE FAIR ${esc(nm(v, a))} ${v.ev.pinnacle[a].fair_prob}% (${american(v.ev.pinnacle[a].decimal)})</span>`);
    if (v.best_ev) chips.push(`<span class="signal-pill ${v.best_ev.ev_pct >= d.thresholds.min_ev_pct ? '' : 'dim'}">BEST EV ${v.best_ev.ev_pct > 0 ? '+' : ''}${v.best_ev.ev_pct}% ${esc(nm(v, v.best_ev.team))} @ ${esc(BOOKS[v.best_ev.book] || v.best_ev.book)} ${american(v.best_ev.decimal)}</span>`);
    return `<article class="market-card"><div class="market-card-head"><div><h3>${chip(v)}<span class="tm">${tl(v, a)}${esc(v.away_name)}</span> @ <span class="tm">${tl(v, h)}${esc(v.home_name)}</span></h3><p>${esc(whenLabel(v.start))} · ${k(v.total)} total · ${v.lean[a]}% ${esc(v.away_name)} / ${v.lean[h]}% ${esc(v.home_name)}</p></div><div>${flagPills(v)}</div></div>
      ${(v.markets || []).length ? marketRows(v) : `<div class="legend"><span><i class="dot"></i>${tl(v, a)}${esc(v.away_name)}</span><span><i class="dot b"></i>${tl(v, h)}${esc(v.home_name)}</span></div>${rows}`}
      <div class="market-bottom">${chips.join('')}</div>
      ${v.sharp_side ? `<div class="card-actions"><button class="action-btn" data-track="${esc(id)}">+ Track ${esc(nm(v, v.sharp_side))}</button></div>` : ''}</article>`;
  }).join('') : '<div class="panel empty-state">No games above the minimum. Lower the slider on the Overview page.</div>';
}

/* ---------- tracking a play from a game ---------- */
async function trackGame(id) {
  const v = state.byId.get(id); if (!v || !v.sharp_side) return;
  const s = v.suggestion || {};
  const side = v.sharp_side;
  const stake = s.stake && state.s.bankroll > 0 ? s.stake : state.s.unit;
  if (!stake) return toast('Set a unit size on the Profile page first.', true);
  try {
    await api('/api/plays', { method: 'POST', body: JSON.stringify({
      sport: v.sport, game_key: v.key, team: side, book: s.book || null, odds_decimal: s.decimal || 1.91, stake,
      description: `${nm(v, side)} ML (${v.away_name} @ ${v.home_name})`, source: 'sharp',
      notes: `${v.sharp_pct}% of ${k(v.total)} on ${nm(v, side)}` + (s.decimal ? '' : ' · assumed -110') }) });
    toast(`Tracking ${nm(v, side)} for ${money(stake, 2)}. See Suggested plays.`);
    loadPlays();
  } catch (e) { toast(e.message, true); }
}

/* ---------- plays ---------- */
state.plays = null;
async function loadPlays() {
  let d; try { d = await api('/api/plays'); state.plays = d; } catch (e) { $('tracked-plays').innerHTML = `<div class="empty-state">${esc(e.message)}</div>`; return; }
  renderPlays(d);
}
function renderPlays(d) {
  const st = d.stats;
  $('play-count').textContent = st.open;
  $('tracked-count').textContent = `${d.plays.length} TRACKED`;
  $('play-stats').innerHTML =
    metricCard('RECORD', '◎', `${st.won}-${st.lost}${st.push ? '-' + st.push : ''}`, st.win_pct == null ? 'No settled plays yet' : `${st.win_pct}% win rate`) +
    metricCard('PROFIT', '$', `<span class="${st.pnl >= 0 ? 'pos' : 'neg'}">${signed(st.pnl)}</span>`, `${money(st.staked, 2)} settled stake`) +
    metricCard('RETURN ON SETTLED', '↗', st.roi_pct == null ? '—' : `<span class="${st.roi_pct >= 0 ? 'pos' : 'neg'}">${st.roi_pct > 0 ? '+' : ''}${st.roi_pct}%</span>`, 'Profit ÷ settled stake') +
    metricCard('OPEN PLAYS', '◷', st.open, 'Waiting for a result');
  const pq = ($('play-search') ? $('play-search').value : '').trim().toLowerCase();
  const shown = d.plays.filter((p) => !pq || `${p.description} ${p.sport} ${p.book || ''} ${p.notes || ''}`.toLowerCase().includes(pq));
  $('tracked-plays').innerHTML = shown.length ? shown.map((p) => `
    <div class="tracked-item"><div><b>${esc(p.description)}</b><small>${esc((p.sport || '').toUpperCase())}${p.book ? ' · ' + esc(BOOKS[p.book] || p.book) : ''} · ${american(p.odds_decimal)}${p.notes ? ' · ' + esc(p.notes) : ''}</small></div>
      <div>${money(p.stake, 2)}<small>stake</small></div>
      <div class="pnl ${p.pnl > 0 ? 'pos' : p.pnl < 0 ? 'neg' : ''}">${p.pnl == null ? '—' : signed(p.pnl)}<small>profit</small></div>
      <select data-result="${esc(p.id)}">${['open', 'won', 'lost', 'push'].map((x) => `<option value="${x}" ${p.status === x ? 'selected' : ''}>${x[0].toUpperCase() + x.slice(1)}</option>`).join('')}</select>
      <button class="remove-btn" data-del="${esc(p.id)}" title="Delete">✕</button></div>`).join('')
    : d.plays.length ? '<div class="empty-state">No tracked play matches your search.</div>' : '<div class="empty-state">No plays tracked yet.<br>Use "+ Track" on the Overview or Market board, or add one above.</div>';
}

/* ---------- nightly log ---------- */
async function loadNightly() {
  let d; try { d = await api('/api/nightly'); } catch (e) { $('nightly-days').innerHTML = `<div class="panel empty-state">${esc(e.message)}</div>`; return; }
  const o = d.overall;
  $('nightly-count').textContent = `TODAY & YESTERDAY · ${money(d.unit)} UNIT`;
  $('nightly-stats').innerHTML =
    metricCard('ALL-TIME RECORD', '◎', `${o.won}-${o.lost}${o.push ? '-' + o.push : ''}`, o.win_pct == null ? 'Nothing settled yet' : `${o.win_pct}% win rate`) +
    metricCard('TOTAL PROFIT', '$', `<span class="${o.pnl >= 0 ? 'pos' : 'neg'}">${signed(o.pnl)}</span>`, `at ${money(d.unit)} flat per play`) +
    metricCard('RETURN ON SETTLED', '↗', o.roi_pct == null ? '—' : `<span class="${o.roi_pct >= 0 ? 'pos' : 'neg'}">${o.roi_pct > 0 ? '+' : ''}${o.roi_pct}%</span>`, `${money(o.staked)} settled stake`) +
    metricCard('STILL OPEN', '◷', o.open, 'Graded automatically after the game');
  const total = d.days.length;
  const mk = d.markets || {}, mrec = (m, label, icon) => { const x = mk[m] || { won: 0, lost: 0, push: 0 };
    return metricCard(label, icon, `${x.won}-${x.lost}${x.push ? '-' + x.push : ''}`, x.win_pct == null ? 'Nothing settled yet' : `${x.win_pct}% · <span class="${(x.pnl || 0) >= 0 ? 'pos' : 'neg'}">${signed(x.pnl || 0)}</span>`); };
  $('nightly-markets').innerHTML = mrec('moneyline', 'MONEYLINES · ALL TIME', 'ML') + mrec('spread', 'SPREADS · ALL TIME', '±') + mrec('total', 'TOTALS · ALL TIME', 'O/U');
  $('nightly-days').innerHTML = d.days.length ? d.days.map((day, i) => {
    const dt = new Date(day.day + 'T12:00:00');
    const label = dt.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
    return `<article class="night-card"><div class="night-top"><div><small>SHARP MONEY · NIGHTLY LOG${d.demo ? ' <span class="tag-demo">DEMO DATA</span>' : ''}</small><h3>${dayLabel(day.day)} · ${day.final ? 'Returns' : 'In progress'}</h3></div><span class="day-chip ${day.final ? '' : 'open'}">${esc(label)}</span></div>
      <div class="night-tiles">
        <div class="night-tile"><small>RECORD</small><b>${esc(day.record)}</b></div>
        <div class="night-tile"><small>PROFIT</small><b class="${day.profit >= 0 ? 'pos' : 'neg'}">${signed(day.profit)}</b></div>
        <div class="night-tile"><small>SETTLED STAKE</small><b>${money(day.settled_stake, 2)}</b></div>
        <div class="night-tile"><small>RETURN ON SETTLED</small><b class="${(day.roi_pct || 0) >= 0 ? 'pos' : 'neg'}">${day.roi_pct == null ? '—' : (day.roi_pct > 0 ? '+' : '') + day.roi_pct + '%'}</b></div></div>
      <div class="table-wrap" style="border:0;background:none"><table><thead><tr><th>PICK</th><th>ODDS</th><th>STAKE</th><th>RESULT</th><th style="text-align:right">PROFIT</th></tr></thead><tbody>
      ${day.plays.map((p) => `<tr><td><div class="event-name">${esc(p.description)}</div><div class="event-meta">${esc((p.sport || '').toUpperCase())}${p.book ? ' · ' + esc(BOOKS[p.book] || p.book) : ''}</div></td><td class="amount">${american(p.odds_decimal)}</td><td class="amount">${money(p.stake, 2)}</td><td><span class="res ${esc(p.status)}">${esc(p.status.toUpperCase())}</span></td><td class="amount ${p.pnl > 0 ? 'pos' : p.pnl < 0 ? 'neg' : ''}" style="text-align:right">${p.pnl == null ? '—' : signed(p.pnl)}</td></tr>`).join('')}
      </tbody></table></div><p class="fine">Flat ${money(d.unit)} unit per play · ${day.open ? day.open + ' still open' : 'All plays settled'}</p></article>`;
  }).join('') : '<div class="panel empty-state">Nothing logged yet.<br>Once live feeds are on, every heavy sharp play is logged before tip-off and graded after the final.</div>';
}

/* ---------- bankroll ---------- */
function calcBankroll() {
  const s = state.s;
  const bank = num($('bankroll-input').value, 0), risk = num($('risk-input').value, 0), odds = num($('calc-odds').value, -110) || -110;
  const dec = toDecimal(odds), stake = bank * risk / 100, profit = stake * (dec - 1);
  $('stake-result').textContent = money(stake, 2);
  $('potential-return').textContent = `Potential profit: ${money(profit, 2)} · Total return: ${money(stake + profit, 2)}`;
  $('risk-bankroll').textContent = money(bank, 2); $('risk-pct').textContent = risk.toFixed(2) + '%';
  $('risk-stake').textContent = money(stake, 2); $('risk-profit').textContent = money(profit, 2);
  $('risk-streak').textContent = risk > 0 ? String(Math.max(1, Math.ceil(Math.log(0.75) / Math.log(1 - risk / 100)))) : '—';
  const p = num($('win-prob').value, 50) / 100, frac = num($('kelly-input').value, 0.25), b = dec - 1;
  const full = (b * p - (1 - p)) / b;
  const kelly = full > 0 ? Math.min(bank * full * frac, bank * 0.05) : 0;
  $('kelly-result').textContent = money(kelly, 2);
  $('kelly-note').textContent = full > 0 ? `Full Kelly would be ${(full * 100).toFixed(1)}% of bankroll; you are using ${(frac * 100).toFixed(0)}% of that.` : 'No edge at these odds and win probability, so the stake is $0.';
  s.bankroll = bank; s.risk = risk; s.kelly = frac;
}

/* ---------- profile ---------- */
function initials(name) { const p = String(name || '').trim().split(/\s+/).filter(Boolean); return p.length ? (p[0][0] + (p[1] ? p[1][0] : '')).toUpperCase() : '?'; }
function paintUser() {
  const s = state.s;
  $('avatar').textContent = $('profile-avatar').textContent = initials(s.name);
  $('chip-name').textContent = s.name || 'Your profile'; $('chip-sub').textContent = s.name ? 'Settings' : 'Set up';
  $('profile-title').textContent = s.name || 'Your profile';
}
function fillProfile() {
  const s = state.s;
  $('set-sport').innerHTML = SPORTS.map(([v, l]) => `<option value="${v}" ${s.sport === v ? 'selected' : ''}>${l}</option>`).join('');
  $('set-name').value = s.name; $('set-email').value = s.email; $('set-minliq').value = s.minLiq; $('set-bankroll').value = s.bankroll;
  $('set-risk').value = s.risk; $('set-kelly').value = s.kelly; $('set-unit').value = s.unit; $('set-token').value = s.token;
  paintUser(); paintAccount(); loadConnections();
}
function saveProfile() {
  const s = state.s;
  Object.assign(s, { name: $('set-name').value.trim(), email: $('set-email').value.trim(), sport: $('set-sport').value,
    minLiq: num($('set-minliq').value, 100000), bankroll: num($('set-bankroll').value, 1000), risk: num($('set-risk').value, 1),
    kelly: num($('set-kelly').value, 0.25), unit: num($('set-unit').value, 25), token: $('set-token').value.trim() });
  saveSettings(s); state.sport = s.sport; state.minLiq = s.minLiq; syncControls(); paintUser();
  toast('Settings saved'); load();
}
const CONN = [
  ['kalshi', 'Kalshi', 'Exchange money. Public market data, no key needed.'],
  ['novig', 'Novig', 'Exchange money. Public Novig order books, no key needed.'],
  ['prophetx', 'ProphetX', 'Exchange money. Needs the ProphetX Market Data key (PROPHETX_AFFILIATE_KEY in .env, PROPHETX_ENABLED=true).'],
  ['polymarket', 'Polymarket', 'Optional extra exchange. Set POLYMARKET_ENABLED=true.'],
  ['odds_api', 'Odds feed (Pinnacle + sportsbooks)', 'Powers +EV vs Pinnacle and auto-grading. Needs ODDS_API_KEY.'],
  ['dk_splits', 'DraftKings public splits', 'Bets % and handle %. Page scrape; or push splits to /api/public-splits.'],
];
async function loadConnections() {
  let d; try { d = await api('/api/sources'); state.sources = d; } catch (e) { $('connections').innerHTML = `<div class="empty-state">${esc(e.message)}</div>`; return; }
  $('conn-mode').textContent = d.mode === 'demo' ? 'DEMO MODE · SAMPLE DATA' : 'LIVE MODE';
  $('connections').innerHTML = CONN.map(([key, label, help]) => {
    const s = d.sources.find((x) => x.name === key);
    let cls = 'off', txt = 'OFF', detail = help;
    if (s) {
      if (s.mode === 'demo') { cls = 'demo'; txt = 'DEMO'; }
      else if (s.mode === 'needs_setup') { cls = 'setup'; txt = 'SETUP'; }
      else if (!s.ok) { cls = 'error'; txt = 'ERROR'; }
      else if (s.mode === 'manual' || s.mode === 'cached') { cls = s.mode; txt = s.mode.toUpperCase(); }
      else { cls = 'live'; txt = 'LIVE'; }
      detail = [help, `${s.games} games`, ...s.errors, ...s.notes].join(' · ');
    }
    return `<div class="conn-row">${badge(key)}<div><b>${esc(label)}</b></div><div><span class="status ${cls}">${txt}</span></div><div><small>${esc(detail)}</small></div></div>`;
  }).join('') + `<div class="conn-row"><span class="bk">DB</span><div><b>Storage</b></div><div><span class="status live">${esc(d.storage.toUpperCase())}</span></div><div><small>${d.storage === 'supabase' ? 'Plays and the nightly log are saved in your Supabase project.' : 'Using a local SQLite file. Add SUPABASE_URL + SUPABASE_SERVICE_KEY for Supabase.'}${d.odds_credits && d.odds_credits.remaining != null ? ' · Odds API credits left: ' + esc(d.odds_credits.remaining) : ''}</small></div></div>`;
  paintProviders(d);
}
function paintProviders(d) {
  const st = (key) => { const s = d.sources.find((x) => x.name === key); if (!s) return ['OFF', '']; if (s.mode === 'demo') return ['DEMO', '']; if (s.mode === 'needs_setup') return ['SETUP', '']; if (!s.ok) return ['ERROR', 'error']; return ['LIVE', 'live']; };
  const list = [['kalshi', 'Kalshi', 'Order book'], ['novig', 'Novig', 'Exchange liquidity'], ['prophetx', 'ProphetX', 'Exchange liquidity'], ['dk_splits', 'DraftKings', 'Public splits'], ['odds_api', 'Pinnacle odds', 'For +EV']];
  $('providers').innerHTML = list.map(([key, name, sub]) => { const [t, c] = st(key); return `<div class="provider">${badge(key)}<div>${name}<small>${sub}</small></div><b class="${c}">${t}</b></div>`; }).join('');
}

/* ---------- loading + chrome ---------- */
function setMode(mode) {
  const chip = $('mode-chip');
  chip.className = 'live-indicator ' + (mode === 'live' ? 'mode-live' : mode === 'demo' ? 'mode-demo' : 'mode-err');
  chip.innerHTML = `<i></i> ${mode === 'live' ? 'LIVE' : mode === 'demo' ? 'SAMPLE FEED' : 'OFFLINE'}`;
  $('demo-banner').hidden = mode !== 'demo';
}
async function load() {
  const q = new URLSearchParams({ sport: state.sport, min_liquidity: state.minLiq, bankroll: state.s.bankroll || 0, kelly_fraction: state.s.kelly || 0.25 });
  try {
    state.data = await api('/api/sharp?' + q);
    $('error-banner').hidden = true; setMode(state.data.mode);
    renderOverview(); renderBoard();
    clearTimeout(state.warmTimer);
    if (state.data.warming) { $('foot-note').textContent = 'Loading live data from the exchanges… first load can take up to a minute.'; $('mode-chip').innerHTML = '<i></i> WARMING UP'; state.warmTimer = setTimeout(load, 5000); return; }
    $('foot-note').textContent = state.data.updated_at ? 'Updated ' + new Date(state.data.updated_at).toLocaleTimeString() : '';
    if (!state.sources) loadConnections();
  } catch (e) {
    if (e.message === 'Please log in.') return;
    setMode('error'); $('error-banner').hidden = false; $('error-text').textContent = e.message + ' — start the server with: python -m uvicorn app.main:app';
  }
}
function syncControls() {
  const chips = SPORTS.map(([v, l]) => `<button class="sport-tab ${state.sport === v ? 'active' : ''}" data-sport="${v}">${l}</button>`).join('');
  $('sport-tabs').innerHTML = chips; $('board-sports').innerHTML = chips;
  $('min-liq').value = state.minLiq; $('min-liq-val').textContent = k(state.minLiq);
  $('bankroll-input').value = state.s.bankroll; $('risk-input').value = state.s.risk; $('kelly-input').value = state.s.kelly;
  calcBankroll();
}
function show(view) {
  state.view = view;
  document.querySelectorAll('.view').forEach((x) => x.classList.toggle('active', x.id === 'view-' + view));
  document.querySelectorAll('.nav').forEach((x) => x.classList.toggle('active', x.dataset.view === view));
  $('page-title').textContent = TITLES[view];
  if (view === 'plays') loadPlays(); if (view === 'nightly') loadNightly(); if (view === 'profile') fillProfile();
}

/* ---------- events ---------- */
document.addEventListener('click', async (e) => {
  const t = e.target.closest('[data-view],[data-sport],[data-day],[data-track],[data-del]'); if (!t) return;
  if (t.dataset.view) show(t.dataset.view);
  else if (t.dataset.sport) { state.sport = t.dataset.sport; state.day = 'all'; syncControls(); load(); }
  else if (t.dataset.day) { state.day = t.dataset.day; renderBoard(); }
  else if (t.dataset.track) trackGame(t.dataset.track);
  else if (t.dataset.del) { try { await api('/api/plays/' + t.dataset.del, { method: 'DELETE' }); loadPlays(); } catch (err) { toast(err.message, true); } }
});
document.addEventListener('change', async (e) => {
  const sel = e.target.closest('[data-result]'); if (!sel) return;
  try { await api('/api/plays/' + sel.dataset.result, { method: 'PATCH', body: JSON.stringify({ result: sel.value }) }); loadPlays(); } catch (err) { toast(err.message, true); }
});
let sliderTimer;
$('min-liq').addEventListener('input', (e) => { state.minLiq = +e.target.value; $('min-liq-val').textContent = k(state.minLiq); clearTimeout(sliderTimer); sliderTimer = setTimeout(load, 250); });
$('refresh').addEventListener('click', async () => {
  const b = $('refresh'); b.disabled = true;
  try { await api('/api/refresh' + (state.sport !== 'all' ? '?sport=' + state.sport : ''), { method: 'POST' }); toast('Refreshed'); } catch (err) { toast(err.message, true); }
  b.disabled = false; load(); if (state.view === 'nightly') loadNightly();
});
$('signout').addEventListener('click', signOut);
$('auth-switch').addEventListener('click', (e) => { e.preventDefault(); showAuth(state.authMode === 'login' ? 'signup' : 'login'); });
$('auth-form').addEventListener('submit', async (e) => {
  e.preventDefault(); const err = $('auth-error'); err.hidden = true; $('auth-submit').disabled = true;
  const signup = state.authMode === 'signup';
  try {
    const d = await api(signup ? '/api/auth/signup' : '/api/auth/login', { method: 'POST', body: JSON.stringify({ email: $('auth-email').value, password: $('auth-pass').value, name: $('auth-name').value, remember: $('auth-remember').checked }) });
    setToken(d.token, $('auth-remember').checked); state.auth.user = d.user; $('auth-pass').value = ''; paintAccount(); $('auth-screen').hidden = true; state.sources = null; loadPlays(); load();
  } catch (x) { err.textContent = x.message; err.hidden = false; }
  $('auth-submit').disabled = false;
});
$('user-chip').addEventListener('click', () => show('profile'));
['bankroll-input', 'calc-odds', 'risk-input', 'win-prob', 'kelly-input'].forEach((id) => $(id).addEventListener('input', calcBankroll));
$('save-settings').addEventListener('click', saveProfile);
$('reset-settings').addEventListener('click', () => { state.s = { ...DEFAULTS }; saveSettings(state.s); state.sport = state.s.sport; state.minLiq = state.s.minLiq; syncControls(); fillProfile(); toast('Settings reset'); load(); });
$('play-form').addEventListener('submit', async (e) => {
  e.preventDefault(); const f = new FormData(e.target);
  try {
    await api('/api/plays', { method: 'POST', body: JSON.stringify({ sport: state.sport === 'all' ? 'nba' : state.sport, description: f.get('description'), odds_decimal: toDecimal(+f.get('odds')), stake: +f.get('stake'), source: 'manual' }) });
    e.target.reset(); loadPlays();
  } catch (err) { toast(err.message, true); }
});

/* ---------- hamburger menu ---------- */
const wide = () => false; // the menu is always a slide-in drawer behind the 3-line button
function setMenu(open) {
  if (wide()) { document.body.classList.toggle('menu-closed', !open); document.body.classList.remove('menu-open'); }
  else { document.body.classList.toggle('menu-open', open); }
  $('menu-btn').setAttribute('aria-expanded', String(open));
}
const menuIsOpen = () => (wide() ? !document.body.classList.contains('menu-closed') : document.body.classList.contains('menu-open'));
$('menu-btn').addEventListener('click', () => setMenu(!menuIsOpen()));
$('backdrop').addEventListener('click', () => setMenu(false));
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { if (!wide()) setMenu(false); $('suggest').hidden = true; } });
document.addEventListener('click', (e) => { if (!wide() && e.target.closest('#nav .nav')) setMenu(false); });


/* ---------- search with suggestions ---------- */
function suggestions(q) {
  q = q.trim().toLowerCase(); if (!q || !state.data) return [];
  const out = [], seen = new Set();
  for (const v of state.data.games) for (const [name, code] of [[v.away_name, v.away], [v.home_name, v.home]]) {
    const key = v.sport + name; if (seen.has(key)) continue;
    if (`${name} ${code}`.toLowerCase().includes(q)) { seen.add(key); out.push({ text: name, tag: v.label, q: name }); }
  }
  out.sort((a, b) => (b.text.toLowerCase().startsWith(q) ? 1 : 0) - (a.text.toLowerCase().startsWith(q) ? 1 : 0));
  const games = state.data.games.filter((v) => `${v.away_name} ${v.home_name}`.toLowerCase().includes(q)).slice(0, 4)
    .map((v) => ({ text: `${v.away_name} @ ${v.home_name}`, tag: v.label, q: v.away_name }));
  return [...out.slice(0, 6), ...games].slice(0, 8);
}
function applySearch(q) { state.q = q; $('search').value = q; $('suggest').hidden = true; renderOverview(); renderBoard(); }
$('search').addEventListener('input', (e) => {
  const q = e.target.value, list = suggestions(q), box = $('suggest');
  state.q = q; renderOverview(); renderBoard();
  box.innerHTML = list.map((x, i) => `<button type="button" data-q="${esc(x.q)}" class="${i === 0 ? 'on' : ''}">${esc(x.text)}<small>${esc(x.tag)}</small></button>`).join('');
  box.hidden = !list.length;
});
$('search').addEventListener('keydown', (e) => { if (e.key === 'Enter') { const f = $('suggest').querySelector('button'); if (f && !$('suggest').hidden) { e.preventDefault(); applySearch(f.dataset.q); } } });
$('suggest').addEventListener('click', (e) => { const b = e.target.closest('button[data-q]'); if (b) applySearch(b.dataset.q); });
document.addEventListener('click', (e) => { if (!e.target.closest('.search-box')) $('suggest').hidden = true; });

/* ---------- type-ahead for the plays page ---------- */
function playItems(q) {
  q = q.trim().toLowerCase(); if (!q) return [];
  const items = [], seen = new Set();
  const add = (text, tag) => { const k = text.toLowerCase(); if (!seen.has(k) && k.includes(q)) { seen.add(k); items.push({ text, tag }); } };
  for (const v of (state.data && state.data.games) || []) {
    add(`${v.away_name} ML`, v.label); add(`${v.home_name} ML`, v.label);
    add(`${v.away_name} @ ${v.home_name}`, v.label);
  }
  for (const p of (state.plays && state.plays.plays) || []) add(p.description, 'tracked');
  items.sort((a, b) => (b.text.toLowerCase().startsWith(q) ? 1 : 0) - (a.text.toLowerCase().startsWith(q) ? 1 : 0));
  return items.slice(0, 8);
}
function typeahead(inputId, boxId, onPick) {
  const input = $(inputId), box = $(boxId);
  const paint = () => {
    const list = playItems(input.value);
    box.innerHTML = list.map((x, i) => `<button type="button" data-q="${esc(x.text)}" class="${i === 0 ? 'on' : ''}">${esc(x.text)}<small>${esc(x.tag)}</small></button>`).join('')
      + (list.length ? '' : '');
    box.hidden = !list.length;
  };
  input.addEventListener('input', paint);
  input.addEventListener('focus', paint);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !box.hidden) { const f = box.querySelector('button'); if (f && input.dataset.pick !== '1') { e.preventDefault(); input.value = f.dataset.q; box.hidden = true; onPick(); } } });
  box.addEventListener('click', (e) => { const b = e.target.closest('button[data-q]'); if (b) { input.value = b.dataset.q; box.hidden = true; onPick(); input.focus(); } });
  document.addEventListener('click', (e) => { if (!e.target.closest('#' + boxId) && e.target !== input) box.hidden = true; });
}
typeahead('play-desc', 'play-suggest', () => {});
typeahead('play-search', 'play-search-suggest', () => { if (state.plays) renderPlays(state.plays); });
$('play-search').addEventListener('input', () => { if (state.plays) renderPlays(state.plays); });

/* ---------- min-liquidity  - / +  buttons ---------- */
for (const [id, dir] of [['liq-minus', -1], ['liq-plus', 1]]) {
  $(id).addEventListener('click', () => {
    const r = $('min-liq'); r.value = Math.max(0, Math.min(+r.max, +r.value + dir * 50000));
    r.dispatchEvent(new Event('input'));
  });
}

/* ---------- How to read popup ---------- */
function openHelp(on) { $('help-modal').hidden = !on; document.body.classList.toggle('modal-open', on); if (on) $('help-close').focus(); }
$('help-btn').addEventListener('click', () => openHelp(true));
$('help-close').addEventListener('click', () => openHelp(false));
$('help-modal').addEventListener('click', (e) => { if (e.target.id === 'help-modal') openHelp(false); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('help-modal').hidden) openHelp(false); });

/* ---------- rules panel on the Suggested plays page ---------- */
function paintRules() {
  const t = (state.data && state.data.thresholds) || {};
  const el = $('plays-rules'); if (!el || !t.heavy_sharp_pct) return;
  el.innerHTML = `<h3>What makes a suggested play</h3>
    <ul>
      <li><b>Heavy sharp</b> &mdash; ${t.heavy_sharp_pct}%+ of the exchange money (Kalshi, Novig, ProphetX) is on one side of a market: the moneyline, the spread or the total.</li>
      <li><b>Fade the public</b> &mdash; the sharp side has 55%+ of the money while ${t.fade_public_pct}%+ of DraftKings public bets are on the other side.</li>
      <li><b>+EV</b> &mdash; a book or exchange price beats the de-vigged Pinnacle price by ${t.min_ev_pct}%+ (edges above ${t.max_ev_pct}% are ignored as stale or bad prices).</li>
      <li><b>Model agrees</b> &mdash; the sharp side is also the team our model picks.</li>
      <li><b>Minimum size</b> &mdash; only games above your <i>min liquidity</i> slider are shown; big money starts at ${k(t.big_money)}.</li>
    </ul>
    <p><b>Nightly log:</b> before tip-off the tool logs every heavy-sharp play with at least ${k(t.nightly_min_total)} on the exchanges, one flat ${money(t.nightly_unit, 0)} unit each, at the best non-Pinnacle price (or Pinnacle, or -110 if no odds feed). Games that already started are never logged, and plays are graded from the final scores.</p>
    <p><b>Stake size:</b> your unit from the Profile page, or quarter-Kelly capped at 5% of your bankroll when you enter one.</p>`;
}

/* ---------- boot ---------- */
$('date-chip').textContent = new Date().toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
paintUser(); syncControls();
api('/api/logos').then((l) => { state.logos = l; }).catch(() => {}).finally(boot);
state.timer = setInterval(() => { if (!document.hidden) { load(); if (state.view === 'nightly') loadNightly(); } }, 60000);
})();
