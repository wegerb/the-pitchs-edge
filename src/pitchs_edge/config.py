from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
CSV_CACHE_DIR = DATA_DIR / "csv_cache"
DB_PATH = Path(os.getenv("DATABASE_PATH", DATA_DIR / "pitchs_edge.db"))

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
FOOTBALL_DATA_ORG_KEY = os.getenv("FOOTBALL_DATA_ORG_KEY", "")


@dataclass(frozen=True)
class League:
    code: str              # football-data.co.uk short code
    fd_org_id: int         # football-data.org competition id
    odds_api_key: str      # The Odds API sport_key
    name: str
    country: str


LEAGUES: list[League] = [
    League("E0",  2021, "soccer_epl",                "Premier League", "England"),
    League("E1",  2016, "soccer_efl_champ",          "Championship",   "England"),
    League("SP1", 2014, "soccer_spain_la_liga",      "La Liga",        "Spain"),
    League("I1",  2019, "soccer_italy_serie_a",      "Serie A",        "Italy"),
    League("D1",  2002, "soccer_germany_bundesliga", "Bundesliga",     "Germany"),
    League("F1",  2015, "soccer_france_ligue_one",   "Ligue 1",        "France"),
]

LEAGUE_BY_CODE = {l.code: l for l in LEAGUES}

# football-data.co.uk season codes: "2324" = 2023-24, etc.
SEASONS_BACKFILL = ["2122", "2223", "2324"]
