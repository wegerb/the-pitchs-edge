"""Per-league production configuration — the params we actually bet with.

These are NOT defaults. They are the specific (xi, edge_threshold, model_weight,
min_training_matches, model_source) tuples that survived our walk-forward sweeps
AND the out-of-sample holdout test. Anything marked `validated=True` produced a
positive ROI and a positive CLV on data the tuner never saw. Unvalidated leagues
are shown on the dashboard for information but edges are surfaced at a stricter
threshold and flagged so the user knows they're speculative.

Single source of truth: the edge sheet exporter, any production backtest run,
and the UI "Track Record" panel all read from here. Changing a number here is
the one-line knob that moves the whole stack onto a new config.

How a league graduates from `validated=False` to `True`:
  1. Run the sweep scripts and pick the config with positive ROI *and* positive
     CLV on the tune window (2122 + 2223).
  2. Run `scripts/backtest_holdout.py` with that config against 2324 + 2425.
  3. If the holdout ROI is positive AND the holdout CLV is positive, the edge
     survived unseen data — mark `validated=True` and paste the numbers into
     `holdout_roi` / `holdout_clv` below for the Track Record panel.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TunedConfig:
    # Model knobs
    xi: float = 0.0019                 # time-decay; 0.0019 ≈ 365-day half-life
    model_source: str = "goals"        # "goals" or "xg"
    min_training_matches: int = 500    # warm-up before we'll trust the fit

    # Edge knobs
    edge_threshold: float = 0.05       # minimum model*price - 1 to surface a play
    model_weight: float = 0.5          # p_bet = w*model + (1-w)*Pinnacle_close
    kelly_scale: float = 0.25          # fractional Kelly
    kelly_cap: float = 0.02            # max 2% of bankroll per bet

    # Provenance / UI
    validated: bool = False            # confirmed on out-of-sample holdout?
    notes: str = ""
    # Last observed out-of-sample numbers (for the Track Record panel).
    # Only meaningful when validated=True.
    holdout_roi: float | None = None
    holdout_clv: float | None = None
    holdout_n_bets: int | None = None


# -----------------------------------------------------------------------------
# Per-league configuration. Numbers sourced from:
#   - scripts/backtest_blend_sweep.py   (model_weight)
#   - scripts/backtest_tune.py          (xi × edge_threshold)
#   - scripts/backtest_min_train_sweep.py (min_training_matches)
#   - scripts/backtest_holdout.py        (the honest out-of-sample gate)
# -----------------------------------------------------------------------------

TUNED_BY_LEAGUE: dict[str, TunedConfig] = {
    # --- Premier League: xG model beat goals model; blend leans on Pinnacle.
    "E0": TunedConfig(
        xi=0.0019,
        model_source="xg",
        min_training_matches=700,
        edge_threshold=0.04,
        model_weight=0.7,
        validated=True,
        notes="xG + w=0.7; Understat xG signal beats market on EPL.",
        holdout_roi=0.1077,
        holdout_clv=0.0414,
    ),

    # --- Championship: goals model, market barely looks at E1 so our edge is
    # structural. Higher threshold + heavy market tilt (w=0.3).
    "E1": TunedConfig(
        xi=0.005,
        model_source="goals",
        min_training_matches=200,
        edge_threshold=0.06,
        model_weight=0.3,
        validated=True,
        notes="Goals + w=0.3; market inefficiency on Championship confirmed on 2324/2425 holdout.",
        holdout_roi=0.0228,
        holdout_clv=0.0402,
    ),

    # --- Top-5 foreign leagues: sweeps did not produce a config that beat the
    # market out-of-sample. We still show the model for information, but keep
    # the edge threshold conservative and flag plays as unvalidated.
    "SP1": TunedConfig(
        xi=0.0019, model_source="goals", min_training_matches=500,
        edge_threshold=0.06, model_weight=0.5, validated=False,
        notes="No confirmed edge vs closing line — informational only.",
    ),
    "I1": TunedConfig(
        xi=0.0019, model_source="goals", min_training_matches=500,
        edge_threshold=0.06, model_weight=0.5, validated=False,
        notes="No confirmed edge vs closing line — informational only.",
    ),
    "D1": TunedConfig(
        xi=0.0019, model_source="goals", min_training_matches=500,
        edge_threshold=0.06, model_weight=0.5, validated=False,
        notes="No confirmed edge vs closing line — informational only.",
    ),
    "F1": TunedConfig(
        xi=0.0019, model_source="goals", min_training_matches=700,
        edge_threshold=0.06, model_weight=0.7, validated=False,
        notes="Flat ROI at min_train=700; close to break-even but not confirmed.",
    ),
}


def get_tuned(league_code: str) -> TunedConfig:
    """Return the production config for a league, or a sensible default."""
    return TUNED_BY_LEAGUE.get(league_code, TunedConfig())


def validated_leagues() -> list[str]:
    """League codes that cleared the out-of-sample gate."""
    return [code for code, cfg in TUNED_BY_LEAGUE.items() if cfg.validated]
