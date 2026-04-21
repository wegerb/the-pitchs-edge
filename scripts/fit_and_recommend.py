"""Fit Dixon-Coles per league, score upcoming fixtures against latest odds snapshots,
and write qualifying bets to the `bets` table.

Requires scheduled fixtures + odds snapshots:
    python scripts/fetch_fixtures.py
    python scripts/fetch_odds.py
    python scripts/fit_and_recommend.py
"""
import argparse
import json

from pitchs_edge.recommend import run


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--leagues", nargs="*", default=None,
                   help="League codes (default: all 6). e.g. E0 E1 SP1 I1 D1 F1")
    p.add_argument("--xi", type=float, default=0.0019,
                   help="Dixon-Coles time-decay (default 0.0019, ~180-day half-life)")
    p.add_argument("--edge-threshold", type=float, default=0.02,
                   help="Minimum edge (p*odds-1) to recommend (default 0.02)")
    p.add_argument("--kelly-scale", type=float, default=0.25,
                   help="Fractional Kelly multiplier (default 0.25)")
    p.add_argument("--kelly-cap", type=float, default=0.02,
                   help="Max fraction of bankroll per bet (default 0.02)")
    p.add_argument("--bankroll", type=float, default=1000.0,
                   help="Bankroll in units (default 1000)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print recommendations without writing to the bets table")
    args = p.parse_args()

    result = run(
        leagues=args.leagues,
        xi=args.xi,
        threshold=args.edge_threshold,
        kelly_scale=args.kelly_scale,
        kelly_cap=args.kelly_cap,
        bankroll=args.bankroll,
        dry_run=args.dry_run,
    )

    print(f"placed_at: {result['placed_at']}")
    print(f"dry_run:   {result['dry_run']}")
    print(f"total bets: {result['total_bets']}")
    for code, stats in result["leagues"].items():
        if "error" in stats:
            print(f"  {code}: {stats['error']}")
            continue
        print(
            f"  {code}: fit {stats['train_rows']} rows (LL {stats['log_likelihood']:.1f}), "
            f"scored {stats['fixtures_scored']} fixtures "
            f"({stats['fixtures_without_odds']} lacked odds), "
            f"{stats['bets']} bets"
        )
    if result["errors"]:
        print("errors:")
        for e in result["errors"]:
            print(f"  {e}")
    if args.dry_run:
        all_recs = [rec for stats in result["leagues"].values()
                    if isinstance(stats, dict) and "recommendations" in stats
                    for rec in stats["recommendations"]]
        if all_recs:
            print("\n--- recommendations (dry-run) ---")
            print(json.dumps(all_recs, indent=2, default=str))


if __name__ == "__main__":
    main()
