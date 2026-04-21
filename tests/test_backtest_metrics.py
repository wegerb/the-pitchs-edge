import numpy as np
import pytest

from pitchs_edge.backtest import brier_score, log_loss, rps


def test_log_loss_perfect_prediction_is_zero():
    probs = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    actuals = np.array([0, 1])
    assert log_loss(probs, actuals) < 1e-10


def test_log_loss_uniform_three_way():
    probs = np.full((4, 3), 1.0 / 3.0)
    actuals = np.array([0, 1, 2, 0])
    assert log_loss(probs, actuals) == pytest.approx(np.log(3), abs=1e-9)


def test_brier_perfect_is_zero():
    probs = np.array([[1.0, 0.0], [0.0, 1.0]])
    actuals = np.array([0, 1])
    assert brier_score(probs, actuals) == pytest.approx(0.0)


def test_brier_uniform_two_way():
    probs = np.array([[0.5, 0.5], [0.5, 0.5]])
    actuals = np.array([0, 1])
    # sum_k (0.5)^2 + (0.5)^2 = 0.5 per row when y=[1,0] or [0,1]
    # Actually: (0.5-1)^2 + (0.5-0)^2 = 0.5
    assert brier_score(probs, actuals) == pytest.approx(0.5)


def test_rps_perfect_is_zero():
    probs = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    actuals = np.array([0, 2])
    assert rps(probs, actuals) == pytest.approx(0.0)


def test_rps_ordinal_penalizes_distant_misses_more():
    # Predicts 100% home, actual is away (2 classes apart)
    probs_far = np.array([[1.0, 0.0, 0.0]])
    # Predicts 100% draw, actual is away (1 class apart)
    probs_near = np.array([[0.0, 1.0, 0.0]])
    actuals = np.array([2])
    assert rps(probs_far, actuals) > rps(probs_near, actuals)


def test_rps_worst_case_is_one():
    # Predicts 100% home, actual is away (max distance in 3-class ordinal)
    probs = np.array([[1.0, 0.0, 0.0]])
    actuals = np.array([2])
    assert rps(probs, actuals) == pytest.approx(1.0)


def test_rps_two_class_reduces_sensibly():
    probs = np.array([[0.5, 0.5]])
    actuals = np.array([0])
    # cum_probs[:,:-1] = [0.5], cum_y[:,:-1] = [1.0] → (0.5)^2 / (2-1) = 0.25
    assert rps(probs, actuals) == pytest.approx(0.25)
