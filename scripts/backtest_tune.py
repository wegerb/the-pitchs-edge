"""Sweep xi (time-decay) × edge_threshold for a fixed (league, model, weight, price).

Use this after blend-sweep identifies a profitable config to squeeze more ROI.
xi controls how aggressively old matches are down-weighted in the likelihood;
edge_threshold controls the conviction required before placing a bet. Both are
the highest-leverage knobs once model family and price source are chosen.

Defaults target E1 (Championship) goals + w=0.6 + best_close — the strongest
profitable config found.
"""
import argparse

from pitchs_edge.backtest import WalkForwardConfig, run


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--league", default="E1")
    p.add_argument("--seasons", nargs="+", default=["2324", "2425"])
    p.add_argument("--price-source", default="best_close",
                   choices=["pinnacle_close", "pinnacle_open", "best_close", "best_open"])
    p.add_argument("--model-source", default="goals", choices=["goals", "xg"])
    p.add_argument("--model-weight", type=float, default=0.6)
    p.add_argument("--xis", nargs="+", type=float,
                   default=[0.0010, 0.0019, 0.0030, 0.0050, 0.0080, 0.0120])
    p.add_argument("--edge-thresholds", nargs="+", type=float,
                   default=[0.01, 0.02, 0.03, 0.04, 0.05, 0.07])
    args = p.parse_args()

    rows = []
    for xi in args.xis:
        for et in args.edge_thresholds:
            cfg = WalkForwardConfig(
                league_code=args.league, seasons=args.seasons,
                xi=xi, edge_threshold=et,
                price_source=args.price_source,
                model_source=args.model_source,
                model_weight=args.model_weight,
            )
            try:
                s = run(cfg)
            except Exception as e:
                print(f"[xi={xi} et={et}] FAILED: {e}")
                continue
            rows.append({
                "xi": xi, "et": et,
                "n": s["simulated_n_bets"],
                "roi": s["simulated_roi"],
                "pnl": s["simulated_pnl"],
                "ll_1x2": s["log_loss_1x2"],
                "blend_1x2": s.get("blended_log_loss_1x2"),
                "ll_ou": s["log_loss_ou25"],
                "blend_ou": s.get("blended_log_loss_ou25"),
            })

    print(f"\n=== {args.league} | model={args.model_source} | w={args.model_weight} "
          f"| price={args.price_source} | seasons={args.seasons} ===\n")
    header = (f"{'xi':>7} {'edge':>6} {'n_bets':>7} {'ROI':>8} {'PnL':>10} "
              f"{'LL_1x2':>8} {'blnd_1x2':>9} {'LL_OU':>8} {'blnd_OU':>9}")
    print(header)
    print("-" * len(header))
    last_xi = None
    best = None
    for r in rows:
        if last_xi is not None and last_xi != r["xi"]:
            print()
        last_xi = r["xi"]
        roi = f"{r['roi']*100:+.2f}%" if r['roi'] is not None else "   —  "
        b1 = f"{r['blend_1x2']:.4f}" if r['blend_1x2'] is not None else "   —   "
        bo = f"{r['blend_ou']:.4f}" if r['blend_ou'] is not None else "   —   "
        print(f"{r['xi']:>7.4f} {r['et']:>6.2f} {r['n']:>7} {roi:>8} "
              f"{r['pnl']:>+10.2f} {r['ll_1x2']:.4f}  {b1:>8}  "
              f"{r['ll_ou']:.4f}  {bo:>8}")
        # Best requires at least 100 bets for meaningfulness
        if r["roi"] is not None and r["n"] >= 100:
            if best is None or r["roi"] > best["roi"]:
                best = r

    if best:
        print(f"\n[BEST] xi={best['xi']} edge={best['et']} "
              f"-> ROI={best['roi']*100:+.2f}% PnL={best['pnl']:+.2f} n_bets={best['n']}")


if __name__ == "__main__":
    main()
