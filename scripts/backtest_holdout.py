"""Out-of-sample validation: tune on early seasons, evaluate on held-out season.

The hyperparameter sweeps produced suspiciously-good results on 2324-2425:
+6% to +10% ROI for some configs. Since those numbers were found by
searching over xi × edge_threshold × model_weight × min_training on that
very data, the numbers are polluted by selection bias.

The only honest test: pick the BEST config based on an earlier period,
then run that exact config on a FUTURE period the tuner never saw. If
the ROI holds up, the edge is structural. If it collapses, we were
overfitting.

Strategy here:
  1. Tune period: 2122 + 2223 → grid-search for best ROI config.
  2. Holdout period: 2324 + 2425 → apply the winning config only, no further tuning.
  3. Compare holdout ROI to what the same config predicted on the tune period.
"""
from __future__ import annotations

import argparse
import itertools

from pitchs_edge.backtest import WalkForwardConfig, run


def tune_and_holdout(
    league: str,
    model_source: str,
    price_source: str,
    tune_seasons: list[str],
    holdout_seasons: list[str],
    xis: list[float],
    edges: list[float],
    weights: list[float],
    min_trains: list[int],
    min_bets_for_validity: int = 200,
) -> None:
    print(f"\n{'='*74}")
    print(f"HOLDOUT VALIDATION | {league} | model={model_source} | price={price_source}")
    print(f"  tune seasons:    {tune_seasons}")
    print(f"  holdout seasons: {holdout_seasons}")
    print(f"  grid: {len(xis)} xi × {len(edges)} edge × {len(weights)} w × "
          f"{len(min_trains)} min_train = "
          f"{len(xis)*len(edges)*len(weights)*len(min_trains)} configs")
    print(f"{'='*74}")

    tune_results = []
    for xi, et, w, mt in itertools.product(xis, edges, weights, min_trains):
        cfg = WalkForwardConfig(
            league_code=league, seasons=tune_seasons,
            xi=xi, edge_threshold=et, model_weight=w,
            min_training_matches=mt,
            price_source=price_source, model_source=model_source,
        )
        try:
            s = run(cfg)
        except Exception:
            continue
        roi = s["simulated_roi"]
        nb = s["simulated_n_bets"]
        if roi is None or nb < min_bets_for_validity:
            continue
        tune_results.append({
            "xi": xi, "et": et, "w": w, "mt": mt,
            "roi": roi, "pnl": s["simulated_pnl"], "n_bets": nb,
            "clv_w": s.get("clv_weighted"),
        })

    if not tune_results:
        print("  no valid tune-period results")
        return

    tune_results.sort(key=lambda r: r["roi"], reverse=True)
    print(f"\n  TOP 5 tune-period configs (by ROI):")
    print(f"    {'xi':>7} {'edge':>5} {'w':>4} {'mt':>5}  {'ROI':>8}  {'CLV':>8}  {'n_bets':>7}")
    for r in tune_results[:5]:
        clv = f"{r['clv_w']*100:+.2f}%" if r['clv_w'] is not None else "  —   "
        print(f"    {r['xi']:>7.4f} {r['et']:>5.2f} {r['w']:>4.2f} {r['mt']:>5}  "
              f"{r['roi']*100:>+7.2f}%  {clv:>8}  {r['n_bets']:>7}")

    best = tune_results[0]
    print(f"\n  APPLYING BEST TUNED CONFIG TO HOLDOUT...")
    print(f"    xi={best['xi']} edge={best['et']} w={best['w']} min_train={best['mt']}")

    # Validate on holdout
    cfg_ho = WalkForwardConfig(
        league_code=league, seasons=holdout_seasons,
        xi=best["xi"], edge_threshold=best["et"], model_weight=best["w"],
        min_training_matches=best["mt"],
        price_source=price_source, model_source=model_source,
    )
    try:
        ho = run(cfg_ho)
    except Exception as e:
        print(f"  HOLDOUT FAILED: {e}")
        return

    ho_roi = ho["simulated_roi"]
    ho_clv = ho.get("clv_weighted")
    print(f"\n  ---- RESULTS ----")
    print(f"  TUNE:    ROI {best['roi']*100:+.2f}%  CLV {best['clv_w']*100:+.2f}%  "
          f"n_bets {best['n_bets']}")
    ho_roi_str = f"{ho_roi*100:+.2f}%" if ho_roi is not None else "  —  "
    ho_clv_str = f"{ho_clv*100:+.2f}%" if ho_clv is not None else "  —  "
    print(f"  HOLDOUT: ROI {ho_roi_str:>8}  CLV {ho_clv_str:>8}  "
          f"n_bets {ho['simulated_n_bets']}")
    if ho_roi is not None and best["roi"] is not None:
        delta_roi = (ho_roi - best["roi"]) * 100
        print(f"  delta ROI (holdout - tune): {delta_roi:+.2f}pp")
        verdict = "GENUINE EDGE" if ho_roi > 0.005 else "SUSPECT — tune ROI did not transfer" \
            if ho_roi < -0.005 else "MARGINAL — further validation needed"
        print(f"  VERDICT: {verdict}")
    if ho_clv is not None and ho_clv > 0.005:
        print(f"  ** CLV on holdout is positive ({ho_clv*100:+.2f}%) — edge IS structurally real.")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--league", default="E1")
    p.add_argument("--model-source", default="goals", choices=["goals", "xg"])
    p.add_argument("--price-source", default="best_close")
    p.add_argument("--tune-seasons", nargs="+", default=["2122", "2223"])
    p.add_argument("--holdout-seasons", nargs="+", default=["2324", "2425"])
    p.add_argument("--xis", nargs="+", type=float, default=[0.001, 0.0019, 0.005])
    p.add_argument("--edges", nargs="+", type=float, default=[0.02, 0.04, 0.06])
    p.add_argument("--weights", nargs="+", type=float, default=[0.3, 0.6, 1.0])
    p.add_argument("--min-trains", nargs="+", type=int, default=[200, 500, 800])
    args = p.parse_args()

    tune_and_holdout(
        league=args.league,
        model_source=args.model_source,
        price_source=args.price_source,
        tune_seasons=args.tune_seasons,
        holdout_seasons=args.holdout_seasons,
        xis=args.xis, edges=args.edges, weights=args.weights,
        min_trains=args.min_trains,
    )


if __name__ == "__main__":
    main()
