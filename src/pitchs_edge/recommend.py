"""Turn a fitted Dixon-Coles model + book odds into actionable recommendations.

For each fixture × market × book triple, we:
  1. Collect book prices for all selections of that market.
  2. Devig with Shin to get fair market probs (diagnostic only — not the bet driver).
  3. Compute model probs from the Dixon-Coles score matrix.
  4. Edge = model_prob * book_price - 1. If > threshold, compute fractional-Kelly stake.

Recommendations are returned as Recommendation objects and (when called from the script)
persisted to the `bets` table. Stake is bankroll × kelly_fraction.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .db import connect
from .edge import kelly, shin
from .models import (
    DixonColesParams,
    fit as fit_dc,
    market_1x2,
    market_asian_handicap,
    market_over_under,
)


@dataclass
class Recommendation:
    fixture_id: int
    market: str
    selection: str
    line: float | None
    book: str
    book_price: float
    model_prob: float
    fair_market_prob: float
    edge_pct: float
    kelly_fraction: float
    kelly_raw: float


def _load_training_rows(conn, league_id: int) -> list[dict]:
    return conn.execute(
        """SELECT f.id, f.kickoff, ht.name AS home, at.name AS away, f.fthg, f.ftag
           FROM fixtures f
           JOIN teams ht ON ht.id = f.home_team_id
           JOIN teams at ON at.id = f.away_team_id
           WHERE f.league_id = ? AND f.fthg IS NOT NULL AND f.ftag IS NOT NULL
           ORDER BY f.kickoff""",
        (league_id,),
    ).fetchall()


def fit_league(conn, league_id: int, *, xi: float = 0.01) -> tuple[DixonColesParams | None, int]:
    rows = _load_training_rows(conn, league_id)
    if len(rows) < 50:
        return None, len(rows)
    params = fit_dc(
        home_teams=[r["home"] for r in rows],
        away_teams=[r["away"] for r in rows],
        home_goals=[r["fthg"] for r in rows],
        away_goals=[r["ftag"] for r in rows],
        match_dates=[datetime.fromisoformat(r["kickoff"]) for r in rows],
        xi=xi,
    )
    return params, len(rows)


def save_model_run(conn, name: str, league_id: int, params: DixonColesParams, train_rows: int) -> int:
    payload = {
        "teams": params.teams,
        "attack": params.attack.tolist(),
        "defense": params.defense.tolist(),
        "home": params.home,
        "rho": params.rho,
        "xi": params.xi,
    }
    cur = conn.execute(
        """INSERT INTO model_runs (name, league_id, as_of, params_json, train_rows, log_likelihood)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name, league_id, params.as_of.isoformat(), json.dumps(payload), train_rows, params.log_likelihood),
    )
    return cur.lastrowid


def _group_odds(rows: list[dict]) -> dict[tuple[str, str, float | None], dict[str, float]]:
    """Group (book, market, line) → {selection: price}."""
    groups: dict[tuple[str, str, float | None], dict[str, float]] = {}
    for r in rows:
        key = (r["book"], r["market"], r["line"])
        groups.setdefault(key, {})[r["selection"]] = float(r["price"])
    return groups


def _model_probs_for_market(
    params: DixonColesParams, home: str, away: str, market: str, line: float | None
) -> dict[str, float] | None:
    try:
        mat = params.score_matrix(home, away)
    except ValueError:
        return None
    if market == "1X2":
        return market_1x2(mat)
    if market == "OU":
        if line is None:
            return None
        return market_over_under(mat, float(line))
    if market == "AH":
        if line is None:
            return None
        ah = market_asian_handicap(mat, float(line))
        return {"home": ah["home"], "away": ah["away"]}
    return None


def recommend_for_fixture(
    params: DixonColesParams,
    *,
    fixture_id: int,
    home: str,
    away: str,
    odds_rows: list[dict],
    threshold: float = 0.02,
    kelly_scale: float = 0.25,
    kelly_cap: float = 0.02,
) -> list[Recommendation]:
    recs: list[Recommendation] = []
    selections_for = {"1X2": ("home", "draw", "away"), "OU": ("over", "under"), "AH": ("home", "away")}

    for (book, market, line), prices in _group_odds(odds_rows).items():
        if market not in selections_for:
            continue
        sels = selections_for[market]
        if not all(s in prices for s in sels):
            continue
        price_arr = [prices[s] for s in sels]
        try:
            fair = shin(price_arr)
        except ValueError:
            continue
        model_probs = _model_probs_for_market(params, home, away, market, line)
        if model_probs is None:
            continue
        for i, sel in enumerate(sels):
            mp = float(model_probs[sel])
            price = price_arr[i]
            e = mp * price - 1.0
            if e <= threshold:
                continue
            if not 0.0 < mp < 1.0:
                continue
            ks = kelly(mp, price, scale=kelly_scale, cap=kelly_cap)
            if ks.fraction <= 0:
                continue
            recs.append(Recommendation(
                fixture_id=fixture_id,
                market=market,
                selection=sel,
                line=line,
                book=book,
                book_price=price,
                model_prob=mp,
                fair_market_prob=float(fair[i]),
                edge_pct=float(e),
                kelly_fraction=float(ks.fraction),
                kelly_raw=float(ks.raw_kelly),
            ))
    return recs


