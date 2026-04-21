"""Fractional Kelly staking."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class KellyStake:
    fraction: float         # fraction of bankroll to stake (already scaled + capped)
    raw_kelly: float        # full-Kelly fraction before scaling (transparency)
    expected_growth: float  # E[log(1 + f * return)]


def kelly(
    prob: float,
    decimal_odds: float,
    *,
    scale: float = 0.25,
    cap: float = 0.02,
) -> KellyStake:
    """Fractional Kelly stake as a fraction of bankroll.

    Defaults (scale=¼, cap=2%) match the non-negotiables in the strategy doc.
    """
    if decimal_odds <= 1.0:
        raise ValueError("decimal odds must be > 1")
    if not 0.0 < prob < 1.0:
        raise ValueError("prob must be in (0, 1)")
    b = decimal_odds - 1.0
    q = 1.0 - prob
    raw = (prob * b - q) / b
    if raw <= 0:
        return KellyStake(fraction=0.0, raw_kelly=raw, expected_growth=0.0)
    f = min(raw * scale, cap)
    g = prob * math.log(1.0 + f * b) + q * math.log(1.0 - f)
    return KellyStake(fraction=f, raw_kelly=raw, expected_growth=g)


def edge_pct(model_prob: float, decimal_odds: float) -> float:
    """Edge expressed as EV per unit stake: p * O - 1."""
    return model_prob * decimal_odds - 1.0
