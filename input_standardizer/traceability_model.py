"""
Traceability model for the PSUR Input Standardizer.

Every field of regulatory significance carries a FieldTrace that answers:
  - where did this value come from?
  - what transform was applied?
  - was an LLM involved?
  - how confident is the mapping?
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .contracts import FieldTrace


class TraceabilityStore:
    """
    Accumulates FieldTrace objects during the pipeline run and renders
    the structured source_traceability.json output.

    Structure of the serialised output:
    {
      "sales.csv": {
        "rows": [
          {"row_index": 0, "fields": {"date": {...FieldTrace...}, ...}}
        ]
      },
      "device_context.json": {
        "fields": {
          "intended_purpose": {...FieldTrace...}
        }
      }
    }
    """

    def __init__(self) -> None:
        # {canonical_file -> {row_index_or_None -> {field -> FieldTrace}}}
        self._tabular: Dict[str, Dict[int, Dict[str, FieldTrace]]] = {}
        # {canonical_file -> {field -> FieldTrace}}
        self._document: Dict[str, Dict[str, FieldTrace]] = {}

    def add_tabular(self, trace: FieldTrace, row_index: int) -> None:
        cf = trace.canonical_file
        self._tabular.setdefault(cf, {}).setdefault(row_index, {})[trace.canonical_field] = trace

    def add_document(self, trace: FieldTrace) -> None:
        cf = trace.canonical_file
        self._document.setdefault(cf, {})[trace.canonical_field] = trace

    def add(self, trace: FieldTrace, row_index: Optional[int] = None) -> None:
        if row_index is not None:
            self.add_tabular(trace, row_index)
        else:
            self.add_document(trace)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}

        for cf, row_map in self._tabular.items():
            rows = []
            for idx in sorted(row_map.keys()):
                rows.append({
                    "row_index": idx,
                    "fields": {
                        fname: trace.to_dict()
                        for fname, trace in sorted(row_map[idx].items())
                    },
                })
            out[cf] = {"rows": rows}

        for cf, field_map in self._document.items():
            out[cf] = {
                "fields": {
                    fname: trace.to_dict()
                    for fname, trace in sorted(field_map.items())
                }
            }

        return out

    def llm_field_count(self) -> int:
        count = 0
        for row_map in self._tabular.values():
            for field_map in row_map.values():
                count += sum(1 for t in field_map.values() if t.llm_used)
        for field_map in self._document.values():
            count += sum(1 for t in field_map.values() if t.llm_used)
        return count

    def total_field_count(self) -> int:
        count = 0
        for row_map in self._tabular.values():
            for field_map in row_map.values():
                count += len(field_map)
        for field_map in self._document.values():
            count += len(field_map)
        return count

    def low_confidence_count(self, threshold: float = 0.7) -> int:
        count = 0
        for row_map in self._tabular.values():
            for field_map in row_map.values():
                count += sum(1 for t in field_map.values() if t.confidence < threshold)
        for field_map in self._document.values():
            count += sum(1 for t in field_map.values() if t.confidence < threshold)
        return count


def make_trace(
    canonical_file: str,
    canonical_field: str,
    source_file: str,
    source_location: str,
    source_key_or_excerpt: str,
    transform_applied: str = "none",
    confidence: float = 1.0,
    llm_used: bool = False,
    notes: Optional[str] = None,
) -> FieldTrace:
    return FieldTrace(
        canonical_file=canonical_file,
        canonical_field=canonical_field,
        source_file=source_file,
        source_location=source_location,
        source_key_or_excerpt=source_key_or_excerpt,
        transform_applied=transform_applied,
        confidence=confidence,
        llm_used=llm_used,
        notes=notes,
    )
