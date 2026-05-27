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


_RACT_REQUIRED_NAMES = {f.name for f in RACT_SCHEMA.fields if f.required}


def _row_completeness(row: Dict[str, Any]) -> int:
    return sum(1 for k in _RACT_REQUIRED_NAMES
               if row.get(k) is not None and str(row.get(k, "")).strip())


def _deduplicate_hazards(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """When the same hazard appears from multiple sheets, keep the most complete."""
    by_harm: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        key = (row.get("harm") or "").strip().lower()
        if not key:
            by_harm.setdefault("__empty__" + str(id(row)), []).append(row)
            continue
        by_harm.setdefault(key, []).append(row)

    deduped: List[Dict[str, Any]] = []
    for _key, group in by_harm.items():
        if len(group) == 1:
            deduped.append(group[0])
        else:
            best = max(group, key=_row_completeness)
            for other in group:
                if other is best:
                    continue
                for k, v in other.items():
                    if (best.get(k) is None or str(best.get(k, "")).strip() == "") and v is not None:
                        best[k] = v
            deduped.append(best)
    return deduped


def _is_valid_hazard_row(row: Dict[str, Any]) -> bool:
    """A row must have at least harm or hazard_description to be real risk data."""
    for key in ("harm", "hazard_description", "hazard_id"):
        val = row.get(key)
        if val is not None and str(val).strip():
            text = str(val).strip().lower()
            if len(text) > 2 and not text.startswith("section ") and text not in (
                "value", "output_section", "field",
            ):
                return True
    return False


def _is_metadata_row(row: Dict[str, Any]) -> bool:
    """Detect rows that are clearly PSUR metadata, not hazard data."""
    populated = {k: v for k, v in row.items() if v is not None and str(v).strip()}
    if len(populated) <= 1:
        return True
    for val in populated.values():
        text = str(val).strip()
        if any(marker in text.lower() for marker in (
            "psur", "section ", "output_section", "yyyy-mm-dd",
            "version of this", "reporting period", "data collection",
            "product_family, product_codes",
            "all psur_input columns", "all risk columns",
        )):
            return True
    return False


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
        if extraction.source_path.endswith(".json") and extraction.rows:
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

    raw_count = len(all_rows)
    all_rows = [r for r in all_rows
                if _is_valid_hazard_row(r) and not _is_metadata_row(r)]
    filtered = raw_count - len(all_rows)

    for row in all_rows:
        if not row.get("probability_after") and row.get("probability_before"):
            row["probability_after"] = row["probability_before"]
        if not row.get("risk_level_after") and row.get("risk_level_before"):
            row["risk_level_after"] = row["risk_level_before"]
        if not row.get("hazard_description") and row.get("hazard_category"):
            row["hazard_description"] = row["hazard_category"]

    all_rows = _deduplicate_hazards(all_rows)

    issues.extend(check_required_fields(all_rows, RACT_SCHEMA, candidates[0].source_path))
    completeness = compute_completeness(all_rows, RACT_SCHEMA)

    notes = [f"Mapped {len(all_rows)} RACT hazard rows from {len(candidates)} candidate(s)."]
    if filtered:
        notes.append(f"Filtered {filtered} non-hazard row(s) (metadata/empty).")

    return MapperOutput(
        canonical_file=RACT_SCHEMA.filename,
        rows=all_rows,
        mapping_decisions=all_decisions,
        traces=[],
        completeness=completeness,
        notes=notes,
        issues=issues,
    )
