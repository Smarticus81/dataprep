"""External events/database mapper — produces canonical external_events.csv."""

from __future__ import annotations

from typing import Any, Dict, List

from ..contracts import ExtractionResult, MapperOutput
from ..issue_model import IssueCode, Severity, make_issue
from ..schema_registry import EXTERNAL_EVENTS_SCHEMA
from ..traceability_model import TraceabilityStore
from ._base import check_required_fields, check_uniqueness, compute_completeness, map_extraction


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
            Severity.MINOR,
            IssueCode.MISSING_STRONGLY_RECOMMENDED_TARGET,
            "No source file classified as 'external_db' found. "
            "External database events (MAUDE, EUDAMED) are strongly recommended.",
            canonical_target=EXTERNAL_EVENTS_SCHEMA.filename,
            suggested_action="Include external adverse event database extract (CSV/XLSX).",
        ))
        return MapperOutput(
            canonical_file=EXTERNAL_EVENTS_SCHEMA.filename,
            rows=[],
            mapping_decisions=[],
            traces=[],
            completeness=0.0,
            notes=["No external_db candidates found."],
        )

    llm_fn = None
    if use_llm:
        from .. import llm_assist
        if llm_assist.is_available():
            llm_fn = llm_assist.map_header_to_field

    for extraction in candidates:
        rows, decisions, row_issues = map_extraction(extraction, EXTERNAL_EVENTS_SCHEMA, store, llm_fn)
        all_decisions.extend(decisions)
        issues.extend(row_issues)
        all_rows.extend(rows)

    issues.extend(check_required_fields(all_rows, EXTERNAL_EVENTS_SCHEMA, candidates[0].source_path))
    issues.extend(check_uniqueness(all_rows, EXTERNAL_EVENTS_SCHEMA, candidates[0].source_path))
    completeness = compute_completeness(all_rows, EXTERNAL_EVENTS_SCHEMA)

    return MapperOutput(
        canonical_file=EXTERNAL_EVENTS_SCHEMA.filename,
        rows=all_rows,
        mapping_decisions=all_decisions,
        traces=[],
        completeness=completeness,
        notes=[f"Mapped {len(all_rows)} external event rows from {len(candidates)} candidate(s)."],
        issues=issues,
    )
