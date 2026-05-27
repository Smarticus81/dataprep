"""
RACT mapper — produces canonical ract.json.

Special rules:
- Preserve raw threshold values exactly (no interpretation of acceptability)
- Support tabular RACT (CSV/XLSX) and structured JSON input
- Normalize only dtype/enum values; never interpret risk conclusions
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..contracts import ExtractionResult, MapperOutput
from ..issue_model import IssueCode, Severity, make_issue
from ..schema_registry import RACT_SCHEMA
from ..traceability_model import TraceabilityStore
from ._base import check_required_fields, compute_completeness, map_extraction


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
            "No source file classified as 'ract' found. "
            "The RACT (risk acceptability table) is required for PSUR generation.",
            canonical_target=RACT_SCHEMA.filename,
            suggested_action="Include the Risk Acceptability Criteria Table as CSV, XLSX, or JSON.",
        ))
        return MapperOutput(
            canonical_file=RACT_SCHEMA.filename,
            rows=[],
            mapping_decisions=[],
            traces=[],
            completeness=0.0,
            notes=["No RACT candidates found."],
        )

    llm_fn = None
    if use_llm:
        from .. import llm_assist
        if llm_assist.is_available():
            llm_fn = llm_assist.map_header_to_field

    for extraction in candidates:
        # JSON: rows may be direct hazard dicts OR wrapped in {"hazards": [...]}
        if extraction.source_path.endswith(".json") and extraction.rows:
            # Detect wrapper key: {"hazards": [...]} or {"hazard_rows": [...]}
            first = extraction.rows[0] if extraction.rows else {}
            hazard_list = None
            for wrapper_key in ("hazards", "hazard_rows", "Hazards", "items", "rows"):
                candidate = first.get(wrapper_key)
                if isinstance(candidate, list) and candidate:
                    hazard_list = candidate
                    break
            rows_to_map = hazard_list if hazard_list is not None else extraction.rows

            aliases = RACT_SCHEMA.all_aliases()
            for row_idx, row in enumerate(rows_to_map):
                if not isinstance(row, dict):
                    continue
                mapped: Dict[str, Any] = {}
                for src_key, val in row.items():
                    canonical_field = aliases.get(src_key.strip().lower())
                    if canonical_field:
                        mapped[canonical_field] = val
                all_rows.append(mapped)
        else:
            rows, decisions, row_issues = map_extraction(extraction, RACT_SCHEMA, store, llm_fn)
            all_decisions.extend(decisions)
            issues.extend(row_issues)
            all_rows.extend(rows)

    issues.extend(check_required_fields(all_rows, RACT_SCHEMA, candidates[0].source_path))
    completeness = compute_completeness(all_rows, RACT_SCHEMA)

    return MapperOutput(
        canonical_file=RACT_SCHEMA.filename,
        rows=all_rows,
        mapping_decisions=all_decisions,
        traces=[],
        completeness=completeness,
        notes=[f"Mapped {len(all_rows)} RACT hazard rows from {len(candidates)} candidate(s)."],
        issues=issues,
    )
