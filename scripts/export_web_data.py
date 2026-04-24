"""Generate web/data.json consumed by the static frontend.

Pulls upcoming fixtures + latest book odds from the DB, fits Dixon-Coles per
league, computes the 1X2 / OU 2.5 / BTTS probability sheet for each fixture,
and turns it into a single JSON payload.

If the DB has no upcoming fixtures or no odds, the payload is emitted empty
and the UI shows a prompt to run the ingest pipeline — nothing is synthesized.

Run:
    python -m scripts.export_web_data
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from pitchs_edge.config import LEAGUES, REPO_ROOT
from pitchs_edge.db import connect
from pitchs_edge.edge.devig import shin
from pitchs_edge.edge.kelly import kelly
from pitchs_edge.models import (
    DixonColesParams,
    fit as fit_dc,
    market_1x2,
    market_btts,
    market_over_under,
)
from pitchs_edge.names import best_match
from pitchs_edge.recommend import fit_league
from pitchs_edge.tuned_configs import TunedConfig, get_tuned

OUT = REPO_ROOT / "web" / "data.json"

# ---------- helpers ----------

def _tier(edge: float) -> str:
    if edge >= 0.07:
        return "strong"
    if edge >= 0.04:
        return "good"
    if edge >= 0.02:
        return "marginal"
    return "flat"


def _stars(edge: float) -> int:
    if edge >= 0.08:
        return 5
    if edge >= 0.05:
        return 4
    if edge >= 0.03:
        return 3
    if edge >= 0.015:
        return 2
    return 1


def _short(name: str) -> str:
    stop = {"fc", "cf", "afc", "united", "city", "rovers"}
    parts = [p for p in name.replace(".", "").split() if p.lower() not in stop]
    if len(parts) == 1:
        return parts[0][:3].upper()
    return (parts[0][:3] if len(parts[0]) > 3 else parts[0]).upper()


def _top_scores(mat: np.ndarray, n: int = 6) -> list[tuple[str, float]]:
    flat = [(f"{h}-{a}", float(mat[h, a])) for h in range(mat.shape[0]) for a in range(mat.shape[1])]
    flat.sort(key=lambda r: -r[1])
    return flat[:n]


def _expected_goals(mat: np.ndarray) -> tuple[float, float]:
    hg = sum(h * mat[h, :].sum() for h in range(mat.shape[0]))
    ag = sum(a * mat[:, a].sum() for a in range(mat.shape[1]))
    return float(hg), float(ag)


def _winrate_for_run(conn, run_id: int) -> dict:
    """Grade every walk-forward 1X2 prediction in `run_id` against the actual result.

    Returns: n_matches, win_rate (model top pick), plus the three naive baselines
    (always-home, always-draw, always-away) on the same fixture set so the
    frontend can show the delta over the best naive strategy — the simplest
    possible "is this model doing real work?" check for non-experts.

    Prediction rows in backtest_predictions carry one row per (fixture, selection)
    with `model_prob` and `actual` (1 if this selection was the result, else 0).
    We pivot to one row per fixture, pick argmax(model_prob), and compare.
    """
    rows = conn.execute(
        """SELECT fixture_id,
                  MAX(CASE WHEN selection='home' THEN model_prob END) AS mh,
                  MAX(CASE WHEN selection='draw' THEN model_prob END) AS md,
                  MAX(CASE WHEN selection='away' THEN model_prob END) AS ma,
                  MAX(CASE WHEN selection='home' AND actual=1 THEN 1 ELSE 0 END) AS ah,
                  MAX(CASE WHEN selection='draw' AND actual=1 THEN 1 ELSE 0 END) AS ad,
                  MAX(CASE WHEN selection='away' AND actual=1 THEN 1 ELSE 0 END) AS aa
           FROM backtest_predictions
           WHERE run_id = ? AND market = '1X2'
           GROUP BY fixture_id""",
        (run_id,),
    ).fetchall()
    n = len(rows)
    if n == 0:
        return {"n_matches": 0, "win_rate": None,
                "baseline_home": None, "baseline_draw": None, "baseline_away": None}
    mhit = hhit = dhit = ahit = 0
    for r in rows:
        probs = ((r["mh"] if r["mh"] is not None else -1, 0),
                 (r["md"] if r["md"] is not None else -1, 1),
                 (r["ma"] if r["ma"] is not None else -1, 2))
        actuals = (r["ah"], r["ad"], r["aa"])
        midx = max(probs, key=lambda x: x[0])[1]
        mhit += actuals[midx]
        hhit += actuals[0]
        dhit += actuals[1]
        ahit += actuals[2]
    return {
        "n_matches": n,
        "win_rate": mhit / n,
        "baseline_home": hhit / n,
        "baseline_draw": dhit / n,
        "baseline_away": ahit / n,
    }


def _latest_backtest_runs(conn) -> list[dict]:
    """Most recent walk-forward backtest per (league, price_source), for the Track Record panel.

    Two rows per league: one settled at Pinnacle closing (the "strict bar" —
    CLV is 0 by construction so ROI alone reflects calibration skill), one at
    best-price-across-books (the "realistic bar" — reflects what a line-shopping
    bettor would actually capture). The frontend collapses these into a single
    league row with both ROI columns.

    We also grade every prediction against actual outcomes (win rate) and
    compute the three always-X baselines — that's what the UI's hero cards
    and per-league win-rate column use.
    """
    rows = conn.execute(
        """SELECT r.id, l.code, l.name AS league_name, r.seasons, r.xi, r.n_predictions,
                  r.log_loss_1x2, r.market_log_loss_1x2,
                  r.rps_1x2, r.market_rps_1x2,
                  r.log_loss_ou25, r.market_log_loss_ou25,
                  r.simulated_n_bets, r.simulated_roi,
                  r.bankroll_start, r.bankroll_final, r.created_at,
                  r.price_source, r.model_weight, r.model_source,
                  r.clv_weighted, r.clv_mean, r.clv_positive_rate
           FROM backtest_runs r
           JOIN leagues l ON l.id = r.league_id
           WHERE r.id IN (
               SELECT MAX(id) FROM backtest_runs
               WHERE price_source IS NOT NULL
               GROUP BY league_id, price_source
           )
           ORDER BY l.code, r.price_source"""
    ).fetchall()
    out = []
    for r in rows:
        wr = _winrate_for_run(conn, r["id"])
        out.append({
            "league_code": r["code"],
            "league_name": r["league_name"],
            "seasons": r["seasons"],
            "xi": r["xi"],
            "n_predictions": r["n_predictions"],
            "n_matches_graded": wr["n_matches"],
            "win_rate": wr["win_rate"],
            "baseline_home": wr["baseline_home"],
            "baseline_draw": wr["baseline_draw"],
            "baseline_away": wr["baseline_away"],
            "model_log_loss_1x2": r["log_loss_1x2"],
            "market_log_loss_1x2": r["market_log_loss_1x2"],
            "model_rps_1x2": r["rps_1x2"],
            "market_rps_1x2": r["market_rps_1x2"],
            "model_log_loss_ou25": r["log_loss_ou25"],
            "market_log_loss_ou25": r["market_log_loss_ou25"],
            "simulated_bets": r["simulated_n_bets"],
            "simulated_roi": r["simulated_roi"],
            "bankroll_start": r["bankroll_start"],
            "bankroll_final": r["bankroll_final"],
            "run_at": r["created_at"],
            "price_source": r["price_source"],
            "model_weight": r["model_weight"],
            "model_source": r["model_source"],
            "clv_weighted": r["clv_weighted"],
            "clv_mean": r["clv_mean"],
            "clv_positive_rate": r["clv_positive_rate"],
        })
    return out


def _recent_form(conn, team_id: int, before_iso: str, n: int = 5) -> str:
    rows = conn.execute(
        """SELECT home_team_id, away_team_id, fthg, ftag
           FROM fixtures
           WHERE (home_team_id = ? OR away_team_id = ?)
             AND fthg IS NOT NULL AND ftag IS NOT NULL
             AND kickoff < ?
           ORDER BY kickoff DESC LIMIT ?""",
        (team_id, team_id, before_iso, n),
    ).fetchall()
    out: list[str] = []
    for r in rows:
        hg, ag = r["fthg"], r["ftag"]
        if r["home_team_id"] == team_id:
            out.append("W" if hg > ag else ("D" if hg == ag else "L"))
        else:
            out.append("W" if ag > hg else ("D" if ag == hg else "L"))
    return "".join(out) or "—"


# ---------- DB access ----------

def _upcoming_for_league(conn, league_id: int, limit: int = 40) -> list[dict]:
    return conn.execute(
        """SELECT f.id, f.kickoff,
                  f.home_team_id, f.away_team_id,
                  ht.name AS home, at.name AS away
           FROM fixtures f
           JOIN teams ht ON ht.id = f.home_team_id
           JOIN teams at ON at.id = f.away_team_id
           WHERE f.league_id = ? AND f.status = 'scheduled'
             AND datetime(f.kickoff) > datetime('now')
           ORDER BY f.kickoff LIMIT ?""",
        (league_id, limit),
    ).fetchall()


def _latest_odds(conn, fixture_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT book, market, selection, line, price
           FROM odds_snapshots
           WHERE fixture_id = ? AND id IN (
               SELECT MAX(id) FROM odds_snapshots
               WHERE fixture_id = ?
               GROUP BY book, market, selection, line
           )""",
        (fixture_id, fixture_id),
    ).fetchall()
    if rows:
        return rows
    return conn.execute(
        """SELECT book, market, selection, line, price FROM odds_closing
           WHERE fixture_id = ?""",
        (fixture_id,),
    ).fetchall()


