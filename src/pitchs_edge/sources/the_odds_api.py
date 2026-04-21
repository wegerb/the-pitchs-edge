"""Live / pre-match odds from the-odds-api.com."""
from __future__ import annotations

import httpx

from ..config import ODDS_API_KEY

BASE_URL = "https://api.the-odds-api.com/v4"


class Client:
    def __init__(self, api_key: str | None = None, timeout: float = 20.0):
        self.api_key = api_key if api_key is not None else ODDS_API_KEY
        if not self.api_key:
            raise RuntimeError("ODDS_API_KEY not set")
        self._client = httpx.Client(base_url=BASE_URL, timeout=timeout)
        self.last_quota_remaining: int | None = None
        self.last_quota_used: int | None = None

    def get(self, path: str, **params):
        params = {"apiKey": self.api_key, **params}
        r = self._client.get(path, params=params)
        r.raise_for_status()
        # Track remaining quota so callers can throttle intelligently.
        try:
            self.last_quota_remaining = int(r.headers.get("x-requests-remaining", -1))
            self.last_quota_used = int(r.headers.get("x-requests-used", -1))
        except (TypeError, ValueError):
            pass
        return r.json()

    def sports(self):
        return self.get("/sports")

    def odds(
        self,
        sport_key: str,
        *,
        regions: str = "uk,eu",
        markets: str = "h2h,totals,spreads",
        odds_format: str = "decimal",
    ):
        return self.get(
            f"/sports/{sport_key}/odds",
            regions=regions,
            markets=markets,
            oddsFormat=odds_format,
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
