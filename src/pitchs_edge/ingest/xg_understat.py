"""Ingest Understat xG into the fixtures table.

Matches Understat match rows to fixtures we already have from football-data.co.uk
by (date ± 1 day, normalized home name, normalized away name), then updates
`fixtures.home_xg` and `fixtures.away_xg`.

Team-name reconciliation uses aggressive normalization + a manual alias table
for common mismatches (Understat says "Manchester United", football-data says
"Man United", etc).
"""
from __future__ import annotations

import re
import unicodedata
from datetime import timedelta

import pandas as pd

from ..db import connect
from ..sources.fbref import UNDERSTAT_LEAGUE_NAMES, fetch_season_xg

# Manual alias table: Understat name -> canonical short form used by football-data.co.uk
# Applied AFTER aggressive normalization, so keys are lowercase-normalized too.
_ALIASES: dict[str, str] = {
    # EPL
    "manchester united":       "man united",
    "manchester city":         "man city",
    "newcastle united":        "newcastle",
    "nottingham forest":       "nott'm forest",
    "leeds united":             "leeds",
    "sheffield united":        "sheffield united",
    "tottenham":               "tottenham",
    "wolverhampton wanderers": "wolves",
    "west bromwich albion":    "west brom",
    "cardiff city":            "cardiff",
    "leicester city":          "leicester",
    "huddersfield town":       "huddersfield",
    "swansea city":            "swansea",
    "stoke city":              "stoke",
    "norwich city":            "norwich",
    "hull city":               "hull",
    # Bundesliga
    "bayern munich":           "bayern munich",
    "borussia monchengladbach":"m'gladbach",
    "borussia mönchengladbach":"m'gladbach",
    "borussia m.gladbach":     "m'gladbach",
    "borussia m gladbach":     "m'gladbach",
    "fc koln":                 "koln",
    "fc köln":                 "koln",
    "1. fc koln":              "koln",
    "cologne":                 "koln",
    "fc cologne":              "koln",
    "rasenballsport leipzig":  "rb leipzig",
    "rb leipzig":              "rb leipzig",
    "leipzig":                 "rb leipzig",
    "arminia bielefeld":       "bielefeld",
    "greuther fuerth":         "greuther furth",
    "spvgg greuther furth":    "greuther furth",
    "hertha berlin":           "hertha",
    "hertha bsc":              "hertha",
    "paderborn":               "paderborn",
    "sc paderborn":            "paderborn",
    "sc paderborn 07":         "paderborn",
    "eintracht frankfurt":     "ein frankfurt",
    "fc augsburg":             "augsburg",
    "vfb stuttgart":           "stuttgart",
    "vfl wolfsburg":           "wolfsburg",
    "sc freiburg":             "freiburg",
    "tsg 1899 hoffenheim":     "hoffenheim",
    "rb leipzig":              "rb leipzig",
    "werder bremen":           "werder bremen",
    "hamburger sv":            "hamburg",
    "hannover 96":             "hannover",
    "bayer leverkusen":        "leverkusen",
    "fortuna dusseldorf":      "dusseldorf",
    "fortuna düsseldorf":      "dusseldorf",
    "greuther furth":          "greuther furth",
    "greuther fürth":          "greuther furth",
    "nuernberg":               "nurnberg",
    "nürnberg":                "nurnberg",
    # La Liga
    "athletic club":           "ath bilbao",
    "athletic bilbao":         "ath bilbao",
    "atletico madrid":         "ath madrid",
    "atletico de madrid":      "ath madrid",
    "rcd mallorca":            "mallorca",
    "real sociedad":           "sociedad",
    "celta vigo":              "celta",
    "real valladolid":         "valladolid",
    "espanyol":                "espanol",
    "rayo vallecano":          "vallecano",
    "deportivo alaves":        "alaves",
    "real betis":              "betis",
    "almería":                 "almeria",
    "cádiz":                   "cadiz",
    "leganés":                 "leganes",
    "real oviedo":             "oviedo",
    # Serie A
    "internazionale":          "inter",
    "inter milan":             "inter",
    "hellas verona":           "verona",
    "ac milan":                "milan",
    "ac pisa":                 "pisa",
    # Ligue 1
    "paris saint germain":     "paris sg",
    "paris saint-germain":     "paris sg",
    "psg":                     "paris sg",
    "olympique lyonnais":      "lyon",
    "olympique de marseille":  "marseille",
    "olympique marseille":     "marseille",
    "stade rennais":           "rennes",
    "stade brestois 29":       "brest",
    "stade brestois":          "brest",
    "racing club de lens":     "lens",
    "saint etienne":           "st etienne",
    "saint-étienne":           "st etienne",
    "saint-etienne":           "st etienne",
    "ajaccio":                 "ajaccio",
    "guingamp":                "guingamp",
    "ac ajaccio":              "ajaccio",
    "clermont foot":           "clermont",
    "paris saint germain":     "paris sg",
}


