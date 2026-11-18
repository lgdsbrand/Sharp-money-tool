"""Central settings. Everything comes from environment variables (see .env.example)."""
import os

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


class Settings:
    # --- general -----------------------------------------------------------
    DEMO_MODE = _bool("DEMO_MODE", True)  # True = fake but realistic data, no keys needed
    ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")  # protects refresh / writes when set
    CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
    REFRESH_SECONDS = int(_float("REFRESH_SECONDS", 120))  # 0 = no background refresh
    SPORTS = [s.strip().lower() for s in os.getenv("SPORTS", "nba,nfl,nhl,mlb,ncaaf,ncaab").split(",") if s.strip()]
    # --- accounts ----------------------------------------------------------
    REQUIRE_LOGIN = _bool("REQUIRE_LOGIN", True)  # dashboard needs an account
    ALLOW_SIGNUP = _bool("ALLOW_SIGNUP", True)  # false = only ADMIN_EMAILS / ALLOWED_EMAILS can create accounts
    ADMIN_EMAILS = [e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()]
    ALLOWED_EMAILS = [e.strip().lower() for e in os.getenv("ALLOWED_EMAILS", "").split(",") if e.strip()]
    DISPLAY_TZ = os.getenv("DISPLAY_TZ", "America/New_York")  # which clock decides "tonight's slate"

    # --- storage -----------------------------------------------------------
    SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
    SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
    SOURCE_TIMEOUT = _float("SOURCE_TIMEOUT", 30)  # seconds before one slow feed is skipped
    SQLITE_PATH = os.getenv("SQLITE_PATH", "sharp_money.db")

    # --- exchanges ---------------------------------------------------------
    KALSHI_ENABLED = _bool("KALSHI_ENABLED", True)
    KALSHI_BASE_URL = os.getenv("KALSHI_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2")
    # which number counts as "money": volume (traded $) | open_interest | liquidity (resting $)
    KALSHI_METRIC = os.getenv("KALSHI_METRIC", "volume")

    POLYMARKET_ENABLED = _bool("POLYMARKET_ENABLED", False)
    POLYMARKET_GAMMA_URL = os.getenv("POLYMARKET_GAMMA_URL", "https://gamma-api.polymarket.com")
    POLYMARKET_CLOB_URL = os.getenv("POLYMARKET_CLOB_URL", "https://clob.polymarket.com")

    PROPHETX_ENABLED = _bool("PROPHETX_ENABLED", False)
    PROPHETX_BASE_URL = os.getenv("PROPHETX_BASE_URL", "https://cash.api.prophetx.co/partner")
    PROPHETX_AFFILIATE_KEY = os.getenv("PROPHETX_AFFILIATE_KEY", "")

    NOVIG_ENABLED = _bool("NOVIG_ENABLED", False)
    NOVIG_BASE_URL = os.getenv("NOVIG_BASE_URL", "")  # empty = https://api.novig.com (confirm on docs.novig.com)
    NOVIG_API_KEY_ID = os.getenv("NOVIG_API_KEY_ID", "")
    NOVIG_PRIVATE_KEY_PATH = os.getenv("NOVIG_PRIVATE_KEY_PATH", "")

    # --- odds (Pinnacle + sportsbooks, for +EV) -----------------------------
    ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
    ODDS_BOOKMAKERS = os.getenv("ODDS_BOOKMAKERS", "pinnacle,draftkings,fanduel,betmgm,caesars")
    ODDS_REFRESH_SECONDS = int(_float("ODDS_REFRESH_SECONDS", 14400))  # odds are cached to save API credits

    # --- public betting % ---------------------------------------------------
    DK_SPLITS_ENABLED = _bool("DK_SPLITS_ENABLED", True)
    DK_SPLITS_URL = os.getenv(
        "DK_SPLITS_URL", "https://dknetwork.draftkings.com/draftkings-sportsbook-betting-splits/"
    )

    # --- your models --------------------------------------------------------
    MODEL_PICKS_URL = os.getenv("MODEL_PICKS_URL", "")  # endpoint returning your picks as JSON
    MODEL_PICKS_TOKEN = os.getenv("MODEL_PICKS_TOKEN", "")

    # --- thresholds ---------------------------------------------------------
    MIN_LIQUIDITY_DEFAULT = _float("MIN_LIQUIDITY_DEFAULT", 100_000)
    HEAVY_SHARP_PCT = _float("HEAVY_SHARP_PCT", 65)  # % of exchange money on one side
    FADE_PUBLIC_PCT = _float("FADE_PUBLIC_PCT", 55)  # public bets % on the other side
    BIG_MONEY_TOTAL = _float("BIG_MONEY_TOTAL", 500_000)
    MIN_EV_PCT = _float("MIN_EV_PCT", 2.0)
    MAX_EV_PCT = _float("MAX_EV_PCT", 25.0)  # edges above this are treated as bad data and ignored

    # --- nightly log (auto-logs tonight's heavy sharp plays, then grades them) ------
    NIGHTLY_ENABLED = _bool("NIGHTLY_ENABLED", True)
    NIGHTLY_UNIT = _float("NIGHTLY_UNIT", 25)  # flat stake per logged play, in your currency
    NIGHTLY_MIN_TOTAL = _float("NIGHTLY_MIN_TOTAL", 100_000)  # only log plays with at least this much exchange money


settings = Settings()
