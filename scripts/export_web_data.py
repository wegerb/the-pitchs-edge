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


def _latest_backtest_runs(conn) -> list[dict]:
    """Most recent walk-forward backtest per league, for the Track Record panel."""
    rows = conn.execute(
        """SELECT r.id, l.code, l.name AS league_name, r.seasons, r.xi, r.n_predictions,
                  r.log_loss_1x2, r.market_log_loss_1x2,
                  r.rps_1x2, r.market_rps_1x2,
                  r.log_loss_ou25, r.market_log_loss_ou25,
                  r.simulated_n_bets, r.simulated_roi,
                  r.bankroll_start, r.bankroll_final, r.created_at
           FROM backtest_runs r
           JOIN leagues l ON l.id = r.league_id
           WHERE r.id IN (
               SELECT MAX(id) FROM backtest_runs GROUP BY league_id
           )
           ORDER BY l.code"""
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "league_code": r["code"],
            "league_name": r["league_name"],
            "seasons": r["seasons"],
            "xi": r["xi"],
            "n_predictions": r["n_predictions"],
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
    threshold: float, kelly_scale: float, kelly_cap: float,
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

    # Edge computation.
    model_lookup = {
        "1X2": (one_x_two, None),
        "OU":  (ou25,      2.5),
    }
    # Pinnacle-devig reference per market, used to annotate each edge with a
    # sanity check: edges where the model disagrees with the sharp market by a
    # large margin are more likely to be miscalibration than real value.
    market_probs_lookup = {
        "1X2": market_1x2_probs,
        "OU":  market_ou25_probs,
    }
    edges: list[dict] = []
    for market_key, (probs, line) in model_lookup.items():
        key = market_key + (f"_{line}" if line is not None else "")
        if key not in best_odds:
            continue
        for sel, book_info in best_odds[key].items():
            mp = float(probs.get(sel, 0.0))
            price = book_info["price"]
            edge = mp * price - 1.0
            if edge <= threshold or not (0 < mp < 1):
                continue
            ks = kelly(mp, price, scale=kelly_scale, cap=kelly_cap)
            if ks.fraction <= 0:
                continue
            market_probs = market_probs_lookup.get(market_key) or {}
            mkt_p = market_probs.get(sel)
            sharp_delta_pp = (mp - mkt_p) * 100.0 if mkt_p is not None else None
            # Flag edges where the model aggressively disagrees with Pinnacle.
            # A model prob >15pp above the sharp devigged prob is usually
            # miscalibration, not value — worth warning rather than hiding.
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
                "market_prob": mkt_p,
                "sharp_delta_pp": sharp_delta_pp,
                "trust": trust,
                "edge_pct": edge,
                "kelly_fraction": float(ks.fraction),
                "tier": _tier(edge),
                "stars": _stars(edge),
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

    with connect() as conn:
        for league_meta in LEAGUES:
            lg = conn.execute(
                "SELECT id, code, name, country FROM leagues WHERE code = ?",
                (league_meta.code,),
            ).fetchone()
            if not lg:
                notes.append(f"{league_meta.code}: league row missing — run scripts/init_db.py")
                continue
            params, train_rows = fit_league(conn, lg["id"])
            if params is None:
                notes.append(f"{league_meta.code}: insufficient training data ({train_rows} rows) — run scripts/backfill_csv.py")
                continue
            fixtures = _upcoming_for_league(conn, lg["id"], limit=20)
            if not fixtures:
                notes.append(f"{league_meta.code}: no upcoming fixtures — run scripts/fetch_fixtures.py")
                continue
            leagues_seen.append({"code": lg["code"], "name": lg["name"], "country": lg["country"]})
            scored_with_odds = 0
            for fx in fixtures:
                odds = _latest_odds(conn, fx["id"])
                if not odds:
                    continue
                built = _build_fixture(
                    params=params, league=lg, conn=conn, fx=fx, odds_rows=odds,
                    threshold=0.03, kelly_scale=0.25, kelly_cap=0.02,
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
