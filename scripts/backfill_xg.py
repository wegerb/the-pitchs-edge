"""Backfill per-fixture xG from Understat into the fixtures table.

Run after `backfill_csv.py` has populated fixtures. Covers EPL, La Liga,
Serie A, Bundesliga, Ligue 1 (Understat coverage). Championship (E1) has no
xG so those fixtures stay on goals-only.
"""
import argparse

from pitchs_edge.ingest.xg_understat import ingest_all

DEFAULT_LEAGUES = ["E0", "SP1", "I1", "D1", "F1"]
DEFAULT_SEASONS = ["2122", "2223", "2324", "2425", "2526"]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--leagues", nargs="+", default=DEFAULT_LEAGUES,
                   help="League codes (default: 5 xG-covered leagues)")
    p.add_argument("--seasons", nargs="+", default=DEFAULT_SEASONS,
                   help="Seasons (default: 2122-2526)")
    args = p.parse_args()

    print(f"Backfilling xG for leagues={args.leagues} seasons={args.seasons}")
    total = ingest_all(leagues=args.leagues, seasons=args.seasons)
    print()
    print("=== Summary ===")
    for k, v in total.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