def _norm(name: str) -> str:
    """Strip accents, lowercase, collapse spaces, remove common suffixes."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    # Drop trailing/leading "FC", "AFC", "CF", "AC", numeric year suffixes
    s = re.sub(r"\b(fc|afc|cf|ac|bc|sc|ud|rcd|ssc|vfb|vfl|ss)\b", " ", s)
    s = re.sub(r"\b\d{2,4}\b", " ", s)  # year suffixes like "1909"
    s = re.sub(r"[^\w\s'&-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _canonical(name: str) -> str:
    """Normalize + apply alias table to get canonical football-data-style short form."""
    n = _norm(name)
    # Try direct normalized match on alias table
    if n in _ALIASES:
        return _norm(_ALIASES[n])
    # Also try lowercase raw for aliases that keep punctuation
    low = name.lower().strip()
    if low in _ALIASES:
        return _norm(_ALIASES[low])
    return n


def _load_db_teams(conn, league_id: int) -> dict[str, int]:
    """Return {canonical_name: team_id} for a league."""
    rows = conn.execute(
        "SELECT id, name FROM teams WHERE league_id = ?", (league_id,)
    ).fetchall()
    out: dict[str, int] = {}
    for r in rows:
        c = _canonical(r["name"])
        # Keep the first (shortest) name that canonicalizes to c
        if c not in out or len(r["name"]) < len(_reverse_lookup_short_name(out, c, conn)):
            out[c] = r["id"]
    return out


def _reverse_lookup_short_name(mapping: dict[str, int], key: str, conn) -> str:
    r = conn.execute("SELECT name FROM teams WHERE id = ?", (mapping[key],)).fetchone()
    return r["name"] if r else ""


def ingest_league_season(league_code: str, season: str, *, verbose: bool = False) -> dict:
    """Fetch xG for one league-season and merge into fixtures table."""
    df = fetch_season_xg(league_code, season)
    stats = {"matches_in_understat": int(len(df)), "matched": 0, "unmatched": 0, "updated": 0}
    if df.empty:
        if verbose:
            print(f"  {league_code} {season}: no xG data available")
        return stats

    with connect() as conn:
        lg = conn.execute("SELECT id FROM leagues WHERE code = ?", (league_code,)).fetchone()
        if not lg:
            if verbose:
                print(f"  {league_code}: league not in DB, skip")
            return stats
        league_id = lg["id"]
        team_map = _load_db_teams(conn, league_id)

        unmatched_examples: list[str] = []

        for _, row in df.iterrows():
            h_canon = _canonical(row["home"])
            a_canon = _canonical(row["away"])
            h_id = team_map.get(h_canon)
            a_id = team_map.get(a_canon)

            if h_id is None or a_id is None:
                stats["unmatched"] += 1
                if len(unmatched_examples) < 5:
                    unmatched_examples.append(
                        f"{row['home']}->{h_canon} / {row['away']}->{a_canon}"
                    )
                continue

            # Match fixture within ±1 day of Understat date
            target = row["date"].to_pydatetime()
            lo = (target - timedelta(days=1)).isoformat()
            hi = (target + timedelta(days=1, hours=23)).isoformat()
            fx = conn.execute(
                """SELECT id FROM fixtures
                   WHERE league_id = ? AND home_team_id = ? AND away_team_id = ?
                     AND season = ? AND kickoff BETWEEN ? AND ?
                   ORDER BY ABS(julianday(kickoff) - julianday(?)) ASC
                   LIMIT 1""",
                (league_id, h_id, a_id, season, lo, hi, target.isoformat()),
            ).fetchone()
            if not fx:
                stats["unmatched"] += 1
                continue

            stats["matched"] += 1
            conn.execute(
                "UPDATE fixtures SET home_xg = ?, away_xg = ? WHERE id = ?",
                (float(row["home_xg"]), float(row["away_xg"]), fx["id"]),
            )
            stats["updated"] += 1

    if verbose:
        print(f"  {league_code} {season}: {stats['matched']}/{stats['matches_in_understat']} matched, "
              f"{stats['unmatched']} unmatched")
        for ex in unmatched_examples[:3]:
            print(f"    unmatched sample: {ex}")
    return stats


def ingest_all(leagues: list[str] | None = None, seasons: list[str] | None = None) -> dict:
    lg_codes = list(leagues) if leagues else list(UNDERSTAT_LEAGUE_NAMES.keys())
    seasons = list(seasons) if seasons else ["2122", "2223", "2324", "2425", "2526"]
    total = {"matches_in_understat": 0, "matched": 0, "unmatched": 0, "updated": 0}
    for lg in lg_codes:
        for ss in seasons:
            s = ingest_league_season(lg, ss, verbose=True)
            for k in total:
                total[k] += s.get(k, 0)
    return total
