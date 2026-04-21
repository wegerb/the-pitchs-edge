"""Closing Line Value tracking.

CLV_pct = price_taken / closing_price - 1 for back bets on decimal odds.
Positive CLV over large samples is the true scoreboard — positive win rate on
negative CLV is noise, not skill.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..db import connect


def record_bet(
    *,
    fixture_id: int,
    market: str,
    selection: str,
    line: float | None,
    stake: float,
    price_taken: float,
    book: str,
    model_prob: float,
    edge_pct: float,
    kelly_fraction: float,
) -> int:
    placed_at = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO bets
               (fixture_id, market, selection, line, stake, price_taken, book,
                model_prob, edge_pct, kelly_fraction, placed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fixture_id, market, selection, line, stake, price_taken, book,
             model_prob, edge_pct, kelly_fraction, placed_at),
        )
        return cur.lastrowid


def compute_clv(bet_id: int, closing_price: float) -> float:
    computed_at = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        r = conn.execute("SELECT price_taken FROM bets WHERE id = ?", (bet_id,)).fetchone()
        if not r:
            raise KeyError(f"bet {bet_id} not found")
        price_taken = r["price_taken"]
        closing_implied = 1.0 / closing_price
        clv_pct = (price_taken / closing_price) - 1.0
        conn.execute(
            """INSERT OR REPLACE INTO clv_log
               (bet_id, closing_price, closing_implied_prob, clv_pct, computed_at)
               VALUES (?, ?, ?, ?, ?)""",
            (bet_id, closing_price, closing_implied, clv_pct, computed_at),
        )
    return clv_pct


def close_bet(bet_id: int, *, result: str, pnl: float) -> None:
    """Mark a bet settled. `result` ∈ {win, loss, push, half_win, half_loss}."""
    with connect() as conn:
        conn.execute(
            "UPDATE bets SET result = ?, pnl = ? WHERE id = ?",
            (result, pnl, bet_id),
        )
