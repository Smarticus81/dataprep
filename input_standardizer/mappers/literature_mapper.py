"""Literature search mapper — produces canonical literature_search.json."""

from __future__ import annotations

from typing import Any, Dict, List

from ..contracts import ExtractionResult, MapperOutput
from ..issue_model import IssueCode, Severity, make_issue
from ..schema_registry import LITERATURE_SCHEMA
from ..traceability_model import TraceabilityStore, make_trace
from ..value_normalizers import normalize_value, is_placeholder
from ._base import compute_completeness


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
            "No source file classified as 'literature' found. "
            "Literature search results are strongly recommended.",
            canonical_target=LITERATURE_SCHEMA.filename,
            suggested_action="Include the literature search report or summary.",
        ))
        return MapperOutput(
            canonical_file=LITERATURE_SCHEMA.filename,
            rows=[],
            mapping_decisions=[],
            traces=[],
            completeness=0.0,
            notes=["No literature candidates found."],
        )

    aliases = LITERATURE_SCHEMA.all_aliases()
    canonical: Dict[str, Any] = {}

    for extraction in candidates:
        src_data = extraction.rows[0] if extraction.rows else {}
        for src_key, value in src_data.items():
            canonical_field = aliases.get(src_key.strip().lower())
            if not canonical_field:
                continue
            if canonical.get(canonical_field) is not None and not is_placeholder(canonical.get(canonical_field)):
                continue
            fdef = LITERATURE_SCHEMA.field_by_name(canonical_field)
            if fdef is None:
                continue
            norm_val, transform, norm_issue = normalize_value(
                value, fdef, LITERATURE_SCHEMA.filename, extraction.source_path
            )
            if norm_issue:
                issues.append(norm_issue)
            canonical[canonical_field] = norm_val
            store.add(make_trace(
                canonical_file=LITERATURE_SCHEMA.filename,
                canonical_field=canonical_field,
                source_file=extraction.source_path,
                source_location=f"key '{src_key}'",
                source_key_or_excerpt=str(value)[:200] if value else "",
                transform_applied=transform,
                confidence=0.95,
                llm_used=False,
            ))

        # LLM enrichment
        if use_llm and extraction.rows:
            full_text = extraction.rows[0].get("full_text", "")
            if full_text:
                _llm_enrich(canonical, full_text, extraction.source_path, store)

    row = [canonical] if canonical else []
    completeness = compute_completeness(row, LITERATURE_SCHEMA)
    notes.append(f"Literature search completeness: {completeness:.0%}")

    return MapperOutput(
        canonical_file=LITERATURE_SCHEMA.filename,
        rows=row,
        mapping_decisions=[],
        traces=[],
        completeness=completeness,
        notes=notes,
        issues=issues,
    )


def _llm_enrich(
    canonical: Dict[str, Any],
    full_text: str,
    source_path: str,
    store: TraceabilityStore,
) -> None:
    from .. import llm_assist
    if not llm_assist.is_available():
        return
    missing = [
        f.name for f in LITERATURE_SCHEMA.fields
        if canonical.get(f.name) is None or is_placeholder(canonical.get(f.name))
    ]
    if not missing:
        return
    extractions = llm_assist.extract_document_fields(
        document_text=full_text,
        target_fields=missing,
        canonical_file=LITERATURE_SCHEMA.filename,
        source_file=source_path,
    )
    for field_name, entry in extractions.items():
        val = entry.get("value")
        if val is None:
            continue
        canonical[field_name] = val
        store.add(make_trace(
            canonical_file=LITERATURE_SCHEMA.filename,
            canonical_field=field_name,
            source_file=source_path,
            source_location="document text (LLM)",
            source_key_or_excerpt=entry.get("excerpt", "")[:200],
            transform_applied="llm_document_extraction",
            confidence=float(entry.get("confidence", 0.3)),
            llm_used=True,
        ))
