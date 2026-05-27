"""
Complaints mapper — produces canonical complaints.csv.

Special rules:
- Deduplicate by complaint_number (unique identity)
- Preserve complaint-level identity even if multiple IMDRF rows per complaint
- Distinguish complaint count from coded occurrences
"""

from __future__ import annotations

from typing import Any, Dict, List, Set

from ..contracts import ExtractionResult, MapperOutput
from ..issue_model import IssueCode, Severity, make_issue
from ..schema_registry import COMPLAINTS_SCHEMA
from ..traceability_model import TraceabilityStore
from ._base import (
    check_required_fields,
    check_uniqueness,
    compute_completeness,
    map_extraction,
)


def run(
    candidates: List[ExtractionResult],
    store: TraceabilityStore,
    use_llm: bool = True,
) -> MapperOutput:
    issues = []
    all_decisions = []
    all_rows: List[Dict[str, Any]] = []
    notes: List[str] = []

    if not candidates:
        issues.append(make_issue(
            Severity.CRITICAL,
            IssueCode.MISSING_REQUIRED_TARGET,
            "No source file classified as 'complaints' was found in the input package.",
            canonical_target=COMPLAINTS_SCHEMA.filename,
            suggested_action="Include a CSV or XLSX file containing complaints/MDR data.",
        ))
        return MapperOutput(
            canonical_file=COMPLAINTS_SCHEMA.filename,
            rows=[],
            mapping_decisions=[],
            traces=[],
            completeness=0.0,
            notes=["No complaints candidates found."],
        )

    llm_fn = None
    if use_llm:
        from .. import llm_assist
        if llm_assist.is_available():
            llm_fn = llm_assist.map_header_to_field

    for extraction in candidates:
        rows, decisions, row_issues = map_extraction(extraction, COMPLAINTS_SCHEMA, store, llm_fn)
        all_decisions.extend(decisions)
        issues.extend(row_issues)
        all_rows.extend(rows)

    # Deduplication by complaint_number
    seen_ids: Dict[str, int] = {}  # complaint_number -> first row index
    deduped: List[Dict[str, Any]] = []
    duplicate_count = 0

    for row in all_rows:
        cn = str(row.get("complaint_number", "")).strip()
        if not cn:
            deduped.append(row)
            continue
        if cn in seen_ids:
            duplicate_count += 1
            issues.append(make_issue(
                Severity.MINOR,
                IssueCode.DUPLICATE_COMPLAINT_IDENTITY,
                f"Duplicate complaint_number '{cn}' found. "
                f"Keeping first occurrence (row {seen_ids[cn]}).",
                source_files=[c.source_path for c in candidates],
                canonical_target=COMPLAINTS_SCHEMA.filename,
                field_name="complaint_number",
                suggested_action="Review source data for duplicate entries.",
            ))
        else:
            seen_ids[cn] = len(deduped)
            deduped.append(row)

    if duplicate_count:
        notes.append(f"Removed {duplicate_count} duplicate complaint record(s).")

    issues.extend(check_required_fields(deduped, COMPLAINTS_SCHEMA, candidates[0].source_path))
    completeness = compute_completeness(deduped, COMPLAINTS_SCHEMA)

    notes.append(
        f"Mapped {len(deduped)} unique complaints from {len(candidates)} candidate(s). "
        f"Unique count: {len(seen_ids)}."
    )

    return MapperOutput(
        canonical_file=COMPLAINTS_SCHEMA.filename,
        rows=deduped,
        mapping_decisions=all_decisions,
        traces=[],
        completeness=completeness,
        notes=notes,
        issues=issues,
    )
