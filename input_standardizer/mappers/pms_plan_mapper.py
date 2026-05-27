"""PMS Plan mapper — produces canonical pms_plan.json."""

from __future__ import annotations

from typing import Any, Dict, List

from ..contracts import ExtractionResult, MapperOutput
from ..issue_model import IssueCode, Severity, make_issue
from ..schema_registry import PMS_PLAN_SCHEMA
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
            "No source file classified as 'pms_plan' found. "
            "A PMS Plan is required for EU MDR PSUR generation.",
            canonical_target=PMS_PLAN_SCHEMA.filename,
            suggested_action="Provide the PMS Plan as a JSON or DOCX file.",
        ))
        return MapperOutput(
            canonical_file=PMS_PLAN_SCHEMA.filename,
            rows=[],
            mapping_decisions=[],
            traces=[],
            completeness=0.0,
            notes=["No pms_plan candidates found."],
        )

    aliases = PMS_PLAN_SCHEMA.all_aliases()
    canonical: Dict[str, Any] = {}

    for extraction in candidates:
        src_data = extraction.rows[0] if extraction.rows else {}
        for src_key, value in src_data.items():
            canonical_field = aliases.get(src_key.strip().lower())
            if not canonical_field:
                continue
            if canonical.get(canonical_field) is not None and not is_placeholder(canonical.get(canonical_field)):
                continue
            fdef = PMS_PLAN_SCHEMA.field_by_name(canonical_field)
            if fdef is None:
                continue
            norm_val, transform, norm_issue = normalize_value(
                value, fdef, PMS_PLAN_SCHEMA.filename, extraction.source_path
            )
            if norm_issue:
                issues.append(norm_issue)
            canonical[canonical_field] = norm_val
            store.add(make_trace(
                canonical_file=PMS_PLAN_SCHEMA.filename,
                canonical_field=canonical_field,
                source_file=extraction.source_path,
                source_location=f"key '{src_key}'",
                source_key_or_excerpt=str(value)[:200] if value else "",
                transform_applied=transform,
                confidence=0.95,
                llm_used=False,
            ))

        # Enrich missing fields from document snippets (no LLM)
        if extraction.raw_text_snippets and not use_llm:
            _heuristic_enrich(canonical, extraction, store, issues)
        elif extraction.raw_text_snippets and use_llm:
            _llm_enrich(canonical, extraction, store, issues)

    row = [canonical] if canonical else []
    issues.extend(check_required_fields(row, PMS_PLAN_SCHEMA, candidates[0].source_path))
    completeness = compute_completeness(row, PMS_PLAN_SCHEMA)
    notes.append(f"PMS plan completeness: {completeness:.0%}")

    return MapperOutput(
        canonical_file=PMS_PLAN_SCHEMA.filename,
        rows=row,
        mapping_decisions=[],
        traces=[],
        completeness=completeness,
        notes=notes,
        issues=issues,
    )


def _heuristic_enrich(
    canonical: Dict[str, Any],
    extraction: ExtractionResult,
    store: TraceabilityStore,
    issues: List,
) -> None:
    field_snippet_map = {
        "proactive_activities": "pmcf_details",
        "reactive_activities": "literature_review",
    }
    for field_name, section in field_snippet_map.items():
        if canonical.get(field_name) or not extraction.raw_text_snippets.get(section):
            continue
        snippet = extraction.raw_text_snippets[section]
        canonical[field_name] = snippet[:500]
        store.add(make_trace(
            canonical_file=PMS_PLAN_SCHEMA.filename,
            canonical_field=field_name,
            source_file=extraction.source_path,
            source_location=f"section '{section}'",
            source_key_or_excerpt=snippet[:200],
            transform_applied="heuristic_section_extraction",
            confidence=0.40,
            llm_used=False,
        ))


def _llm_enrich(
    canonical: Dict[str, Any],
    extraction: ExtractionResult,
    store: TraceabilityStore,
    issues: List,
) -> None:
    from .. import llm_assist
    if not llm_assist.is_available() or not extraction.rows:
        return
    missing = [
        f.name for f in PMS_PLAN_SCHEMA.fields
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
        canonical_file=PMS_PLAN_SCHEMA.filename,
        source_file=extraction.source_path,
    )
    for field_name, entry in extractions.items():
        val = entry.get("value")
        if val is None:
            continue
        canonical[field_name] = val
        store.add(make_trace(
            canonical_file=PMS_PLAN_SCHEMA.filename,
            canonical_field=field_name,
            source_file=extraction.source_path,
            source_location="document text (LLM)",
            source_key_or_excerpt=entry.get("excerpt", "")[:200],
            transform_applied="llm_document_extraction",
            confidence=float(entry.get("confidence", 0.3)),
            llm_used=True,
        ))
