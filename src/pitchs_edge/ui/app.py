"""The Pitch's Edge — Streamlit consumer UI.

Designed for progressive disclosure: a Beginner sees cards with plain-language
reasoning; an Expert sees raw numbers. Sidebar sets bankroll, skill level, and
league filter globally — all tabs react.

Tabs: Dashboard · Edges · Fixtures · Bankroll · CLV · Backtest · Learn
"""
from __future__ import annotations

from datetime import datetime, timezone

import altair as alt
import pandas as pd
import streamlit as st

from pitchs_edge.config import LEAGUES
from pitchs_edge.edge import shin
from pitchs_edge.models import (
    market_1x2,
    market_btts,
    market_over_under,
    market_team_total,
)
from pitchs_edge.recommend import fit_league
from pitchs_edge.ui._lib import (
    bankroll_curve_df,
    clv_weekly_df,
    edge_card,
    equity_curve_from_backtest,
    fmt_kickoff,
    fmt_pct,
    fmt_price,
    fmt_units,
    help_term,
    init_session_state,
    league_filter_sql,
    query,
    selection_label,
)

st.set_page_config(
    page_title="The Pitch's Edge",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_session_state()


# ═══════════════════════════════════════════════════════════════════════════
# SIDEBAR — global state
# ═══════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("## ⚽ The Pitch's Edge")
    st.caption("by Wegs Benedict")

    st.session_state.skill_level = st.radio(
        "Experience level",
        ["Beginner", "Expert"],
        index=["Beginner", "Expert"].index(st.session_state.skill_level),
        horizontal=True,
        help="Beginner shows plain-language reasoning. Expert shows raw numbers and parameters.",
    )

    st.session_state.bankroll = st.number_input(
        "Bankroll (units)",
        min_value=10.0,
        max_value=1_000_000.0,
        value=float(st.session_state.bankroll),
        step=50.0,
        help="Your total betting bankroll. Stakes are sized as a fraction of this number.",
    )

    league_opts = query("SELECT code, name FROM leagues ORDER BY name")
    if not league_opts.empty:
        code_to_name = dict(zip(league_opts["code"], league_opts["name"]))
        st.session_state.selected_leagues = st.multiselect(
            "Leagues",
            options=league_opts["code"].tolist(),
            default=st.session_state.selected_leagues,
            format_func=lambda c: code_to_name.get(c, c),
            help="Filter the app to these leagues. Empty = all leagues.",
        )

    st.divider()

    with st.expander("Data freshness", expanded=False):
        counts = query(
            """SELECT
                (SELECT MAX(placed_at) FROM bets) AS last_rec,
                (SELECT MAX(captured_at) FROM odds_snapshots) AS last_odds,
                (SELECT MAX(kickoff) FROM fixtures WHERE status = 'scheduled') AS latest_fixture,
                (SELECT MAX(created_at) FROM backtest_runs) AS last_backtest
            """
        )
        if not counts.empty:
            r = counts.iloc[0]
            st.write(f"**Last recs:** {fmt_kickoff(r['last_rec'])}")
            st.write(f"**Last odds:** {fmt_kickoff(r['last_odds'])}")
            st.write(f"**Last fixture:** {fmt_kickoff(r['latest_fixture'])}")
            st.write(f"**Last backtest:** {fmt_kickoff(r['last_backtest'])}")

    st.divider()
    st.caption(
        "Scripts to refresh data:\n\n"
        "• `fetch_fixtures.py` — schedule\n"
        "• `fetch_odds.py` — live odds\n"
        "• `fit_and_recommend.py` — run model\n"
        "• `backtest.py --league E0 --seasons 2324`"
    )


# ═══════════════════════════════════════════════════════════════════════════
# MAIN HEADER
# ═══════════════════════════════════════════════════════════════════════════

st.title("The Pitch's Edge")
st.caption(
    "Dixon-Coles · Shin devig · fractional Kelly · CLV-tracked — "
    "soccer betting edge detection for EPL, Championship, La Liga, Serie A, "
    "Bundesliga, and Ligue 1."
)

tabs = st.tabs(["🏠 Dashboard", "🎯 Edges", "📅 Fixtures", "💰 Bankroll",
                "📈 CLV", "🔬 Backtest", "📚 Learn"])
tab_home, tab_edges, tab_fixtures, tab_bank, tab_clv, tab_bt, tab_learn = tabs


# ═══════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════

with tab_home:
    st.subheader("At a glance")

    lg_clause, lg_params = league_filter_sql()
    active = query(
        f"""SELECT COUNT(*) AS n, COALESCE(SUM(b.stake), 0) AS exposure,
                   COALESCE(AVG(b.edge_pct), 0) AS avg_edge
            FROM bets b
            JOIN fixtures f ON f.id = b.fixture_id
            JOIN leagues l ON l.id = f.league_id
            WHERE b.result IS NULL{lg_clause}""",
        tuple(lg_params),
    )
    settled = query(
        f"""SELECT COALESCE(SUM(b.pnl), 0) AS pnl, COUNT(*) AS n
            FROM bets b
            JOIN fixtures f ON f.id = b.fixture_id
            JOIN leagues l ON l.id = f.league_id
            WHERE b.result IS NOT NULL{lg_clause}""",
        tuple(lg_params),
    )
    clv_row = query(
        f"""SELECT AVG(c.clv_pct) AS avg_clv, COUNT(*) AS n
            FROM bets b
            JOIN clv_log c ON c.bet_id = b.id
            JOIN fixtures f ON f.id = b.fixture_id
            JOIN leagues l ON l.id = f.league_id
            WHERE 1=1{lg_clause}""",
        tuple(lg_params),
    )

    m = st.columns(4)
    with m[0]:
        st.metric(
            "Open edges",
            int(active.iloc[0]["n"]) if not active.empty else 0,
            help="Unsettled recommendations from the latest model run in the leagues you picked.",
        )
    with m[1]:
        exp = float(active.iloc[0]["exposure"]) if not active.empty else 0.0
        st.metric("Current exposure", f"{exp:.2f}u")
    with m[2]:
        pnl = float(settled.iloc[0]["pnl"]) if not settled.empty else 0.0
        st.metric("Settled P/L", fmt_units(pnl))
    with m[3]:
        avg_clv = float(clv_row.iloc[0]["avg_clv"]) if not clv_row.empty and clv_row.iloc[0]["avg_clv"] is not None else None
        st.metric("Average CLV", fmt_pct(avg_clv), help="Average closing-line-value across all tracked bets.")

    st.markdown("#### Top open plays")
    top_recs = query(
        f"""SELECT b.*, f.kickoff, l.code AS league,
                   ht.name AS home, at.name AS away
            FROM bets b
            JOIN fixtures f ON f.id = b.fixture_id
            JOIN leagues l ON l.id = f.league_id
            JOIN teams ht ON ht.id = f.home_team_id
            JOIN teams at ON at.id = f.away_team_id
            WHERE b.result IS NULL{lg_clause}
            ORDER BY b.edge_pct DESC
            LIMIT 3""",
        tuple(lg_params),
    )
    if top_recs.empty:
        st.info(
            "No open plays. Run `python scripts/fit_and_recommend.py` to "
            "generate recommendations from the latest fixtures and odds."
        )
    else:
        expert = st.session_state.skill_level == "Expert"
        for _, rec in top_recs.iterrows():
            edge_card(rec.to_dict(), expert=expert)

    st.markdown("#### Next fixtures")
    next_fx = query(
        f"""SELECT f.kickoff, l.code AS league, ht.name AS home, at.name AS away,
                   (SELECT COUNT(*) FROM bets bb WHERE bb.fixture_id = f.id AND bb.result IS NULL) AS n_edges
            FROM fixtures f
            JOIN leagues l ON l.id = f.league_id
            JOIN teams ht ON ht.id = f.home_team_id
            JOIN teams at ON at.id = f.away_team_id
            WHERE f.status = 'scheduled' AND datetime(f.kickoff) > datetime('now'){lg_clause}
            ORDER BY f.kickoff
            LIMIT 10""",
        tuple(lg_params),
    )
    if next_fx.empty:
        st.write("No scheduled fixtures in the DB yet.")
    else:
        next_fx["kickoff"] = next_fx["kickoff"].map(fmt_kickoff)
        next_fx = next_fx.rename(columns={
            "kickoff": "Kickoff", "league": "League",
            "home": "Home", "away": "Away", "n_edges": "Edges",
        })
        st.dataframe(next_fx, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════
# EDGES — the main event
# ═══════════════════════════════════════════════════════════════════════════

with tab_edges:
    st.subheader("Recommended bets")
    expert = st.session_state.skill_level == "Expert"

    latest = query("SELECT MAX(placed_at) AS last FROM bets")
    last = latest.iloc[0]["last"] if not latest.empty else None
    if last:
        st.caption(f"Latest model run: {fmt_kickoff(last)}")
    else:
        st.warning(
            "No recommendations yet. Run `python scripts/fit_and_recommend.py` "
            "after fetching fixtures and odds."
        )

    with st.expander("🔧 Filters", expanded=True):
        c = st.columns(4)
        with c[0]:
            min_edge_pct = st.slider("Minimum edge %", 0.0, 20.0, 2.0, step=0.5)
            min_edge = min_edge_pct / 100.0
        with c[1]:
            markets = st.multiselect(
                "Markets",
                options=["1X2", "OU", "AH", "BTTS"],
                default=[],
                help="Empty = all markets.",
            )
        with c[2]:
            only_open = st.checkbox("Unsettled only", value=True)
        with c[3]:
            books = query("SELECT DISTINCT book FROM bets ORDER BY book")
            book_pick = st.multiselect(
                "Books",
                options=books["book"].tolist() if not books.empty else [],
                default=[],
            )

    lg_clause, lg_params = league_filter_sql()
    where = ["b.edge_pct >= ?"]
    params: list = [min_edge]
    if only_open:
        where.append("b.result IS NULL")
    if markets:
        ph = ",".join("?" * len(markets))
        where.append(f"b.market IN ({ph})")
        params.extend(markets)
    if book_pick:
        ph = ",".join("?" * len(book_pick))
        where.append(f"b.book IN ({ph})")
        params.extend(book_pick)
    if lg_params:
        params.extend(lg_params)
    where_sql = " AND ".join(where) + lg_clause

    df = query(
        f"""SELECT b.*, f.kickoff, l.code AS league,
                   ht.name AS home, at.name AS away
            FROM bets b
            JOIN fixtures f ON f.id = b.fixture_id
            JOIN leagues l ON l.id = f.league_id
            JOIN teams ht ON ht.id = f.home_team_id
            JOIN teams at ON at.id = f.away_team_id
            WHERE {where_sql}
            ORDER BY b.edge_pct DESC, b.placed_at DESC
            LIMIT 500""",
        tuple(params),
    )

    if df.empty:
        st.info("No bets match these filters.")
    else:
        top = st.columns(4)
        top[0].metric("Plays", len(df))
        top[1].metric("Avg edge", fmt_pct(df["edge_pct"].mean()))
        top[2].metric("Total stake", f"{df['stake'].sum():.2f}u")
        pnl_settled = df["pnl"].dropna()
        top[3].metric(
            "Settled P/L",
            fmt_units(pnl_settled.sum()) if not pnl_settled.empty else "—",
        )

        if expert:
            show = df[[
                "kickoff", "league", "home", "away", "market", "selection", "line",
                "book", "price_taken", "model_prob", "edge_pct", "kelly_fraction",
                "stake", "result", "pnl",
            ]].copy()
            show["kickoff"] = show["kickoff"].map(fmt_kickoff)
            show["model_prob"] = show["model_prob"].map(lambda v: f"{v:.3f}")
            show["edge_pct"] = show["edge_pct"].map(fmt_pct)
            show["kelly_fraction"] = show["kelly_fraction"].map(lambda v: f"{v:.4f}")
            st.dataframe(show, use_container_width=True, hide_index=True)
        else:
            for _, rec in df.head(25).iterrows():
                edge_card(rec.to_dict(), expert=False)
            if len(df) > 25:
                st.caption(f"Showing top 25 of {len(df)}. Switch to Expert view for a full table.")


# ═══════════════════════════════════════════════════════════════════════════
# FIXTURES — per-fixture model dashboard
# ═══════════════════════════════════════════════════════════════════════════

@st.cache_resource(ttl=3600, show_spinner="Fitting Dixon-Coles…")
def _cached_fit(league_code: str, xi: float = 0.01):
    with __import__("pitchs_edge.db", fromlist=["connect"]).connect() as conn:
        lg = conn.execute("SELECT id FROM leagues WHERE code = ?", (league_code,)).fetchone()
        if not lg:
            return None, 0
        params, n_train = fit_league(conn, lg["id"], xi=xi)
    return params, n_train


with tab_fixtures:
    st.subheader("Model forecasts by fixture")
    st.caption(
        "Pick a fixture — the model fits Dixon-Coles on the full league history, "
        "then derives every market from the same score matrix."
    )

    lg_clause, lg_params = league_filter_sql()
    scheduled = query(
        f"""SELECT f.id, f.kickoff, l.code AS league, l.name AS league_name,
                   ht.name AS home, at.name AS away
            FROM fixtures f
            JOIN leagues l ON l.id = f.league_id
            JOIN teams ht ON ht.id = f.home_team_id
            JOIN teams at ON at.id = f.away_team_id
            WHERE f.status = 'scheduled' AND datetime(f.kickoff) > datetime('now'){lg_clause}
            ORDER BY f.kickoff
            LIMIT 200""",
        tuple(lg_params),
    )

    if scheduled.empty:
        st.info(
            "No scheduled fixtures. Run `python scripts/fetch_fixtures.py` first."
        )
    else:
        options = {
            int(r["id"]): f"{fmt_kickoff(r['kickoff'])} · {r['league']} · {r['home']} vs {r['away']}"
            for _, r in scheduled.iterrows()
        }
        pick = st.selectbox("Fixture", list(options.keys()), format_func=lambda i: options[i])
        fx = scheduled[scheduled["id"] == pick].iloc[0]

        params, n_train = _cached_fit(fx["league"])
        if params is None:
            st.error(f"No fitted model for {fx['league_name']} — need at least 50 finished fixtures.")
        else:
            try:
                mat = params.score_matrix(fx["home"], fx["away"])
            except ValueError:
                st.error(
                    f"Can't score {fx['home']} vs {fx['away']} — at least one team isn't in the "
                    f"training set (likely just promoted)."
                )
                mat = None

            if mat is not None:
                st.caption(f"Fitted on {n_train} rows · log-likelihood {params.log_likelihood:.1f}")

                m1 = market_1x2(mat)
                ou25 = market_over_under(mat, 2.5)
                btts = market_btts(mat)
                tth = market_team_total(mat, team="home", line=1.5)
                tta = market_team_total(mat, team="away", line=1.5)

                odds = query(
                    """SELECT book, market, selection, line, price
                       FROM odds_snapshots
                       WHERE fixture_id = ? AND id IN (
                           SELECT MAX(id) FROM odds_snapshots
                           WHERE fixture_id = ?
                           GROUP BY book, market, selection, line
                       )""",
                    (int(fx["id"]), int(fx["id"])),
                )

                # --- Build one consolidated table ---
                rows = [
                    ("1X2", "home", None, m1["home"], f"{fx['home']} to win"),
                    ("1X2", "draw", None, m1["draw"], "Draw"),
                    ("1X2", "away", None, m1["away"], f"{fx['away']} to win"),
                    ("OU",  "over",  2.5, ou25["over"],  "Over 2.5 goals"),
                    ("OU",  "under", 2.5, ou25["under"], "Under 2.5 goals"),
                    ("BTTS", "yes", None, btts["yes"], "Both teams to score"),
                    ("BTTS", "no",  None, btts["no"],  "No BTTS"),
                    ("TT-H", "over", 1.5, tth["over"],  f"{fx['home']} over 1.5"),
                    ("TT-A", "over", 1.5, tta["over"],  f"{fx['away']} over 1.5"),
                ]
                data = []
                for market, sel, line, prob, label in rows:
                    best = None
                    for _, o in odds.iterrows():
                        if o["market"] == market and o["selection"] == sel:
                            if line is None or (o["line"] is not None and abs(float(o["line"]) - line) < 1e-6):
                                if best is None or float(o["price"]) > best["price"]:
                                    best = {"book": o["book"], "price": float(o["price"])}
                    price = best["price"] if best else None
                    book = best["book"] if best else None
                    edge = prob * price - 1.0 if price else None
                    data.append({
                        "Selection": label,
                        "Model %": fmt_pct(prob),
                        "Best book": book or "—",
                        "Best price": fmt_price(price),
                        "Implied %": fmt_pct(1 / price) if price else "—",
                        "Edge": fmt_pct(edge) if edge is not None else "—",
                        "_edge_num": edge if edge is not None else -99.0,
                    })
                tbl = pd.DataFrame(data).sort_values("_edge_num", ascending=False).drop(columns=["_edge_num"])

                st.markdown("##### Headline numbers")
                hm = st.columns(3)
                hm[0].metric(f"{fx['home']} win", fmt_pct(m1["home"]))
                hm[1].metric("Draw", fmt_pct(m1["draw"]))
                hm[2].metric(f"{fx['away']} win", fmt_pct(m1["away"]))

                hm2 = st.columns(3)
                hm2[0].metric("Over 2.5", fmt_pct(ou25["over"]))
                hm2[1].metric("BTTS yes", fmt_pct(btts["yes"]))
                hm2[2].metric("Fair total", f"{(params.rates(fx['home'], fx['away'])[0] + params.rates(fx['home'], fx['away'])[1]):.2f} goals")

                st.markdown("##### All markets — model vs best available book")
                st.dataframe(tbl, use_container_width=True, hide_index=True)

                with st.expander("Score matrix heatmap (model probs by scoreline)"):
                    max_show = 6
                    sub = mat[:max_show, :max_show]
                    heat_df = pd.DataFrame(
                        sub, index=[f"{i}" for i in range(max_show)],
                        columns=[f"{i}" for i in range(max_show)],
                    )
                    heat_df.index.name = f"{fx['home']} goals"
                    heat_long = heat_df.reset_index().melt(
                        id_vars=heat_df.index.name, var_name=f"{fx['away']} goals", value_name="prob"
                    )
                    chart = alt.Chart(heat_long).mark_rect().encode(
                        x=alt.X(f"{fx['away']} goals:O"),
                        y=alt.Y(f"{fx['home']} goals:O"),
                        color=alt.Color("prob:Q", scale=alt.Scale(scheme="greens")),
                        tooltip=["prob:Q"],
                    ).properties(height=300)
                    st.altair_chart(chart, use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════
# BANKROLL
# ═══════════════════════════════════════════════════════════════════════════

with tab_bank:
    st.subheader("Bankroll management")

    lg_clause, lg_params = league_filter_sql()
    open_bets = query(
        f"""SELECT b.id, f.kickoff, l.code AS league, ht.name AS home, at.name AS away,
                   b.market, b.selection, b.line, b.book, b.price_taken,
                   b.stake, b.edge_pct, b.placed_at
            FROM bets b
            JOIN fixtures f ON f.id = b.fixture_id
            JOIN leagues l ON l.id = f.league_id
            JOIN teams ht ON ht.id = f.home_team_id
            JOIN teams at ON at.id = f.away_team_id
            WHERE b.result IS NULL{lg_clause}
            ORDER BY f.kickoff""",
        tuple(lg_params),
    )

    m = st.columns(4)
    m[0].metric("Bankroll", f"{st.session_state.bankroll:.2f}u")
    m[1].metric("Open bets", len(open_bets))
    m[2].metric("Exposure", f"{open_bets['stake'].sum():.2f}u" if not open_bets.empty else "0.00u")
    m[3].metric(
        "Exposure %",
        fmt_pct(open_bets["stake"].sum() / st.session_state.bankroll)
        if not open_bets.empty and st.session_state.bankroll > 0 else "0%",
    )

    st.markdown("##### Equity curve (settled bets)")
    curve = bankroll_curve_df(st.session_state.bankroll)
    if curve.empty:
        st.caption("No settled bets yet — the curve appears once bets are graded.")
    else:
        chart = alt.Chart(curve).mark_line(point=True).encode(
            x=alt.X("placed_at:T", title="Placed at"),
            y=alt.Y("bankroll:Q", title="Bankroll (units)",
                    scale=alt.Scale(zero=False)),
            tooltip=["placed_at:T", alt.Tooltip("bankroll:Q", format=".2f")],
        ).properties(height=280)
        st.altair_chart(chart, use_container_width=True)

    st.markdown("##### Open bets")
    if open_bets.empty:
        st.caption("Nothing currently unsettled.")
    else:
        show = open_bets.copy()
        show["kickoff"] = show["kickoff"].map(fmt_kickoff)
        show["edge_pct"] = show["edge_pct"].map(fmt_pct)
        show["line"] = show["line"].fillna("—")
        st.dataframe(
            show[["kickoff", "league", "home", "away", "market", "selection", "line",
                  "book", "price_taken", "stake", "edge_pct"]],
            use_container_width=True, hide_index=True,
        )

        with st.expander("Settle a bet manually", expanded=False):
            opts = {int(r["id"]): f"#{r['id']} {r['home']} vs {r['away']} — {r['market']}/{r['selection']}"
                    for _, r in open_bets.iterrows()}
            pick = st.selectbox("Bet", list(opts.keys()), format_func=lambda i: opts[i])
            result = st.radio("Result", ["won", "lost", "push"], horizontal=True)
            if st.button("Mark settled", type="primary"):
                row = open_bets[open_bets["id"] == pick].iloc[0]
                stake = float(row["stake"])
                price = float(row["price_taken"])
                pnl = stake * (price - 1.0) if result == "won" else (-stake if result == "lost" else 0.0)
                from pitchs_edge.db import connect
                with connect() as conn:
                    conn.execute(
                        "UPDATE bets SET result = ?, pnl = ? WHERE id = ?",
                        (result, pnl, pick),
                    )
                st.success(f"Bet #{pick} marked {result}, P/L {pnl:+.2f}u.")
                st.rerun()


# ═══════════════════════════════════════════════════════════════════════════
# CLV
# ═══════════════════════════════════════════════════════════════════════════

with tab_clv:
    st.subheader("Closing Line Value")
    st.caption(
        "CLV measures whether the price you took was better than the closing price — "
        "the single strongest predictor of long-term edge."
    )

    df = query(
        """SELECT b.id, b.placed_at, b.market, b.selection, b.price_taken, b.book,
                  c.closing_price, c.clv_pct, b.result, b.pnl
           FROM bets b LEFT JOIN clv_log c ON c.bet_id = b.id
           ORDER BY b.placed_at DESC
           LIMIT 500"""
    )
    if df.empty:
        st.info("No tracked bets yet. CLV appears once bets are recorded and closing prices are logged.")
    else:
        m = st.columns(4)
        m[0].metric("Bets tracked", len(df))
        tracked_clv = df["clv_pct"].dropna()
        m[1].metric("Avg CLV", fmt_pct(tracked_clv.mean()) if not tracked_clv.empty else "—")
        m[2].metric("Positive CLV rate", fmt_pct((tracked_clv > 0).mean()) if not tracked_clv.empty else "—")
        settled = df["pnl"].dropna()
        m[3].metric("Settled P/L", fmt_units(settled.sum()) if not settled.empty else "—")

        weekly = clv_weekly_df()
        if not weekly.empty:
            st.markdown("##### Weekly average CLV")
            chart = alt.Chart(weekly).mark_bar().encode(
                x=alt.X("week:T", title="Week"),
                y=alt.Y("avg_clv:Q", title="Avg CLV", axis=alt.Axis(format="%")),
                color=alt.condition("datum.avg_clv > 0", alt.value("#16a34a"), alt.value("#dc2626")),
                tooltip=["week:T", alt.Tooltip("avg_clv:Q", format=".2%")],
            ).properties(height=260)
            st.altair_chart(chart, use_container_width=True)

        st.markdown("##### All tracked bets")
        show = df.copy()
        show["placed_at"] = show["placed_at"].map(fmt_kickoff)
        show["clv_pct"] = show["clv_pct"].map(lambda v: fmt_pct(v) if pd.notna(v) else "—")
        st.dataframe(show, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════
# BACKTEST
# ═══════════════════════════════════════════════════════════════════════════

with tab_bt:
    st.subheader("Walk-forward backtests")
    st.caption(
        "Each run refits Dixon-Coles on prior fixtures, predicts the next window, "
        "and compares against Pinnacle closing. The simulated P/L bets at-close, "
        "so CLV is 0 by construction — this is the 'beat-the-close' test."
    )

    runs = query(
        """SELECT r.id, r.name, r.created_at, l.code AS league, r.seasons,
                  r.xi, r.step_fixtures, r.edge_threshold,
                  r.n_predictions, r.log_loss_1x2, r.market_log_loss_1x2,
                  r.rps_1x2, r.market_rps_1x2,
                  r.log_loss_ou25, r.market_log_loss_ou25,
                  r.simulated_n_bets, r.simulated_pnl, r.simulated_roi,
                  r.bankroll_start, r.bankroll_final
           FROM backtest_runs r
           JOIN leagues l ON l.id = r.league_id
           ORDER BY r.created_at DESC"""
    )
    if runs.empty:
        st.info(
            "No backtest runs yet. Try "
            "`python scripts/backtest.py --league E0 --seasons 2324`."
        )
    else:
        options = {
            int(r["id"]): f"[{r['id']}] {r['league']} {r['seasons']} — {fmt_kickoff(r['created_at'])}"
            for _, r in runs.iterrows()
        }
        pick = st.selectbox("Run", list(options.keys()), format_func=lambda i: options[i])
        row = runs[runs["id"] == pick].iloc[0]

        m = st.columns(4)
        ll_model, ll_mkt = row["log_loss_1x2"], row["market_log_loss_1x2"]
        delta = (ll_model - ll_mkt) if (ll_model is not None and ll_mkt is not None) else None
        m[0].metric(
            "1X2 log-loss (model)", f"{ll_model:.4f}" if ll_model is not None else "—",
            delta=f"{delta:+.4f} vs market" if delta is not None else None,
            delta_color="inverse",
        )
        rps_d = (row["rps_1x2"] - row["market_rps_1x2"]) if (row["rps_1x2"] is not None and row["market_rps_1x2"] is not None) else None
        m[1].metric(
            "1X2 RPS (model)", f"{row['rps_1x2']:.4f}" if row["rps_1x2"] is not None else "—",
            delta=f"{rps_d:+.4f} vs market" if rps_d is not None else None,
            delta_color="inverse",
        )
        ou_d = (row["log_loss_ou25"] - row["market_log_loss_ou25"]) if (row["log_loss_ou25"] is not None and row["market_log_loss_ou25"] is not None) else None
        m[2].metric(
            "OU2.5 log-loss (model)", f"{row['log_loss_ou25']:.4f}" if row["log_loss_ou25"] is not None else "—",
            delta=f"{ou_d:+.4f} vs market" if ou_d is not None else None,
            delta_color="inverse",
        )
        roi = row["simulated_roi"]
        m[3].metric(
            "Simulated ROI", fmt_pct(roi) if roi is not None else "—",
            delta=f"P/L {row['simulated_pnl']:+.2f}u" if row["simulated_pnl"] is not None else None,
        )
        st.caption(
            f"Bankroll: {row['bankroll_start']:.0f} → {row['bankroll_final']:.2f} "
            f"over {row['simulated_n_bets']} bets at closing prices."
        )

        eq = equity_curve_from_backtest(int(pick))
        if not eq.empty:
            st.markdown("##### Simulated equity curve (at closing)")
            eq["bankroll"] = row["bankroll_start"] + eq["bankroll"]
            chart = alt.Chart(eq).mark_line().encode(
                x=alt.X("kickoff:T", title="Kickoff"),
                y=alt.Y("bankroll:Q", title="Bankroll", scale=alt.Scale(zero=False)),
                tooltip=["kickoff:T", alt.Tooltip("bankroll:Q", format=".2f")],
            ).properties(height=280)
            st.altair_chart(chart, use_container_width=True)

        with st.expander("All predictions from this run"):
            preds = query(
                """SELECT f.kickoff, ht.name AS home, at.name AS away,
                          p.market, p.selection, p.line,
                          p.model_prob, p.closing_prob, p.closing_price,
                          p.actual, p.edge_pct, p.bet_stake, p.bet_pnl
                   FROM backtest_predictions p
                   JOIN fixtures f ON f.id = p.fixture_id
                   JOIN teams ht ON ht.id = f.home_team_id
                   JOIN teams at ON at.id = f.away_team_id
                   WHERE p.run_id = ?
                   ORDER BY f.kickoff, p.market, p.selection""",
                (int(pick),),
            )
            only_bets = st.checkbox("Only rows where a bet was placed", value=False)
            if only_bets:
                preds = preds[preds["bet_stake"].notna()]
            preds["kickoff"] = preds["kickoff"].map(fmt_kickoff)
            st.dataframe(preds, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════
# LEARN
# ═══════════════════════════════════════════════════════════════════════════

with tab_learn:
    st.subheader("How to use this tool")

    st.markdown(
        """
### If you're new to this
1. **Bankroll first.** Set a number in the sidebar you could afford to lose entirely.
   Every stake is a fraction of it — no exceptions.
2. **Read the Edges tab.** Each card shows a suggested bet, the model's probability,
   the book's implied probability, and the gap (the edge).
3. **Only bets with ≥2% edge are recommended.** That's the minimum to overcome the
   bookmaker's vig.
4. **Stake exactly what's suggested.** The number comes from ¼-Kelly, capped at 2%
   of bankroll — small enough that a bad streak won't sink you, big enough that an
   edge compounds.
5. **Always try to beat the closing price.** If you can't get an equal-or-better
   number than the market close, the edge probably wasn't real.

### The golden rule
**Closing Line Value beats win rate.** A bettor who beats closing by 2% on average
will win long-term, even on an unlucky week. A bettor who wins 55% of bets but
loses to the close is just getting lucky. This app tracks CLV, not wins.
"""
    )

    st.divider()
    st.subheader("Glossary")

    terms = [
        ("Edge", "The gap between what we think will happen and what the book is pricing. "
                 "If our model says Team A wins 50% of the time and the book is paying 2.20 "
                 "(implied 45.5%), the edge is 50% × 2.20 − 1 = 10%."),
        ("Kelly stake / fractional Kelly", "The mathematically optimal fraction of your bankroll "
                 "to wager given an edge. Full Kelly is volatile — we use a quarter of it (¼-Kelly) "
                 "and cap at 2% of bankroll to survive bad runs."),
        ("CLV (Closing Line Value)", "How much better your price was than the final market price at "
                 "kickoff. +2% CLV means the market eventually agreed your bet was a bit undervalued. "
                 "This is the single most reliable measure of skill."),
        ("Shin's method", "A way to strip the bookmaker's margin (the vig) out of displayed odds to "
                 "recover the fair probabilities. Unlike naive devigging, it handles favorite-longshot "
                 "bias properly."),
        ("Dixon-Coles", "The soccer scoring model this app uses. It assigns every team an attack and "
                 "defense strength, adds home advantage, fixes an under-prediction of low scores, and "
                 "decays old matches so last month matters more than last year."),
        ("xi (time decay)", "How fast the model forgets old matches. 0.0019 ≈ a 180-day half-life: "
                 "a game played 6 months ago carries half the weight of a game played today."),
        ("Log-loss", "The standard probabilistic-forecast score. Lower is better. Punishes confident "
                 "wrong answers much harder than hedged wrong answers — exactly what you want."),
        ("Brier score", "Another calibration score; also lower-is-better. Similar in spirit to log-loss "
                 "but less punishing on confident misses."),
        ("RPS (Rank Probability Score)", "Ordinal version of Brier — for ordered outcomes like "
                 "home/draw/away, it penalizes predicting home when away happens more than predicting "
                 "draw when away happens. Exactly right for 1X2 markets."),
        ("Walk-forward backtest", "Train on past, test on future, walk one window forward, repeat. "
                 "Unlike random k-fold it can't cheat by peeking at future games — the only honest way "
                 "to test a time-series model."),
        ("1X2", "Home/Draw/Away — the simplest soccer market."),
        ("O/U (Over/Under)", "Total goals over or under a line. O/U 2.5 is the most liquid line."),
        ("BTTS", "Both Teams To Score — yes/no."),
        ("AH (Asian Handicap)", "Handicapped win market with half-point lines that remove the draw. "
                 "Usually the sharpest market with the lowest vig."),
        ("Team totals", "Goals scored by one specific team over/under a line."),
    ]
    for term, definition in terms:
        with st.expander(term):
            st.write(definition)

    st.divider()
    st.subheader("Strategy guardrails")
    st.markdown(
        """
- **Never exceed 2% of bankroll on a single bet.** Even with a huge perceived edge.
  The edge is never as big as you think.
- **Don't chase.** A losing streak is normal for a ¼-Kelly bettor. Expect 5-to-10
  bet drawdowns even with a real edge.
- **Track CLV religiously.** If your average CLV is not positive over 100+ bets,
  the edge isn't there — recalibrate or stop.
- **Respect market moves.** If the price you want is suddenly available at every
  soft book but not at Pinnacle or Betfair, something's wrong. Don't bet.
- **Diversify across markets and fixtures.** Correlated bets (same team, same game)
  multiply variance; use them sparingly.
"""
    )

    st.divider()
    st.caption(
        "Data sources: football-data.co.uk (history + Pinnacle closing), football-data.org "
        "(schedule), The Odds API (live prices), FBref (xG — v1.5), StatsBomb Open (validation)."
    )
