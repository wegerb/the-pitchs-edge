"""Walk-forward backtest of Dixon-Coles vs Pinnacle closing odds.

Trains on fixtures prior to each evaluation window, predicts the next
`--step` fixtures, and compares to the market's own (devigged) closing
probabilities. Also runs a Kelly-sized betting simulation at closing prices.

Requires historical fixtures + Pinnacle closing odds loaded via
`scripts/backfill_csv.py`.
"""
import argparse

from pitchs_edge.backtest import WalkForwardConfig, run, save_run


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--league", required=True,
                   help="League code: E0 E1 SP1 I1 D1 F1")
    p.add_argument("--seasons", nargs="+", required=True,
                   help="Seasons to include, e.g. 2122 2223 2324")
    p.add_argument("--xi", type=float, default=0.0019)
    p.add_argument("--min-train", type=int, default=100,
                   help="Minimum fixtures before first prediction window (default 100)")
    p.add_argument("--step", type=int, default=20,
                   help="Fixtures per evaluation window (default 20)")
    p.add_argument("--edge-threshold", type=float, default=0.02)
    p.add_argument("--kelly-scale", type=float, default=0.25)
    p.add_argument("--kelly-cap", type=float, default=0.02)
    p.add_argument("--bankroll", type=float, default=1000.0)
    p.add_argument("--name", default=None, help="Optional run name for the DB record")
    p.add_argument("--no-save", action="store_true", help="Don't persist to backtest_runs")
    args = p.parse_args()

    cfg = WalkForwardConfig(
        league_code=args.league,
        seasons=args.seasons,
        xi=args.xi,
        min_training_matches=args.min_train,
        step_fixtures=args.step,
        edge_threshold=args.edge_threshold,
        kelly_scale=args.kelly_scale,
        kelly_cap=args.kelly_cap,
        bankroll_start=args.bankroll,
    )

    print(f"Backtesting {args.league} over {args.seasons} — "
          f"xi={args.xi}, min_train={args.min_train}, step={args.step}")
    summary = run(cfg)

    def fmt(x, d=4):
        return "—" if x is None else f"{x:.{d}f}"

    print("\n--- 1X2 ---")
    print(f"  n_predictions: {summary['n_predictions_1x2']}")
    print(f"  model log-loss: {fmt(summary['log_loss_1x2'])}   "
          f"market log-loss: {fmt(summary['market_log_loss_1x2'])}")
    print(f"  model brier:    {fmt(summary['brier_1x2'])}")
    print(f"  model RPS:      {fmt(summary['rps_1x2'])}   "
          f"market RPS:      {fmt(summary['market_rps_1x2'])}")

    print("\n--- OU 2.5 ---")
    print(f"  n_predictions: {summary['n_predictions_ou25']}")
    print(f"  model log-loss: {fmt(summary['log_loss_ou25'])}   "
          f"market log-loss: {fmt(summary['market_log_loss_ou25'])}")
    print(f"  model brier:    {fmt(summary['brier_ou25'])}")

    print("\n--- Simulated bets at closing prices ---")
    print(f"  n_bets:        {summary['simulated_n_bets']}")
    print(f"  starting bank: {summary['bankroll_start']:.2f}")
    print(f"  ending bank:   {summary['bankroll_final']:.2f}")
    print(f"  total P/L:     {summary['simulated_pnl']:+.2f}")
    print(f"  ROI:           {fmt(summary['simulated_roi'])}")
    print("\n  NOTE: CLV is 0 by construction (we're betting AT closing). This is the "
          "'would you beat closing odds' test, not a live CLV measurement.")

    if not args.no_save:
        run_id = save_run(summary, name=args.name)
        print(f"\nSaved as backtest_runs.id = {run_id}")


if __name__ == "__main__":
    main()
