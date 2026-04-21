"""Pull upcoming fixtures from football-data.org into the fixtures table."""
from __future__ import annotations

from ..config import LEAGUES
from ..db import connect
from ..sources.footballdata_org import Client


def _ensure_team(conn, league_id: int, name: str) -> int:
    row = conn.execute(
        "SELECT id FROM teams WHERE league_id = ? AND name = ?", (league_id, name)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO teams (league_id, name) VALUES (?, ?)", (league_id, name)
    )
    return cur.lastrowid


def fetch_upcoming() -> dict:
    out: dict = {"leagues": {}, "inserted": 0, "errors": []}
    with Client() as c, connect() as conn:
        for league in LEAGUES:
            lg_row = conn.execute(
                "SELECT id FROM leagues WHERE code = ?", (league.code,)
            ).fetchone()
            if not lg_row:
                out["errors"].append(f"{league.code}: run init_db + backfill first")
                continue
            league_id = lg_row["id"]
            try:
                payload = c.competition_matches(league.fd_org_id, status="SCHEDULED")
            except Exception as e:
                out["errors"].append(f"{league.code}: {e}")
                continue
            matches = payload.get("matches", [])
            out["leagues"][league.code] = len(matches)
            for m in matches:
                home_name = m["homeTeam"]["name"]
                away_name = m["awayTeam"]["name"]
                home_id = _ensure_team(conn, league_id, home_name)
                away_id = _ensure_team(conn, league_id, away_name)
                kickoff = m["utcDate"]
                season = str(m.get("season", {}).get("startDate", "")[:4])
                conn.execute(
                    """INSERT OR IGNORE INTO fixtures
                       (league_id, season, kickoff, home_team_id, away_team_id, status, external_ids)
                       VALUES (?, ?, ?, ?, ?, 'scheduled', ?)""",
                    (league_id, season, kickoff, home_id, away_id, f'fd_org:{m.get("id")}'),
                )
                out["inserted"] += 1
    return out
