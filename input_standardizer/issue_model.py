"""
Issue model for the PSUR Input Standardizer.

All issues emitted by the pipeline are typed PreparerIssues with stable codes.
Severity governs downstream readiness gating.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"
    INFO = "INFO"


class IssueCode(str, Enum):
    # Discovery / classification
    UNKNOWN_FILE = "IS-001"
    DUPLICATE_CANDIDATE = "IS-002"
    UNSUPPORTED_FORMAT = "IS-003"
    LOW_CONFIDENCE_CLASSIFICATION = "IS-004"
    MULTIPLE_SHEET_AMBIGUITY = "IS-005"

    # Required canonical targets
    MISSING_REQUIRED_TARGET = "IS-010"
    MISSING_STRONGLY_RECOMMENDED_TARGET = "IS-011"

    # Field-level
    MISSING_REQUIRED_FIELD = "IS-020"
    MISSING_RECOMMENDED_FIELD = "IS-021"
    LOW_CONFIDENCE_MAPPING = "IS-022"
    AMBIGUOUS_HEADER = "IS-023"
    UNRESOLVED_HEADER = "IS-024"
    CONFLICTING_FIELD_VALUES = "IS-025"
    FIELD_INFERRED_FROM_UNSTRUCTURED = "IS-026"
    PLACEHOLDER_VALUE_DETECTED = "IS-027"

    # Normalization
    DATE_PARSE_FAILURE = "IS-030"
    COUNTRY_NORMALIZATION_FAILURE = "IS-031"
    REGION_NORMALIZATION_FAILURE = "IS-032"
    ENUM_NORMALIZATION_FAILURE = "IS-033"
    BOOLEAN_NORMALIZATION_FAILURE = "IS-034"

    # Identity
    UNRESOLVED_DEVICE_IDENTITY = "IS-040"
    UNRESOLVED_REGULATORY_METADATA = "IS-041"

    # Deduplication
    DUPLICATE_COMPLAINT_IDENTITY = "IS-050"
    DUPLICATE_CAPA_IDENTITY = "IS-051"
    DUPLICATE_FSCA_IDENTITY = "IS-052"

    # Document extraction
    SECTION_NOT_FOUND_IN_DOCUMENT = "IS-060"
    LLM_EXTRACTION_LOW_CONFIDENCE = "IS-061"

    # Schema
    SCHEMA_VALIDATION_FAILURE = "IS-070"
    UNIQUENESS_VIOLATION = "IS-071"

    # FSCA-specific
    CLOSED_FSCA_MISSING_EFFECTIVENESS = "IS-080"

    # Readiness
    READINESS_GATE_FAILED = "IS-090"


# Human-readable default titles keyed by code
_ISSUE_TITLES: Dict[IssueCode, str] = {
    IssueCode.UNKNOWN_FILE: "File could not be classified",
    IssueCode.DUPLICATE_CANDIDATE: "Multiple candidate files for same canonical target",
    IssueCode.UNSUPPORTED_FORMAT: "File format not supported for extraction",
    IssueCode.LOW_CONFIDENCE_CLASSIFICATION: "Low-confidence file classification",
    IssueCode.MULTIPLE_SHEET_AMBIGUITY: "Multiple sheets with ambiguous target type",
    IssueCode.MISSING_REQUIRED_TARGET: "Required canonical target is absent",
    IssueCode.MISSING_STRONGLY_RECOMMENDED_TARGET: "Strongly recommended canonical target is absent",
    IssueCode.MISSING_REQUIRED_FIELD: "Required canonical field is missing",
    IssueCode.MISSING_RECOMMENDED_FIELD: "Recommended canonical field is missing",
    IssueCode.LOW_CONFIDENCE_MAPPING: "Low-confidence header-to-field mapping",
    IssueCode.AMBIGUOUS_HEADER: "Header maps to multiple canonical fields",
    IssueCode.UNRESOLVED_HEADER: "Source header could not be mapped to any canonical field",
    IssueCode.CONFLICTING_FIELD_VALUES: "Conflicting values for the same field across sources",
    IssueCode.FIELD_INFERRED_FROM_UNSTRUCTURED: "Field value inferred from unstructured document text",
    IssueCode.PLACEHOLDER_VALUE_DETECTED: "Placeholder or template value detected in field",
    IssueCode.DATE_PARSE_FAILURE: "Date value could not be parsed to ISO format",
    IssueCode.COUNTRY_NORMALIZATION_FAILURE: "Country value could not be normalized",
    IssueCode.REGION_NORMALIZATION_FAILURE: "Region value could not be normalized",
    IssueCode.ENUM_NORMALIZATION_FAILURE: "Enum value could not be normalized to canonical set",
    IssueCode.BOOLEAN_NORMALIZATION_FAILURE: "Boolean value could not be normalized",
    IssueCode.UNRESOLVED_DEVICE_IDENTITY: "Device identity is not sufficiently resolved",
    IssueCode.UNRESOLVED_REGULATORY_METADATA: "Required regulatory metadata is unresolved",
    IssueCode.DUPLICATE_COMPLAINT_IDENTITY: "Duplicate complaint identifiers detected",
    IssueCode.DUPLICATE_CAPA_IDENTITY: "Duplicate CAPA identifiers detected",
    IssueCode.DUPLICATE_FSCA_IDENTITY: "Duplicate FSCA identifiers detected",
    IssueCode.SECTION_NOT_FOUND_IN_DOCUMENT: "Expected section not found in document",
    IssueCode.LLM_EXTRACTION_LOW_CONFIDENCE: "LLM-assisted extraction produced low-confidence result",
    IssueCode.SCHEMA_VALIDATION_FAILURE: "Output failed canonical schema validation",
    IssueCode.UNIQUENESS_VIOLATION: "Uniqueness constraint violated in canonical output",
    IssueCode.CLOSED_FSCA_MISSING_EFFECTIVENESS: "Closed FSCA is missing effectiveness assessment",
    IssueCode.READINESS_GATE_FAILED: "Package failed readiness gate for downstream PSUR generation",
}


@dataclass
class PreparerIssue:
    severity: Severity
    code: IssueCode
    detail: str
    source_files: List[str] = field(default_factory=list)
    canonical_target: Optional[str] = None
    field_name: Optional[str] = None
    suggested_action: Optional[str] = None

    @property
    def title(self) -> str:
        return _ISSUE_TITLES.get(self.code, self.code.value)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity.value,
            "code": self.code.value,
            "title": self.title,
            "detail": self.detail,
            "source_files": self.source_files,
            "canonical_target": self.canonical_target,
            "field_name": self.field_name,
            "suggested_action": self.suggested_action,
        }


def make_issue(
    severity: Severity,
    code: IssueCode,
    detail: str,
    *,
    source_files: Optional[List[str]] = None,
    canonical_target: Optional[str] = None,
    field_name: Optional[str] = None,
    suggested_action: Optional[str] = None,
) -> PreparerIssue:
    return PreparerIssue(
        severity=severity,
        code=code,
        detail=detail,
        source_files=source_files or [],
        canonical_target=canonical_target,
        field_name=field_name,
        suggested_action=suggested_action,
    )
