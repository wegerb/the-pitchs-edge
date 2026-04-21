"""Ingest football-data.co.uk CSVs into SQLite.

Writes fixtures (with full-time + half-time scores) and Pinnacle closing odds
across 1X2, O/U 2.5, and Asian Handicap. B365 columns are captured as a
secondary book for cross-checks.
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
    status = "finished" if fthg is not None else "scheduled"
    conn.execute(
        """INSERT OR IGNORE INTO fixtures
           (league_id, season, kickoff, home_team_id, away_team_id,
            fthg, ftag, hthg, htag, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (league_id, season, kickoff, home_id, away_id, fthg, ftag, hthg, htag, status),
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
            """INSERT OR IGNORE INTO odds_closing
               (fixture_id, book, market, selection, line, price)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (fixture_id, book, market, selection, line, price),
        )
        count += 1

    # Pinnacle 1X2
    for sel, col in (("home", "PSH"), ("draw", "PSD"), ("away", "PSA")):
        v = row.get(col)
        if pd.notna(v):
            ins("Pinnacle", "1X2", sel, None, float(v))
    # Pinnacle O/U 2.5
    for sel, col in (("over", "P>2.5"), ("under", "P<2.5")):
        v = row.get(col)
        if pd.notna(v):
            ins("Pinnacle", "OU", sel, 2.5, float(v))
    # Pinnacle AH
    ah_line = row.get("AHh")
    if pd.notna(ah_line):
        for sel, col in (("home", "PAHH"), ("away", "PAHA")):
            v = row.get(col)
            if pd.notna(v):
                ins("Pinnacle", "AH", sel, float(ah_line), float(v))
    # B365 1X2 (secondary)
    for sel, col in (("home", "B365H"), ("draw", "B365D"), ("away", "B365A")):
        v = row.get(col)
        if pd.notna(v):
            ins("B365", "1X2", sel, None, float(v))
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
