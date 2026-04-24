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
    p.add_argument("--price-source", default="pinnacle_close",
                   choices=["pinnacle_close", "pinnacle_open", "best_close", "best_open"],
                   help="Which odds to settle simulated bets at (default: pinnacle_close)")
    p.add_argument("--model-source", default="goals",
                   choices=["goals", "xg"],
                   help="Fit on FTHG/FTAG (goals) or Understat xG (default: goals)")
    p.add_argument("--model-weight", type=float, default=1.0,
                   help="Blend weight: p_bet = w*p_model + (1-w)*p_pinnacle_close. "
                        "1.0 = pure model (default); 0.7 typical for market-blending.")
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
        price_source=args.price_source,
        model_source=args.model_source,
        model_weight=args.model_weight,
    )

    print(f"Backtesting {args.league} over {args.seasons} — "
          f"xi={args.xi}, min_train={args.min_train}, step={args.step}, "
          f"model_source={args.model_source}, price_source={args.price_source}, "
          f"model_weight={args.model_weight}")
    summary = run(cfg)

    def fmt(x, d=4):
        return "—" if x is None else f"{x:.{d}f}"

    print("\n--- 1X2 ---")
    print(f"  n_predictions: {summary['n_predictions_1x2']}")
    print(f"  model log-loss:   {fmt(summary['log_loss_1x2'])}   "
          f"market log-loss: {fmt(summary['market_log_loss_1x2'])}")
    print(f"  blended log-loss: {fmt(summary.get('blended_log_loss_1x2'))}")
    print(f"  model brier:      {fmt(summary['brier_1x2'])}")
    print(f"  model RPS:        {fmt(summary['rps_1x2'])}   "
          f"market RPS:      {fmt(summary['market_rps_1x2'])}")
    print(f"  blended RPS:      {fmt(summary.get('blended_rps_1x2'))}")

    print("\n--- OU 2.5 ---")
    print(f"  n_predictions: {summary['n_predictions_ou25']}")
    print(f"  model log-loss:   {fmt(summary['log_loss_ou25'])}   "
          f"market log-loss: {fmt(summary['market_log_loss_ou25'])}")
    print(f"  blended log-loss: {fmt(summary.get('blended_log_loss_ou25'))}")
    print(f"  model brier:      {fmt(summary['brier_ou25'])}")

    print(f"\n--- Simulated bets @ {summary['price_source']} ---")
    print(f"  n_bets:        {summary['simulated_n_bets']}")
    print(f"  starting bank: {summary['bankroll_start']:.2f}")
    print(f"  ending bank:   {summary['bankroll_final']:.2f}")
    print(f"  total P/L:     {summary['simulated_pnl']:+.2f}")
    print(f"  ROI:           {fmt(summary['simulated_roi'])}")

    # CLV is the leading-indicator: positive CLV = we're beating the sharp close,
    # which over the long run is the only reliable signal of +EV.
    if summary.get('clv_mean') is not None:
        print(f"\n--- CLV (vs Pinnacle close) ---")
        print(f"  mean CLV:          {summary['clv_mean']*100:+.3f}%")
        print(f"  median CLV:        {summary['clv_median']*100:+.3f}%")
        print(f"  stake-weighted CLV:{summary['clv_weighted']*100:+.3f}%"
              if summary['clv_weighted'] is not None else "  stake-weighted CLV:   —")
        print(f"  % bets w/ +CLV:    {summary['clv_positive_rate']*100:.1f}%")
    if summary['price_source'] == 'pinnacle_close':
        print("\n  NOTE: CLV is 0 by construction (betting AT Pinnacle close). Strictest bar.")
    elif summary['price_source'] == 'best_close':
        print("\n  NOTE: Bets at best-price-across-books (line shopping). Realistic upper bound.")
    elif summary['price_source'].endswith('_open'):
        print("\n  NOTE: Bets at opening lines. Tests whether early edges are real.")

    if not args.no_save:
        run_id = save_run(summary, name=args.name)
        print(f"\nSaved as backtest_runs.id = {run_id}")


if __name__ == "__main__":
    main()
