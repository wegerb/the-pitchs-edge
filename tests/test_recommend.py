"""Tests for the recommend module (pure edge/Kelly logic, no DB)."""
from datetime import datetime, timezone

import pytest

from pitchs_edge.models import fit as fit_dc
from pitchs_edge.recommend import recommend_for_fixture


def _toy_params():
    """Fit DC on a tiny synthetic league so score_matrix works for A/B/C/D."""
    home = ["A", "B", "C", "D", "A", "B", "C", "D", "A", "C", "B", "D"]
    away = ["B", "C", "D", "A", "C", "D", "A", "B", "D", "B", "A", "C"]
    hg = [2, 1, 1, 0, 2, 1, 0, 1, 3, 1, 0, 2]
    ag = [0, 1, 2, 1, 1, 0, 1, 2, 1, 1, 2, 0]
    dates = [datetime(2025, 1, i + 1, tzinfo=timezone.utc).replace(tzinfo=None)
             for i in range(len(home))]
    return fit_dc(
        home_teams=home, away_teams=away,
        home_goals=hg, away_goals=ag,
        match_dates=dates, xi=0.0,
    )


def test_no_recommendations_when_threshold_is_unreachable():
    params = _toy_params()
    odds_rows = [
        {"book": "Pinnacle", "market": "1X2", "selection": "home", "line": None, "price": 2.40},
        {"book": "Pinnacle", "market": "1X2", "selection": "draw", "line": None, "price": 3.80},
        {"book": "Pinnacle", "market": "1X2", "selection": "away", "line": None, "price": 2.70},
    ]
    # Impossible bar: edge = model_prob*price - 1 can never exceed 99.
    recs = recommend_for_fixture(
        params, fixture_id=1, home="A", away="B",
        odds_rows=odds_rows, threshold=99.0,
    )
    assert recs == []


def test_recommendation_triggered_when_price_much_higher_than_model():
    params = _toy_params()
    # Give 'home' a ridiculous overlay (20.0) so edge > threshold regardless of model.
    odds_rows = [
        {"book": "Pinnacle", "market": "1X2", "selection": "home", "line": None, "price": 20.0},
        {"book": "Pinnacle", "market": "1X2", "selection": "draw", "line": None, "price": 3.50},
        {"book": "Pinnacle", "market": "1X2", "selection": "away", "line": None, "price": 3.00},
    ]
    recs = recommend_for_fixture(
        params, fixture_id=1, home="A", away="B",
        odds_rows=odds_rows, threshold=0.02,
    )
    assert any(r.selection == "home" and r.edge_pct > 0.02 for r in recs)
    for r in recs:
        assert 0 < r.kelly_fraction <= 0.02  # fractional-Kelly cap honored
        assert r.model_prob > 0 and r.model_prob < 1
        assert r.book == "Pinnacle"


def test_incomplete_market_is_skipped():
    params = _toy_params()
    # Missing 'away' price — market is incomplete, should not recommend.
    odds_rows = [
        {"book": "Pinnacle", "market": "1X2", "selection": "home", "line": None, "price": 20.0},
        {"book": "Pinnacle", "market": "1X2", "selection": "draw", "line": None, "price": 3.50},
    ]
    recs = recommend_for_fixture(
        params, fixture_id=1, home="A", away="B",
        odds_rows=odds_rows, threshold=0.02,
    )
    assert recs == []


def test_unknown_team_yields_no_recommendations():
    params = _toy_params()
    odds_rows = [
        {"book": "Pinnacle", "market": "1X2", "selection": "home", "line": None, "price": 20.0},
        {"book": "Pinnacle", "market": "1X2", "selection": "draw", "line": None, "price": 3.50},
        {"book": "Pinnacle", "market": "1X2", "selection": "away", "line": None, "price": 3.00},
    ]
    recs = recommend_for_fixture(
        params, fixture_id=1, home="A", away="ZZZ_NOT_IN_TRAINING",
        odds_rows=odds_rows, threshold=0.02,
    )
    assert recs == []


def test_ou_market_requires_line():
    params = _toy_params()
    # OU with no line is malformed — should skip.
    odds_rows = [
        {"book": "Pinnacle", "market": "OU", "selection": "over", "line": None, "price": 20.0},
        {"book": "Pinnacle", "market": "OU", "selection": "under", "line": None, "price": 1.05},
    ]
    recs = recommend_for_fixture(
        params, fixture_id=1, home="A", away="B",
        odds_rows=odds_rows, threshold=0.02,
    )
    assert recs == []
