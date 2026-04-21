"""Team-name normalization + similarity helpers.

Three sources use three naming conventions for the same club:
- football-data.co.uk CSVs (training data): "Man City", "Nott'm Forest"
- football-data.org API (fixtures): "Manchester City FC", "Nottingham Forest FC"
- The Odds API (live odds): "Manchester City", "Nottingham Forest"

This module reconciles them for lookup purposes. We never rewrite the stored
names — each row keeps the authoritative form from its source — but any code
that crosses a boundary resolves names through `best_match()`.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable

# Common non-distinguishing tokens dropped during normalization.
_DROP_TOKENS = {
    "fc", "cf", "afc", "sc", "ac", "as", "aj", "ud", "cd", "rc", "rcd", "sd",
    "cp", "fk", "bk", "bc", "ca", "club", "calcio", "the", "de", "del", "la",
    "le", "di", "du", "da", "des", "als",
    "olympique", "stade", "racing", "hellas", "real", "deportivo", "sporting",
    "usc", "usl", "ss", "ssc", "us", "acf", "sv", "vfl", "vfb", "tsg", "fsv",
    "borussia", "eintracht", "athletic",
}


def _strip_accents(s: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", s or "")
        if not unicodedata.combining(ch)
    )


def tokens(name: str) -> list[str]:
    ascii_name = _strip_accents(name).lower()
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", ascii_name)
    out: list[str] = []
    for t in cleaned.split():
        if not t or t.isdigit() or t in _DROP_TOKENS:
            continue
        out.append(t)
    return out


def normalize(name: str) -> str:
    return "".join(tokens(name))


def similarity(a: str, b: str) -> float:
    """0..1 similarity between two team names after normalization.

    Boosts token-prefix matches (e.g. "brest" vs "brestois") that SequenceMatcher
    under-weights on short names.
    """
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb or na in nb or nb in na:
        return 1.0
    ta, tb = tokens(a), tokens(b)
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    prefix_hits = sum(
        1 for s in short
        if len(s) >= 4 and any(l.startswith(s) or s.startswith(l) for l in long_)
    )
    if short and prefix_hits == len(short):
        return 0.95
    return SequenceMatcher(None, na, nb).ratio()


def best_match(name: str, candidates: Iterable[str], threshold: float = 0.7) -> str | None:
    """Return the best candidate name for `name`, or None if none meet the threshold."""
    best: tuple[float, str | None] = (0.0, None)
    for c in candidates:
        s = similarity(name, c)
        if s >= threshold and s > best[0]:
            best = (s, c)
    return best[1]
