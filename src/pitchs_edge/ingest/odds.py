"""Snapshot live / pre-match odds from The Odds API.

Stores every (fixture, book, market, selection, line, price) row with a capture timestamp.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..config import LEAGUES
from ..db import connect
from ..names import similarity as _team_sim
from ..sources.the_odds_api import Client


def _market_from_odds_api(key: str) -> str:
    return {"h2h": "1X2", "totals": "OU", "spreads": "AH"}.get(key, key)


def _selection_from_outcome(market: str, outcome: dict, home_name: str, away_name: str) -> tuple[str, float | None]:
    name = outcome.get("name", "")
    point = outcome.get("point")
    if market == "1X2":
        if name == home_name:
            return "home", None
        if name == away_name:
            return "away", None
        return "draw", None
    if market == "OU":
        sel = "over" if name.lower().startswith("over") else "under"
        return sel, float(point) if point is not None else None
    if market == "AH":
        sel = "home" if name == home_name else "away"
        return sel, float(point) if point is not None else None
    return name, float(point) if point is not None else None


def snapshot_all() -> dict:
    ts = datetime.now(timezone.utc).isoformat()
    out: dict = {"captured_at": ts, "leagues": {}, "errors": [], "inserted": 0, "quota_remaining": None}
    with Client() as api, connect() as conn:
        for league in LEAGUES:
            try:
                events = api.odds(league.odds_api_key)
            except Exception as e:
                out["errors"].append(f"{league.code}: {e}")
                continue
            out["quota_remaining"] = api.last_quota_remaining

            # Pull this league's scheduled fixtures; we'll score each Odds API event
            # against them and pick the best similarity match (>= threshold).
            fixtures = conn.execute(
                """SELECT f.id, ht.name AS home, at.name AS away, f.kickoff
                   FROM fixtures f
                   JOIN leagues l ON l.id = f.league_id
                   JOIN teams ht ON ht.id = f.home_team_id
                   JOIN teams at ON at.id = f.away_team_id
                   WHERE l.code = ? AND f.status = 'scheduled'""",
                (league.code,),
            ).fetchall()

            matched = 0
            unmatched_samples: list[str] = []
            for ev in events:
                home = ev.get("home_team") or ""
                away = ev.get("away_team") or ""
                ct = ev.get("commence_time") or ""
                ev_date = ct[:10]

                best = (0.0, None)  # (score, fixture_id)
                for f in fixtures:
                    h_sim = _team_sim(home, f["home"])
                    a_sim = _team_sim(away, f["away"])
                    if h_sim < 0.7 or a_sim < 0.7:
                        continue
                    score = h_sim + a_sim
                    # Prefer fixtures on the same day — small boost.
                    if ev_date and f["kickoff"] and f["kickoff"][:10] == ev_date:
                        score += 0.1
                    if score > best[0]:
                        best = (score, f["id"])

                if best[1] is None:
                    if len(unmatched_samples) < 3:
                        unmatched_samples.append(f"{home} vs {away} @ {ev_date}")
                    continue
                fid = best[1]
                matched += 1
                for bookmaker in ev.get("bookmakers", []):
                    book = bookmaker.get("title") or bookmaker.get("key")
                    for market in bookmaker.get("markets", []):
                        mkt = _market_from_odds_api(market.get("key", ""))
                        for outcome in market.get("outcomes", []):
                            sel, line = _selection_from_outcome(mkt, outcome, home, away)
                            price = outcome.get("price")
                            if price is None:
                                continue
                            conn.execute(
                                """INSERT INTO odds_snapshots
                                   (fixture_id, captured_at, book, market, selection, line, price)
                                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                (fid, ts, book, mkt, sel, line, float(price)),
                            )
                            out["inserted"] += 1
            out["leagues"][league.code] = {
                "events": len(events),
                "matched": matched,
                "unmatched_samples": unmatched_samples,
            }
    return out
