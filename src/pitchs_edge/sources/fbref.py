"""xG supplement via Understat (wrapped with soccerdata).

Understat is the standard public xG source; `soccerdata` handles the
scraper-evasion issues (TLS fingerprinting, header ordering) that made a
naive httpx client fail. We thin-wrap it so the rest of the codebase sees a
stable interface: `fetch_season_xg(league_code, season) -> DataFrame`.

Coverage: EPL, La Liga, Serie A, Bundesliga, Ligue 1 (2014/15-present).
No Championship xG — E1 stays on goals-only.
"""
from __future__ import annotations

import pandas as pd

UNDERSTAT_LEAGUE_NAMES = {
    "E0":  "ENG-Premier League",
    "SP1": "ESP-La Liga",
    "I1":  "ITA-Serie A",
    "D1":  "GER-Bundesliga",
    "F1":  "FRA-Ligue 1",
}


def _understat_season(season: str) -> str:
    """'2324' -> '2324' (soccerdata accepts this short form)."""
    if len(season) == 4:
        return season
    raise ValueError(f"expected 4-digit season like '2324', got {season!r}")


def fetch_season_xg(league_code: str, season: str, *, force_cache: bool = False) -> pd.DataFrame:
    """Return one row per finished match: date, home, away, home_xg, away_xg,
    home_score, away_score.

    Empty DataFrame if the league isn't covered (Championship) or the season
    predates Understat coverage.
    """
    if league_code not in UNDERSTAT_LEAGUE_NAMES:
        return pd.DataFrame(columns=["date","home","away","home_xg","away_xg","home_score","away_score"])

    # soccerdata is imported lazily so the rest of the app doesn't pay its startup cost.
    import soccerdata as sd

    try:
        us = sd.Understat(
            leagues=UNDERSTAT_LEAGUE_NAMES[league_code],
            seasons=_understat_season(season),
            no_cache=force_cache,
        )
        df = us.read_schedule()
    except Exception:
        return pd.DataFrame(columns=["date","home","away","home_xg","away_xg","home_score","away_score"])

    if df is None or df.empty:
        return pd.DataFrame(columns=["date","home","away","home_xg","away_xg","home_score","away_score"])

    # soccerdata returns a multi-index (league, season, game); flatten.
    df = df.reset_index(drop=False) if isinstance(df.index, pd.MultiIndex) else df.copy()

    # Keep only finished results with xG populated
    df = df.dropna(subset=["home_xg", "away_xg", "home_goals", "away_goals"])

    out = pd.DataFrame({
        "date":       pd.to_datetime(df["date"]),
        "home":       df["home_team"].astype(str).str.strip(),
        "away":       df["away_team"].astype(str).str.strip(),
        "home_xg":    df["home_xg"].astype(float),
        "away_xg":    df["away_xg"].astype(float),
        "home_score": df["home_goals"].astype(int),
        "away_score": df["away_goals"].astype(int),
    })
    return out.sort_values("date").reset_index(drop=True)
