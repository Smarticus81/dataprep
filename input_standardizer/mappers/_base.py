"""
Base mapper utilities shared across all domain mappers.

Each domain mapper uses these helpers to:
- map source row dicts through a header->canonical map
- normalize values with traceability
- emit missing-field issues
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..contracts import ExtractionResult, FieldTrace, MappingDecision, MapperOutput
from ..header_mapper import HeaderMapper
from ..issue_model import IssueCode, PreparerIssue, Severity, make_issue
from ..schema_registry import CanonicalSchema
from ..traceability_model import TraceabilityStore, make_trace
from ..value_normalizers import normalize_value


def map_extraction(
    extraction: ExtractionResult,
    schema: CanonicalSchema,
    store: TraceabilityStore,
    llm_assist_fn=None,
) -> Tuple[List[Dict[str, Any]], List[MappingDecision], List[PreparerIssue]]:
    """
    Core mapping routine for tabular extractions.

    Returns (canonical_rows, all_mapping_decisions, all_issues).
    Traces are added directly to `store`.
    """
    mapper = HeaderMapper(schema)
    header_to_canonical, decisions, issues = mapper.map_headers(
        extraction.headers,
        source_file=extraction.source_path,
        llm_assist_fn=llm_assist_fn,
    )
    canonical_rows: List[Dict[str, Any]] = []

    for row_idx, src_row in enumerate(extraction.rows):
        canonical_row: Dict[str, Any] = {}

        for src_header, canonical_field in header_to_canonical.items():
            raw = src_row.get(src_header)
            fdef = schema.field_by_name(canonical_field)
            if fdef is None:
                continue

            norm_val, transform, norm_issue = normalize_value(
                raw, fdef, schema.filename, extraction.source_path
            )
            if norm_issue:
                issues.append(norm_issue)

            canonical_row[canonical_field] = norm_val

            source_location = (
                f"row {(extraction.header_row_index or 0) + row_idx + 2}, "
                f"col '{src_header}'"
            )
            trace = make_trace(
                canonical_file=schema.filename,
                canonical_field=canonical_field,
                source_file=extraction.source_path,
                source_location=source_location,
                source_key_or_excerpt=str(raw)[:200] if raw is not None else "",
                transform_applied=transform,
                confidence=header_to_canonical and 0.95 or 0.7,
                llm_used=False,
            )
            store.add(trace, row_index=row_idx)

        canonical_rows.append(canonical_row)

    return canonical_rows, decisions, issues


def check_required_fields(
    rows: List[Dict[str, Any]],
    schema: CanonicalSchema,
    source_file: str,
) -> List[PreparerIssue]:
    """Check that all required fields are populated across all rows."""
    issues: List[PreparerIssue] = []
    required = {f.name for f in schema.required_fields()}
    missing_globally: set = set()

    for row in rows:
        for fname in required:
            val = row.get(fname)
            if val is None or str(val).strip() == "":
                missing_globally.add(fname)

    for fname in sorted(missing_globally):
        issues.append(make_issue(
            Severity.MAJOR,
            IssueCode.MISSING_REQUIRED_FIELD,
            f"Required field '{fname}' is missing or empty in '{schema.filename}'.",
            source_files=[source_file],
            canonical_target=schema.filename,
            field_name=fname,
            suggested_action=f"Ensure '{fname}' is present in the source data.",
        ))
    return issues


def check_uniqueness(
    rows: List[Dict[str, Any]],
    schema: CanonicalSchema,
    source_file: str,
) -> List[PreparerIssue]:
    if not schema.uniqueness_fields:
        return []
    issues: List[PreparerIssue] = []
    seen: Dict[Tuple, int] = {}
    for idx, row in enumerate(rows):
        key = tuple(str(row.get(f, "")) for f in schema.uniqueness_fields)
        if key in seen:
            issues.append(make_issue(
                Severity.MAJOR,
                IssueCode.UNIQUENESS_VIOLATION,
                f"Duplicate key {dict(zip(schema.uniqueness_fields, key))} "
                f"at rows {seen[key]} and {idx} in '{schema.filename}'.",
                source_files=[source_file],
                canonical_target=schema.filename,
                suggested_action="Deduplicate source data.",
            ))
        else:
            seen[key] = idx
    return issues


def compute_completeness(
    rows: List[Dict[str, Any]],
    schema: CanonicalSchema,
) -> float:
    required = [f.name for f in schema.required_fields()]
    if not required or not rows:
        return 1.0
    filled = 0
    total = len(required) * len(rows)
    for row in rows:
        for fname in required:
            if row.get(fname) not in (None, ""):
                filled += 1
    return filled / total if total > 0 else 1.0
