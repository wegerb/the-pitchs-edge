"""Sweep market-blend weights across leagues to find the break-even blend.

For each (league, weight) combo we run a walk-forward backtest where
    p_bet = w * p_model + (1 - w) * p_pinnacle_close

w=1.0 is pure model (baseline).
w<1.0 "imports" information from the sharp book that our model can't see
     (lineups, weather, late money). At w=0.0 we'd have no edge vs Pinnacle
     by construction; somewhere between 1.0 and 0.0 usually maximizes ROI.

Defaults to line-shopping prices (best_close) since that's our realistic
bettor scenario. Uses goals-based Dixon-Coles across all 6 leagues (xG
coverage is only 5 of them, and blending should help both variants).
"""
import argparse

from pitchs_edge.backtest import WalkForwardConfig, run

LEAGUES = ["E0", "E1", "SP1", "I1", "D1", "F1"]
WEIGHTS = [1.0, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seasons", nargs="+", default=["2324", "2425"])
    p.add_argument("--xi", type=float, default=0.0019)
    p.add_argument("--edge-threshold", type=float, default=0.02)
    p.add_argument("--price-source", default="best_close",
                   choices=["pinnacle_close", "pinnacle_open", "best_close", "best_open"])
    p.add_argument("--leagues", nargs="+", default=LEAGUES)
    p.add_argument("--weights", nargs="+", type=float, default=WEIGHTS)
    p.add_argument("--model-source", default="goals", choices=["goals", "xg"])
    args = p.parse_args()

    rows = []
    for lg in args.leagues:
        for w in args.weights:
            cfg = WalkForwardConfig(
                league_code=lg, seasons=args.seasons, xi=args.xi,
                edge_threshold=args.edge_threshold,
                price_source=args.price_source,
                model_source=args.model_source,
                model_weight=w,
            )
            try:
                s = run(cfg)
            except Exception as e:
                print(f"[{lg} w={w:.2f}] FAILED: {e}")
                continue
            rows.append({
                "league": lg,
                "weight": w,
                "n_bets": s["simulated_n_bets"],
                "roi": s["simulated_roi"],
                "pnl": s["simulated_pnl"],
                "ll_1x2": s["log_loss_1x2"],
                "blend_ll_1x2": s.get("blended_log_loss_1x2"),
                "mkt_ll_1x2": s["market_log_loss_1x2"],
                "ll_ou": s["log_loss_ou25"],
                "blend_ll_ou": s.get("blended_log_loss_ou25"),
                "mkt_ll_ou": s["market_log_loss_ou25"],
            })

    print(f"\n=== seasons={args.seasons} | xi={args.xi} | price={args.price_source} "
          f"| model={args.model_source} ===\n")
    header = (f"{'lg':4} {'weight':>6} {'n_bets':>7} {'ROI':>8} {'PnL':>10} "
              f"{'LL_1x2':>8} {'blnd_1x2':>9} {'mkt_1x2':>8} "
              f"{'LL_OU':>8} {'blnd_OU':>9} {'mkt_OU':>8}")
    print(header)
    print("-" * len(header))
    last = None
    for r in rows:
        if last and last != r["league"]:
            print()
        last = r["league"]
        roi = f"{r['roi']*100:+.2f}%" if r['roi'] is not None else "   —  "
        blend_1x2 = f"{r['blend_ll_1x2']:.4f}" if r['blend_ll_1x2'] is not None else "   —  "
        blend_ou = f"{r['blend_ll_ou']:.4f}" if r['blend_ll_ou'] is not None else "   —  "
        print(f"{r['league']:4} {r['weight']:>6.2f} {r['n_bets']:>7} {roi:>8} "
              f"{r['pnl']:>+10.2f} "
              f"{r['ll_1x2']:.4f}  {blend_1x2:>8}  {r['mkt_ll_1x2']:.4f}  "
              f"{r['ll_ou']:.4f}  {blend_ou:>8}  {r['mkt_ll_ou']:.4f}")

    # Per-weight aggregate across leagues
    print("\n=== per-weight aggregate (avg across leagues) ===")
    print(f"{'weight':>6}  {'n_bets':>7}  {'total_PnL':>10}  {'avg_ROI':>8}  "
          f"{'avg_LL_1x2':>10}  {'avg_blend_1x2':>13}  "
          f"{'avg_LL_OU':>9}  {'avg_blend_OU':>12}")
    for w in args.weights:
        sub = [r for r in rows if r["weight"] == w]
        if not sub:
            continue
        total_pnl = sum(r["pnl"] for r in sub)
        total_n = sum(r["n_bets"] for r in sub)
        roi_vals = [r["roi"] for r in sub if r["roi"] is not None]
        avg_roi = (sum(roi_vals) / len(roi_vals) * 100) if roi_vals else 0.0
        avg_ll_1x2 = sum(r["ll_1x2"] for r in sub) / len(sub)
        blend_vals_1x2 = [r["blend_ll_1x2"] for r in sub if r["blend_ll_1x2"] is not None]
        avg_blend_1x2 = sum(blend_vals_1x2) / len(blend_vals_1x2) if blend_vals_1x2 else float("nan")
        avg_ll_ou = sum(r["ll_ou"] for r in sub) / len(sub)
        blend_vals_ou = [r["blend_ll_ou"] for r in sub if r["blend_ll_ou"] is not None]
        avg_blend_ou = sum(blend_vals_ou) / len(blend_vals_ou) if blend_vals_ou else float("nan")
        print(f"{w:>6.2f}  {total_n:>7}  {total_pnl:>+10.2f}  {avg_roi:>+7.2f}%  "
              f"{avg_ll_1x2:>10.4f}  {avg_blend_1x2:>13.4f}  "
              f"{avg_ll_ou:>9.4f}  {avg_blend_ou:>12.4f}")


if __name__ == "__main__":
    main()
