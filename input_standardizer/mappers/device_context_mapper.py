"""
Device context mapper — produces canonical device_context.json.

Special rules:
- Prefer structured JSON over document inference
- Use documents only to enrich missing fields
- Never let inferred document fields silently override structured values
  without recording a conflict
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..contracts import CanonicalType, ExtractionResult, MapperOutput
from ..issue_model import IssueCode, Severity, make_issue
from ..schema_registry import DEVICE_CONTEXT_SCHEMA
from ..traceability_model import TraceabilityStore, make_trace
from ..value_normalizers import normalize_value, is_placeholder
from ._base import check_required_fields, compute_completeness


# Document section -> canonical field mappings for enrichment
_SECTION_TO_FIELD: Dict[str, str] = {
    "device_description": "device_description",
    "intended_purpose": "intended_purpose",
    "indications_for_use": "indications_for_use",
    "contraindications": "contraindications",
    "notified_body": "notified_body_name_and_id",
    "eu_mdr_classification": "eu_mdr_classification_and_rule",
    "udi": "basic_udi_di_or_device_family_name",
    "pmcf_details": "pmcf_plan_document",
    "rmf_reference": "risk_management_file_document_number",
    "ifu_reference": "ifu_document",
    "certificate_details": "date_of_first_ce_marking_or_doc",
    "sterility": "sterility_status",
}


def _map_structured_json(
    extraction: ExtractionResult,
    store: TraceabilityStore,
) -> Dict[str, Any]:
    """Map a JSON extraction directly to canonical fields."""
    result: Dict[str, Any] = {}
    if not extraction.rows:
        return result
    src = extraction.rows[0]
    aliases = DEVICE_CONTEXT_SCHEMA.all_aliases()

    for src_key, value in src.items():
        # Direct canonical match
        key_lower = src_key.strip().lower()
        canonical_field = aliases.get(key_lower)
        if canonical_field is None:
            continue
        fdef = DEVICE_CONTEXT_SCHEMA.field_by_name(canonical_field)
        if fdef is None:
            continue
        norm_val, transform, _ = normalize_value(value, fdef, DEVICE_CONTEXT_SCHEMA.filename, extraction.source_path)
        result[canonical_field] = norm_val
        store.add(make_trace(
            canonical_file=DEVICE_CONTEXT_SCHEMA.filename,
            canonical_field=canonical_field,
            source_file=extraction.source_path,
            source_location=f"key '{src_key}'",
            source_key_or_excerpt=str(value)[:200] if value else "",
            transform_applied=transform,
            confidence=0.98,
            llm_used=False,
        ))
    return result


def _enrich_from_documents(
    doc_extractions: List[ExtractionResult],
    current: Dict[str, Any],
    store: TraceabilityStore,
    issues: List,
    use_llm: bool,
) -> Dict[str, Any]:
    """Fill missing fields from document extractions (CER, IFU, RMF)."""
    for doc in doc_extractions:
        missing_fields = [
            f.name for f in DEVICE_CONTEXT_SCHEMA.fields
            if current.get(f.name) is None or is_placeholder(current.get(f.name))
        ]
        if not missing_fields:
            break

        # Use LLM to extract specific fields from document text
        if use_llm:
            from .. import llm_assist
            if llm_assist.is_available() and doc.rows:
                full_text = doc.rows[0].get("full_text", "")
                extractions = llm_assist.extract_document_fields(
                    document_text=full_text,
                    target_fields=missing_fields,
                    canonical_file=DEVICE_CONTEXT_SCHEMA.filename,
                    source_file=doc.source_path,
                )
                for field_name, entry in extractions.items():
                    val = entry.get("value")
                    conf = float(entry.get("confidence", 0.3))
                    excerpt = entry.get("excerpt", "")
                    if val is None:
                        continue
                    # Conflict check
                    existing = current.get(field_name)
                    if existing not in (None, "") and not is_placeholder(existing):
                        if str(existing).strip().lower() != str(val).strip().lower():
                            issues.append(make_issue(
                                Severity.MINOR,
                                IssueCode.CONFLICTING_FIELD_VALUES,
                                f"Conflict for '{field_name}': structured='{existing}' "
                                f"vs document='{val}'. Keeping structured value.",
                                source_files=[doc.source_path],
                                canonical_target=DEVICE_CONTEXT_SCHEMA.filename,
                                field_name=field_name,
                                suggested_action="Verify which value is authoritative.",
                            ))
                        continue

                    current[field_name] = val
                    issues.append(make_issue(
                        Severity.INFO,
                        IssueCode.FIELD_INFERRED_FROM_UNSTRUCTURED,
                        f"Field '{field_name}' inferred from document '{doc.source_path}' "
                        f"via LLM extraction (confidence={conf:.2f}).",
                        source_files=[doc.source_path],
                        canonical_target=DEVICE_CONTEXT_SCHEMA.filename,
                        field_name=field_name,
                    ))
                    store.add(make_trace(
                        canonical_file=DEVICE_CONTEXT_SCHEMA.filename,
                        canonical_field=field_name,
                        source_file=doc.source_path,
                        source_location="document text",
                        source_key_or_excerpt=excerpt[:200],
                        transform_applied="llm_document_extraction",
                        confidence=conf,
                        llm_used=True,
                    ))
        else:
            # Heuristic snippet-based enrichment (no LLM)
            for section_name, snippet in doc.raw_text_snippets.items():
                canonical_field = _SECTION_TO_FIELD.get(section_name)
                if not canonical_field:
                    continue
                if current.get(canonical_field) not in (None, "") and not is_placeholder(current.get(canonical_field)):
                    continue
                # Use the snippet as a rough value
                current[canonical_field] = snippet[:500].strip()
                issues.append(make_issue(
                    Severity.INFO,
                    IssueCode.FIELD_INFERRED_FROM_UNSTRUCTURED,
                    f"Field '{canonical_field}' populated from document section "
                    f"'{section_name}' in '{doc.source_path}' (heuristic, no LLM).",
                    source_files=[doc.source_path],
                    canonical_target=DEVICE_CONTEXT_SCHEMA.filename,
                    field_name=canonical_field,
                ))
                store.add(make_trace(
                    canonical_file=DEVICE_CONTEXT_SCHEMA.filename,
                    canonical_field=canonical_field,
                    source_file=doc.source_path,
                    source_location=f"section '{section_name}'",
                    source_key_or_excerpt=snippet[:200],
                    transform_applied="heuristic_section_extraction",
                    confidence=0.45,
                    llm_used=False,
                ))
    return current


def run(
    structured_candidates: List[ExtractionResult],   # JSON / spreadsheet
    document_candidates: List[ExtractionResult],      # CER / IFU / RMF
    store: TraceabilityStore,
    use_llm: bool = True,
) -> MapperOutput:
    issues: List = []
    notes: List[str] = []
    canonical: Dict[str, Any] = {}

    # Prefer JSON structured sources
    json_candidates = [e for e in structured_candidates if e.source_path.endswith(".json")]
    other_candidates = [e for e in structured_candidates if not e.source_path.endswith(".json")]

    for extraction in json_candidates + other_candidates:
        mapped = _map_structured_json(extraction, store)
        for field_name, val in mapped.items():
            if canonical.get(field_name) is None or is_placeholder(canonical.get(field_name)):
                canonical[field_name] = val
            elif str(canonical[field_name]).strip() != str(val).strip():
                issues.append(make_issue(
                    Severity.MINOR,
                    IssueCode.CONFLICTING_FIELD_VALUES,
                    f"Multiple structured sources provide different values for "
                    f"'{field_name}': '{canonical[field_name]}' vs '{val}'.",
                    source_files=[extraction.source_path],
                    canonical_target=DEVICE_CONTEXT_SCHEMA.filename,
                    field_name=field_name,
                ))

    # Enrich from documents
    if document_candidates:
        canonical = _enrich_from_documents(
            document_candidates, canonical, store, issues, use_llm
        )
        notes.append(f"Enriched from {len(document_candidates)} document(s).")

    if not structured_candidates and not document_candidates:
        issues.append(make_issue(
            Severity.CRITICAL,
            IssueCode.MISSING_REQUIRED_TARGET,
            "No device_context source found (JSON, spreadsheet, or document).",
            canonical_target=DEVICE_CONTEXT_SCHEMA.filename,
            suggested_action="Provide a device_context.json or CER/IFU document.",
        ))

    row = [canonical] if canonical else []
    issues.extend(check_required_fields(row, DEVICE_CONTEXT_SCHEMA,
                                        structured_candidates[0].source_path if structured_candidates
                                        else (document_candidates[0].source_path if document_candidates else "")))
    completeness = compute_completeness(row, DEVICE_CONTEXT_SCHEMA)

    notes.append(f"Device context completeness: {completeness:.0%}")
    return MapperOutput(
        canonical_file=DEVICE_CONTEXT_SCHEMA.filename,
        rows=row,
        mapping_decisions=[],
        traces=[],
        completeness=completeness,
        notes=notes,
        issues=issues,
    )
