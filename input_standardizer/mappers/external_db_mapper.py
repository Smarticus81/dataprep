"""External events/database mapper — produces canonical external_events.csv."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..contracts import ExtractionResult, MapperOutput
from ..issue_model import IssueCode, Severity, make_issue
from ..schema_registry import EXTERNAL_EVENTS_SCHEMA
from ..traceability_model import TraceabilityStore, make_trace
from ..value_normalizers import normalize_str
from ._base import check_required_fields, check_uniqueness, compute_completeness, map_extraction


def _flatten_nested_json(extraction: ExtractionResult, store: TraceabilityStore) -> List[Dict[str, Any]]:
    """Flatten nested product/event JSON into flat event rows."""
    rows: List[Dict[str, Any]] = []
    if not extraction.rows:
        return rows

    src = extraction.rows[0]
    products = src.get("products", [])
    if not isinstance(products, list):
        return rows

    databases = []
    meta = src.get("metadata", {})
    if isinstance(meta, dict):
        sp = meta.get("search_parameters", {})
        if isinstance(sp, dict):
            databases = sp.get("databases_searched", [])

    source_db = "; ".join(databases) if databases else "External database"

    for product in products:
        if not isinstance(product, dict):
            continue

        product_name = product.get("product_name", "")
        part_numbers = product.get("part_number", "")
        if not part_numbers:
            pns = product.get("part_numbers", [])
            if isinstance(pns, list):
                part_numbers = "; ".join(str(p) for p in pns)

        ae = product.get("adverse_events", {})
        if isinstance(ae, dict):
            reports = ae.get("reports", [])
            if isinstance(reports, list):
                for report in reports:
                    if not isinstance(report, dict):
                        continue
                    event_id = (report.get("mdr_report_key") or
                                report.get("report_number") or
                                report.get("event_id") or "")
                    event_date = report.get("event_date") or report.get("report_date") or ""
                    event_type = report.get("event_type", "")
                    description = report.get("event_description", "")
                    outcome = report.get("outcome", "")
                    severity = report.get("severity", "")
                    serious = severity.upper() in ("SERIOUS", "CRITICAL", "MODERATE")

                    narrative_parts = []
                    if description:
                        narrative_parts.append(description)
                    if outcome:
                        narrative_parts.append(f"Outcome: {outcome}")
                    demo = report.get("patient_demographics", {})
                    if isinstance(demo, dict):
                        demo_parts = []
                        if demo.get("age"):
                            demo_parts.append(f"Age: {demo['age']}")
                        if demo.get("sex"):
                            demo_parts.append(f"Sex: {demo['sex']}")
                        if demo_parts:
                            narrative_parts.append(f"Patient: {', '.join(demo_parts)}")

                    device_problem = report.get("device_problem", "")
                    if device_problem and device_problem not in description:
                        narrative_parts.append(f"Device problem: {device_problem}")

                    row = {
                        "event_id": normalize_str(event_id),
                        "date": normalize_str(event_date),
                        "device_model": normalize_str(part_numbers),
                        "device_name": normalize_str(product_name),
                        "external_source": source_db,
                        "description": normalize_str(description),
                        "narrative": normalize_str(" | ".join(narrative_parts)),
                        "event_type": normalize_str(event_type),
                        "serious": serious,
                        "outcome": normalize_str(outcome),
                    }
                    rows.append(row)

                    for field_name, value in row.items():
                        if value is not None and value != "":
                            store.add(make_trace(
                                canonical_file=EXTERNAL_EVENTS_SCHEMA.filename,
                                canonical_field=field_name,
                                source_file=extraction.source_path,
                                source_location=f"products[].adverse_events.reports[] (event {event_id})",
                                source_key_or_excerpt=str(value)[:200],
                                transform_applied="nested_json_flatten",
                                confidence=0.95,
                                llm_used=False,
                            ), row_index=len(rows) - 1)

        recalls = product.get("recalls", {})
        if isinstance(recalls, dict):
            history = recalls.get("recall_history", [])
            if isinstance(history, list):
                for recall in history:
                    if not isinstance(recall, dict):
                        continue
                    recall_num = recall.get("recall_number", "")
                    date = recall.get("date_initiated", recall.get("date_posted", ""))
                    reason = recall.get("reason", "")
                    status = recall.get("status", "")
                    recall_class = recall.get("recall_class", "")

                    narrative_parts = [reason] if reason else []
                    if recall_class:
                        narrative_parts.append(f"Classification: {recall_class}")
                    if status:
                        narrative_parts.append(f"Status: {status}")
                    qty = recall.get("quantity_affected")
                    if qty:
                        narrative_parts.append(f"Units affected: {qty}")

                    recall_outcome = status if status else "Recall initiated"
                    row = {
                        "event_id": normalize_str(recall_num),
                        "date": normalize_str(date),
                        "device_model": normalize_str(part_numbers),
                        "device_name": normalize_str(product_name),
                        "external_source": "FDA Recall Database",
                        "description": normalize_str(reason),
                        "narrative": normalize_str(" | ".join(narrative_parts)),
                        "event_type": "recall",
                        "serious": True,
                        "outcome": normalize_str(recall_outcome),
                    }
                    rows.append(row)

                    for field_name, value in row.items():
                        if value is not None and value != "":
                            store.add(make_trace(
                                canonical_file=EXTERNAL_EVENTS_SCHEMA.filename,
                                canonical_field=field_name,
                                source_file=extraction.source_path,
                                source_location=f"products[].recalls.recall_history[] (recall {recall_num})",
                                source_key_or_excerpt=str(value)[:200],
                                transform_applied="nested_json_flatten",
                                confidence=0.95,
                                llm_used=False,
                            ), row_index=len(rows) - 1)

    return rows


def _is_nested_json(extraction: ExtractionResult) -> bool:
    if not extraction.rows:
        return False
    src = extraction.rows[0]
    return isinstance(src, dict) and "products" in src


def run(
    candidates: List[ExtractionResult],
    store: TraceabilityStore,
    use_llm: bool = True,
) -> MapperOutput:
    issues: List = []
    all_decisions: List = []
    all_rows: List[Dict[str, Any]] = []

    if not candidates:
        issues.append(make_issue(
            Severity.MINOR,
            IssueCode.MISSING_STRONGLY_RECOMMENDED_TARGET,
            "No source file classified as 'external_db' found. "
            "External database events (MAUDE, EUDAMED) are strongly recommended.",
            canonical_target=EXTERNAL_EVENTS_SCHEMA.filename,
            suggested_action="Include external adverse event database extract (CSV/XLSX).",
        ))
        return MapperOutput(
            canonical_file=EXTERNAL_EVENTS_SCHEMA.filename,
            rows=[],
            mapping_decisions=[],
            traces=[],
            completeness=0.0,
            notes=["No external_db candidates found."],
        )

    llm_fn = None
    if use_llm:
        from .. import llm_assist
        if llm_assist.is_available():
            llm_fn = llm_assist.map_header_to_field

    for extraction in candidates:
        if _is_nested_json(extraction):
            flat_rows = _flatten_nested_json(extraction, store)
            all_rows.extend(flat_rows)
        else:
            rows, decisions, row_issues = map_extraction(extraction, EXTERNAL_EVENTS_SCHEMA, store, llm_fn)
            all_decisions.extend(decisions)
            issues.extend(row_issues)
            all_rows.extend(rows)

    issues.extend(check_required_fields(all_rows, EXTERNAL_EVENTS_SCHEMA, candidates[0].source_path))
    issues.extend(check_uniqueness(all_rows, EXTERNAL_EVENTS_SCHEMA, candidates[0].source_path))
    completeness = compute_completeness(all_rows, EXTERNAL_EVENTS_SCHEMA)

    return MapperOutput(
        canonical_file=EXTERNAL_EVENTS_SCHEMA.filename,
        rows=all_rows,
        mapping_decisions=all_decisions,
        traces=[],
        completeness=completeness,
        notes=[f"Mapped {len(all_rows)} external event rows from {len(candidates)} candidate(s)."],
        issues=issues,
    )
