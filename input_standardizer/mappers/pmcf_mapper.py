"""PMCF mapper — produces canonical pmcf.json."""

from __future__ import annotations

from typing import Any, Dict, List

from ..contracts import ExtractionResult, MapperOutput
from ..issue_model import IssueCode, Severity, make_issue
from ..schema_registry import PMCF_SCHEMA
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
            Severity.MINOR,
            IssueCode.MISSING_STRONGLY_RECOMMENDED_TARGET,
            "No source file classified as 'pmcf' found. "
            "PMCF data is required for Class IIb/III devices.",
            canonical_target=PMCF_SCHEMA.filename,
            suggested_action="Provide PMCF Plan or PMCF Evaluation Report data.",
        ))
        return MapperOutput(
            canonical_file=PMCF_SCHEMA.filename,
            rows=[],
            mapping_decisions=[],
            traces=[],
            completeness=0.0,
            notes=["No PMCF candidates found."],
        )

    aliases = PMCF_SCHEMA.all_aliases()
    canonical: Dict[str, Any] = {}

    for extraction in candidates:
        src_data = extraction.rows[0] if extraction.rows else {}

        for src_key, value in src_data.items():
            canonical_field = aliases.get(src_key.strip().lower())
            if not canonical_field:
                continue
            if canonical.get(canonical_field) is not None and not is_placeholder(canonical.get(canonical_field)):
                continue
            fdef = PMCF_SCHEMA.field_by_name(canonical_field)
            if fdef is None:
                continue
            norm_val, transform, norm_issue = normalize_value(
                value, fdef, PMCF_SCHEMA.filename, extraction.source_path
            )
            if norm_issue:
                issues.append(norm_issue)
            canonical[canonical_field] = norm_val
            store.add(make_trace(
                canonical_file=PMCF_SCHEMA.filename,
                canonical_field=canonical_field,
                source_file=extraction.source_path,
                source_location=f"key '{src_key}'",
                source_key_or_excerpt=str(value)[:200] if value else "",
                transform_applied=transform,
                confidence=0.95,
                llm_used=False,
            ))

        # LLM enrichment from document text
        if use_llm and extraction.raw_text_snippets and extraction.rows:
            _llm_enrich(canonical, extraction, store)

    row = [canonical] if canonical else []
    issues.extend(check_required_fields(row, PMCF_SCHEMA, candidates[0].source_path))
    completeness = compute_completeness(row, PMCF_SCHEMA)
    notes.append(f"PMCF completeness: {completeness:.0%}")

    return MapperOutput(
        canonical_file=PMCF_SCHEMA.filename,
        rows=row,
        mapping_decisions=[],
        traces=[],
        completeness=completeness,
        notes=notes,
        issues=issues,
    )


def _llm_enrich(
    canonical: Dict[str, Any],
    extraction: ExtractionResult,
    store: TraceabilityStore,
) -> None:
    from .. import llm_assist
    if not llm_assist.is_available() or not extraction.rows:
        return
    missing = [
        f.name for f in PMCF_SCHEMA.fields
        if canonical.get(f.name) is None or is_placeholder(canonical.get(f.name))
    ]
    if not missing:
        return
    full_text = extraction.rows[0].get("full_text", "")
    if not full_text:
        return
    extractions = llm_assist.extract_document_fields(
        document_text=full_text,
        target_fields=missing,
        canonical_file=PMCF_SCHEMA.filename,
        source_file=extraction.source_path,
    )
    for field_name, entry in extractions.items():
        val = entry.get("value")
        if val is None:
            continue
        canonical[field_name] = val
        store.add(make_trace(
            canonical_file=PMCF_SCHEMA.filename,
            canonical_field=field_name,
            source_file=extraction.source_path,
            source_location="document text (LLM)",
            source_key_or_excerpt=entry.get("excerpt", "")[:200],
            transform_applied="llm_document_extraction",
            confidence=float(entry.get("confidence", 0.3)),
            llm_used=True,
        ))
