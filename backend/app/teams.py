"""Team-name normalisation for six sports, so the same game can be matched across feeds.

Pro leagues (NBA, NFL, NHL, MLB) map every name variant to a short code ("Boston Celtics" -> "BOS").
College sports (NCAAF, NCAAB) have ~360 schools, so they use a cleaned school name plus a careful
prefix matcher ("Ohio State" matches "Ohio State Buckeyes" but NOT "Ohio"). Anything the matcher
gets wrong can be fixed without code in `team_aliases.json` (see README).
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache

PRO = ("nba", "nfl", "nhl", "mlb")
COLLEGE = ("ncaaf", "ncaab")
ALL_SPORTS = PRO + COLLEGE
SPORT_LABELS = {"nba": "NBA", "nfl": "NFL", "nhl": "NHL", "mlb": "MLB", "ncaaf": "NCAAF", "ncaab": "NCAAB"}

# (code, city, nickname)
_TEAMS: dict[str, list[tuple[str, str, str]]] = {
    "nba": [
        ("ATL", "atlanta", "hawks"), ("BOS", "boston", "celtics"), ("BKN", "brooklyn", "nets"),
        ("CHA", "charlotte", "hornets"), ("CHI", "chicago", "bulls"), ("CLE", "cleveland", "cavaliers"),
        ("DAL", "dallas", "mavericks"), ("DEN", "denver", "nuggets"), ("DET", "detroit", "pistons"),
        ("GSW", "golden state", "warriors"), ("HOU", "houston", "rockets"), ("IND", "indiana", "pacers"),
        ("LAC", "la clippers", "clippers"), ("LAL", "los angeles lakers", "lakers"),
        ("MEM", "memphis", "grizzlies"), ("MIA", "miami", "heat"), ("MIL", "milwaukee", "bucks"),
        ("MIN", "minnesota", "timberwolves"), ("NOP", "new orleans", "pelicans"),
        ("NYK", "new york", "knicks"), ("OKC", "oklahoma city", "thunder"), ("ORL", "orlando", "magic"),
        ("PHI", "philadelphia", "76ers"), ("PHX", "phoenix", "suns"), ("POR", "portland", "trail blazers"),
        ("SAC", "sacramento", "kings"), ("SAS", "san antonio", "spurs"), ("TOR", "toronto", "raptors"),
        ("UTA", "utah", "jazz"), ("WAS", "washington", "wizards"),
    ],
    "nfl": [
        ("ARI", "arizona", "cardinals"), ("ATL", "atlanta", "falcons"), ("BAL", "baltimore", "ravens"),
        ("BUF", "buffalo", "bills"), ("CAR", "carolina", "panthers"), ("CHI", "chicago", "bears"),
        ("CIN", "cincinnati", "bengals"), ("CLE", "cleveland", "browns"), ("DAL", "dallas", "cowboys"),
        ("DEN", "denver", "broncos"), ("DET", "detroit", "lions"), ("GB", "green bay", "packers"),
        ("HOU", "houston", "texans"), ("IND", "indianapolis", "colts"), ("JAX", "jacksonville", "jaguars"),
        ("KC", "kansas city", "chiefs"), ("LV", "las vegas", "raiders"), ("LAC", "los angeles chargers", "chargers"),
        ("LAR", "los angeles rams", "rams"), ("MIA", "miami", "dolphins"), ("MIN", "minnesota", "vikings"),
        ("NE", "new england", "patriots"), ("NO", "new orleans", "saints"), ("NYG", "new york giants", "giants"),
        ("NYJ", "new york jets", "jets"), ("PHI", "philadelphia", "eagles"), ("PIT", "pittsburgh", "steelers"),
        ("SF", "san francisco", "49ers"), ("SEA", "seattle", "seahawks"), ("TB", "tampa bay", "buccaneers"),
        ("TEN", "tennessee", "titans"), ("WAS", "washington", "commanders"),
    ],
    "nhl": [
        ("ANA", "anaheim", "ducks"), ("BOS", "boston", "bruins"), ("BUF", "buffalo", "sabres"),
        ("CGY", "calgary", "flames"), ("CAR", "carolina", "hurricanes"), ("CHI", "chicago", "blackhawks"),
        ("COL", "colorado", "avalanche"), ("CBJ", "columbus", "blue jackets"), ("DAL", "dallas", "stars"),
        ("DET", "detroit", "red wings"), ("EDM", "edmonton", "oilers"), ("FLA", "florida", "panthers"),
        ("LAK", "los angeles", "kings"), ("MIN", "minnesota", "wild"), ("MTL", "montreal", "canadiens"),
        ("NSH", "nashville", "predators"), ("NJD", "new jersey", "devils"), ("NYI", "new york islanders", "islanders"),
        ("NYR", "new york rangers", "rangers"), ("OTT", "ottawa", "senators"), ("PHI", "philadelphia", "flyers"),
        ("PIT", "pittsburgh", "penguins"), ("SJS", "san jose", "sharks"), ("SEA", "seattle", "kraken"),
        ("STL", "st louis", "blues"), ("TBL", "tampa bay", "lightning"), ("TOR", "toronto", "maple leafs"),
        ("UTA", "utah", "mammoth"), ("VAN", "vancouver", "canucks"), ("VGK", "vegas", "golden knights"),
        ("WSH", "washington", "capitals"), ("WPG", "winnipeg", "jets"),
    ],
    "mlb": [
        ("ARI", "arizona", "diamondbacks"), ("ATL", "atlanta", "braves"), ("BAL", "baltimore", "orioles"),
        ("BOS", "boston", "red sox"), ("CHC", "chicago cubs", "cubs"), ("CWS", "chicago white sox", "white sox"),
        ("CIN", "cincinnati", "reds"), ("CLE", "cleveland", "guardians"), ("COL", "colorado", "rockies"),
        ("DET", "detroit", "tigers"), ("HOU", "houston", "astros"), ("KC", "kansas city", "royals"),
        ("LAA", "los angeles angels", "angels"), ("LAD", "los angeles dodgers", "dodgers"),
        ("MIA", "miami", "marlins"), ("MIL", "milwaukee", "brewers"), ("MIN", "minnesota", "twins"),
        ("NYM", "new york mets", "mets"), ("NYY", "new york yankees", "yankees"), ("ATH", "athletics", "athletics"),
        ("PHI", "philadelphia", "phillies"), ("PIT", "pittsburgh", "pirates"), ("SD", "san diego", "padres"),
        ("SF", "san francisco", "giants"), ("SEA", "seattle", "mariners"), ("STL", "st louis", "cardinals"),
        ("TB", "tampa bay", "rays"), ("TEX", "texas", "rangers"), ("TOR", "toronto", "blue jays"),
        ("WSH", "washington", "nationals"),
    ],
}

# extra spellings the feeds use
_EXTRA: dict[str, dict[str, str]] = {
    "nba": {"los angeles clippers": "LAC", "los angeles c": "LAC", "la c": "LAC", "los angeles l": "LAL",
            "la lakers": "LAL", "la l": "LAL", "okc": "OKC", "gs": "GSW", "ny": "NYK", "no": "NOP", "sa": "SAS",
            "blazers": "POR", "sixers": "PHI", "portland trail blazers": "POR"},
    "nfl": {"la chargers": "LAC", "la rams": "LAR", "los angeles c": "LAC", "los angeles r": "LAR",
            "ny giants": "NYG", "ny jets": "NYJ", "new york g": "NYG", "new york j": "NYJ",
            "washington football team": "WAS", "wsh": "WAS", "jac": "JAX", "lar": "LAR", "lac": "LAC", "la": "LAR", "oakland raiders": "LV", "gb": "GB", "sf": "SF", "tb": "TB"},
    "nhl": {"la kings": "LAK", "ny rangers": "NYR", "ny islanders": "NYI", "new york r": "NYR", "new york i": "NYI",
            "utah hockey club": "UTA", "utah mammoth": "UTA", "utah": "UTA", "montréal": "MTL", "st louis": "STL",
            "saint louis": "STL", "tampa": "TBL", "vegas": "VGK", "las vegas": "VGK", "nj": "NJD", "sj": "SJS", "la": "LAK"},
    "mlb": {"ny mets": "NYM", "ny yankees": "NYY", "la angels": "LAA", "la dodgers": "LAD", "chicago c": "CHC",
            "chicago w": "CWS", "new york m": "NYM", "new york y": "NYY", "los angeles a": "LAA",
            "los angeles d": "LAD", "oakland athletics": "ATH", "sacramento athletics": "ATH", "oakland": "ATH",
            "d backs": "ARI", "diamondbacks": "ARI", "st louis": "STL", "saint louis": "STL", "tampa": "TB"},
}


def _clean(name: str) -> str:
    n = (name or "").lower().replace("&", " and ").replace("é", "e")
    n = re.sub(r"['’`]", "", n)  # "Mary's" -> "marys", not "mary s"
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


_LOOSE: dict[str, dict[str, str]] = {}  # nicknames + full names only (a bare city is too ambiguous to search inside text)


def _build_alias() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for sport, teams in _TEAMS.items():
        city_count: dict[str, int] = {}
        last_count: dict[str, int] = {}
        for _, city, nick in teams:
            city_count[city] = city_count.get(city, 0) + 1
            last = _clean(nick).split()[-1]
            last_count[last] = last_count.get(last, 0) + 1
        a: dict[str, str] = {}
        for code, city, nick in teams:
            a[code.lower()] = code
            a[_clean(nick)] = code
            a[_clean(f"{city} {nick}")] = code
            if last_count[_clean(nick).split()[-1]] == 1:  # "Sox" is ambiguous, "Celtics" is not
                a[_clean(nick).split()[-1]] = code
            if city_count[city] == 1:
                a[_clean(city)] = code
        for k, v in _EXTRA.get(sport, {}).items():
            a[_clean(k)] = v
        _LOOSE[sport] = {_clean(nick): code for code, _c, nick in teams}
        _LOOSE[sport].update({_clean(f"{city} {nick}"): code for code, city, nick in teams})
        out[sport] = a
    return out


_ALIAS = _build_alias()
KNOWN_CODES = {sport: {c for c, _, _ in teams} for sport, teams in _TEAMS.items()}

# ---- college -----------------------------------------------------------------
_COLLEGE_FIXED = {
    "miami oh": "miami oh", "miami ohio": "miami oh", "miami of ohio": "miami oh", "miami redhawks": "miami oh",
    "miami fl": "miami fl", "miami florida": "miami fl", "miami hurricanes": "miami fl", "the u": "miami fl",
    "uconn": "connecticut", "unlv": "nevada las vegas", "ole miss": "mississippi", "pitt": "pittsburgh",
    "lsu": "louisiana state", "usc": "southern california", "ucf": "central florida", "smu": "southern methodist",
    "tcu": "texas christian", "byu": "brigham young", "unc": "north carolina", "nc state": "north carolina state",
    "umass": "massachusetts", "uab": "alabama birmingham", "utep": "texas el paso", "utsa": "texas san antonio",
}
# a longer name whose next word is one of these is a DIFFERENT school ("Ohio" != "Ohio State")
_BLOCK_NEXT = {"state", "college", "tech", "southern", "northern", "eastern", "western", "central", "atlantic",
               "international", "christian", "am", "aandm", "valley", "poly", "baptist", "methodist", "lutheran",
               "wesleyan", "university", "charlotte", "dame", "cruces", "carolina", "dakota", "kentucky", "mexico",
               "illinois", "michigan", "tennessee", "texas", "florida", "washington", "oregon", "virginia", "jose"}


@lru_cache(maxsize=1)
def _user_aliases() -> dict[str, str]:
    path = os.getenv("TEAM_ALIASES_PATH", os.path.join(os.path.dirname(__file__), "..", "team_aliases.json"))
    try:
        with open(path, encoding="utf-8") as f:
            return {_clean(k): _clean(v) for k, v in json.load(f).items()}
    except (OSError, ValueError):
        return {}


def _college_name(name: str) -> str:
    n = _clean(name)
    toks = n.split()
    toks = [("state" if (t == "st" and i > 0) else "saint" if (t in ("st", "saint") and i == 0) else t) for i, t in enumerate(toks)]
    n = " ".join(toks)
    n = _user_aliases().get(n, n)
    return _COLLEGE_FIXED.get(n, n)


def norm_team(name: str, sport: str = "nba") -> str:
    """Pro leagues -> short code. College -> cleaned school name."""
    sport = (sport or "nba").lower()
    if sport in COLLEGE:
        return _college_name(name)
    alias = _ALIAS.get(sport, _ALIAS["nba"])
    n = _clean(name)
    if n in alias:
        return alias[n]
    for a, code in sorted(_LOOSE.get(sport, {}).items(), key=lambda kv: -len(kv[0])):
        if len(a) > 3 and f" {a} " in f" {n} ":
            return code
    return (n.split(" ")[-1] if n else "unk").upper()[:6]


def is_known(name: str, sport: str) -> bool:
    if sport in COLLEGE:
        return bool(_clean(name)) and any(c.isalpha() for c in name)
    return norm_team(name, sport) in KNOWN_CODES.get(sport, set())


def same_team(a: str, b: str, sport: str) -> bool:
    if a == b:
        return True
    if sport in PRO:
        return False
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if long_[: len(short)] != short:
        return False
    nxt = long_[len(short)] if len(long_) > len(short) else None
    return nxt not in _BLOCK_NEXT if nxt else True


def match_pair(a1: str, h1: str, a2: str, h2: str, sport: str) -> bool:
    """Same two teams (either orientation)?"""
    return (same_team(a1, a2, sport) and same_team(h1, h2, sport)) or (
        same_team(a1, h2, sport) and same_team(h1, a2, sport)
    )


def canon(label: str, away: str, home: str, sport: str) -> str:
    """Map a feed's team label onto the game's own away/home label."""
    if same_team(label, away, sport):
        return away
    if same_team(label, home, sport):
        return home
    return label


def pair_key(a: str, b: str, sport: str = "nba") -> str:
    """Stable, order-independent key for a game, e.g. 'BOS v PHI'."""
    x, y = sorted([norm_team(a, sport), norm_team(b, sport)])
    return f"{x} v {y}"


def display(team: str, sport: str) -> str:
    if sport not in COLLEGE:
        return team
    t = team.title().replace("Marys", "Mary's").replace("Johns", "John's")
    return re.sub(r" (Oh|Fl)$", lambda m: f" ({m.group(1).upper()})", t)
