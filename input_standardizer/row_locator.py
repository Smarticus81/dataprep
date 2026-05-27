"""
Row locator — finds the header row in a spreadsheet even when it's not row 1.

Strategy:
1. Scan the first N rows for a row that contains the most recognizable header tokens.
2. Score each candidate row by counting non-numeric, non-empty, and non-date cells.
3. Prefer rows that contain known alias tokens from the schema registry.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


_MAX_SCAN_ROWS = 20
_MIN_HEADER_SCORE = 2  # minimum non-numeric cells for a row to be considered a header


def _looks_like_header_cell(value: Any) -> bool:
    """A cell looks like a header if it's a non-empty string that isn't purely numeric."""
    if value is None:
        return False
    s = str(value).strip()
    if not s:
        return False
    # purely numeric (int or float)
    try:
        float(s.replace(",", ""))
        return False
    except ValueError:
        pass
    # looks like a date
    if re.match(r"^\d{1,4}[/.\-]\d{1,4}[/.\-]\d{2,4}$", s):
        return False
    return True


def _score_row(row: List[Any], known_aliases: Optional[Dict[str, str]] = None) -> int:
    score = sum(1 for cell in row if _looks_like_header_cell(cell))
    if known_aliases and score > 0:
        for cell in row:
            if cell and str(cell).strip().lower() in known_aliases:
                score += 3  # bonus for a recognized alias
    return score


def locate_header_row(
    rows: List[List[Any]],
    known_aliases: Optional[Dict[str, str]] = None,
    max_scan: int = _MAX_SCAN_ROWS,
) -> Tuple[int, List[str]]:
    """
    Returns (header_row_index, header_names).
    header_row_index is 0-based within `rows`.
    Falls back to row 0 if no strong candidate is found.
    """
    if not rows:
        return 0, []

    best_idx = 0
    best_score = -1

    scan_limit = min(max_scan, len(rows))
    for i in range(scan_limit):
        score = _score_row(rows[i], known_aliases)
        if score > best_score:
            best_score = score
            best_idx = i

    if best_score < _MIN_HEADER_SCORE:
        best_idx = 0  # fallback

    headers = [str(c).strip() if c is not None else "" for c in rows[best_idx]]
    return best_idx, headers


def extract_data_rows(
    all_rows: List[List[Any]],
    header_row_index: int,
    headers: List[str],
) -> List[Dict[str, Any]]:
    """
    Given all raw rows and the located header row index, return a list of
    dicts mapping header -> cell value for every data row below the header.
    """
    data_rows = all_rows[header_row_index + 1:]
    result = []
    for row in data_rows:
        # Skip fully empty rows
        if all(cell is None or str(cell).strip() == "" for cell in row):
            continue
        padded = list(row) + [None] * max(0, len(headers) - len(row))
        record = {h: padded[i] for i, h in enumerate(headers) if h}
        result.append(record)
    return result
