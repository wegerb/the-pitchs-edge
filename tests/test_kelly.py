import pytest

from pitchs_edge.edge import edge_pct, kelly


def test_no_edge_returns_zero():
    assert kelly(0.5, 2.0).fraction == 0.0


def test_positive_edge_positive_stake():
    s = kelly(0.55, 2.0, scale=0.25, cap=0.10)
    assert s.fraction > 0
    assert s.raw_kelly > 0


def test_cap_respected():
    s = kelly(0.99, 100.0, scale=1.0, cap=0.02)
    assert s.fraction == 0.02


def test_fractional_scale():
    full = kelly(0.60, 2.0, scale=1.0, cap=1.0).fraction
    quarter = kelly(0.60, 2.0, scale=0.25, cap=1.0).fraction
    assert abs(quarter - 0.25 * full) < 1e-9


def test_edge_pct_basic():
    assert edge_pct(0.5, 2.1) == pytest.approx(0.05)
    assert edge_pct(0.5, 2.0) == pytest.approx(0.0)
