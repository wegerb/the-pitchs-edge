"""Compare goals-based vs xG-based Dixon-Coles backtests.

Runs both model_source='goals' and model_source='xg' for each of the 5 leagues
Understat covers (E0, SP1, I1, D1, F1). Championship (E1) has no xG so is
skipped. Uses the best_close price source (realistic bettor scenario).
"""
import argparse

from pitchs_edge.backtest import WalkForwardConfig, run

LEAGUES = ["E0", "SP1", "I1", "D1", "F1"]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seasons", nargs="+", default=["2324", "2425"])
    p.add_argument("--xi", type=float, default=0.005)
    p.add_argument("--edge-threshold", type=float, default=0.02)
    p.add_argument("--price-source", default="best_close",
                   choices=["pinnacle_close", "pinnacle_open", "best_close", "best_open"])
    p.add_argument("--leagues", nargs="+", default=LEAGUES)
    args = p.parse_args()

    rows = []
    for lg in args.leagues:
        for model_source in ("goals", "xg"):
            cfg = WalkForwardConfig(
                league_code=lg, seasons=args.seasons, xi=args.xi,
                edge_threshold=args.edge_threshold,
                price_source=args.price_source,
                model_source=model_source,
            )
            try:
                s = run(cfg)
            except Exception as e:
                print(f"[{lg}/{model_source}] FAILED: {e}")
                continue
            rows.append({
                "league": lg, "model": model_source,
                "n_bets": s["simulated_n_bets"],
                "roi": s["simulated_roi"],
                "pnl": s["simulated_pnl"],
                "log_loss_1x2": s["log_loss_1x2"],
                "market_ll_1x2": s["market_log_loss_1x2"],
                "log_loss_ou": s["log_loss_ou25"],
                "market_ll_ou": s["market_log_loss_ou25"],
            })

    print(f"\n=== seasons={args.seasons} | xi={args.xi} | price={args.price_source} ===\n")
    header = f"{'lg':4} {'model':6} {'n_bets':>7} {'ROI':>8} {'PnL':>10} {'LL_1x2':>8} {'mkt_1x2':>8} {'LL_OU':>8} {'mkt_OU':>8}"
    print(header)
    print("-" * len(header))
    last = None
    for r in rows:
        if last and last != r["league"]:
            print()
        last = r["league"]
        roi = f"{r['roi']*100:+.2f}%" if r['roi'] is not None else "—"
        print(f"{r['league']:4} {r['model']:6} {r['n_bets']:>7} {roi:>8} "
              f"{r['pnl']:>+10.2f} "
              f"{r['log_loss_1x2']:.4f}  {r['market_ll_1x2']:.4f}  "
              f"{r['log_loss_ou']:.4f}  {r['market_ll_ou']:.4f}")

    print("\n=== per-model aggregate ===")
    for m in ("goals", "xg"):
        sub = [r for r in rows if r["model"] == m]
        if not sub:
            continue
        total_pnl = sum(r["pnl"] for r in sub)
        total_n = sum(r["n_bets"] for r in sub)
        avg_roi = sum(r["roi"] or 0 for r in sub) / len(sub) * 100
        avg_ll_1x2 = sum(r["log_loss_1x2"] for r in sub) / len(sub)
        avg_ll_ou = sum(r["log_loss_ou"] for r in sub) / len(sub)
        avg_mkt_1x2 = sum(r["market_ll_1x2"] for r in sub) / len(sub)
        avg_mkt_ou = sum(r["market_ll_ou"] for r in sub) / len(sub)
        print(f"  {m:6}  n_bets={total_n:>6}  pnl={total_pnl:+9.2f}  "
              f"avg_ROI={avg_roi:+6.2f}%  "
              f"LL_1x2={avg_ll_1x2:.4f} (mkt {avg_mkt_1x2:.4f})  "
              f"LL_OU={avg_ll_ou:.4f} (mkt {avg_mkt_ou:.4f})")


if __name__ == "__main__":
    main()
