"""Previous PSUR mapper — produces canonical previous_psur.json."""

from __future__ import annotations

from typing import Any, Dict, List

from ..contracts import ExtractionResult, MapperOutput
from ..issue_model import IssueCode, Severity, make_issue
from ..schema_registry import PREVIOUS_PSUR_SCHEMA
from ..traceability_model import TraceabilityStore, make_trace
from ..value_normalizers import normalize_value, is_placeholder
from ._base import check_required_fields, compute_completeness


def run(
    candidates: List[ExtractionResult],
    store: TraceabilityStore,
    use_llm: bool = True,
) -> MapperOutput:
    issues = []
    notes: List[str] = []

    if not candidates:
        issues.append(make_issue(
            Severity.CRITICAL,
            IssueCode.MISSING_REQUIRED_TARGET,
            "No source file classified as 'previous_psur' found. "
            "Prior PSUR data is required for continuous PSUR generation.",
            canonical_target=PREVIOUS_PSUR_SCHEMA.filename,
            suggested_action="Provide the previous PSUR as a JSON file.",
        ))
        return MapperOutput(
            canonical_file=PREVIOUS_PSUR_SCHEMA.filename,
            rows=[],
            mapping_decisions=[],
            traces=[],
            completeness=0.0,
            notes=["No previous_psur candidates found."],
        )

    aliases = PREVIOUS_PSUR_SCHEMA.all_aliases()
    canonical: Dict[str, Any] = {}

    for extraction in candidates:
        if not extraction.rows:
            continue
        src = extraction.rows[0]
        for src_key, value in src.items():
            canonical_field = aliases.get(src_key.strip().lower())
            if not canonical_field:
                continue
            if canonical.get(canonical_field) is not None and not is_placeholder(canonical.get(canonical_field)):
                continue
            fdef = PREVIOUS_PSUR_SCHEMA.field_by_name(canonical_field)
            if fdef is None:
                continue
            norm_val, transform, norm_issue = normalize_value(
                value, fdef, PREVIOUS_PSUR_SCHEMA.filename, extraction.source_path
            )
            if norm_issue:
                issues.append(norm_issue)
            canonical[canonical_field] = norm_val
            store.add(make_trace(
                canonical_file=PREVIOUS_PSUR_SCHEMA.filename,
                canonical_field=canonical_field,
                source_file=extraction.source_path,
                source_location=f"key '{src_key}'",
                source_key_or_excerpt=str(value)[:200] if value else "",
                transform_applied=transform,
                confidence=0.95,
                llm_used=False,
            ))

    row = [canonical] if canonical else []
    issues.extend(check_required_fields(row, PREVIOUS_PSUR_SCHEMA, candidates[0].source_path))
    completeness = compute_completeness(row, PREVIOUS_PSUR_SCHEMA)
    notes.append(f"Previous PSUR completeness: {completeness:.0%}")

    return MapperOutput(
        canonical_file=PREVIOUS_PSUR_SCHEMA.filename,
        rows=row,
        mapping_decisions=[],
        traces=[],
        completeness=completeness,
        notes=notes,
        issues=issues,
    )
