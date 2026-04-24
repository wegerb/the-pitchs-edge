"""Walk-forward calibration test for the corners model.

No corner odds in football-data.co.uk, so we can't test ROI directly.
What we CAN test is model calibration: does our per-team corner-attack +
corner-defense + home-advantage Poisson fit produce realistic probabilities?

Metrics computed per league:
  - log-loss on over 9.5/10.5/11.5 corners
  - Brier score on each
  - implied-rate calibration: mean predicted total vs mean actual total

Benchmark: a naive "league-average" model that just predicts each team's
seasonal corner rate. If our fitted model doesn't beat that, the team
effects aren't buying us anything.
"""
from __future__ import annotations

import argparse
from datetime import datetime

import numpy as np

from pitchs_edge.db import connect
from pitchs_edge.models import fit_corners, market_corners_total


def run_league(league_code: str, seasons: list[str], xi: float,
               min_training: int, step: int) -> None:
    with connect() as conn:
        lg = conn.execute("SELECT id FROM leagues WHERE code = ?", (league_code,)).fetchone()
        if not lg:
            print(f"[{league_code}] not in DB")
            return
        rows = conn.execute(
            """SELECT f.id, f.kickoff, ht.name AS home, at.name AS away,
                      f.home_corners, f.away_corners
                 FROM fixtures f
                 JOIN teams ht ON ht.id = f.home_team_id
                 JOIN teams at ON at.id = f.away_team_id
                WHERE f.league_id = ? AND f.season IN ({})
                  AND f.home_corners IS NOT NULL AND f.away_corners IS NOT NULL
                ORDER BY f.kickoff""".format(",".join("?" * len(seasons))),
            (lg["id"], *seasons),
        ).fetchall()

    if len(rows) < min_training + step:
        print(f"[{league_code}] only {len(rows)} fixtures; need {min_training + step}+")
        return

    lines = [8.5, 9.5, 10.5, 11.5, 12.5]
    # Accumulated (prob_over, actual_over) for each line
    per_line: dict[float, list[tuple[float, int]]] = {l: [] for l in lines}
    total_preds = []   # (predicted_total, actual_total)

    t = min_training
    while t < len(rows):
        train_slice = rows[:t]
        eval_slice = rows[t : t + step]
        if not eval_slice:
            break
        as_of = datetime.fromisoformat(eval_slice[0]["kickoff"])

        params = fit_corners(
            home_teams=[r["home"] for r in train_slice],
            away_teams=[r["away"] for r in train_slice],
            home_corners=[r["home_corners"] for r in train_slice],
            away_corners=[r["away_corners"] for r in train_slice],
            match_dates=[datetime.fromisoformat(r["kickoff"]) for r in train_slice],
            xi=xi, as_of=as_of,
        )
        for fx in eval_slice:
            try:
                mat = params.score_matrix(fx["home"], fx["away"])
            except ValueError:
                continue  # promoted team unseen in training
            lam, mu = params.rates(fx["home"], fx["away"])
            actual_total = int(fx["home_corners"] + fx["away_corners"])
            total_preds.append((lam + mu, actual_total))
            for line in lines:
                m = market_corners_total(mat, line)
                per_line[line].append((m["over"], 1 if actual_total > line else 0))

        t += step

    if not total_preds:
        print(f"[{league_code}] no predictions generated")
        return

    # Mean prediction vs mean actual (calibration)
    preds = np.array([p[0] for p in total_preds])
    acts = np.array([p[1] for p in total_preds])
    print(f"\n=== {league_code} | xi={xi} | min_train={min_training} | step={step} ===")
    print(f"  n_predictions: {len(total_preds)}")
    print(f"  mean predicted total: {preds.mean():.3f}  (actual mean: {acts.mean():.3f})")
    print(f"  RMSE on total corners: {np.sqrt(((preds - acts) ** 2).mean()):.3f}")
    print(f"\n  per-line log-loss / Brier / accuracy:")
    print(f"    line   n   LL      Brier   acc")
    for line in lines:
        pairs = per_line[line]
        if not pairs:
            continue
        p = np.array([x[0] for x in pairs])
        y = np.array([x[1] for x in pairs])
        # clip to avoid log(0)
        p_c = np.clip(p, 1e-6, 1 - 1e-6)
        ll = float(-(y * np.log(p_c) + (1 - y) * np.log(1 - p_c)).mean())
        brier = float(((p - y) ** 2).mean())
        acc = float(((p > 0.5).astype(int) == y).mean())
        # Baseline: just predict base rate (class prior)
        base = y.mean()
        base_ll = float(-(y * np.log(max(base, 1e-6)) + (1 - y) * np.log(max(1 - base, 1e-6))).mean())
        lift = base_ll - ll
        print(f"    {line:>4}  {len(pairs):>4}  {ll:.4f}  {brier:.4f}  {acc:.3f}  (base_LL={base_ll:.4f}, lift={lift:+.4f})")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--leagues", nargs="+",
                   default=["E0", "E1", "SP1", "I1", "D1", "F1"])
    p.add_argument("--seasons", nargs="+", default=["2324", "2425"])
    p.add_argument("--xi", type=float, default=0.0019)
    p.add_argument("--min-training", type=int, default=100)
    p.add_argument("--step", type=int, default=20)
    args = p.parse_args()

    for lg in args.leagues:
        run_league(lg, args.seasons, args.xi, args.min_training, args.step)


if __name__ == "__main__":
    main()
