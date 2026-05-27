"""Coding dictionary mapper — produces canonical coding_dictionary.json."""

from __future__ import annotations

from typing import Any, Dict, List

from ..contracts import ExtractionResult, MapperOutput
from ..issue_model import IssueCode, Severity, make_issue
from ..schema_registry import CODING_DICTIONARY_SCHEMA
from ..traceability_model import TraceabilityStore, make_trace
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
            "No source file classified as 'coding_dictionary' found. "
            "IMDRF coding dictionary is recommended for consistent complaint coding.",
            canonical_target=CODING_DICTIONARY_SCHEMA.filename,
            suggested_action="Provide the IMDRF Annex A/F coding dictionary.",
        ))
        return MapperOutput(
            canonical_file=CODING_DICTIONARY_SCHEMA.filename,
            rows=[],
            mapping_decisions=[],
            traces=[],
            completeness=0.0,
            notes=["No coding_dictionary candidates found."],
        )

    canonical: Dict[str, Any] = {}
    aliases = CODING_DICTIONARY_SCHEMA.all_aliases()

    for extraction in candidates:
        src = extraction.rows[0] if extraction.rows else {}
        for src_key, value in src.items():
            canonical_field = aliases.get(src_key.strip().lower())
            if not canonical_field:
                # Preserve AnnexA / AnnexF by direct key
                if src_key in ("AnnexA", "AnnexF"):
                    canonical_field = src_key
                else:
                    continue
            canonical[canonical_field] = value
            store.add(make_trace(
                canonical_file=CODING_DICTIONARY_SCHEMA.filename,
                canonical_field=canonical_field,
                source_file=extraction.source_path,
                source_location=f"key '{src_key}'",
                source_key_or_excerpt=str(value)[:100] if value else "",
                transform_applied="passthrough",
                confidence=0.95,
                llm_used=False,
            ))

    row = [canonical] if canonical else []
    completeness = compute_completeness(row, CODING_DICTIONARY_SCHEMA)
    notes.append(f"Coding dictionary completeness: {completeness:.0%}")

    return MapperOutput(
        canonical_file=CODING_DICTIONARY_SCHEMA.filename,
        rows=row,
        mapping_decisions=[],
        traces=[],
        completeness=completeness,
        notes=notes,
        issues=issues,
    )
