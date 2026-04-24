"""Corners model — bivariate Poisson on team corner counts.

Corners are high-volume (typical match avg ~10), so the DC low-score tau(x, y)
correction that matters for goals doesn't apply here. We just fit per-team
corner-attack + corner-defense rates with a shared home advantage, using
the same exponential time decay and sum-to-zero identifiability as the
goals model.

Corner bookmaker markets are usually:
  - Total corners O/U line (e.g. 9.5, 10.5, 11.5)
  - Team total corners O/U (home O/U 4.5, away O/U 4.5 etc.)
  - First-half corners O/U (not modelled here — would need half-time corner data)
  - Match corner handicap

This module gives you the score-matrix P(home_corners=i, away_corners=j)
from which all those markets derive.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

MAX_CORNERS = 25  # truncation for score-matrix computations; matches historically never exceed 25 total


@dataclass
class CornersParams:
    teams: list[str]
    attack: np.ndarray     # shape (n_teams,) — corner-generating rate per team (sum-to-zero)
    defense: np.ndarray    # shape (n_teams,) — corner-conceding rate per team (sum-to-zero)
    base: float            # log global mean match corner rate (per team baseline)
    home: float            # log home advantage (multiplicative over base)
    xi: float              # time decay used at fit time
    log_likelihood: float
    as_of: datetime

    def index(self, team: str) -> int:
        return self.teams.index(team)

    def rates(self, home_team: str, away_team: str) -> tuple[float, float]:
        i = self.index(home_team)
        j = self.index(away_team)
        # lam = home corner rate = exp(base + home_adv + attack_i + defense_j)
        # mu  = away corner rate = exp(base + attack_j + defense_i)
        lam = float(np.exp(self.base + self.home + self.attack[i] + self.defense[j]))
        mu = float(np.exp(self.base + self.attack[j] + self.defense[i]))
        return lam, mu

    def score_matrix(self, home_team: str, away_team: str,
                     max_corners: int = MAX_CORNERS) -> np.ndarray:
        lam, mu = self.rates(home_team, away_team)
        xs = np.arange(max_corners + 1)
        px = poisson.pmf(xs, lam)
        py = poisson.pmf(xs, mu)
        return np.outer(px, py)


def _time_weights(match_dates: np.ndarray, as_of: datetime, xi: float) -> np.ndarray:
    as_of_np = np.datetime64(as_of)
    days = (as_of_np - match_dates) / np.timedelta64(1, "D")
    days = np.clip(days.astype(float), 0.0, None)
    return np.exp(-xi * days)


def fit_corners(
    home_teams: Sequence[str],
    away_teams: Sequence[str],
    home_corners: Sequence[int],
    away_corners: Sequence[int],
    match_dates: Sequence[datetime] | np.ndarray,
    *,
    xi: float = 0.0019,
    as_of: datetime | None = None,
) -> CornersParams:
    teams = sorted(set(list(home_teams) + list(away_teams)))
    n = len(teams)
    t_idx = {t: i for i, t in enumerate(teams)}
    h = np.array([t_idx[t] for t in home_teams])
    a = np.array([t_idx[t] for t in away_teams])
    hc = np.asarray(home_corners, dtype=int)
    ac = np.asarray(away_corners, dtype=int)

    if not isinstance(match_dates, np.ndarray):
        match_dates = np.array([np.datetime64(d) for d in match_dates])
    as_of = as_of or datetime.now(timezone.utc).replace(tzinfo=None)
    weights = _time_weights(match_dates, as_of, xi)

    # Starting base = log of observed mean match corner rate (weighted average of home+away)
    mean_rate = float((hc.mean() + ac.mean()) / 2.0)
    init_base = np.log(max(mean_rate, 1.0))

    def nll(packed: np.ndarray) -> float:
        alpha = np.empty(n)
        beta = np.empty(n)
        alpha[:-1] = packed[: n - 1]
        beta[:-1] = packed[n - 1 : 2 * (n - 1)]
        alpha[-1] = -alpha[:-1].sum()
        beta[-1] = -beta[:-1].sum()
        base, home = packed[-2], packed[-1]
        lam = np.exp(base + home + alpha[h] + beta[a])
        mu = np.exp(base + alpha[a] + beta[h])
        ll = poisson.logpmf(hc, lam) + poisson.logpmf(ac, mu)
        return -float(np.sum(weights * ll))

    x0 = np.concatenate([np.zeros(n - 1), np.zeros(n - 1), [init_base, 0.1]])
    bounds = [(-3, 3)] * (n - 1) + [(-3, 3)] * (n - 1) + [(0.0, 4.0), (-0.5, 1.0)]
    res = minimize(nll, x0, method="L-BFGS-B", bounds=bounds)

    alpha = np.empty(n)
    beta = np.empty(n)
    alpha[:-1] = res.x[: n - 1]
    beta[:-1] = res.x[n - 1 : 2 * (n - 1)]
    alpha[-1] = -alpha[:-1].sum()
    beta[-1] = -beta[:-1].sum()
    base = float(res.x[-2])
    home = float(res.x[-1])

    return CornersParams(
        teams=teams,
        attack=alpha,
        defense=beta,
        base=base,
        home=home,
        xi=xi,
        log_likelihood=-float(res.fun),
        as_of=as_of,
    )


# --- Corner market derivations ---

def market_corners_total(mat: np.ndarray, line: float) -> dict[str, float]:
    """Total corners O/U at the given line (e.g. 9.5, 10.5, 11.5)."""
    n = mat.shape[0]
    over = 0.0
    under = 0.0
    push = 0.0
    for x in range(n):
        for y in range(n):
            t = x + y
            p = float(mat[x, y])
            if t > line:
                over += p
            elif t < line:
                under += p
            else:
                push += p
    # For integer lines some probability lands exactly on the line; split / refund
    return {"over": over, "under": under, "push": push}


def market_corners_team_total(
    mat: np.ndarray, *, team: str = "home", line: float = 4.5
) -> dict[str, float]:
    """Team corners O/U for home or away team."""
    n = mat.shape[0]
    axis = 1 if team == "home" else 0
    marginal = mat.sum(axis=axis)
    over = float(marginal[int(np.ceil(line)):].sum())
    return {"over": over, "under": 1.0 - over}


def market_corners_handicap(mat: np.ndarray, line: float) -> dict[str, float]:
    """Asian-handicap-style corners handicap (home perspective)."""
    n = mat.shape[0]
    home_win = 0.0
    push = 0.0
    for x in range(n):
        for y in range(n):
            d = x - y + line
            p = mat[x, y]
            if d > 0:
                home_win += p
            elif d == 0:
                push += p
    if 1.0 - push > 0:
        fair_home = home_win / (1.0 - push)
    else:
        fair_home = 0.5
    return {"home": float(fair_home), "away": float(1.0 - fair_home), "push": float(push)}
