from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..config import DB_PATH


def _row_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


@contextmanager
def connect(db_path: Path | None = None):
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = _row_factory
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(db_path: Path | None = None) -> None:
    schema = (Path(__file__).parent / "schema.sql").read_text()
    with connect(db_path) as conn:
        conn.executescript(schema)
