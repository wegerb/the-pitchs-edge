"""Re-run walk-forward backtests for every league using its tuned config.

This is the "refresh the Track Record panel" script. It exists because
`backtest_runs` is the single source of truth for the dashboard's track
record, and without this script those rows drift away from whatever config
we're actually betting with (see `tuned_configs.py`).

Flow:
  for each league →
    1. Load its TunedConfig.
    2. Build a WalkForwardConfig from it using `--seasons` from the CLI.
    3. Run walk-forward, save_run() into backtest_runs.
    4. Print the headline ROI/CLV per league.

Run:
    python -m scripts.refresh_tuned_backtests --seasons 2324 2425

The seasons flag chooses the backtest window — we default to the holdout
period (2324 + 2425) since that's what the tuned configs were validated
against, so the Track Record ROI mirrors what a forward-tested bettor would
have actually made.
"""
from __future__ import annotations

import argparse
import traceback

from pitchs_edge.backtest import WalkForwardConfig, run, save_run
from pitchs_edge.config import LEAGUES
from pitchs_edge.tuned_configs import get_tuned


def _fmt_pct(x: float | None, places: int = 2) -> str:
    if x is None:
        return "   —   "
    return f"{x*100:+.{places}f}%"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", default=["2324", "2425"],
                    help="Season codes to backtest against (default: 2324 2425 = holdout window).")
    ap.add_argument("--price-source", default="best_close",
                    choices=["pinnacle_close", "pinnacle_open", "best_close", "best_open"],
                    help="Where simulated bets are settled. Matches production edge sheet "
                         "(best-price-across-books) by default.")
    ap.add_argument("--leagues", nargs="+", default=None,
                    help="Subset of league codes to refresh (default: all).")
    args = ap.parse_args()

    codes = args.leagues or [l.code for l in LEAGUES]
    print(f"\n{'='*78}")
    print(f"REFRESHING TUNED BACKTESTS | seasons={args.seasons} | price={args.price_source}")
    print(f"{'='*78}\n")
    print(f"  {'lg':>3}  {'src':>5}  {'xi':>7}  {'mt':>4}  {'et':>5}  {'w':>4}   "
          f"{'ROI':>8}   {'CLV_w':>8}   {'bets':>6}  val")
    print("  " + "-" * 76)

    for code in codes:
        tuned = get_tuned(code)
        cfg = WalkForwardConfig(
            league_code=code,
            seasons=list(args.seasons),
            xi=tuned.xi,
            min_training_matches=tuned.min_training_matches,
            edge_threshold=tuned.edge_threshold,
            model_weight=tuned.model_weight,
            model_source=tuned.model_source,
            kelly_scale=tuned.kelly_scale,
            kelly_cap=tuned.kelly_cap,
            price_source=args.price_source,
        )
        try:
            summary = run(cfg)
        except Exception as e:
            print(f"  {code:>3}  {tuned.model_source:>5}  "
                  f"FAILED — {e.__class__.__name__}: {e}")
            traceback.print_exc()
            continue

        run_id = save_run(summary, name=f"PROD_{code}_{'_'.join(args.seasons)}")
        val = "YES" if tuned.validated else " — "
        print(f"  {code:>3}  {tuned.model_source:>5}  {tuned.xi:>7.4f}  "
              f"{tuned.min_training_matches:>4}  {tuned.edge_threshold:>5.2f}  "
              f"{tuned.model_weight:>4.2f}   "
              f"{_fmt_pct(summary.get('simulated_roi')):>8}   "
              f"{_fmt_pct(summary.get('clv_weighted')):>8}   "
              f"{summary.get('simulated_n_bets', 0):>6}  {val}  "
              f"(run_id={run_id})")

    print()
    print("  Dashboard Track Record pulls MAX(id) per league, so these are now live.")


if __name__ == "__main__":
    main()
