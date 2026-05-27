"""
Sales mapper — produces canonical sales.csv.

Special rules:
- Detect ship date / invoice date / dispatch date candidates as 'date'
- Support Month + Year columns composed into a proper date
- Support country -> region derivation when region column is absent
- Preserve per-row shipment identity if available
"""

from __future__ import annotations

import calendar
import re
from typing import Any, Dict, List, Optional

from ..contracts import ExtractionResult, MapperOutput
from ..issue_model import IssueCode, Severity, make_issue
from ..schema_registry import SALES_SCHEMA
from ..traceability_model import TraceabilityStore, make_trace
from ..value_normalizers import country_to_region
from ._base import (
    check_required_fields,
    check_uniqueness,
    compute_completeness,
    map_extraction,
)


_MONTH_NAMES = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTH_ABBR = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}

_YEAR_HEADER_NAMES = frozenset({
    "calendar year", "year", "cal year", "fiscal year", "calendar_year",
    "calendaryear", "yr",
})

_MONTH_HEADER_NAMES = frozenset({
    "month", "period", "date period",
})


def _find_header(headers: List[str], candidates: frozenset) -> Optional[str]:
    for h in headers:
        if h.strip().lower() in candidates:
            return h
    return None


def _month_to_num(month_str: str) -> Optional[int]:
    s = month_str.strip().lower()
    if s in _MONTH_NAMES:
        return _MONTH_NAMES[s]
    if s in _MONTH_ABBR:
        return _MONTH_ABBR[s]
    m = re.match(r"^(\d{1,2})$", s)
    if m and 1 <= int(m.group(1)) <= 12:
        return int(m.group(1))
    return None


def _compose_date_from_month_year(
    extraction: ExtractionResult,
    canonical_rows: List[Dict[str, Any]],
    store: TraceabilityStore,
) -> int:
    """Look for raw Month + Year columns in the extraction and compose YYYY-MM-01."""
    year_header = _find_header(extraction.headers, _YEAR_HEADER_NAMES)
    month_header = _find_header(extraction.headers, _MONTH_HEADER_NAMES)
    if not month_header:
        return 0

    composed = 0
    for row_idx, (src_row, can_row) in enumerate(zip(extraction.rows, canonical_rows)):
        raw_month = src_row.get(month_header)
        if raw_month is None:
            continue
        month_num = _month_to_num(str(raw_month))
        if month_num is None:
            continue

        year_val = None
        if year_header:
            raw_year = src_row.get(year_header)
            if raw_year is not None:
                y = str(raw_year).strip().split(".")[0]
                if re.match(r"^\d{4}$", y):
                    year_val = y

        if year_val:
            can_row["date"] = f"{year_val}-{month_num:02d}-01"
            store.add(make_trace(
                canonical_file=SALES_SCHEMA.filename,
                canonical_field="date",
                source_file=extraction.source_path,
                source_location=f"composed from '{month_header}' + '{year_header}'",
                source_key_or_excerpt=f"{raw_month} {year_val}",
                transform_applied="month_year_composition",
                confidence=0.90,
                llm_used=False,
                notes="Date composed from separate Month and Year columns.",
            ), row_index=row_idx)
            composed += 1
        else:
            can_row["date"] = None

    return composed


def run(
    candidates: List[ExtractionResult],
    store: TraceabilityStore,
    use_llm: bool = True,
) -> MapperOutput:
    issues = []
    all_decisions = []
    all_rows: List[Dict[str, Any]] = []

    if not candidates:
        issues.append(make_issue(
            Severity.CRITICAL,
            IssueCode.MISSING_REQUIRED_TARGET,
            "No source file classified as 'sales' was found in the input package.",
            canonical_target=SALES_SCHEMA.filename,
            suggested_action="Include a CSV or XLSX file containing sales/shipment data.",
        ))
        return MapperOutput(
            canonical_file=SALES_SCHEMA.filename,
            rows=[],
            mapping_decisions=[],
            traces=[],
            completeness=0.0,
            notes=["No sales candidates found."],
        )

    for extraction in candidates:
        llm_fn = None
        if use_llm:
            from .. import llm_assist
            if llm_assist.is_available():
                llm_fn = llm_assist.map_header_to_field

        rows, decisions, row_issues = map_extraction(extraction, SALES_SCHEMA, store, llm_fn)
        all_decisions.extend(decisions)
        issues.extend(row_issues)

        _compose_date_from_month_year(extraction, rows, store)
        all_rows.extend(rows)

    for row_idx, row in enumerate(all_rows):
        if not row.get("region") and row.get("country"):
            derived = country_to_region(str(row["country"]))
            if derived:
                row["region"] = derived
                trace = make_trace(
                    canonical_file=SALES_SCHEMA.filename,
                    canonical_field="region",
                    source_file=candidates[0].source_path,
                    source_location=f"derived from country '{row['country']}'",
                    source_key_or_excerpt=str(row["country"]),
                    transform_applied="country_to_region_derivation",
                    confidence=0.85,
                    llm_used=False,
                    notes="Region derived from country lookup table.",
                )
                store.add(trace, row_index=row_idx)

    issues.extend(check_required_fields(all_rows, SALES_SCHEMA, candidates[0].source_path))
    issues.extend(check_uniqueness(all_rows, SALES_SCHEMA, candidates[0].source_path))
    completeness = compute_completeness(all_rows, SALES_SCHEMA)

    return MapperOutput(
        canonical_file=SALES_SCHEMA.filename,
        rows=all_rows,
        mapping_decisions=all_decisions,
        traces=[],
        completeness=completeness,
        notes=[f"Mapped {len(all_rows)} sales rows from {len(candidates)} candidate(s)."],
        issues=issues,
    )
