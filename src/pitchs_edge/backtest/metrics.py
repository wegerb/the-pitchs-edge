"""Evaluation metrics for probabilistic predictions.

Used by the walk-forward backtest. All functions accept predictions as a (n, k)
probability matrix and actuals as a (n,) integer class-index array.

- log_loss: mean -log p(actual).
- brier_score: multi-class Brier = mean sum_k (p_k - y_k)^2.
- rps: Rank Probability Score for ordinal outcomes (classes must be ordered,
  e.g. home/draw/away). Normalized by (k-1) so perfect score = 0, worst = 1.
"""
from __future__ import annotations

import numpy as np


def log_loss(probs: np.ndarray, actuals: np.ndarray, *, eps: float = 1e-15) -> float:
    probs = np.asarray(probs, dtype=float)
    actuals = np.asarray(actuals, dtype=int)
    p = np.clip(probs[np.arange(len(actuals)), actuals], eps, 1.0)
    return float(-np.mean(np.log(p)))


def brier_score(probs: np.ndarray, actuals: np.ndarray) -> float:
    probs = np.asarray(probs, dtype=float)
    actuals = np.asarray(actuals, dtype=int)
    n, _ = probs.shape
    y = np.zeros_like(probs)
    y[np.arange(n), actuals] = 1.0
    return float(np.mean(np.sum((probs - y) ** 2, axis=1)))


def rps(probs: np.ndarray, actuals: np.ndarray) -> float:
    probs = np.asarray(probs, dtype=float)
    actuals = np.asarray(actuals, dtype=int)
    n, k = probs.shape
    if k < 2:
        raise ValueError("RPS requires at least 2 classes")
    cum_probs = np.cumsum(probs, axis=1)
    y = np.zeros_like(probs)
    y[np.arange(n), actuals] = 1.0
    cum_y = np.cumsum(y, axis=1)
    return float(np.mean(np.sum((cum_probs[:, :-1] - cum_y[:, :-1]) ** 2, axis=1)) / (k - 1))
