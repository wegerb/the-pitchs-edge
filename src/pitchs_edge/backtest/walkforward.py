"""Walk-forward backtest.

Walks through a league's finished fixtures chronologically. At each step, refits
Dixon-Coles on fixtures prior to the current split, then produces predictions for
the next `step_fixtures` fixtures. Predictions are compared against:

  1. **Actual outcomes** → log loss, Brier, RPS (the model's calibration).
  2. **Market closing probs** (Pinnacle, devigged with Shin) → same metrics applied
     to the market's own prediction, giving you the "bar to beat."

A Kelly-sized simulation is also run: whenever model edge > threshold at the
chosen price source, a stake is placed. Bankroll compounds; P/L and ROI are reported.

`price_source` controls the prices used to settle simulated bets:

  "pinnacle_close" — Pinnacle closing (PSCH/PSCD/PSCA, PC>2.5/PC<2.5, PCAHH/PCAHA).
                     Strictest bar: CLV = 0 by construction against the sharpest book.
  "pinnacle_open"  — Pinnacle opening (PSH/PSD/PSA, P>2.5/P<2.5, PAHH/PAHA).
                     Realistic "shopped early at Pinnacle" scenario.
  "best_close"     — Max price across {Pinnacle, B365, BW, BF, WH, BFE} for 1X2,
                     and football-data Max column for OU 2.5. Tests whether
                     line-shopping across mainstream books rescues the edge.
  "best_open"      — Max price across opening books for 1X2, Max_open for OU.
                     Most realistic for a bettor willing to hunt best lines early.

Market devig (for log-loss benchmarks and trust classification) always uses
Pinnacle closing — the sharpest anchor — regardless of price_source.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import numpy as np

from ..db import connect
from ..edge import kelly, shin
from ..models import fit as fit_dc, fit_xg, market_1x2, market_over_under
from .metrics import brier_score, log_loss, rps

OUTCOME_1X2 = ("home", "draw", "away")
OUTCOME_OU = ("over", "under")


@dataclass
class WalkForwardConfig:
    league_code: str
    seasons: list[str]
    xi: float = 0.0019
    min_training_matches: int = 100
    step_fixtures: int = 20
    edge_threshold: float = 0.02
    kelly_scale: float = 0.25
    kelly_cap: float = 0.02
    bankroll_start: float = 1000.0
    markets: tuple[str, ...] = ("1X2", "OU25")
    price_source: str = "pinnacle_close"  # pinnacle_close | pinnacle_open | best_close | best_open
    model_source: str = "goals"           # "goals" (fit on FTHG/FTAG) | "xg" (fit on Understat xG)

    # Market blending: final probability used for edge/Kelly is:
    #   p_bet = model_weight * p_model + (1 - model_weight) * p_pinnacle_close
    # model_weight=1.0 ⇒ pure model (current behavior).
    # model_weight=0.0 ⇒ pure market (no edge vs sharp book, by construction).
    # 0 < model_weight < 1 lets the sharp market contribute the information
    # our model can't see (injuries, late line moves, lineup news) while our
    # model still pushes on games where we disagree. Standard practice at
    # quant sportsbooks.
    model_weight: float = 1.0


_PRICE_BOOKS_1X2 = {
    "pinnacle_close": ["Pinnacle"],
    "pinnacle_open":  ["Pinnacle_open"],
    "best_close":     ["Pinnacle", "B365", "BW", "BF", "WH", "BFE"],
    "best_open":      ["Pinnacle_open", "B365_open"],
}
_PRICE_BOOKS_OU = {
    "pinnacle_close": ["Pinnacle"],
    "pinnacle_open":  ["Pinnacle_open"],
    "best_close":     ["Max"],       # football-data pre-computes best-price column
    "best_open":      ["Max_open"],
}


def _load_fixtures(conn, league_id: int, seasons: list[str]) -> list[dict]:
    placeholders = ",".join("?" * len(seasons))
    rows = conn.execute(
        f"""SELECT f.id, f.kickoff, f.season, ht.name AS home, at.name AS away,
                   f.fthg, f.ftag, f.home_xg, f.away_xg
            FROM fixtures f
            JOIN teams ht ON ht.id = f.home_team_id
            JOIN teams at ON at.id = f.away_team_id
            WHERE f.league_id = ? AND f.season IN ({placeholders})
              AND f.fthg IS NOT NULL AND f.ftag IS NOT NULL
            ORDER BY f.kickoff, f.id""",
        (league_id, *seasons),
    ).fetchall()
    return rows


def _odds_for_fixture(
    conn, fixture_id: int, *, books_1x2: list[str], books_ou: list[str]
) -> dict[tuple[str, str, float | None], float]:
    """Return a dict of (market, selection, line) -> MAX price across the given books.

    For single-book lookups this is just that book's price. For multi-book it's the
    best available price — which is what a line-shopping bettor would have captured.
    """
    all_books = list(set(books_1x2 + books_ou))
    placeholders = ",".join("?" * len(all_books))
    rows = conn.execute(
        f"""SELECT market, selection, line, MAX(price) AS price
            FROM odds_closing
            WHERE fixture_id = ? AND book IN ({placeholders})
            GROUP BY market, selection, line""",
        (fixture_id, *all_books),
    ).fetchall()
    return {(r["market"], r["selection"], r["line"]): float(r["price"]) for r in rows}


def _pinnacle_closing_for_fixture(conn, fixture_id: int) -> dict[tuple[str, str, float | None], float]:
    """Sharp-anchor prices used for devig (market_probs benchmark)."""
    rows = conn.execute(
        """SELECT market, selection, line, price
           FROM odds_closing
           WHERE fixture_id = ? AND book = 'Pinnacle'""",
        (fixture_id,),
    ).fetchall()
    return {(r["market"], r["selection"], r["line"]): float(r["price"]) for r in rows}


def _devig_1x2(odds: dict) -> dict[str, float] | None:
    prices = [odds.get(("1X2", s, None)) for s in OUTCOME_1X2]
    if any(p is None or p <= 1.0 for p in prices):
        return None
    fair = shin(prices)
    return {s: float(fair[i]) for i, s in enumerate(OUTCOME_1X2)}


def _devig_ou(odds: dict, line: float = 2.5) -> dict[str, float] | None:
    prices = [odds.get(("OU", s, line)) for s in OUTCOME_OU]
    if any(p is None or p <= 1.0 for p in prices):
        return None
    fair = shin(prices)
    return {s: float(fair[i]) for i, s in enumerate(OUTCOME_OU)}


def _actual_1x2(fthg: int, ftag: int) -> int:
    if fthg > ftag:
        return 0  # home
    if fthg == ftag:
        return 1  # draw
    return 2      # away


def _actual_ou(fthg: int, ftag: int, line: float = 2.5) -> int:
    return 0 if (fthg + ftag) > line else 1   # 0 = over, 1 = under


def _simulate_bet(
    *, model_prob: float, closing_price: float, won: bool,
    bankroll: float, scale: float, cap: float, threshold: float,
) -> tuple[float, float] | None:
    """Returns (stake, pnl) if bet placed; None otherwise."""
    if not 0.0 < model_prob < 1.0 or closing_price <= 1.0:
        return None
    edge = model_prob * closing_price - 1.0
    if edge <= threshold:
        return None
    ks = kelly(model_prob, closing_price, scale=scale, cap=cap)
    if ks.fraction <= 0:
        return None
    stake = ks.fraction * bankroll
    pnl = stake * (closing_price - 1.0) if won else -stake
    return stake, pnl


def run(cfg: WalkForwardConfig) -> dict:
    from ..config import LEAGUE_BY_CODE
    league = LEAGUE_BY_CODE.get(cfg.league_code)
    if league is None:
        raise ValueError(f"unknown league code: {cfg.league_code}")

    if cfg.price_source not in _PRICE_BOOKS_1X2:
        raise ValueError(
            f"unknown price_source {cfg.price_source!r}; must be one of "
            f"{list(_PRICE_BOOKS_1X2.keys())}"
        )
    bet_books_1x2 = _PRICE_BOOKS_1X2[cfg.price_source]
    bet_books_ou = _PRICE_BOOKS_OU[cfg.price_source]

    if cfg.model_source not in ("goals", "xg"):
        raise ValueError(f"unknown model_source {cfg.model_source!r}; must be 'goals' or 'xg'")

    with connect() as conn:
        lg = conn.execute("SELECT id FROM leagues WHERE code = ?", (cfg.league_code,)).fetchone()
        if not lg:
            raise RuntimeError(f"league {cfg.league_code} not in DB — run init + backfill first")
        league_id = lg["id"]
        fixtures = _load_fixtures(conn, league_id, cfg.seasons)

    if len(fixtures) < cfg.min_training_matches + 1:
        raise RuntimeError(
            f"only {len(fixtures)} finished fixtures; need at least "
            f"{cfg.min_training_matches + 1}"
        )

    # Walk-forward loop
    preds_1x2: list[tuple[int, np.ndarray, int, float]] = []   # (fx_id, probs, actual, edge_best)
    preds_ou25: list[tuple[int, np.ndarray, int, float]] = []
    blended_1x2_rows: list[tuple[np.ndarray, int]] = []        # (blended_probs, actual)
    blended_ou25_rows: list[tuple[np.ndarray, int]] = []
    market_1x2_rows: list[tuple[np.ndarray, int]] = []
    market_ou25_rows: list[tuple[np.ndarray, int]] = []

    bets: list[dict] = []
    bankroll = cfg.bankroll_start

    w = float(cfg.model_weight)
    if not (0.0 <= w <= 1.0):
        raise ValueError(f"model_weight must be in [0, 1], got {w}")

    t = cfg.min_training_matches
    n = len(fixtures)

    while t < n:
        train_slice = fixtures[:t]
        eval_slice = fixtures[t : t + cfg.step_fixtures]
        if not eval_slice:
            break

        as_of_dt = datetime.fromisoformat(eval_slice[0]["kickoff"])

        if cfg.model_source == "xg":
            # Only train on fixtures that have xG populated (skip pre-xG/unmatched rows)
            xg_train = [r for r in train_slice
                        if r["home_xg"] is not None and r["away_xg"] is not None]
            if len(xg_train) < cfg.min_training_matches:
                # Not enough xG-covered history — fall back to goals for this window
                params = fit_dc(
                    home_teams=[r["home"] for r in train_slice],
                    away_teams=[r["away"] for r in train_slice],
                    home_goals=[r["fthg"] for r in train_slice],
                    away_goals=[r["ftag"] for r in train_slice],
                    match_dates=[datetime.fromisoformat(r["kickoff"]) for r in train_slice],
                    xi=cfg.xi, as_of=as_of_dt,
                )
            else:
                params = fit_xg(
                    home_teams=[r["home"] for r in xg_train],
                    away_teams=[r["away"] for r in xg_train],
                    home_xg=[r["home_xg"] for r in xg_train],
                    away_xg=[r["away_xg"] for r in xg_train],
                    match_dates=[datetime.fromisoformat(r["kickoff"]) for r in xg_train],
                    xi=cfg.xi, as_of=as_of_dt,
                )
        else:
            params = fit_dc(
                home_teams=[r["home"] for r in train_slice],
                away_teams=[r["away"] for r in train_slice],
                home_goals=[r["fthg"] for r in train_slice],
                away_goals=[r["ftag"] for r in train_slice],
                match_dates=[datetime.fromisoformat(r["kickoff"]) for r in train_slice],
                xi=cfg.xi, as_of=as_of_dt,
            )

        with connect() as conn:
            for fx in eval_slice:
                try:
                    mat = params.score_matrix(fx["home"], fx["away"])
                except ValueError:
                    continue  # team not in training (promoted)

                # Sharp anchor for devig (always Pinnacle closing)
                pin_odds = _pinnacle_closing_for_fixture(conn, fx["id"])
                # Bet-source prices (per cfg.price_source)
                bet_odds = _odds_for_fixture(
                    conn, fx["id"], books_1x2=bet_books_1x2, books_ou=bet_books_ou
                )

                # 1X2
                if "1X2" in cfg.markets:
                    m = market_1x2(mat)
                    probs = np.array([m[s] for s in OUTCOME_1X2])
                    actual = _actual_1x2(fx["fthg"], fx["ftag"])
                    preds_1x2.append((fx["id"], probs, actual, 0.0))
                    market_probs = _devig_1x2(pin_odds)
                    if market_probs is not None:
                        market_probs_arr = np.array([market_probs[s] for s in OUTCOME_1X2])
                        market_1x2_rows.append((market_probs_arr, actual))

                        # Blend model with Pinnacle closing probs (no-op when w=1)
                        blended = w * probs + (1.0 - w) * market_probs_arr
                        blended = blended / blended.sum()  # defensive renorm
                        blended_1x2_rows.append((blended, actual))

                        # simulate bets at configured price source using blended probs
                        for i, sel in enumerate(OUTCOME_1X2):
                            price = bet_odds.get(("1X2", sel, None))
                            if price is None:
                                continue
                            # Pinnacle closing price for CLV (sharp benchmark)
                            close_price = pin_odds.get(("1X2", sel, None))
                            won = (i == actual)
                            sim = _simulate_bet(
                                model_prob=float(blended[i]), closing_price=price, won=won,
                                bankroll=bankroll, scale=cfg.kelly_scale, cap=cfg.kelly_cap,
                                threshold=cfg.edge_threshold,
                            )
                            if sim is None:
                                continue
                            stake, pnl = sim
                            bankroll += pnl
                            # CLV = (entered_price / pin_close) - 1; positive = beat sharp close
                            clv = (price / close_price - 1.0) if close_price and close_price > 1.0 else None
                            bets.append({
                                "fixture_id": fx["id"], "market": "1X2", "selection": sel,
                                "line": None, "model_prob": float(blended[i]),
                                "closing_prob": float(market_probs[sel]), "closing_price": price,
                                "pinnacle_close_price": close_price,
                                "clv": clv,
                                "actual": 1 if won else 0,
                                "edge_pct": float(blended[i]) * price - 1.0,
                                "stake": stake, "pnl": pnl,
                            })

                # OU 2.5
                if "OU25" in cfg.markets:
                    m = market_over_under(mat, 2.5)
                    probs = np.array([m[s] for s in OUTCOME_OU])
                    actual = _actual_ou(fx["fthg"], fx["ftag"], 2.5)
                    preds_ou25.append((fx["id"], probs, actual, 0.0))
                    market_probs = _devig_ou(pin_odds, 2.5)
                    if market_probs is not None:
                        market_probs_arr = np.array([market_probs[s] for s in OUTCOME_OU])
                        market_ou25_rows.append((market_probs_arr, actual))

                        blended = w * probs + (1.0 - w) * market_probs_arr
                        blended = blended / blended.sum()
                        blended_ou25_rows.append((blended, actual))

                        for i, sel in enumerate(OUTCOME_OU):
                            price = bet_odds.get(("OU", sel, 2.5))
                            if price is None:
                                continue
                            close_price = pin_odds.get(("OU", sel, 2.5))
                            won = (i == actual)
                            sim = _simulate_bet(
                                model_prob=float(blended[i]), closing_price=price, won=won,
                                bankroll=bankroll, scale=cfg.kelly_scale, cap=cfg.kelly_cap,
                                threshold=cfg.edge_threshold,
                            )
                            if sim is None:
                                continue
                            stake, pnl = sim
                            bankroll += pnl
                            clv = (price / close_price - 1.0) if close_price and close_price > 1.0 else None
                            bets.append({
                                "fixture_id": fx["id"], "market": "OU", "selection": sel,
                                "line": 2.5, "model_prob": float(blended[i]),
                                "closing_prob": float(market_probs[sel]), "closing_price": price,
                                "pinnacle_close_price": close_price,
                                "clv": clv,
                                "actual": 1 if won else 0,
                                "edge_pct": float(blended[i]) * price - 1.0,
                                "stake": stake, "pnl": pnl,
                            })

        t += cfg.step_fixtures

    # Aggregate metrics
    def _arrays(rows: list[tuple[int, np.ndarray, int, float]]) -> tuple[np.ndarray, np.ndarray]:
        if not rows:
            return np.empty((0, 1)), np.empty((0,), dtype=int)
        probs = np.stack([r[1] for r in rows], axis=0)
        actuals = np.array([r[2] for r in rows], dtype=int)
        return probs, actuals

    def _market_arrays(rows: list[tuple[np.ndarray, int]]) -> tuple[np.ndarray, np.ndarray]:
        if not rows:
            return np.empty((0, 1)), np.empty((0,), dtype=int)
        probs = np.stack([r[0] for r in rows], axis=0)
        actuals = np.array([r[1] for r in rows], dtype=int)
        return probs, actuals

    p_1x2, a_1x2 = _arrays(preds_1x2)
    p_ou, a_ou = _arrays(preds_ou25)
    mp_1x2, ma_1x2 = _market_arrays(market_1x2_rows)
    mp_ou, ma_ou = _market_arrays(market_ou25_rows)
    bp_1x2, ba_1x2 = _market_arrays(blended_1x2_rows)
    bp_ou, ba_ou = _market_arrays(blended_ou25_rows)

    summary = {
        "league": cfg.league_code,
        "seasons": cfg.seasons,
        "xi": cfg.xi,
        "price_source": cfg.price_source,
        "model_source": cfg.model_source,
        "model_weight": cfg.model_weight,
        "min_training_matches": cfg.min_training_matches,
        "step_fixtures": cfg.step_fixtures,
        "edge_threshold": cfg.edge_threshold,
        "kelly_scale": cfg.kelly_scale,
        "kelly_cap": cfg.kelly_cap,
        "bankroll_start": cfg.bankroll_start,
        "n_predictions_1x2": int(len(preds_1x2)),
        "n_predictions_ou25": int(len(preds_ou25)),
        "log_loss_1x2": log_loss(p_1x2, a_1x2) if len(preds_1x2) else None,
        "brier_1x2": brier_score(p_1x2, a_1x2) if len(preds_1x2) else None,
        "rps_1x2": rps(p_1x2, a_1x2) if len(preds_1x2) else None,
        "market_log_loss_1x2": log_loss(mp_1x2, ma_1x2) if len(market_1x2_rows) else None,
        "market_rps_1x2": rps(mp_1x2, ma_1x2) if len(market_1x2_rows) else None,
        "blended_log_loss_1x2": log_loss(bp_1x2, ba_1x2) if len(blended_1x2_rows) else None,
        "blended_rps_1x2": rps(bp_1x2, ba_1x2) if len(blended_1x2_rows) else None,
        "log_loss_ou25": log_loss(p_ou, a_ou) if len(preds_ou25) else None,
        "brier_ou25": brier_score(p_ou, a_ou) if len(preds_ou25) else None,
        "market_log_loss_ou25": log_loss(mp_ou, ma_ou) if len(market_ou25_rows) else None,
        "blended_log_loss_ou25": log_loss(bp_ou, ba_ou) if len(blended_ou25_rows) else None,
        "simulated_n_bets": len(bets),
        "simulated_pnl": float(sum(b["pnl"] for b in bets)) if bets else 0.0,
        "bankroll_final": float(bankroll),
        "simulated_roi": (
            float(sum(b["pnl"] for b in bets) / sum(b["stake"] for b in bets))
            if bets and sum(b["stake"] for b in bets) > 0 else None
        ),
        "bets": bets,
        "predictions_1x2": preds_1x2,
        "predictions_ou25": preds_ou25,
    }

    # CLV aggregates (the sharper-than-close signal of whether our entries are +EV)
    clv_vals = [b["clv"] for b in bets if b.get("clv") is not None]
    if clv_vals:
        summary["clv_mean"] = float(sum(clv_vals) / len(clv_vals))
        sorted_clv = sorted(clv_vals)
        mid = len(sorted_clv) // 2
        summary["clv_median"] = float(
            sorted_clv[mid] if len(sorted_clv) % 2 == 1
            else (sorted_clv[mid - 1] + sorted_clv[mid]) / 2
        )
        summary["clv_positive_rate"] = float(sum(1 for v in clv_vals if v > 0) / len(clv_vals))
        # Stake-weighted CLV — the dollar-weighted edge against the sharp close
        total_stake = sum(b["stake"] for b in bets if b.get("clv") is not None)
        if total_stake > 0:
            summary["clv_weighted"] = float(
                sum(b["clv"] * b["stake"] for b in bets if b.get("clv") is not None) / total_stake
            )
        else:
            summary["clv_weighted"] = None
    else:
        summary["clv_mean"] = None
        summary["clv_median"] = None
        summary["clv_positive_rate"] = None
        summary["clv_weighted"] = None
    return summary


def save_run(summary: dict, *, name: str | None = None) -> int:
    """Persist a run to backtest_runs + backtest_predictions. Returns run_id."""
    from ..config import LEAGUE_BY_CODE
    created_at = datetime.now(timezone.utc).isoformat()
    name = name or f"BT_{summary['league']}_{created_at}"

    with connect() as conn:
        lg = conn.execute("SELECT id FROM leagues WHERE code = ?", (summary["league"],)).fetchone()
        league_id = lg["id"]

        n_pred_total = int(summary.get("n_predictions_1x2", 0) + summary.get("n_predictions_ou25", 0))
        cur = conn.execute(
            """INSERT INTO backtest_runs
               (name, created_at, league_id, seasons, xi, min_training_matches, step_fixtures,
                edge_threshold, kelly_scale, kelly_cap, bankroll_start, n_predictions,
                log_loss_1x2, brier_1x2, rps_1x2, log_loss_ou25, brier_ou25,
                market_log_loss_1x2, market_rps_1x2, market_log_loss_ou25,
                simulated_n_bets, simulated_pnl, simulated_roi, bankroll_final,
                price_source, model_weight, model_source,
                clv_weighted, clv_mean, clv_positive_rate)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?)""",
            (name, created_at, league_id, ",".join(summary["seasons"]), summary["xi"],
             summary["min_training_matches"], summary["step_fixtures"],
             summary["edge_threshold"], summary["kelly_scale"], summary["kelly_cap"],
             summary["bankroll_start"], n_pred_total,
             summary.get("log_loss_1x2"), summary.get("brier_1x2"), summary.get("rps_1x2"),
             summary.get("log_loss_ou25"), summary.get("brier_ou25"),
             summary.get("market_log_loss_1x2"), summary.get("market_rps_1x2"),
             summary.get("market_log_loss_ou25"),
             summary.get("simulated_n_bets"), summary.get("simulated_pnl"),
             summary.get("simulated_roi"), summary.get("bankroll_final"),
             summary.get("price_source"), summary.get("model_weight"),
             summary.get("model_source"),
             summary.get("clv_weighted"), summary.get("clv_mean"),
             summary.get("clv_positive_rate")),
        )
        run_id = cur.lastrowid

        # Persist 1X2 predictions
        bet_lookup = {(b["fixture_id"], b["market"], b["selection"], b["line"]): b for b in summary["bets"]}
        for fx_id, probs, actual, _ in summary["predictions_1x2"]:
            for i, sel in enumerate(OUTCOME_1X2):
                key = (fx_id, "1X2", sel, None)
                bet = bet_lookup.get(key)
                conn.execute(
                    """INSERT INTO backtest_predictions
                       (run_id, fixture_id, market, selection, line, model_prob,
                        closing_prob, closing_price, actual, edge_pct, bet_stake, bet_pnl)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (run_id, fx_id, "1X2", sel, None, float(probs[i]),
                     bet["closing_prob"] if bet else None,
                     bet["closing_price"] if bet else None,
                     1 if actual == i else 0,
                     bet["edge_pct"] if bet else None,
                     bet["stake"] if bet else None,
                     bet["pnl"] if bet else None),
                )
        for fx_id, probs, actual, _ in summary["predictions_ou25"]:
            for i, sel in enumerate(OUTCOME_OU):
                key = (fx_id, "OU", sel, 2.5)
                bet = bet_lookup.get(key)
                conn.execute(
                    """INSERT INTO backtest_predictions
                       (run_id, fixture_id, market, selection, line, model_prob,
                        closing_prob, closing_price, actual, edge_pct, bet_stake, bet_pnl)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (run_id, fx_id, "OU", sel, 2.5, float(probs[i]),
                     bet["closing_prob"] if bet else None,
                     bet["closing_price"] if bet else None,
                     1 if actual == i else 0,
                     bet["edge_pct"] if bet else None,
                     bet["stake"] if bet else None,
                     bet["pnl"] if bet else None),
                )
    return run_id
