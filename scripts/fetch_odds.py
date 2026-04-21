"""Snapshot current odds from The Odds API."""
from pitchs_edge.ingest.odds import snapshot_all

if __name__ == "__main__":
    print(snapshot_all())
