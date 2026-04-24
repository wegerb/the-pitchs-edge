"""Backfill HC/AC (home/away corner counts) into the fixtures table.

Reads the cached football-data.co.uk CSVs in data/csv_cache and updates
existing fixtures rows. Idempotent — uses COALESCE so already-populated
rows aren't overwritten.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from pitchs_edge.config import LEAGUE_BY_CODE
from pitchs_edge.db import connect


def backfill(csv_dir: Path, leagues: list[str], seasons: list[str]) -> None:
    with connect() as conn:
        for lg_code in leagues:
            lg = conn.execute("SELECT id FROM leagues WHERE code = ?", (lg_code,)).fetchone()
            if not lg:
                print(f"[{lg_code}] not in DB, skipping")
                continue
            league_id = lg["id"]

            for season in seasons:
                csv_path = csv_dir / f"{season}_{lg_code}.csv"
                if not csv_path.exists():
                    print(f"[{lg_code}/{season}] CSV missing: {csv_path}")
                    continue
                df = pd.read_csv(csv_path, encoding="latin-1")
                if "HC" not in df.columns or "AC" not in df.columns:
                    print(f"[{lg_code}/{season}] no HC/AC columns")
                    continue
                df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
                n_updated = 0
                for _, row in df.iterrows():
                    if pd.isna(row.get("HC")) or pd.isna(row.get("AC")) or pd.isna(row["Date"]):
                        continue
                    kickoff = row["Date"].isoformat()
                    home = str(row["HomeTeam"]).strip()
                    away = str(row["AwayTeam"]).strip()
                    res = conn.execute(
                        """UPDATE fixtures
                              SET home_corners = COALESCE(home_corners, ?),
                                  away_corners = COALESCE(away_corners, ?)
                            WHERE league_id = ? AND season = ? AND kickoff = ?
                              AND home_team_id = (SELECT id FROM teams
                                                   WHERE league_id = ? AND name = ?)
                              AND away_team_id = (SELECT id FROM teams
                                                   WHERE league_id = ? AND name = ?)""",
                        (int(row["HC"]), int(row["AC"]),
                         league_id, season, kickoff,
                         league_id, home, league_id, away),
                    )
                    if res.rowcount:
                        n_updated += res.rowcount
                print(f"[{lg_code}/{season}] updated {n_updated} fixtures with corners")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv-dir", default="data/csv_cache")
    p.add_argument("--leagues", nargs="+",
                   default=["E0", "E1", "SP1", "I1", "D1", "F1"])
    p.add_argument("--seasons", nargs="+",
                   default=["2122", "2223", "2324", "2425"])
    args = p.parse_args()
    backfill(Path(args.csv_dir), args.leagues, args.seasons)


if __name__ == "__main__":
    main()
