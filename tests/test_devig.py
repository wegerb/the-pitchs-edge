import numpy as np

from pitchs_edge.edge import proportional, shin


def test_shin_sums_to_one():
    p = shin([2.10, 3.40, 3.70])
    assert abs(p.sum() - 1.0) < 1e-6
    assert np.all(p > 0)


def test_shin_reduces_to_proportional_in_symmetric_two_way():
    p_shin = shin([1.95, 1.95])
    p_prop = proportional([1.95, 1.95])
    assert np.allclose(p_shin, p_prop, atol=1e-4)


def test_shin_moves_favorite_toward_higher_prob():
    prices = [1.50, 4.50, 7.00]
    p = shin(prices)
    # favorite's fair prob should be below raw-implied (since overround inflates it)
    raw = np.array([1.0 / o for o in prices])
    assert p[0] < raw[0]
