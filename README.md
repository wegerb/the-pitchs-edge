# The Pitch's Edge

Soccer betting edge detection for EPL, Championship, La Liga, Serie A, Bundesliga, Ligue 1.

Stack: Python · SQLite · Streamlit.

## Data sources

- **football-data.co.uk** — historical results + Pinnacle/B365 closing odds (training set and CLV baseline)
- **football-data.org** — upcoming fixtures / standings (free tier, 10 req/min)
- **The Odds API** — live and pre-match odds (subscription)
- **FBref** — xG supplement (v1.5)
- **StatsBomb Open** — validation on overlapping competitions

## Quick start

```bash
pip install -e '.[dev]'
cp .env.example .env
# fill in ODDS_API_KEY (FOOTBALL_DATA_ORG_KEY optional)

python scripts/init_db.py
python scripts/backfill_csv.py
python scripts/run_ui.py
```

## Layout

```
src/pitchs_edge/
  config.py                # env, league registry, season codes
  db/                      # SQLite schema + connection helper
  sources/                 # data connectors
  ingest/                  # glue code from sources → db
  models/dixon_coles.py    # bivariate-Poisson goals model with time decay + rho
  edge/                    # Shin devig, fractional Kelly
  clv/                     # Closing Line Value tracking
  ui/app.py                # Streamlit dashboard
scripts/                   # runnable entrypoints
tests/                     # pytest
```

## Modeling principles

- CLV over win rate — closing-price anchor is Pinnacle
- Fractional Kelly (¼) with 2% bankroll cap
- Walk-forward temporal validation (never random k-fold)
- Evaluate with log loss, Brier, RPS — not accuracy
