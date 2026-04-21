"""Odds devigging.

Shin's method (Shin 1993) handles asymmetric overround correctly and is the
standard for sharp operations. Proportional devig is provided for diagnostics
only — do not anchor pricing off it.
"""
from __future__ import annotations

import numpy as np


def implied(prices: np.ndarray | list[float]) -> np.ndarray:
    p = np.asarray(prices, dtype=float)
    if np.any(p <= 1.0):
        raise ValueError("decimal odds must be > 1")
    return 1.0 / p


def proportional(prices: np.ndarray | list[float]) -> np.ndarray:
    q = implied(prices)
    return q / q.sum()


def shin(prices: np.ndarray | list[float], *, max_iter: int = 100, tol: float = 1e-10) -> np.ndarray:
    """Shin's method. Solves for the 'insider proportion' z such that the recovered
    fair probabilities sum to 1.

    p_i(z) = (sqrt(z^2 + 4*(1-z) * q_i^2 / S) - z) / (2*(1-z))
    where q_i = 1 / O_i and S = sum(q_i). Bisect on z in [0, 1).
    """
    q = implied(prices)
    s = float(q.sum())
    n = len(q)
    if s <= 1.0:
        return q / s if s > 0 else np.full(n, 1.0 / n)

    def probs(z: float) -> np.ndarray:
        inside = z * z + 4.0 * (1.0 - z) * q * q / s
        return (np.sqrt(inside) - z) / (2.0 * (1.0 - z))

    lo, hi = 0.0, 0.999
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        if probs(mid).sum() > 1.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return probs(0.5 * (lo + hi))
