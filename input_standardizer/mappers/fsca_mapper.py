"""
FSCA mapper — produces canonical fsca.csv.

Special rules:
- Explicitly separate: initiation date, final FSN date, MHRA reported date
- Flag closed FSCAs without an effectiveness value
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..contracts import ExtractionResult, MapperOutput
from ..issue_model import IssueCode, Severity, make_issue
from ..schema_registry import FSCA_SCHEMA
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

    if not candidates:
        issues.append(make_issue(
            Severity.MINOR,
            IssueCode.MISSING_STRONGLY_RECOMMENDED_TARGET,
            "No source file classified as 'fsca' was found. "
            "FSCA data is strongly recommended for completeness.",
            canonical_target=FSCA_SCHEMA.filename,
            suggested_action="Include an FSCA log or field safety corrective action register.",
        ))
        return MapperOutput(
            canonical_file=FSCA_SCHEMA.filename,
            rows=[],
            mapping_decisions=[],
            traces=[],
            completeness=0.0,
            notes=["No FSCA candidates found."],
        )

    llm_fn = None
    if use_llm:
        from .. import llm_assist
        if llm_assist.is_available():
            llm_fn = llm_assist.map_header_to_field

    for extraction in candidates:
        rows, decisions, row_issues = map_extraction(extraction, FSCA_SCHEMA, store, llm_fn)
        all_decisions.extend(decisions)
        issues.extend(row_issues)
        all_rows.extend(rows)

    # Dedup by action_id
    seen: Dict[str, int] = {}
    deduped: List[Dict[str, Any]] = []
    for row in all_rows:
        key = str(row.get("action_id", "")).strip()
        if key and key in seen:
            issues.append(make_issue(
                Severity.MINOR,
                IssueCode.DUPLICATE_FSCA_IDENTITY,
                f"Duplicate action_id '{key}' found in FSCA data.",
                source_files=[c.source_path for c in candidates],
                canonical_target=FSCA_SCHEMA.filename,
                field_name="action_id",
            ))
        else:
            if key:
                seen[key] = len(deduped)
            deduped.append(row)

    # Flag closed FSCAs without effectiveness
    for row in deduped:
        status = str(row.get("status", "")).lower()
        effectiveness = row.get("effectiveness")
        if status in ("closed", "completed") and (
            effectiveness is None or str(effectiveness).strip() == ""
        ):
            issues.append(make_issue(
                Severity.MAJOR,
                IssueCode.CLOSED_FSCA_MISSING_EFFECTIVENESS,
                f"FSCA action_id '{row.get('action_id', '?')}' is closed "
                f"but has no effectiveness assessment recorded.",
                source_files=[c.source_path for c in candidates],
                canonical_target=FSCA_SCHEMA.filename,
                field_name="effectiveness",
                suggested_action="Record the effectiveness assessment result for this closed FSCA.",
            ))

    issues.extend(check_required_fields(deduped, FSCA_SCHEMA, candidates[0].source_path))
    issues.extend(check_uniqueness(deduped, FSCA_SCHEMA, candidates[0].source_path))
    completeness = compute_completeness(deduped, FSCA_SCHEMA)

    return MapperOutput(
        canonical_file=FSCA_SCHEMA.filename,
        rows=deduped,
        mapping_decisions=all_decisions,
        traces=[],
        completeness=completeness,
        notes=[f"Mapped {len(deduped)} FSCA records from {len(candidates)} candidate(s)."],
        issues=issues,
    )
