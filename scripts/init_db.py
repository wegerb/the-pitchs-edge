"""Create the SQLite schema."""
from pitchs_edge.db import init_schema

if __name__ == "__main__":
    init_schema()
    print("DB initialized.")
