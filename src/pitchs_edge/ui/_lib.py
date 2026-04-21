"""Shared UI helpers for the Streamlit app.

Keeps formatters, query glue, and reusable widgets in one place so app.py
stays declarative. Everything here is pure Streamlit + pandas — no model
logic lives in the UI layer.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from ..db import connect

# ---------- session state defaults ----------

DEFAULTS = {
    "skill_level": "Beginner",
    "bankroll": 1000.0,
    "selected_leagues": [],  # empty = all
}


def init_session_state() -> None:
    for k, v in DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ---------- db ----------

def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame(rows)


# ---------- formatting ----------

def fmt_pct(v: float | None, places: int = 2) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v * 100:.{places}f}%"


def fmt_price(v: float | None) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:.2f}"


def fmt_units(v: float | None, places: int = 2) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:+.{places}f}"


def fmt_kickoff(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%a %b %d · %H:%M")
    except (ValueError, TypeError):
        return iso


def selection_label(market: str, selection: str, line: float | None, home: str, away: str) -> str:
    """Turn raw (market, selection, line) into a human string."""
    if market == "1X2":
        return {"home": f"{home} to win", "draw": "Draw", "away": f"{away} to win"}.get(selection, selection)
    if market == "OU":
        lbl = {"over": "Over", "under": "Under"}.get(selection, selection)
        return f"{lbl} {line} goals"
    if market == "AH":
        side = home if selection == "home" else away
        if line is None:
            return f"{side} (AH)"
        sign = "+" if line > 0 else ""
        return f"{side} {sign}{line}"
    if market == "BTTS":
        return "Both teams to score" if selection in ("yes", "over") else "No BTTS"
    return f"{market}/{selection}"


def edge_color(edge: float) -> str:
    """Hex color for an edge value. Green strong, amber soft, grey flat."""
    if edge >= 0.05:
        return "#16a34a"   # green-600
    if edge >= 0.02:
        return "#d97706"   # amber-600
    return "#6b7280"       # grey-500


# ---------- widgets ----------

def help_term(term: str, definition: str) -> str:
    """Inline markdown string with a hover tooltip via HTML `abbr`."""
    return f'<abbr title="{definition}" style="text-decoration: underline dotted; cursor: help;">{term}</abbr>'


def edge_card(row: dict, *, expert: bool = False) -> None:
    """Render a single recommendation as a card. `row` is a dict-like record."""
    home = row.get("home", "?")
    away = row.get("away", "?")
    edge = float(row["edge_pct"])
    color = edge_color(edge)
    title = selection_label(row["market"], row["selection"], row.get("line"), home, away)
    kickoff = fmt_kickoff(row.get("kickoff"))
    league = row.get("league", "")

    with st.container(border=True):
        top = st.columns([3, 1, 1, 1])
        with top[0]:
            st.markdown(f"### {home} vs {away}")
            st.caption(f"{league} · {kickoff}")
        with top[1]:
            st.metric("Edge", fmt_pct(edge), label_visibility="visible")
        with top[2]:
            st.metric("Model", fmt_pct(row["model_prob"]))
        with top[3]:
            st.metric("Price", fmt_price(row["price_taken"]))

        st.markdown(
            f'<div style="padding:8px 12px;background:{color}22;border-left:4px solid {color};'
            f'border-radius:4px;margin:8px 0;">'
            f'<b>{title}</b> @ {row.get("book", "book")} <b>{fmt_price(row["price_taken"])}</b>'
            f'</div>',
            unsafe_allow_html=True,
        )

        if not expert:
            bankroll = st.session_state.get("bankroll", 1000.0)
            stake = float(row.get("stake") or row["kelly_fraction"] * bankroll)
            fair_implied = 1.0 / row["price_taken"]
            why = (
                f"The book prices this as a **{fmt_pct(fair_implied)}** chance, "
                f"our model says **{fmt_pct(row['model_prob'])}**. "
                f"That's a **{fmt_pct(edge)}** edge."
            )
            st.markdown(why)
            st.caption(
                f"Suggested stake: **{stake:.2f}u** "
                f"({fmt_pct(row['kelly_fraction'])} of a {bankroll:.0f}u bankroll, "
                "¼-Kelly, capped at 2%)."
            )
        else:
            detail = st.columns(5)
            detail[0].metric("Kelly f", fmt_pct(row["kelly_fraction"], 3))
            detail[1].metric("Stake", f"{float(row.get('stake') or 0):.2f}u")
            detail[2].metric("Book", row.get("book", "—"))
            detail[3].metric("Market", row["market"])
            detail[4].metric("Result", row.get("result") or "open")


def league_filter_sql(alias: str = "l") -> tuple[str, list]:
    """Return (clause, params) for filtering by the current selected leagues."""
    picked = st.session_state.get("selected_leagues") or []
    if not picked:
        return "", []
    ph = ",".join("?" * len(picked))
    return f" AND {alias}.code IN ({ph})", list(picked)


# ---------- charts ----------

def bankroll_curve_df(starting: float = 1000.0) -> pd.DataFrame:
    """Cumulative P/L curve over settled bets, indexed by placed_at."""
    df = query(
        """SELECT placed_at, COALESCE(pnl, 0.0) AS pnl, result
           FROM bets
           WHERE result IS NOT NULL
           ORDER BY placed_at"""
    )
    if df.empty:
        return pd.DataFrame(columns=["placed_at", "bankroll"])
    df["placed_at"] = pd.to_datetime(df["placed_at"], errors="coerce")
    df = df.dropna(subset=["placed_at"])
    df["bankroll"] = starting + df["pnl"].cumsum()
    return df[["placed_at", "bankroll"]]


def equity_curve_from_backtest(run_id: int) -> pd.DataFrame:
    df = query(
        """SELECT f.kickoff, COALESCE(p.bet_pnl, 0.0) AS pnl
           FROM backtest_predictions p
           JOIN fixtures f ON f.id = p.fixture_id
           WHERE p.run_id = ? AND p.bet_stake IS NOT NULL
           ORDER BY f.kickoff""",
        (run_id,),
    )
    if df.empty:
        return pd.DataFrame(columns=["kickoff", "bankroll"])
    df["kickoff"] = pd.to_datetime(df["kickoff"], errors="coerce")
    df = df.dropna(subset=["kickoff"])
    df["bankroll"] = df["pnl"].cumsum()
    return df[["kickoff", "bankroll"]]


def clv_weekly_df() -> pd.DataFrame:
    df = query(
        """SELECT b.placed_at, c.clv_pct
           FROM bets b JOIN clv_log c ON c.bet_id = b.id
           WHERE c.clv_pct IS NOT NULL
           ORDER BY b.placed_at"""
    )
    if df.empty:
        return pd.DataFrame(columns=["week", "avg_clv"])
    df["placed_at"] = pd.to_datetime(df["placed_at"], errors="coerce")
    df = df.dropna(subset=["placed_at"])
    df["week"] = df["placed_at"].dt.to_period("W").dt.start_time
    agg = df.groupby("week", as_index=False)["clv_pct"].mean().rename(columns={"clv_pct": "avg_clv"})
    return agg
