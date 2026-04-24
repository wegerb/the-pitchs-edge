"""Sweep min_training_matches for a fixed (league, xi, edge_threshold) combo.

Tests whether letting the model train on less data (shorter warmup) or
more data (longer warmup) improves walk-forward ROI. Smaller min_train
gives earlier predictions but on a noisier fit; larger is the opposite.
"""
import argparse

from pitchs_edge.backtest import WalkForwardConfig, run


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--league", default="E1")
    p.add_argument("--seasons", nargs="+", default=["2324", "2425"])
    p.add_argument("--price-source", default="best_close")
    p.add_argument("--model-source", default="goals", choices=["goals", "xg"])
    p.add_argument("--model-weight", type=float, default=0.6)
    p.add_argument("--xi", type=float, default=0.001)
    p.add_argument("--edge-threshold", type=float, default=0.04)
    p.add_argument("--min-trains", nargs="+", type=int,
                   default=[50, 80, 100, 150, 200, 300, 500])
    args = p.parse_args()

    rows = []
    for mt in args.min_trains:
        cfg = WalkForwardConfig(
            league_code=args.league, seasons=args.seasons,
            xi=args.xi, edge_threshold=args.edge_threshold,
            min_training_matches=mt,
            price_source=args.price_source,
            model_source=args.model_source,
            model_weight=args.model_weight,
        )
        try:
            s = run(cfg)
        except Exception as e:
            print(f"[min_train={mt}] FAILED: {e}")
            continue
        rows.append({
            "mt": mt,
            "n_pred_1x2": s["n_predictions_1x2"],
            "n_bets": s["simulated_n_bets"],
            "roi": s["simulated_roi"],
            "pnl": s["simulated_pnl"],
            "ll_1x2": s["log_loss_1x2"],
            "blend_1x2": s.get("blended_log_loss_1x2"),
            "clv_w": s.get("clv_weighted"),
        })

    print(f"\n=== {args.league} | model={args.model_source} | w={args.model_weight} "
          f"| xi={args.xi} | edge={args.edge_threshold} ===\n")
    header = (f"{'min_tr':>7} {'n_pred':>7} {'n_bets':>7} {'ROI':>8} {'PnL':>10} "
              f"{'LL_1x2':>8} {'blnd_1x2':>9} {'CLV_w':>7}")
    print(header)
    print("-" * len(header))
    best = None
    for r in rows:
        roi = f"{r['roi']*100:+.2f}%" if r['roi'] is not None else "   —  "
        clv = f"{r['clv_w']*100:+.2f}%" if r['clv_w'] is not None else "   —  "
        print(f"{r['mt']:>7} {r['n_pred_1x2']:>7} {r['n_bets']:>7} {roi:>8} "
              f"{r['pnl']:>+10.2f} {r['ll_1x2']:.4f}  {r['blend_1x2']:.4f}  {clv:>7}")
        if r["roi"] is not None and r["n_bets"] >= 500:
            if best is None or r["roi"] > best["roi"]:
                best = r
    if best:
        print(f"\n[BEST] min_train={best['mt']} -> ROI={best['roi']*100:+.2f}% "
              f"PnL={best['pnl']:+.2f} n_bets={best['n_bets']}")


if __name__ == "__main__":
    main()
