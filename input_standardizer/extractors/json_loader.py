"""
JSON extractor.

Capabilities:
- Load and inspect top-level keys
- Detect likely canonical type from key signatures
- Validate structure against canonical schema candidates
- Wrap arrays as row dicts; wrap objects as single-row list
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Set

from ..contracts import CanonicalType, ExtractionResult
from ..schema_registry import ALL_SCHEMAS

logger = logging.getLogger(__name__)


# Key signatures that strongly suggest a canonical type (subset match is enough)
_JSON_SIGNATURES: Dict[str, Set[str]] = {
    "device_context": {
        "device_trade_names", "intended_purpose", "eu_mdr_classification_and_rule",
        "notified_body_name_and_id", "indications_for_use",
    },
    "ract": {"hazard_id", "harm", "severity", "risk_level_before", "risk_control"},
    "previous_psur": {"period", "cadence", "complaint_summary", "serious_incidents_count"},
    "pms_plan": {"proactive_activities", "reactive_activities", "psur_cadence"},
    "pmcf": {"pmcf_plan_reference", "activities"},
    "literature": {"search_period", "records_screened", "relevant_articles_identified"},
    "coding_dictionary": {"AnnexA", "AnnexF"},
}


def _detect_type_from_keys(top_keys: Set[str]) -> Optional[str]:
    best_type: Optional[str] = None
    best_overlap = 0
    for ct, sig_keys in _JSON_SIGNATURES.items():
        overlap = len(sig_keys & top_keys)
        if overlap > best_overlap:
            best_overlap = overlap
            best_type = ct
    if best_overlap >= 2:
        return best_type
    return None


def _flatten_to_rows(obj: Any) -> List[Dict[str, Any]]:
    """Normalize a JSON value to a list of dicts."""
    if isinstance(obj, list):
        if all(isinstance(item, dict) for item in obj):
            return obj
        return [{"value": item} for item in obj]
    if isinstance(obj, dict):
        return [obj]
    return [{"value": obj}]


def read_json(
    path: str,
    canonical_type: CanonicalType = CanonicalType.UNKNOWN,
) -> ExtractionResult:
    notes: List[str] = []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)

        top_keys: Set[str] = set()
        if isinstance(data, dict):
            top_keys = set(data.keys())
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            top_keys = set(data[0].keys())

        # Auto-detect type if not provided
        if canonical_type == CanonicalType.UNKNOWN:
            detected = _detect_type_from_keys(top_keys)
            if detected:
                try:
                    canonical_type = CanonicalType(detected)
                    notes.append(f"Auto-detected canonical type from JSON keys: {detected}")
                except ValueError:
                    pass

        rows = _flatten_to_rows(data)
        headers = list(top_keys) if top_keys else []

        return ExtractionResult(
            source_path=path,
            source_type=canonical_type,
            headers=headers,
            rows=rows,
            header_row_index=None,
            sheet_name=None,
            extraction_notes=notes,
        )
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error in {path}: {e}")
        return ExtractionResult(
            source_path=path,
            source_type=canonical_type,
            headers=[],
            rows=[],
            header_row_index=None,
            sheet_name=None,
            extraction_notes=[f"ERROR: JSON parse error: {e}"],
        )
    except Exception as e:
        logger.error(f"JSON load failed for {path}: {e}")
        return ExtractionResult(
            source_path=path,
            source_type=canonical_type,
            headers=[],
            rows=[],
            header_row_index=None,
            sheet_name=None,
            extraction_notes=[f"ERROR: {e}"],
        )
