"""
Device context mapper — produces canonical device_context.json.

Special rules:
- Prefer structured JSON over document inference
- Use documents only to enrich missing fields
- Never let inferred document fields silently override structured values
  without recording a conflict
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from ..contracts import CanonicalType, ExtractionResult, MapperOutput
from ..issue_model import IssueCode, Severity, make_issue
from ..schema_registry import DEVICE_CONTEXT_SCHEMA
from ..traceability_model import TraceabilityStore, make_trace
from ..value_normalizers import normalize_value, normalize_str, is_placeholder
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

# JSON path resolvers: canonical_field -> list of (json_path_fn, formatter_fn)
# Each json_path_fn takes the source dict and returns a raw value or None.
# The formatter_fn converts it to a clean string.

def _join_list(items: list, sep: str = "; ") -> str:
    return sep.join(str(i) for i in items if i)


def _resolve_trade_names(src: dict) -> Optional[str]:
    dd = src.get("device_description", {})
    if isinstance(dd, dict):
        products = dd.get("products", [])
        if isinstance(products, list):
            names = [p.get("product_name", "") for p in products if isinstance(p, dict)]
            if names:
                return _join_list(names)
        stmt = dd.get("trade_name_statement")
        if stmt:
            return normalize_str(stmt)
    return None


def _resolve_description(src: dict) -> Optional[str]:
    dd = src.get("device_description", {})
    if isinstance(dd, dict):
        stmt = dd.get("trade_name_statement", "")
        modes = dd.get("mode_of_action_summary", [])
        parts = []
        if stmt:
            parts.append(normalize_str(stmt))
        if isinstance(modes, list) and modes:
            parts.append(_join_list(modes))
        if parts:
            return " ".join(parts)
    return None


def _resolve_intended_purpose(src: dict) -> Optional[str]:
    dd = src.get("device_description", {})
    if isinstance(dd, dict):
        products = dd.get("products", [])
        if isinstance(products, list):
            uses = [f"{p.get('product_name', '?')}: {p.get('intended_use', '')}"
                    for p in products if isinstance(p, dict) and p.get("intended_use")]
            if uses:
                return _join_list(uses)
    return None


def _resolve_indications(src: dict) -> Optional[str]:
    dd = src.get("device_description", {})
    if isinstance(dd, dict):
        pop = dd.get("intended_patient_population", {})
        if isinstance(pop, dict):
            general = pop.get("general", "")
            if general:
                return normalize_str(general)
        products = dd.get("products", [])
        if isinstance(products, list):
            uses = [p.get("intended_use", "") for p in products if isinstance(p, dict)]
            if uses:
                return _join_list(uses)
    return None


def _resolve_patient_population(src: dict) -> Optional[str]:
    dd = src.get("device_description", {})
    if isinstance(dd, dict):
        pop = dd.get("intended_patient_population", {})
        if isinstance(pop, dict):
            parts = []
            if pop.get("general"):
                parts.append(pop["general"])
            specific = pop.get("product_specific", {})
            if isinstance(specific, dict):
                parts.extend(f"{k}: {v}" for k, v in specific.items())
            if parts:
                return _join_list(parts)
    return None


def _resolve_intended_users(src: dict) -> Optional[str]:
    dd = src.get("device_description", {})
    if isinstance(dd, dict):
        users = dd.get("intended_users", [])
        if isinstance(users, list) and users:
            return _join_list(users)
    return None


def _resolve_udi(src: dict) -> Optional[str]:
    udi = src.get("udi", {})
    if isinstance(udi, dict):
        ids = udi.get("table_4_device_identifiers", [])
        if isinstance(ids, list) and ids:
            parts = [f"{d.get('product_description', '?')}: {d.get('device_identifier_di', '?')}"
                     for d in ids if isinstance(d, dict)]
            return _join_list(parts)
    dd = src.get("device_description", {})
    if isinstance(dd, dict):
        basic = dd.get("basic_udi_di", {})
        if isinstance(basic, dict):
            return basic.get("basic_udi_di", basic.get("gmn", ""))
    return None


def _resolve_model_numbers(src: dict) -> Optional[str]:
    pvp = src.get("product_variants_and_part_numbers", {})
    if isinstance(pvp, dict):
        parts = pvp.get("table_1_part_numbers", [])
        if isinstance(parts, list) and parts:
            nums = [f"{p.get('part_number', '?')} ({p.get('product_name', '?')})"
                    for p in parts if isinstance(p, dict)]
            return _join_list(nums)
    udi = src.get("udi", {})
    if isinstance(udi, dict):
        ids = udi.get("table_4_device_identifiers", [])
        if isinstance(ids, list) and ids:
            nums = [d.get("model_version_number", "") for d in ids if isinstance(d, dict)]
            return _join_list(nums)
    return None


def _resolve_classification(src: dict) -> Optional[str]:
    dd = src.get("device_description", {})
    if isinstance(dd, dict):
        clf = dd.get("classification", {})
        if isinstance(clf, dict):
            eu = clf.get("eu_mdr", {})
            if isinstance(eu, dict):
                parts = []
                if eu.get("class"):
                    parts.append(f"Class {eu['class']}")
                if eu.get("rule"):
                    parts.append(f"Rule {eu['rule']}")
                if eu.get("regulation"):
                    parts.append(eu["regulation"])
                if parts:
                    return ", ".join(parts)
    return None


def _resolve_notified_body(src: dict) -> Optional[str]:
    ai = src.get("administrative_information", {})
    if isinstance(ai, dict):
        nb = ai.get("notified_body", {})
        if isinstance(nb, dict):
            name = nb.get("company_name", "")
            num = nb.get("notified_body_number", "")
            if name:
                return f"{name} (NB {num})" if num else name
    return None


def _resolve_sterility(src: dict) -> Optional[str]:
    dd = src.get("device_description", {})
    if isinstance(dd, dict):
        chars = dd.get("device_characteristics", {})
        if isinstance(chars, dict):
            s = chars.get("sterility")
            if s:
                return normalize_str(s)
        sterilization = dd.get("sterilization", {})
        if isinstance(sterilization, dict) and sterilization.get("summary"):
            return normalize_str(sterilization["summary"])
    return None


def _resolve_single_use(src: dict) -> Optional[str]:
    dd = src.get("device_description", {})
    if isinstance(dd, dict):
        chars = dd.get("device_characteristics", {})
        if isinstance(chars, dict) and chars.get("reusability"):
            return normalize_str(chars["reusability"])
    return None


def _resolve_contraindications(src: dict) -> Optional[str]:
    dd = src.get("device_description", {})
    if isinstance(dd, dict):
        ci = dd.get("contraindications", {})
        if isinstance(ci, dict) and ci.get("summary"):
            return normalize_str(ci["summary"])
    wp = src.get("warnings_and_precautions", {})
    if isinstance(wp, dict):
        cw = wp.get("common_warnings", [])
        if isinstance(cw, list) and cw:
            return _join_list(cw)
    return None


def _resolve_gmdn(src: dict) -> Optional[str]:
    dd = src.get("device_description", {})
    if isinstance(dd, dict):
        products = dd.get("products", [])
        if isinstance(products, list):
            codes = set()
            for p in products:
                if isinstance(p, dict):
                    gmdn = p.get("gmdn", {})
                    if isinstance(gmdn, dict) and gmdn.get("code"):
                        codes.add(f"{gmdn['code']} ({gmdn.get('term', '?')})")
            if codes:
                return _join_list(sorted(codes))
    return None


def _resolve_market_history(src: dict) -> Optional[str]:
    dd = src.get("device_description", {})
    if isinstance(dd, dict):
        gen = dd.get("generations_and_market_history", {})
        if isinstance(gen, dict):
            market = gen.get("market_since", {})
            parts = []
            if isinstance(market, dict):
                if market.get("since_year"):
                    parts.append(f"On market since {market['since_year']}")
                if market.get("original_company"):
                    parts.append(f"originally by {market['original_company']}")
            if gen.get("previous_generations"):
                parts.append(gen["previous_generations"])
            if parts:
                return "; ".join(parts)
    return None


def _resolve_device_lifetime(src: dict) -> Optional[str]:
    dd = src.get("device_description", {})
    if isinstance(dd, dict):
        sl = dd.get("shelf_life", {})
        if isinstance(sl, dict) and sl.get("expected_performance_life"):
            return normalize_str(sl["expected_performance_life"])
    return None


def _resolve_rmf(src: dict) -> Optional[str]:
    rm = src.get("risk_management", {})
    if isinstance(rm, dict) and rm.get("rmf_document_id"):
        return rm["rmf_document_id"]
    return None


def _resolve_pms_plan(src: dict) -> Optional[str]:
    pms = src.get("post_market_surveillance", {})
    if isinstance(pms, dict):
        docs = pms.get("pms_documents", [])
        if isinstance(docs, list) and docs:
            return _join_list(docs)
    return None


def _resolve_cer_number(src: dict) -> Optional[str]:
    ce = src.get("clinical_evaluation", {})
    if isinstance(ce, dict) and ce.get("clinical_evaluation_report_document_id"):
        return ce["clinical_evaluation_report_document_id"]
    return None


_NESTED_RESOLVERS: Dict[str, Any] = {
    "device_trade_names": _resolve_trade_names,
    "device_description": _resolve_description,
    "intended_purpose": _resolve_intended_purpose,
    "indications_for_use": _resolve_indications,
    "target_patient_population": _resolve_patient_population,
    "intended_user_profile": _resolve_intended_users,
    "basic_udi_di_or_device_family_name": _resolve_udi,
    "model_or_catalog_numbers": _resolve_model_numbers,
    "eu_mdr_classification_and_rule": _resolve_classification,
    "notified_body_name_and_id": _resolve_notified_body,
    "sterility_status": _resolve_sterility,
    "single_use_or_reusable": _resolve_single_use,
    "contraindications": _resolve_contraindications,
    "gmdn_code": _resolve_gmdn,
    "market_history": _resolve_market_history,
    "device_lifetime": _resolve_device_lifetime,
    "risk_management_file_document_number": _resolve_rmf,
    "pms_plan_document": _resolve_pms_plan,
    "cer_document_number_and_version": _resolve_cer_number,
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
        key_lower = src_key.strip().lower()
        canonical_field = aliases.get(key_lower)
        if canonical_field is None:
            continue
        if isinstance(value, (dict, list)):
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

    for canonical_field, resolver in _NESTED_RESOLVERS.items():
        if result.get(canonical_field) and not is_placeholder(result.get(canonical_field)):
            continue
        try:
            resolved = resolver(src)
        except Exception:
            continue
        if resolved is None or is_placeholder(resolved):
            continue
        result[canonical_field] = resolved
        store.add(make_trace(
            canonical_file=DEVICE_CONTEXT_SCHEMA.filename,
            canonical_field=canonical_field,
            source_file=extraction.source_path,
            source_location=f"nested JSON resolution",
            source_key_or_excerpt=str(resolved)[:200],
            transform_applied="nested_json_extraction",
            confidence=0.95,
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

    json_exts = (".json", ".txt")
    json_candidates = [e for e in structured_candidates if e.source_path.lower().endswith(json_exts)]
    other_candidates = [e for e in structured_candidates if not e.source_path.lower().endswith(json_exts)]

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
