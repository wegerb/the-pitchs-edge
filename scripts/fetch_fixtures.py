"""Pull upcoming fixtures from football-data.org."""
from pitchs_edge.ingest.fixtures import fetch_upcoming

if __name__ == "__main__":
    print(fetch_upcoming())
