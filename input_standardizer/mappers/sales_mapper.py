"""
Sales mapper — produces canonical sales.csv.

Special rules:
- Detect ship date / invoice date / dispatch date candidates as 'date'
- Support country -> region derivation when region column is absent
- Preserve per-row shipment identity if available
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..contracts import ExtractionResult, MapperOutput
from ..issue_model import IssueCode, Severity, make_issue
from ..schema_registry import SALES_SCHEMA
from ..traceability_model import TraceabilityStore, make_trace
from ..value_normalizers import country_to_region
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
            Severity.CRITICAL,
            IssueCode.MISSING_REQUIRED_TARGET,
            "No source file classified as 'sales' was found in the input package.",
            canonical_target=SALES_SCHEMA.filename,
            suggested_action="Include a CSV or XLSX file containing sales/shipment data.",
        ))
        return MapperOutput(
            canonical_file=SALES_SCHEMA.filename,
            rows=[],
            mapping_decisions=[],
            traces=[],
            completeness=0.0,
            notes=["No sales candidates found."],
        )

    for extraction in candidates:
        llm_fn = None
        if use_llm:
            from .. import llm_assist
            if llm_assist.is_available():
                llm_fn = llm_assist.map_header_to_field

        rows, decisions, row_issues = map_extraction(extraction, SALES_SCHEMA, store, llm_fn)
        all_decisions.extend(decisions)
        issues.extend(row_issues)
        all_rows.extend(rows)

    # Region derivation from country where region is absent
    for row_idx, row in enumerate(all_rows):
        if not row.get("region") and row.get("country"):
            derived = country_to_region(str(row["country"]))
            if derived:
                row["region"] = derived
                trace = make_trace(
                    canonical_file=SALES_SCHEMA.filename,
                    canonical_field="region",
                    source_file=candidates[0].source_path,
                    source_location=f"derived from country '{row['country']}'",
                    source_key_or_excerpt=str(row["country"]),
                    transform_applied="country_to_region_derivation",
                    confidence=0.85,
                    llm_used=False,
                    notes="Region derived from country lookup table.",
                )
                store.add(trace, row_index=row_idx)

    issues.extend(check_required_fields(all_rows, SALES_SCHEMA, candidates[0].source_path))
    issues.extend(check_uniqueness(all_rows, SALES_SCHEMA, candidates[0].source_path))
    completeness = compute_completeness(all_rows, SALES_SCHEMA)

    return MapperOutput(
        canonical_file=SALES_SCHEMA.filename,
        rows=all_rows,
        mapping_decisions=all_decisions,
        traces=[],  # traces live in store
        completeness=completeness,
        notes=[f"Mapped {len(all_rows)} sales rows from {len(candidates)} candidate(s)."],
        issues=issues,
    )
