"""Per-fixture team-strength adjustments applied on top of the fitted model.

The walk-forward model learns one attack/defense rate per team from historical
results. It cannot see:
  - starting XI changes (top striker suspended, star defender injured)
  - mid-week fatigue (Europa League / Champions League flights)
  - tactical shifts (new manager installed days before match)
  - roster sales (winter transfer window — January fire sales)

Closing lines at sharp books DO price these in. That's a structural edge the
market has over us. This module lets us claw some of it back by applying a
per-team, per-fixture adjustment to the model's attack/defense rates in log
space before deriving market probabilities.

Adjustment semantics (additive in log-space):
  new_attack  = fitted_attack + attack_delta
  new_defense = fitted_defense + defense_delta

A delta of −0.10 cuts the rate by ~9.5% (exp(−0.10) ≈ 0.905).
A delta of +0.10 boosts the rate by ~10.5%.

Typical ranges:
  Star striker out:                   attack_delta = −0.10 to −0.18
  Key centre-back out:                defense_delta = −0.05 to −0.10
  Goalkeeper out (backup untested):   defense_delta = −0.10 to −0.15
  European mid-week hangover:         both delta     = −0.03 to −0.08

Data sources (in order of freshness vs cost):
  1. API-Football /fixtures/lineups endpoint (paid tier for historical backfill,
     free tier for current-day lookups) — most authoritative.
  2. Transfermarkt / Whoscored scraping — public, but rate-limited and TOS grey.
  3. Manual entry in the dashboard — lowest latency, highest effort.

For now this module provides the adjustment-apply plumbing; data ingestion
hooks land as paid-tier or scraper access becomes available.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import numpy as np

from ..models.dixon_coles import DixonColesParams, MAX_GOALS
from scipy.stats import poisson


@dataclass
class Adjustment:
    team: str
    attack_delta: float = 0.0
    defense_delta: float = 0.0
    note: str | None = None
    source: str = "manual"


def apply_adjusted_score_matrix(
    params: DixonColesParams,
    home_team: str,
    away_team: str,
    *,
    home_adj: Adjustment | None = None,
    away_adj: Adjustment | None = None,
    max_goals: int = MAX_GOALS,
) -> np.ndarray:
    """Identical to DixonColesParams.score_matrix but applies per-team deltas
    before computing Poisson rates. Adjustments are additive in log-space so they
    behave as multiplicative scalars on the rate (exp(delta)).

    Returns the normalized joint score distribution.
    """
    i = params.index(home_team)
    j = params.index(away_team)

    home_attack_delta = home_adj.attack_delta if home_adj else 0.0
    home_defense_delta = home_adj.defense_delta if home_adj else 0.0
    away_attack_delta = away_adj.attack_delta if away_adj else 0.0
    away_defense_delta = away_adj.defense_delta if away_adj else 0.0

    lam = float(np.exp(
        params.attack[i] + home_attack_delta
        + params.defense[j] + away_defense_delta
        + params.home
    ))
    mu = float(np.exp(
        params.attack[j] + away_attack_delta
        + params.defense[i] + home_defense_delta
    ))

    xs = np.arange(max_goals + 1)
    px = poisson.pmf(xs, lam)
    py = poisson.pmf(xs, mu)
    m = np.outer(px, py)
    m[0, 0] *= 1.0 - lam * mu * params.rho
    m[0, 1] *= 1.0 + lam * params.rho
    m[1, 0] *= 1.0 + mu * params.rho
    m[1, 1] *= 1.0 - params.rho
    m = np.clip(m, 0.0, None)
    s = m.sum()
    if s > 0:
        m = m / s
    return m


def load_adjustments(conn, fixture_id: int) -> dict[int, Adjustment]:
    """Load all adjustments for a fixture from the DB, keyed by team_id.
    If multiple rows exist for the same team_id (e.g. different sources), the
    deltas are summed — so a manual override stacks with an API-sourced one.
    """
    rows = conn.execute(
        """SELECT t.id AS team_id, t.name AS team,
                  a.attack_delta, a.defense_delta, a.note, a.source
             FROM team_adjustments a
             JOIN teams t ON t.id = a.team_id
            WHERE a.fixture_id = ?""",
        (fixture_id,),
    ).fetchall()

    out: dict[int, Adjustment] = {}
    for r in rows:
        if r["team_id"] in out:
            existing = out[r["team_id"]]
            out[r["team_id"]] = Adjustment(
                team=existing.team,
                attack_delta=existing.attack_delta + r["attack_delta"],
                defense_delta=existing.defense_delta + r["defense_delta"],
                note=(existing.note or "") + " + " + (r["note"] or ""),
                source=existing.source + "+" + r["source"],
            )
        else:
            out[r["team_id"]] = Adjustment(
                team=r["team"],
                attack_delta=float(r["attack_delta"]),
                defense_delta=float(r["defense_delta"]),
                note=r["note"],
                source=r["source"],
            )
    return out


def record_adjustment(
    conn,
    *,
    fixture_id: int,
    team_id: int,
    attack_delta: float = 0.0,
    defense_delta: float = 0.0,
    note: str | None = None,
    source: str = "manual",
) -> int:
    """Insert or update an adjustment for a (fixture, team, source) triple."""
    applied_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO team_adjustments
           (fixture_id, team_id, attack_delta, defense_delta, note, source, applied_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (fixture_id, team_id, source) DO UPDATE SET
             attack_delta = excluded.attack_delta,
             defense_delta = excluded.defense_delta,
             note = excluded.note,
             applied_at = excluded.applied_at""",
        (fixture_id, team_id, attack_delta, defense_delta, note, source, applied_at),
    )
    row = conn.execute(
        """SELECT id FROM team_adjustments
            WHERE fixture_id = ? AND team_id = ? AND source = ?""",
        (fixture_id, team_id, source),
    ).fetchone()
    return int(row["id"])