_SHARP_BOOKS = ("Pinnacle", "Smarkets", "Betfair", "Matchbook")


def _best_book_price(odds_rows: list[dict], market: str, selection: str, line: float | None) -> tuple[float, str] | None:
    """Return a realistic best price (price, book) for (market, selection).

    Taking literal `max(price)` across every book rewards stale quotes and soft
    books that price lazily — producing +180% "edges" that no bettor can
    actually realise. We instead anchor on the sharp-book / median fair price
    and only accept a price at most 3% above that anchor, which keeps genuine
    high-value plays while dropping obvious outliers.
    """
    candidates = [
        r for r in odds_rows
        if r["market"] == market and r["selection"] == selection
        and (line is None or (r["line"] is not None and abs(float(r["line"]) - float(line)) < 1e-6))
    ]
    if not candidates:
        return None

    prices = sorted(float(r["price"]) for r in candidates)
    n = len(prices)
    median = prices[n // 2] if n % 2 else 0.5 * (prices[n // 2 - 1] + prices[n // 2])

    # Prefer a sharp-book price if we have one — Pinnacle especially runs near
    # the true market and dismisses stale outliers.
    anchor = median
    for book in _SHARP_BOOKS:
        sharp = next((float(r["price"]) for r in candidates if r["book"] == book), None)
        if sharp is not None:
            anchor = sharp
            break

    cap = anchor * 1.03 if n >= 3 else float("inf")
    in_range = [r for r in candidates if float(r["price"]) <= cap]
    if not in_range:
        in_range = candidates
    best = max(in_range, key=lambda r: float(r["price"]))
    return float(best["price"]), str(best["book"])


# ---------- fixture build ----------

def _build_fixture(
    *, params: DixonColesParams, league: dict, conn, fx: dict, odds_rows: list[dict],
    tuned: TunedConfig,
) -> dict | None:
    # Fixture sources use long names ("Manchester City FC"); training CSVs use
    # short names ("Man City"). Resolve through the canonical team list the
    # Dixon-Coles params were fit on.
    home_trained = best_match(fx["home"], params.teams)
    away_trained = best_match(fx["away"], params.teams)
    if home_trained is None or away_trained is None:
        return None
    try:
        mat = params.score_matrix(home_trained, away_trained)
    except ValueError:
        return None
    one_x_two = market_1x2(mat)
    ou25 = market_over_under(mat, 2.5)
    btts = market_btts(mat)
    exp_home, exp_away = _expected_goals(mat)

    # Market-devigged 1X2 (tries a few sharp books in order).
    market_1x2_probs = None
    market_1x2_source = None
    for book in ("Pinnacle", "Bet365", "WilliamHill"):
        prices = {}
        for sel in ("home", "draw", "away"):
            p = _best_book_price(
                [r for r in odds_rows if r["book"] == book], "1X2", sel, None
            )
            if p:
                prices[sel] = p[0]
        if len(prices) == 3:
            try:
                devig = shin([prices["home"], prices["draw"], prices["away"]])
                market_1x2_probs = {"home": float(devig[0]), "draw": float(devig[1]), "away": float(devig[2])}
                market_1x2_source = book
                break
            except ValueError:
                continue

    # Market-devigged OU 2.5 (same sharp-book preference).
    market_ou25_probs = None
    for book in ("Pinnacle", "Bet365", "WilliamHill"):
        prices = {}
        for sel in ("over", "under"):
            p = _best_book_price(
                [r for r in odds_rows if r["book"] == book], "OU", sel, 2.5
            )
            if p:
                prices[sel] = p[0]
        if len(prices) == 2:
            try:
                devig = shin([prices["over"], prices["under"]])
                market_ou25_probs = {"over": float(devig[0]), "under": float(devig[1])}
                break
            except ValueError:
                continue

    # Best price per selection across all books — used for edge display.
    best_odds: dict[str, dict] = {}
    for market, selections, line in [
        ("1X2", ("home", "draw", "away"), None),
        ("OU",  ("over", "under"), 2.5),
    ]:
        per_sel = {}
        for sel in selections:
            got = _best_book_price(odds_rows, market, sel, line)
            if got:
                per_sel[sel] = {"price": got[0], "book": got[1]}
        if per_sel:
            best_odds[market + (f"_{line}" if line is not None else "")] = per_sel

    # Edge computation — mirrors the walk-forward engine.
    #
    # Three things happen per (market, selection):
    #   1. Blend model probs with Pinnacle's devigged close:
    #        p_bet = w·p_model + (1-w)·p_pinnacle_close
    #      This is the probability we bet with. w is per-league tuned.
    #   2. Edge = p_bet × price − 1 gates on `tuned.edge_threshold`.
    #   3. Kelly sizing uses p_bet (not raw p_model).
    #
    # If we have no Pinnacle close for a market we fall back to pure model
    # probs (w=1) — better to surface a possibly-weaker edge than drop the
    # fixture. Those plays are still flagged via `trust="unknown"`.
    model_lookup = {
        "1X2": (one_x_two, None),
        "OU":  (ou25,      2.5),
    }
    market_probs_lookup = {
        "1X2": market_1x2_probs,
        "OU":  market_ou25_probs,
    }
    w = float(tuned.model_weight)
    edges: list[dict] = []
    for market_key, (probs, line) in model_lookup.items():
        key = market_key + (f"_{line}" if line is not None else "")
        if key not in best_odds:
            continue
        market_probs = market_probs_lookup.get(market_key)
        for sel, book_info in best_odds[key].items():
            mp = float(probs.get(sel, 0.0))
            if not 0.0 < mp < 1.0:
                continue
            price = book_info["price"]
            mkt_p = market_probs.get(sel) if market_probs else None

            # Blend if we have a sharp anchor; otherwise bet pure model.
            if mkt_p is not None:
                p_bet = w * mp + (1.0 - w) * float(mkt_p)
            else:
                p_bet = mp
            p_bet = max(min(p_bet, 0.9999), 0.0001)

            edge = p_bet * price - 1.0
            if edge <= tuned.edge_threshold:
                continue
            ks = kelly(p_bet, price, scale=tuned.kelly_scale, cap=tuned.kelly_cap)
            if ks.fraction <= 0:
                continue

            # Trust flag is driven by the raw model-vs-market gap, not the blend —
            # the blend itself already tempers the model, but a raw >15pp gap is
            # still useful diagnostic info for the UI.
            sharp_delta_pp = (mp - mkt_p) * 100.0 if mkt_p is not None else None
            if sharp_delta_pp is None:
                trust = "unknown"
            elif sharp_delta_pp > 15:
                trust = "extreme"
            elif sharp_delta_pp > 7:
                trust = "wide"
            else:
                trust = "aligned"

            edges.append({
                "market": market_key,
                "selection": sel,
                "line": line,
                "book": book_info["book"],
                "price": price,
                "model_prob": mp,
                "blended_prob": p_bet,
                "market_prob": mkt_p,
                "sharp_delta_pp": sharp_delta_pp,
                "trust": trust,
                "edge_pct": edge,
                "kelly_fraction": float(ks.fraction),
                "tier": _tier(edge),
                "stars": _stars(edge),
                "validated": tuned.validated,
            })
    edges.sort(key=lambda e: -e["edge_pct"])

    return {
        "id": fx["id"],
        "league_code": league["code"],
        "league_name": league["name"],
        "country": league["country"],
        "kickoff": fx["kickoff"],
        "home": {
            "name": fx["home"],
            "short": _short(fx["home"]),
            "form": _recent_form(conn, fx["home_team_id"], fx["kickoff"]),
        },
        "away": {
            "name": fx["away"],
            "short": _short(fx["away"]),
            "form": _recent_form(conn, fx["away_team_id"], fx["kickoff"]),
        },
        "model": {
            "home_win": float(one_x_two["home"]),
            "draw":     float(one_x_two["draw"]),
            "away_win": float(one_x_two["away"]),
            "over25":   float(ou25["over"]),
            "under25":  float(ou25["under"]),
            "btts_yes": float(btts["yes"]),
            "btts_no":  float(btts["no"]),
            "xg_home":  exp_home,
            "xg_away":  exp_away,
            "top_scores": _top_scores(mat, 6),
        },
        "market": market_1x2_probs,
        "odds": best_odds,
        "edges": edges,
    }


def build() -> dict:
    """Walk every configured league, score every upcoming fixture with odds, return payload."""
    fixtures_out: list[dict] = []
    leagues_seen: list[dict] = []
    notes: list[str] = []

    # Capture the tuned config per league so the frontend can surface it on
    # the Track Record panel / "why this play" tooltip.
    tuning_out: list[dict] = []

    with connect() as conn:
        for league_meta in LEAGUES:
            lg = conn.execute(
                "SELECT id, code, name, country FROM leagues WHERE code = ?",
                (league_meta.code,),
            ).fetchone()
            if not lg:
                notes.append(f"{league_meta.code}: league row missing — run scripts/init_db.py")
                continue
            tuned = get_tuned(lg["code"])
            params, train_rows = fit_league(
                conn, lg["id"], xi=tuned.xi, model_source=tuned.model_source,
            )
            if params is None:
                notes.append(f"{league_meta.code}: insufficient training data ({train_rows} rows) — run scripts/backfill_csv.py")
                continue
            fixtures = _upcoming_for_league(conn, lg["id"], limit=20)
            if not fixtures:
                notes.append(f"{league_meta.code}: no upcoming fixtures — run scripts/fetch_fixtures.py")
                continue
            leagues_seen.append({
                "code": lg["code"], "name": lg["name"], "country": lg["country"],
            })
            tuning_out.append({
                "league_code": lg["code"],
                "xi": tuned.xi,
                "model_source": tuned.model_source,
                "min_training_matches": tuned.min_training_matches,
                "edge_threshold": tuned.edge_threshold,
                "model_weight": tuned.model_weight,
                "validated": tuned.validated,
                "notes": tuned.notes,
                "holdout_roi": tuned.holdout_roi,
                "holdout_clv": tuned.holdout_clv,
                "holdout_n_bets": tuned.holdout_n_bets,
            })
            scored_with_odds = 0
            for fx in fixtures:
                odds = _latest_odds(conn, fx["id"])
                if not odds:
                    continue
                built = _build_fixture(
                    params=params, league=lg, conn=conn, fx=fx, odds_rows=odds,
                    tuned=tuned,
                )
                if built:
                    fixtures_out.append(built)
                    scored_with_odds += 1
            if scored_with_odds == 0:
                notes.append(f"{league_meta.code}: {len(fixtures)} fixtures but no live odds — run scripts/fetch_odds.py")

    fixtures_out.sort(key=lambda f: f["kickoff"])
    total_edges = sum(len(f["edges"]) for f in fixtures_out)
    strong = sum(1 for f in fixtures_out for e in f["edges"] if e["tier"] == "strong")

    # Load the latest backtest metrics for the Track Record section on the site.
    with connect() as conn:
        backtest = _latest_backtest_runs(conn)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "db",
        "disclaimer": "Data via football-data.org + The Odds API. Model: Dixon-Coles with Shin devig. "
                      "For entertainment only — not betting advice.",
        "leagues": leagues_seen,
        "fixtures": fixtures_out,
        "backtest": backtest,
        "tuning": tuning_out,
        "stats": {
            "total_fixtures": len(fixtures_out),
            "total_edges": total_edges,
            "strong_edges": strong,
            "leagues_active": len(leagues_seen),
        },
        "pipeline_notes": notes,
    }


# ---------- entry ----------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT, help="output path")
    args = ap.parse_args()

    payload = build()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, default=str))
    print(
        f"[export] wrote {args.out} — {payload['stats']['total_fixtures']} fixtures, "
        f"{payload['stats']['total_edges']} edges across {payload['stats']['leagues_active']} leagues"
    )
    if payload["pipeline_notes"]:
        print("[export] pipeline notes:")
        for n in payload["pipeline_notes"]:
            print(f"  - {n}")


if __name__ == "__main__":
    main()
