"""Ingest football-data.co.uk CSVs into SQLite.

Writes fixtures (with full-time + half-time scores) plus historical odds in two
timings — opening (pre-kickoff listing price) and closing (last pre-kickoff
price) — across six books: Pinnacle, B365, Betway, Betfair Sportsbook, William
Hill, and Betfair Exchange. For O/U 2.5 and Asian Handicap the football-data
CSVs also carry pre-computed "Max" (best-price-across-books) columns which we
ingest under the book label "Max"/"Max_open". These let us answer two critical
questions in backtesting:

  1. Is edge at Pinnacle closing real? (strict: sharp closing line)
  2. Is edge at opening lines or best-book closing realistic for a bettor to
     actually hit? (pragmatic: would you have been able to get on?)

Book labels used in odds_closing.book:
  Pinnacle      — Pinnacle 1X2/OU/AH closing (PSCH/PSCD/PSCA, PC>2.5/PC<2.5, PCAHH/PCAHA)
  Pinnacle_open — Pinnacle 1X2/OU/AH opening (PSH/PSD/PSA, P>2.5/P<2.5, PAHH/PAHA)
  B365          — Bet365 1X2 closing
  B365_open     — Bet365 1X2 opening
  BW            — Betway 1X2 closing
  BF            — Betfair Sportsbook 1X2 closing
  WH            — William Hill 1X2 closing
  BFE           — Betfair Exchange 1X2 closing
  Max           — Best-price-across-books OU/AH closing
  Max_open      — Best-price-across-books OU/AH opening

Uses INSERT OR REPLACE so re-ingesting cleanly corrects any previously-
mislabeled rows from earlier ingest runs.
"""
from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from ..config import LEAGUE_BY_CODE, LEAGUES, SEASONS_BACKFILL
from ..db import connect
from ..sources.footballdata_csv import CsvSpec, download, load


