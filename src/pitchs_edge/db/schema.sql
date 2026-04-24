PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS leagues (
    id           INTEGER PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,
    fd_org_id    INTEGER,
    odds_api_key TEXT,
    name         TEXT NOT NULL,
    country      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS teams (
    id         INTEGER PRIMARY KEY,
    league_id  INTEGER NOT NULL REFERENCES leagues(id),
    name       TEXT NOT NULL,
    UNIQUE(league_id, name)
);

CREATE TABLE IF NOT EXISTS fixtures (
    id             INTEGER PRIMARY KEY,
    league_id      INTEGER NOT NULL REFERENCES leagues(id),
    season         TEXT NOT NULL,
    kickoff        TEXT NOT NULL,
    home_team_id   INTEGER NOT NULL REFERENCES teams(id),
    away_team_id   INTEGER NOT NULL REFERENCES teams(id),
    fthg           INTEGER,
    ftag           INTEGER,
    hthg           INTEGER,
    htag           INTEGER,
    home_xg        REAL,
    away_xg        REAL,
    home_corners   INTEGER,
    away_corners   INTEGER,
    status         TEXT NOT NULL DEFAULT 'scheduled',
    external_ids   TEXT,
    UNIQUE(league_id, season, kickoff, home_team_id, away_team_id)
);
CREATE INDEX IF NOT EXISTS ix_fixtures_league_season ON fixtures(league_id, season);
CREATE INDEX IF NOT EXISTS ix_fixtures_kickoff ON fixtures(kickoff);

CREATE TABLE IF NOT EXISTS odds_closing (
    id         INTEGER PRIMARY KEY,
    fixture_id INTEGER NOT NULL REFERENCES fixtures(id),
    book       TEXT NOT NULL,
    market     TEXT NOT NULL,
    selection  TEXT NOT NULL,
    line       REAL,
    price      REAL NOT NULL,
    UNIQUE(fixture_id, book, market, selection, line)
);
CREATE INDEX IF NOT EXISTS ix_odds_closing_fixture ON odds_closing(fixture_id);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    id          INTEGER PRIMARY KEY,
    fixture_id  INTEGER NOT NULL REFERENCES fixtures(id),
    captured_at TEXT NOT NULL,
    book        TEXT NOT NULL,
    market      TEXT NOT NULL,
    selection   TEXT NOT NULL,
    line        REAL,
    price       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_snapshots_fixture ON odds_snapshots(fixture_id);
CREATE INDEX IF NOT EXISTS ix_snapshots_captured ON odds_snapshots(captured_at);

CREATE TABLE IF NOT EXISTS bets (
    id               INTEGER PRIMARY KEY,
    fixture_id       INTEGER NOT NULL REFERENCES fixtures(id),
    market           TEXT NOT NULL,
    selection        TEXT NOT NULL,
    line             REAL,
    stake            REAL NOT NULL,
    price_taken      REAL NOT NULL,
    book             TEXT NOT NULL,
    model_prob       REAL NOT NULL,
    edge_pct         REAL NOT NULL,
    kelly_fraction   REAL NOT NULL,
    placed_at        TEXT NOT NULL,
    result           TEXT,
    pnl              REAL
);
CREATE INDEX IF NOT EXISTS ix_bets_fixture ON bets(fixture_id);

CREATE TABLE IF NOT EXISTS clv_log (
    id                    INTEGER PRIMARY KEY,
    bet_id                INTEGER NOT NULL REFERENCES bets(id),
    closing_price         REAL NOT NULL,
    closing_implied_prob  REAL NOT NULL,
    clv_pct               REAL NOT NULL,
    computed_at           TEXT NOT NULL,
    UNIQUE(bet_id)
);

CREATE TABLE IF NOT EXISTS model_runs (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    league_id      INTEGER NOT NULL REFERENCES leagues(id),
    as_of          TEXT NOT NULL,
    params_json    TEXT NOT NULL,
    train_rows     INTEGER NOT NULL,
    log_likelihood REAL
);

-- Per-fixture, per-team adjustments to attack/defense rates, typically driven by
-- lineup or injury news. Applied multiplicatively on top of the fitted rates at
-- prediction time. attack_delta / defense_delta are ADDITIVE in log-space (so
-- -0.10 ≈ 10% rate cut, +0.10 ≈ 10% rate boost). Source is a free-form tag
-- ('manual', 'api_football', 'transfermarkt', etc.) so we can audit later.
CREATE TABLE IF NOT EXISTS team_adjustments (
    id              INTEGER PRIMARY KEY,
    fixture_id      INTEGER NOT NULL REFERENCES fixtures(id),
    team_id         INTEGER NOT NULL REFERENCES teams(id),
    attack_delta    REAL NOT NULL DEFAULT 0.0,
    defense_delta   REAL NOT NULL DEFAULT 0.0,
    note            TEXT,
    source          TEXT NOT NULL DEFAULT 'manual',
    applied_at      TEXT NOT NULL,
    UNIQUE(fixture_id, team_id, source)
);
CREATE INDEX IF NOT EXISTS ix_team_adj_fixture ON team_adjustments(fixture_id);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id                   INTEGER PRIMARY KEY,
    name                 TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    league_id            INTEGER NOT NULL REFERENCES leagues(id),
    seasons              TEXT NOT NULL,
    xi                   REAL NOT NULL,
    min_training_matches INTEGER NOT NULL,
    step_fixtures        INTEGER NOT NULL,
    edge_threshold       REAL NOT NULL,
    kelly_scale          REAL NOT NULL,
    kelly_cap            REAL NOT NULL,
    bankroll_start       REAL NOT NULL,
    n_predictions        INTEGER NOT NULL,
    log_loss_1x2         REAL,
    brier_1x2            REAL,
    rps_1x2              REAL,
    log_loss_ou25        REAL,
    brier_ou25           REAL,
    market_log_loss_1x2  REAL,
    market_rps_1x2       REAL,
    market_log_loss_ou25 REAL,
    simulated_n_bets     INTEGER,
    simulated_pnl        REAL,
    simulated_roi        REAL,
    bankroll_final       REAL,
    -- How simulated bets were priced. The Track Record panel shows "strict"
    -- (pinnacle_close, CLV=0 bar) alongside "realistic" (best_close, line
    -- shopping across 6 books) so positive CLV isn't mistaken for edge.
    price_source         TEXT DEFAULT 'pinnacle_close',
    model_weight         REAL DEFAULT 1.0,
    model_source         TEXT DEFAULT 'goals',
    clv_weighted         REAL,
    clv_mean             REAL,
    clv_positive_rate    REAL
);

CREATE TABLE IF NOT EXISTS backtest_predictions (
    id             INTEGER PRIMARY KEY,
    run_id         INTEGER NOT NULL REFERENCES backtest_runs(id),
    fixture_id     INTEGER NOT NULL REFERENCES fixtures(id),
    market         TEXT NOT NULL,
    selection      TEXT NOT NULL,
    line           REAL,
    model_prob     REAL NOT NULL,
    closing_prob   REAL,
    closing_price  REAL,
    actual         INTEGER NOT NULL,
    edge_pct       REAL,
    bet_stake      REAL,
    bet_pnl        REAL
);
CREATE INDEX IF NOT EXISTS ix_bt_preds_run ON backtest_predictions(run_id);
CREATE INDEX IF NOT EXISTS ix_bt_preds_fixture ON backtest_predictions(fixture_id);
