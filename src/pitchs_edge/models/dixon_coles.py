"""Dixon-Coles bivariate-Poisson goals model.

Implements the 1997 Dixon-Coles formulation:
- Per-team attack (alpha_i) and defense (beta_i); identifiable via sum-to-zero constraint.
- Home advantage (gamma), shared across the league.
- Low-score dependency correction (rho) via tau(x, y) on (0,0), (0,1), (1,0), (1,1).
- Exponential time decay (xi): weight exp(-xi * days_since_match) on each match's log-likelihood.

One fit produces the full score matrix P(home_goals=x, away_goals=y) from which
1X2, O/U, BTTS, AH, correct score, and team totals all derive.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln
from scipy.stats import poisson

MAX_GOALS = 10  # truncation for score-matrix computations


@dataclass
class DixonColesParams:
    teams: list[str]
    attack: np.ndarray     # shape (n_teams,)
    defense: np.ndarray    # shape (n_teams,)
    home: float
    rho: float
    xi: float              # the time-decay used at fit time (frozen on params)
    log_likelihood: float
    as_of: datetime

    def index(self, team: str) -> int:
        return self.teams.index(team)

    def rates(self, home_team: str, away_team: str) -> tuple[float, float]:
        i = self.index(home_team)
        j = self.index(away_team)
        lam = float(np.exp(self.attack[i] + self.defense[j] + self.home))
        mu = float(np.exp(self.attack[j] + self.defense[i]))
        return lam, mu

    def score_matrix(self, home_team: str, away_team: str, max_goals: int = MAX_GOALS) -> np.ndarray:
        lam, mu = self.rates(home_team, away_team)
        xs = np.arange(max_goals + 1)
        px = poisson.pmf(xs, lam)
        py = poisson.pmf(xs, mu)
        m = np.outer(px, py)
        m[0, 0] *= 1.0 - lam * mu * self.rho
        m[0, 1] *= 1.0 + lam * self.rho
        m[1, 0] *= 1.0 + mu * self.rho
        m[1, 1] *= 1.0 - self.rho
        m = np.clip(m, 0.0, None)
        s = m.sum()
        if s > 0:
            m = m / s
        return m


def _unpack(params: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray, float, float]:
    a = np.empty(n)
    b = np.empty(n)
    a[:-1] = params[: n - 1]
    b[:-1] = params[n - 1 : 2 * (n - 1)]
    a[-1] = -a[:-1].sum()
    b[-1] = -b[:-1].sum()
    home, rho = params[-2], params[-1]
    return a, b, home, rho


def _time_weights(match_dates: np.ndarray, as_of: datetime, xi: float) -> np.ndarray:
    as_of_np = np.datetime64(as_of)
    days = (as_of_np - match_dates) / np.timedelta64(1, "D")
    days = np.clip(days.astype(float), 0.0, None)
    return np.exp(-xi * days)


def fit(
    home_teams: Sequence[str],
    away_teams: Sequence[str],
    home_goals: Sequence[int],
    away_goals: Sequence[int],
    match_dates: Sequence[datetime] | np.ndarray,
    *,
    xi: float = 0.0019,   # ~180-day half-life, per Dixon-Coles reference range
    as_of: datetime | None = None,
) -> DixonColesParams:
    teams = sorted(set(list(home_teams) + list(away_teams)))
    n = len(teams)
    t_idx = {t: i for i, t in enumerate(teams)}
    h = np.array([t_idx[t] for t in home_teams])
    a = np.array([t_idx[t] for t in away_teams])
    hg = np.asarray(home_goals, dtype=int)
    ag = np.asarray(away_goals, dtype=int)

    if not isinstance(match_dates, np.ndarray):
        match_dates = np.array([np.datetime64(d) for d in match_dates])
    as_of = as_of or datetime.now(timezone.utc).replace(tzinfo=None)
    weights = _time_weights(match_dates, as_of, xi)

    def nll(packed: np.ndarray) -> float:
        alpha, beta, home, rho = _unpack(packed, n)
        lam = np.exp(alpha[h] + beta[a] + home)
        mu = np.exp(alpha[a] + beta[h])
        ll = poisson.logpmf(hg, lam) + poisson.logpmf(ag, mu)
        tau = np.ones_like(lam)
        m00 = (hg == 0) & (ag == 0)
        m01 = (hg == 0) & (ag == 1)
        m10 = (hg == 1) & (ag == 0)
        m11 = (hg == 1) & (ag == 1)
        tau[m00] = 1.0 - lam[m00] * mu[m00] * rho
        tau[m01] = 1.0 + lam[m01] * rho
        tau[m10] = 1.0 + mu[m10] * rho
        tau[m11] = 1.0 - rho
        if np.any(tau <= 0):
            return 1e10
        ll = ll + np.log(tau)
        return -float(np.sum(weights * ll))

    x0 = np.concatenate([np.zeros(n - 1), np.zeros(n - 1), [0.3, -0.1]])
    bounds = [(-3, 3)] * (n - 1) + [(-3, 3)] * (n - 1) + [(-0.5, 1.5), (-0.25, 0.25)]
    res = minimize(nll, x0, method="L-BFGS-B", bounds=bounds)
    alpha, beta, home, rho = _unpack(res.x, n)
    return DixonColesParams(
        teams=teams,
        attack=alpha,
        defense=beta,
        home=float(home),
        rho=float(rho),
        xi=xi,
        log_likelihood=-float(res.fun),
        as_of=as_of,
    )


def _continuous_poisson_ll(x: np.ndarray, lam: np.ndarray) -> np.ndarray:
    """Poisson log-density generalized to continuous x via gamma function:
        log p(x; λ) = x·log(λ) − λ − lgamma(x + 1)
    Returns element-wise log-likelihood. Safe for x >= 0 and λ > 0.
    """
    lam = np.clip(lam, 1e-10, None)
    return x * np.log(lam) - lam - gammaln(x + 1.0)


def fit_xg(
    home_teams: Sequence[str],
    away_teams: Sequence[str],
    home_xg: Sequence[float],
    away_xg: Sequence[float],
    match_dates: Sequence[datetime] | np.ndarray,
    *,
    xi: float = 0.005,
    as_of: datetime | None = None,
    # For prediction we need rho (low-score DC correction). We don't try to learn
    # rho from xG (nonsense — xG is continuous). Users can pass a rho learned
    # from a goals-fit, or leave it at a league-typical default.
    rho: float = -0.08,
) -> DixonColesParams:
    """xG-based Dixon-Coles fit.

    Fits per-team attack + defense + home advantage so that expected Poisson
    rates λ_home, λ_away match the observed **xG** values (not the observed
    goals). This substantially improves calibration because xG is a
    variance-reduced estimator of the Poisson rate — one xG observation
    per match carries roughly 3x the information of one goal count.

    Uses the continuous Poisson log-likelihood (generalized via gamma) so the
    same scoring rule applies to the xG-is-a-shot-based-estimate-of-λ
    formulation Dixon-Coles-style models typically use.

    The low-score-dependency `rho` is NOT learned here (it's defined on
    integer-goal joint distributions); we accept it as a parameter. Defaults
    to −0.08 which is a well-established league-average value.
    """
    teams = sorted(set(list(home_teams) + list(away_teams)))
    n = len(teams)
    t_idx = {t: i for i, t in enumerate(teams)}
    h = np.array([t_idx[t] for t in home_teams])
    a = np.array([t_idx[t] for t in away_teams])
    hx = np.asarray(home_xg, dtype=float)
    ax = np.asarray(away_xg, dtype=float)

    if not isinstance(match_dates, np.ndarray):
        match_dates = np.array([np.datetime64(d) for d in match_dates])
    as_of = as_of or datetime.now(timezone.utc).replace(tzinfo=None)
    weights = _time_weights(match_dates, as_of, xi)

    def nll(packed: np.ndarray) -> float:
        # packed = [alpha (n-1), beta (n-1), home]
        alpha = np.empty(n)
        beta = np.empty(n)
        alpha[:-1] = packed[: n - 1]
        beta[:-1] = packed[n - 1 : 2 * (n - 1)]
        alpha[-1] = -alpha[:-1].sum()
        beta[-1] = -beta[:-1].sum()
        home = packed[-1]
        lam = np.exp(alpha[h] + beta[a] + home)
        mu = np.exp(alpha[a] + beta[h])
        ll = _continuous_poisson_ll(hx, lam) + _continuous_poisson_ll(ax, mu)
        return -float(np.sum(weights * ll))

    x0 = np.concatenate([np.zeros(n - 1), np.zeros(n - 1), [0.3]])
    bounds = [(-3, 3)] * (n - 1) + [(-3, 3)] * (n - 1) + [(-0.5, 1.5)]
    res = minimize(nll, x0, method="L-BFGS-B", bounds=bounds)

    alpha = np.empty(n)
    beta = np.empty(n)
    alpha[:-1] = res.x[: n - 1]
    beta[:-1] = res.x[n - 1 : 2 * (n - 1)]
    alpha[-1] = -alpha[:-1].sum()
    beta[-1] = -beta[:-1].sum()
    home = float(res.x[-1])

    return DixonColesParams(
        teams=teams,
        attack=alpha,
        defense=beta,
        home=home,
        rho=float(rho),
        xi=xi,
        log_likelihood=-float(res.fun),
        as_of=as_of,
    )


# --- Market derivations from the score matrix ---

def market_1x2(mat: np.ndarray) -> dict[str, float]:
    n = mat.shape[0]
    home = float(sum(mat[x, y] for x in range(n) for y in range(n) if x > y))
    draw = float(sum(mat[x, x] for x in range(n)))
    away = float(sum(mat[x, y] for x in range(n) for y in range(n) if x < y))
    return {"home": home, "draw": draw, "away": away}


def market_over_under(mat: np.ndarray, line: float = 2.5) -> dict[str, float]:
    n = mat.shape[0]
    over = float(sum(mat[x, y] for x in range(n) for y in range(n) if x + y > line))
    under = float(sum(mat[x, y] for x in range(n) for y in range(n) if x + y < line))
    return {"over": over, "under": under}


def market_btts(mat: np.ndarray) -> dict[str, float]:
    n = mat.shape[0]
    yes = float(sum(mat[x, y] for x in range(1, n) for y in range(1, n)))
    return {"yes": yes, "no": 1.0 - yes}


def market_asian_handicap(mat: np.ndarray, line: float) -> dict[str, float]:
    """Fair probabilities for an AH line from the home-team perspective.

    `line` is the handicap applied to the home team (negative = home is favoured).
    Push stake is refunded, so the fair win-probability for a pure bet excludes push.
    Quarter lines should be split by the caller across two adjacent half/integer lines.
    """
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


def market_team_total(mat: np.ndarray, *, team: str = "home", line: float = 1.5) -> dict[str, float]:
    n = mat.shape[0]
    axis = 1 if team == "home" else 0
    marginal = mat.sum(axis=axis)
    over = float(marginal[int(np.ceil(line)):].sum())
    return {"over": over, "under": 1.0 - over}


def market_correct_score(mat: np.ndarray, *, top_k: int = 10) -> list[tuple[int, int, float]]:
    flat: list[tuple[int, int, float]] = []
    n = mat.shape[0]
    for x in range(n):
        for y in range(n):
            flat.append((x, y, float(mat[x, y])))
    flat.sort(key=lambda t: t[2], reverse=True)
    return flat[:top_k]
