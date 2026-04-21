"""Historical results + closing odds from football-data.co.uk.

Primary training source. Each league-season is a single CSV with results and
closing odds from many books. We lean on Pinnacle (PSH/PSD/PSA/P>2.5/P<2.5/PAHH/PAHA/AHh)
as the CLV anchor per the strategy doc; B365 columns are captured as a secondary book.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import httpx
import pandas as pd

from ..config import CSV_CACHE_DIR

BASE_URL = "https://www.football-data.co.uk/mmz4281"

PINNACLE_1X2 = ("PSH", "PSD", "PSA")
PINNACLE_OU25 = ("P>2.5", "P<2.5")
PINNACLE_AH = ("PAHH", "PAHA", "AHh")
B365_1X2 = ("B365H", "B365D", "B365A")


@dataclass
class CsvSpec:
    league_code: str   # e.g. "E0"
    season: str        # e.g. "2324"

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.season}/{self.league_code}.csv"

    @property
    def cache_path(self) -> Path:
        return CSV_CACHE_DIR / f"{self.season}_{self.league_code}.csv"


def download(spec: CsvSpec, *, force: bool = False, client: httpx.Client | None = None) -> Path:
    spec.cache_path.parent.mkdir(parents=True, exist_ok=True)
    if spec.cache_path.exists() and not force:
        return spec.cache_path
    own_client = client is None
    c = client or httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        r = c.get(spec.url)
        r.raise_for_status()
        spec.cache_path.write_bytes(r.content)
    finally:
        if own_client:
            c.close()
    return spec.cache_path


def load(spec: CsvSpec) -> pd.DataFrame:
    path = spec.cache_path if spec.cache_path.exists() else download(spec)
    raw = path.read_bytes()
    # football-data.co.uk files are latin-1 and occasionally have malformed trailing rows
    df = pd.read_csv(io.BytesIO(raw), encoding="latin-1", on_bad_lines="skip")
    df = df.dropna(subset=["HomeTeam", "AwayTeam"])
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["Date"])
    df["_season"] = spec.season
    df["_league_code"] = spec.league_code
    return df
