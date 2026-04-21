from datetime import datetime, timedelta, timezone

import numpy as np

from pitchs_edge.models import (
    DixonColesParams,
    fit,
    market_1x2,
    market_btts,
    market_over_under,
)


def test_fit_small_synthetic():
    rng = np.random.default_rng(42)
    teams = ["A", "B", "C"]
    strength = {"A": 1.0, "B": 0.0, "C": -0.8}
    rows: list[tuple[str, str, int, int, datetime]] = []
    base = datetime(2024, 1, 1)
    for k in range(180):
        h, a = rng.choice(teams, size=2, replace=False)
        lam = float(np.exp(strength[h] - strength[a] + 0.3))
        mu = float(np.exp(strength[a] - strength[h]))
        rows.append((h, a, int(rng.poisson(lam)), int(rng.poisson(mu)),
                     base + timedelta(days=k)))

    params = fit(
        home_teams=[r[0] for r in rows],
        away_teams=[r[1] for r in rows],
        home_goals=[r[2] for r in rows],
        away_goals=[r[3] for r in rows],
        match_dates=[r[4] for r in rows],
        xi=0.0,
    )
    assert params.attack[params.index("A")] > params.attack[params.index("C")]
    mat = params.score_matrix("A", "C")
    probs = market_1x2(mat)
    assert probs["home"] > probs["away"]


def test_markets_sum_to_one():
    params = DixonColesParams(
        teams=["A", "B"],
        attack=np.array([0.1, -0.1]),
        defense=np.array([-0.05, 0.05]),
        home=0.3,
        rho=-0.1,
        xi=0.0,
        log_likelihood=0.0,
        as_of=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    mat = params.score_matrix("A", "B")
    assert abs(mat.sum() - 1.0) < 1e-9
    one = market_1x2(mat)
    assert abs(one["home"] + one["draw"] + one["away"] - 1.0) < 1e-9
    ou = market_over_under(mat, 2.5)
    assert abs(ou["over"] + ou["under"] - 1.0) < 1e-9
    btts = market_btts(mat)
    assert abs(btts["yes"] + btts["no"] - 1.0) < 1e-9
