"""Run walk-forward backtest for every league across multiple price sources.

Compares:
  pinnacle_close — sharpest bar (CLV = 0)
  pinnacle_open  — Pinnacle opening (early market bet)
  best_close     — max price across {Pinnacle, B365, BW, BF, WH, BFE}
  best_open      — max price across {Pinnacle_open, B365_open}

Prints a compact ROI table so we can see which variant (if any) breaks even.
"""
import argparse

from pitchs_edge.backtest import WalkForwardConfig, run

LEAGUES = ["E0", "E1", "SP1", "I1", "D1", "F1"]
PRICE_SOURCES = ["pinnacle_close", "pinnacle_open", "best_close", "best_open"]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seasons", nargs="+", default=["2324", "2425"],
                   help="Seasons to backtest (default 2324 2425)")
    p.add_argument("--xi", type=float, default=0.005)
    p.add_argument("--edge-threshold", type=float, default=0.02)
    p.add_argument("--min-train", type=int, default=100)
    p.add_argument("--leagues", nargs="+", default=LEAGUES)
    p.add_argument("--sources", nargs="+", default=PRICE_SOURCES)
    args = p.parse_args()

    rows: list[dict] = []
    for lg in args.leagues:
        for src in args.sources:
            cfg = WalkForwardConfig(
                league_code=lg,
                seasons=args.seasons,
                xi=args.xi,
                min_training_matches=args.min_train,
                edge_threshold=args.edge_threshold,
                price_source=src,
            )
            try:
                s = run(cfg)
            except Exception as e:
                print(f"[{lg}/{src}] FAILED: {e}")
                continue
            rows.append({
                "league": lg, "source": src,
                "n_bets": s["simulated_n_bets"],
                "roi": s["simulated_roi"],
                "pnl": s["simulated_pnl"],
                "bankroll_final": s["bankroll_final"],
                "model_ll_1x2": s["log_loss_1x2"],
                "mkt_ll_1x2":   s["market_log_loss_1x2"],
            })

    # Print comparison table
    print(f"\n=== seasons={args.seasons} | xi={args.xi} | edge>{args.edge_threshold*100:.0f}% ===\n")
    print(f"{'league':6} {'source':18} {'n_bets':>7} {'ROI':>8} {'PnL':>10} {'bank_fin':>10} {'model_LL':>9} {'mkt_LL':>9}")
    print("-" * 90)
    last_league = None
    for r in rows:
        if last_league and last_league != r["league"]:
            print()
        last_league = r["league"]
        roi = f"{r['roi']*100:+.2f}%" if r['roi'] is not None else "—"
        mll = f"{r['model_ll_1x2']:.4f}" if r['model_ll_1x2'] is not None else "—"
        kll = f"{r['mkt_ll_1x2']:.4f}"   if r['mkt_ll_1x2']   is not None else "—"
        print(f"{r['league']:6} {r['source']:18} {r['n_bets']:>7} {roi:>8} "
              f"{r['pnl']:>+10.2f} {r['bankroll_final']:>10.2f} {mll:>9} {kll:>9}")

    # Aggregate by source
    print("\n=== aggregate by price_source ===")
    for src in args.sources:
        sub = [r for r in rows if r["source"] == src]
        if not sub:
            continue
        total_pnl = sum(r["pnl"] for r in sub)
        total_n = sum(r["n_bets"] for r in sub)
        # Bankroll-weighted ROI: total PnL / cumulative bankroll risked
        # (approximate: multiply n_bets times avg stake; just use weighted by pnl/n_bets)
        print(f"  {src:18}  n_bets={total_n:>6}  total_pnl={total_pnl:+.2f}  "
              f"avg_roi={sum(r['roi'] or 0 for r in sub) / len(sub) * 100:+.2f}%")


if __name__ == "__main__":
    main()
