"""Download football-data.co.uk CSVs and load historical fixtures + Pinnacle closing odds."""
import argparse

from pitchs_edge.ingest.historical import ingest


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--leagues", nargs="*", default=None,
                   help="League codes (default: all 6). e.g. E0 E1 SP1 I1 D1 F1")
    p.add_argument("--seasons", nargs="*", default=None,
                   help="Season codes (default: last 3). e.g. 2122 2223 2324")
    args = p.parse_args()
    stats = ingest(leagues=args.leagues, seasons=args.seasons)
    print(stats)


if __name__ == "__main__":
    main()