def _ensure_league(conn, code: str) -> int:
    league = LEAGUE_BY_CODE[code]
    row = conn.execute("SELECT id FROM leagues WHERE code = ?", (code,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO leagues (code, fd_org_id, odds_api_key, name, country) VALUES (?, ?, ?, ?, ?)",
        (league.code, league.fd_org_id, league.odds_api_key, league.name, league.country),
    )
    return cur.lastrowid


def _ensure_team(conn, league_id: int, name: str) -> int:
    row = conn.execute(
        "SELECT id FROM teams WHERE league_id = ? AND name = ?", (league_id, name)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO teams (league_id, name) VALUES (?, ?)", (league_id, name)
    )
    return cur.lastrowid


def _insert_fixture(conn, league_id: int, season: str, row: pd.Series) -> int | None:
    home_id = _ensure_team(conn, league_id, row["HomeTeam"])
    away_id = _ensure_team(conn, league_id, row["AwayTeam"])
    kickoff = row["Date"].isoformat()
    fthg = int(row["FTHG"]) if pd.notna(row.get("FTHG")) else None
    ftag = int(row["FTAG"]) if pd.notna(row.get("FTAG")) else None
    hthg = int(row["HTHG"]) if pd.notna(row.get("HTHG")) else None
    htag = int(row["HTAG"]) if pd.notna(row.get("HTAG")) else None
    # Corners — HC/AC in football-data.co.uk CSVs
    hc = int(row["HC"]) if pd.notna(row.get("HC")) else None
    ac = int(row["AC"]) if pd.notna(row.get("AC")) else None
    status = "finished" if fthg is not None else "scheduled"
    conn.execute(
        """INSERT OR IGNORE INTO fixtures
           (league_id, season, kickoff, home_team_id, away_team_id,
            fthg, ftag, hthg, htag, home_corners, away_corners, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (league_id, season, kickoff, home_id, away_id, fthg, ftag, hthg, htag, hc, ac, status),
    )
    # Backfill corners onto existing fixtures (preserves xG and other side-channel fields)
    conn.execute(
        """UPDATE fixtures
              SET home_corners = COALESCE(home_corners, ?),
                  away_corners = COALESCE(away_corners, ?)
            WHERE league_id = ? AND season = ? AND kickoff = ?
              AND home_team_id = ? AND away_team_id = ?""",
        (hc, ac, league_id, season, kickoff, home_id, away_id),
    )
    fx = conn.execute(
        """SELECT id FROM fixtures WHERE league_id = ? AND season = ? AND kickoff = ?
           AND home_team_id = ? AND away_team_id = ?""",
        (league_id, season, kickoff, home_id, away_id),
    ).fetchone()
    return fx["id"] if fx else None


def _insert_closing(conn, fixture_id: int, row: pd.Series) -> int:
    count = 0

    def ins(book: str, market: str, selection: str, line: float | None, price: float) -> None:
        nonlocal count
        conn.execute(
            """INSERT OR REPLACE INTO odds_closing
               (fixture_id, book, market, selection, line, price)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (fixture_id, book, market, selection, line, price),
        )
        count += 1

    def maybe(book: str, market: str, selection: str, line: float | None, col: str) -> None:
        v = row.get(col)
        if pd.notna(v):
            try:
                p = float(v)
                if p > 1.0:  # decimal odds sanity
                    ins(book, market, selection, line, p)
            except (TypeError, ValueError):
                pass

    # ----- 1X2: opening + closing across 6 books -----
    _BOOKS_1X2 = [
        # (book_label, home_col, draw_col, away_col)
        ("Pinnacle",      "PSCH",  "PSCD",  "PSCA"),
        ("Pinnacle_open", "PSH",   "PSD",   "PSA"),
        ("B365",          "B365CH","B365CD","B365CA"),
        ("B365_open",     "B365H", "B365D", "B365A"),
        ("BW",            "BWCH",  "BWCD",  "BWCA"),
        ("BF",            "BFCH",  "BFCD",  "BFCA"),
        ("WH",            "WHCH",  "WHCD",  "WHCA"),
        ("BFE",           "BFECH", "BFECD", "BFECA"),
    ]
    for book, h, d, a in _BOOKS_1X2:
        maybe(book, "1X2", "home", None, h)
        maybe(book, "1X2", "draw", None, d)
        maybe(book, "1X2", "away", None, a)

    # ----- O/U 2.5: Pinnacle + Max, opening + closing -----
    _BOOKS_OU = [
        ("Pinnacle",      "PC>2.5",  "PC<2.5"),   # Pinnacle closing
        ("Pinnacle_open", "P>2.5",   "P<2.5"),    # Pinnacle opening
        ("B365",          "B365C>2.5","B365C<2.5"),
        ("B365_open",     "B365>2.5", "B365<2.5"),
        ("Max",           "MaxC>2.5", "MaxC<2.5"),  # best-price-across-books closing
        ("Max_open",      "Max>2.5",  "Max<2.5"),   # best-price-across-books opening
    ]
    for book, over_col, under_col in _BOOKS_OU:
        maybe(book, "OU", "over",  2.5, over_col)
        maybe(book, "OU", "under", 2.5, under_col)

    # ----- Asian Handicap: Pinnacle + Max, opening + closing (line can differ!) -----
    ah_open = row.get("AHh")
    if pd.notna(ah_open):
        try:
            line_open = float(ah_open)
            for book, h, a in (("Pinnacle_open", "PAHH", "PAHA"),
                               ("Max_open",      "MaxAHH","MaxAHA")):
                maybe(book, "AH", "home", line_open, h)
                maybe(book, "AH", "away", line_open, a)
        except (TypeError, ValueError):
            pass
    ah_close = row.get("AHCh")
    if pd.notna(ah_close):
        try:
            line_close = float(ah_close)
            for book, h, a in (("Pinnacle", "PCAHH", "PCAHA"),
                               ("Max",      "MaxCAHH","MaxCAHA")):
                maybe(book, "AH", "home", line_close, h)
                maybe(book, "AH", "away", line_close, a)
        except (TypeError, ValueError):
            pass

    return count


def ingest(
    leagues: Iterable[str] | None = None,
    seasons: Iterable[str] | None = None,
) -> dict:
    lg_codes = list(leagues) if leagues else [l.code for l in LEAGUES]
    ss = list(seasons) if seasons else SEASONS_BACKFILL
    stats = {"fixtures": 0, "odds": 0, "league_seasons": 0, "errors": []}

    with connect() as conn:
        for code in lg_codes:
            league_id = _ensure_league(conn, code)
            for season in ss:
                spec = CsvSpec(league_code=code, season=season)
                try:
                    download(spec)
                    df = load(spec)
                except Exception as e:
                    stats["errors"].append(f"{code} {season}: {e}")
                    continue
                stats["league_seasons"] += 1
                for _, row in df.iterrows():
                    fid = _insert_fixture(conn, league_id, season, row)
                    if fid is None:
                        continue
                    stats["fixtures"] += 1
                    stats["odds"] += _insert_closing(conn, fid, row)
    return stats
