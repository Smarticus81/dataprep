"""CAPA mapper — produces canonical capa.csv."""

from __future__ import annotations

from typing import Any, Dict, List

from ..contracts import ExtractionResult, MapperOutput
from ..issue_model import IssueCode, Severity, make_issue
from ..schema_registry import CAPA_SCHEMA
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
            "No source file classified as 'capa' was found. "
            "CAPA data is strongly recommended for a complete PSUR package.",
            canonical_target=CAPA_SCHEMA.filename,
            suggested_action="Include a CAPA register or corrective action log.",
        ))
        return MapperOutput(
            canonical_file=CAPA_SCHEMA.filename,
            rows=[],
            mapping_decisions=[],
            traces=[],
            completeness=0.0,
            notes=["No CAPA candidates found."],
        )

    llm_fn = None
    if use_llm:
        from .. import llm_assist
        if llm_assist.is_available():
            llm_fn = llm_assist.map_header_to_field

    for extraction in candidates:
        rows, decisions, row_issues = map_extraction(extraction, CAPA_SCHEMA, store, llm_fn)
        all_decisions.extend(decisions)
        issues.extend(row_issues)
        all_rows.extend(rows)

    # Dedup by capa_number
    seen: Dict[str, int] = {}
    deduped: List[Dict[str, Any]] = []
    for row in all_rows:
        key = str(row.get("capa_number", "")).strip()
        if key and key in seen:
            issues.append(make_issue(
                Severity.MINOR,
                IssueCode.DUPLICATE_CAPA_IDENTITY,
                f"Duplicate capa_number '{key}' found.",
                source_files=[c.source_path for c in candidates],
                canonical_target=CAPA_SCHEMA.filename,
                field_name="capa_number",
            ))
        else:
            if key:
                seen[key] = len(deduped)
            deduped.append(row)

    issues.extend(check_required_fields(deduped, CAPA_SCHEMA, candidates[0].source_path))
    issues.extend(check_uniqueness(deduped, CAPA_SCHEMA, candidates[0].source_path))
    completeness = compute_completeness(deduped, CAPA_SCHEMA)

    return MapperOutput(
        canonical_file=CAPA_SCHEMA.filename,
        rows=deduped,
        mapping_decisions=all_decisions,
        traces=[],
        completeness=completeness,
        notes=[f"Mapped {len(deduped)} CAPA records from {len(candidates)} candidate(s)."],
        issues=issues,
    )
