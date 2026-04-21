"""Upcoming fixtures / standings from football-data.org (v4).

Free tier: 10 requests/minute. We self-throttle with a rolling window so callers
don't have to care.
"""
from __future__ import annotations

import time
from collections import deque

import httpx

from ..config import FOOTBALL_DATA_ORG_KEY

BASE_URL = "https://api.football-data.org/v4"
RATE_LIMIT_PER_MINUTE = 10


class Client:
    def __init__(self, api_key: str | None = None, timeout: float = 20.0):
        self.api_key = api_key if api_key is not None else FOOTBALL_DATA_ORG_KEY
        headers = {"X-Auth-Token": self.api_key} if self.api_key else {}
        self._client = httpx.Client(base_url=BASE_URL, headers=headers, timeout=timeout)
        self._history: deque[float] = deque(maxlen=RATE_LIMIT_PER_MINUTE)

    def _throttle(self) -> None:
        now = time.monotonic()
        if len(self._history) == RATE_LIMIT_PER_MINUTE:
            elapsed = now - self._history[0]
            if elapsed < 60.0:
                time.sleep(60.0 - elapsed)
        self._history.append(time.monotonic())

    def get(self, path: str, **params):
        self._throttle()
        r = self._client.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def competition_matches(self, competition_id: int, *, status: str = "SCHEDULED"):
        return self.get(f"/competitions/{competition_id}/matches", status=status)

    def competition_standings(self, competition_id: int):
        return self.get(f"/competitions/{competition_id}/standings")

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