def _latest_snapshots(conn, fixture_id: int) -> list[dict]:
    return conn.execute(
        """SELECT book, market, selection, line, price, captured_at
           FROM odds_snapshots
           WHERE fixture_id = ? AND id IN (
               SELECT MAX(id) FROM odds_snapshots
               WHERE fixture_id = ?
               GROUP BY book, market, selection, line
           )""",
        (fixture_id, fixture_id),
    ).fetchall()


def _upcoming_fixtures(conn, league_id: int) -> list[dict]:
    return conn.execute(
        """SELECT f.id, f.kickoff, ht.name AS home, at.name AS away
           FROM fixtures f
           JOIN teams ht ON ht.id = f.home_team_id
           JOIN teams at ON at.id = f.away_team_id
           WHERE f.league_id = ? AND f.status = 'scheduled'
             AND datetime(f.kickoff) > datetime('now')
           ORDER BY f.kickoff""",
        (league_id,),
    ).fetchall()


def persist_bet(conn, rec: Recommendation, *, bankroll: float, placed_at: str) -> int:
    stake = rec.kelly_fraction * bankroll
    cur = conn.execute(
        """INSERT INTO bets
           (fixture_id, market, selection, line, stake, price_taken, book,
            model_prob, edge_pct, kelly_fraction, placed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (rec.fixture_id, rec.market, rec.selection, rec.line, stake, rec.book_price,
         rec.book, rec.model_prob, rec.edge_pct, rec.kelly_fraction, placed_at),
    )
    return cur.lastrowid


def run(
    *,
    leagues: list[str] | None = None,
    xi: float = 0.01,
    threshold: float = 0.02,
    kelly_scale: float = 0.25,
    kelly_cap: float = 0.02,
    bankroll: float = 1000.0,
    dry_run: bool = False,
) -> dict:
    """Fit each league, score scheduled fixtures against latest odds, persist bets above threshold."""
    from .config import LEAGUES, LEAGUE_BY_CODE

    lg_codes = leagues or [l.code for l in LEAGUES]
    placed_at = datetime.now(timezone.utc).isoformat()
    out: dict = {"placed_at": placed_at, "dry_run": dry_run, "leagues": {}, "total_bets": 0, "errors": []}

    with connect() as conn:
        for code in lg_codes:
            if code not in LEAGUE_BY_CODE:
                out["errors"].append(f"{code}: unknown league")
                continue
            lg_row = conn.execute("SELECT id, name FROM leagues WHERE code = ?", (code,)).fetchone()
            if not lg_row:
                out["errors"].append(f"{code}: not in DB — run init_db + backfill first")
                continue
            league_id = lg_row["id"]
            params, train_rows = fit_league(conn, league_id, xi=xi)
            if params is None:
                out["leagues"][code] = {"error": f"insufficient training data ({train_rows} rows)"}
                continue
            model_run_name = f"DC_{code}_{placed_at}"
            run_id = save_model_run(conn, model_run_name, league_id, params, train_rows)

            league_stats = {
                "train_rows": train_rows,
                "log_likelihood": params.log_likelihood,
                "model_run_id": run_id,
                "fixtures_scored": 0,
                "fixtures_without_odds": 0,
                "bets": 0,
                "recommendations": [],
            }

            fixtures = _upcoming_fixtures(conn, league_id)
            for fx in fixtures:
                snapshots = _latest_snapshots(conn, fx["id"])
                if not snapshots:
                    league_stats["fixtures_without_odds"] += 1
                    continue
                league_stats["fixtures_scored"] += 1
                recs = recommend_for_fixture(
                    params,
                    fixture_id=fx["id"],
                    home=fx["home"],
                    away=fx["away"],
                    odds_rows=snapshots,
                    threshold=threshold,
                    kelly_scale=kelly_scale,
                    kelly_cap=kelly_cap,
                )
                for rec in recs:
                    if not dry_run:
                        persist_bet(conn, rec, bankroll=bankroll, placed_at=placed_at)
                    league_stats["bets"] += 1
                    league_stats["recommendations"].append(asdict(rec))
            out["leagues"][code] = league_stats
            out["total_bets"] += league_stats["bets"]
    return out
