"""
Core domain contracts for the PSUR Input Standardizer.

These dataclasses define the typed boundaries between every layer of the pipeline.
No business logic lives here — only structural definitions.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ClassifierMethod(str, Enum):
    FILENAME = "filename"
    HEADER_SIGNATURE = "header_signature"
    SHEET_NAME = "sheet_name"
    JSON_KEY_STRUCTURE = "json_key_structure"
    CONTENT_HEURISTIC = "content_heuristic"
    LLM_ASSISTED = "llm_assisted"
    UNRESOLVED = "unresolved"


class MappingMethod(str, Enum):
    EXACT_CANONICAL = "exact_canonical"
    EXACT_ALIAS = "exact_alias"
    NORMALIZED_ALIAS = "normalized_alias"
    FUZZY = "fuzzy"
    LLM_ASSISTED = "llm_assisted"
    UNRESOLVED = "unresolved"


class CanonicalType(str, Enum):
    SALES = "sales"
    COMPLAINTS = "complaints"
    CAPA = "capa"
    FSCA = "fsca"
    DEVICE_CONTEXT = "device_context"
    RACT = "ract"
    PREVIOUS_PSUR = "previous_psur"
    PMS_PLAN = "pms_plan"
    PMCF = "pmcf"
    LITERATURE = "literature"
    EXTERNAL_DB = "external_db"
    CODING_DICTIONARY = "coding_dictionary"
    CER = "cer"
    IFU = "ifu"
    RMF = "rmf"
    ANALYSIS_WORKBOOK = "analysis_workbook"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Phase 1 contracts
# ---------------------------------------------------------------------------

@dataclass
class FileClassification:
    source_path: str
    extension: str
    detected_type: CanonicalType
    confidence: float                       # 0.0–1.0
    classifier_method: ClassifierMethod
    evidence: List[str]                     # human-readable evidence strings
    sheet_names: List[str] = field(default_factory=list)
    candidate_headers: List[str] = field(default_factory=list)
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = dataclasses.asdict(self)
        d["detected_type"] = self.detected_type.value
        d["classifier_method"] = self.classifier_method.value
        return d


@dataclass
class FieldTrace:
    canonical_file: str
    canonical_field: str
    source_file: str
    source_location: str              # e.g. "row 7, col B" or "section 3.2"
    source_key_or_excerpt: str        # raw original value or snippet
    transform_applied: str            # e.g. "date_iso", "bool_normalize", "none"
    confidence: float
    llm_used: bool
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class MappingDecision:
    canonical_field: str
    mapped_from: str                  # source header or key
    method: MappingMethod
    confidence: float
    llm_used: bool
    alternatives_considered: List[str] = field(default_factory=list)
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = dataclasses.asdict(self)
        d["method"] = self.method.value
        return d


@dataclass
class ReadinessResult:
    ready_for_psur_pipeline: bool
    blocking_issues: List[str]
    major_issues: List[str]
    minor_issues: List[str]
    informational_issues: List[str]
    summary: str
    canonical_files_produced: List[str] = field(default_factory=list)
    missing_strongly_recommended: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class ExtractionResult:
    """Raw structured output from an extractor before mapping."""
    source_path: str
    source_type: CanonicalType
    headers: List[str]                # column names (tabular) or top-level keys (JSON)
    rows: List[Dict[str, Any]]        # list of row dicts (tabular) or [obj] (JSON/document)
    header_row_index: Optional[int]   # 0-based row index where headers were found
    sheet_name: Optional[str]
    extraction_notes: List[str] = field(default_factory=list)
    raw_text_snippets: Dict[str, str] = field(default_factory=dict)  # section -> text

    def to_dict(self) -> Dict[str, Any]:
        d = dataclasses.asdict(self)
        d["source_type"] = self.source_type.value
        return d


@dataclass
class MapperOutput:
    """Typed return value from every domain mapper."""
    canonical_file: str
    rows: List[Dict[str, Any]]         # ready-to-write canonical rows (tabular) or [obj] (JSON)
    mapping_decisions: List[MappingDecision]
    traces: List[FieldTrace]
    completeness: float                 # 0.0–1.0 fraction of required fields populated
    notes: List[str] = field(default_factory=list)
    issues: List[Any] = field(default_factory=list)  # List[PreparerIssue] — typed as Any to avoid circular import
