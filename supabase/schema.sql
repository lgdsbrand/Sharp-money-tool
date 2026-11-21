-- Run this once in Supabase: SQL Editor -> New query -> paste -> Run.
-- kv also holds user accounts (hashed passwords) and login sessions, so keep RLS ON (below).
create table if not exists public.kv (
  key text primary key,
  value jsonb not null,
  updated_at timestamptz default now()
);

create table if not exists public.plays (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz default now(),
  sport text, game_key text, description text, team text, book text,
  odds_decimal double precision, stake double precision,
  status text default 'open',          -- open | won | lost | push
  pnl double precision,
  source text,                         -- sharp (auto-logged) | manual | demo
  notes text,
  day text,                            -- the "night" (yyyy-mm-dd, in DISPLAY_TZ)
  log_key text unique,                 -- prevents logging the same play twice
  market text,
  line double precision,
  user_id text                         -- owner of a manual play ('' = shared / auto-logged)
);
-- upgrading an older project? this adds the new column safely:
alter table public.plays add column if not exists user_id text;
alter table public.plays add column if not exists market text;            -- moneyline | spread | total
alter table public.plays add column if not exists line double precision;  -- the play's own spread / total number
create index if not exists plays_day_idx on public.plays (day);

-- The API talks to Supabase with the service_role key (server only). Turning RLS on with no
-- policies means the public anon key can NOT read or write these tables.
alter table public.kv enable row level security;
alter table public.plays enable row level security;
