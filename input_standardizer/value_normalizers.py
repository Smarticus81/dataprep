"""
Value normalizers for the PSUR Input Standardizer.

Each normalizer returns (normalized_value, transform_name, issue_or_None).
The original raw value is always preserved separately in traceability.

Rules:
- Never silently fabricate a regulatory value.
- If normalization is uncertain, return (None, name, issue) with lower confidence.
- Placeholder detection returns (None, "placeholder_detected", issue).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .issue_model import IssueCode, PreparerIssue, Severity, make_issue


# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

NormalizeResult = Tuple[Any, str, Optional[PreparerIssue]]


# ---------------------------------------------------------------------------
# Placeholder detection
# ---------------------------------------------------------------------------

_PLACEHOLDER_TOKENS = frozenset({
    "", "tbd", "n/a", "na", "none", "pending", "unknown", "xxx", "?", "-",
    "not applicable", "to be determined", "to be confirmed", "tbc", "null",
    "nil", "missing", "not provided", "not available", "n.a.", "n/a.",
})


def is_placeholder(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip().lower() in _PLACEHOLDER_TOKENS


def normalize_placeholder(
    raw: Any,
    canonical_file: str,
    field_name: str,
    source_file: str,
) -> Optional[NormalizeResult]:
    """Return a NormalizeResult if value is a placeholder, else None."""
    if is_placeholder(raw):
        issue = make_issue(
            Severity.MINOR,
            IssueCode.PLACEHOLDER_VALUE_DETECTED,
            f"Placeholder value '{raw}' detected for field '{field_name}'.",
            source_files=[source_file],
            canonical_target=canonical_file,
            field_name=field_name,
            suggested_action=f"Provide a real value for '{field_name}' in the source data.",
        )
        return None, "placeholder_detected", issue
    return None  # not a placeholder


# ---------------------------------------------------------------------------
# Date normalization -> ISO YYYY-MM-DD
# ---------------------------------------------------------------------------

_DATE_FORMATS = [
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%m-%d-%Y",
    "%d.%m.%Y",
    "%Y/%m/%d",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%Y%m%d",
    "%d/%m/%y",
    "%m/%d/%y",
]


def normalize_date(
    raw: Any,
    canonical_file: str,
    field_name: str,
    source_file: str,
) -> NormalizeResult:
    if is_placeholder(raw):
        issue = make_issue(
            Severity.MINOR,
            IssueCode.DATE_PARSE_FAILURE,
            f"Placeholder date value for field '{field_name}'.",
            source_files=[source_file],
            canonical_target=canonical_file,
            field_name=field_name,
        )
        return None, "placeholder_detected", issue

    raw_str = str(raw).strip()

    # Try datetime object directly (from openpyxl/pandas)
    if isinstance(raw, datetime):
        return raw.strftime("%Y-%m-%d"), "date_iso", None

    # Strip time component if present
    date_part = raw_str.split("T")[0].split(" ")[0]

    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(date_part, fmt)
            return dt.strftime("%Y-%m-%d"), "date_iso", None
        except ValueError:
            continue

    # Fallback: try parsing with partial year
    match = re.search(r"(\d{4})", raw_str)
    if match:
        issue = make_issue(
            Severity.MINOR,
            IssueCode.DATE_PARSE_FAILURE,
            f"Could not fully parse date '{raw_str}' for field '{field_name}'. "
            f"Year {match.group(1)} extracted only.",
            source_files=[source_file],
            canonical_target=canonical_file,
            field_name=field_name,
            suggested_action="Ensure dates are in ISO (YYYY-MM-DD) or DD/MM/YYYY format.",
        )
        return f"{match.group(1)}-??-??", "date_partial", issue

    issue = make_issue(
        Severity.MINOR,
        IssueCode.DATE_PARSE_FAILURE,
        f"Could not parse date value '{raw_str}' for field '{field_name}'.",
        source_files=[source_file],
        canonical_target=canonical_file,
        field_name=field_name,
        suggested_action="Ensure dates are in ISO (YYYY-MM-DD) or DD/MM/YYYY format.",
    )
    return None, "date_parse_failure", issue


# ---------------------------------------------------------------------------
# Boolean normalization
# ---------------------------------------------------------------------------

_TRUE_VALUES = frozenset({
    "yes", "y", "true", "t", "1", "x", "checked", "positive",
    "si", "oui", "ja", "ja.", "true.", "yes.", "reportable",
})
_FALSE_VALUES = frozenset({
    "no", "n", "false", "f", "0", "unchecked", "negative",
    "non", "non.", "no.", "false.", "not reportable",
})


def normalize_boolean(
    raw: Any,
    canonical_file: str,
    field_name: str,
    source_file: str,
) -> NormalizeResult:
    if isinstance(raw, bool):
        return raw, "bool_passthrough", None
    if isinstance(raw, (int, float)):
        return bool(raw), "bool_from_numeric", None

    s = str(raw).strip().lower().rstrip(".")
    if s in _TRUE_VALUES:
        return True, "bool_normalize", None
    if s in _FALSE_VALUES:
        return False, "bool_normalize", None

    if is_placeholder(raw):
        return None, "placeholder_detected", None

    issue = make_issue(
        Severity.MINOR,
        IssueCode.BOOLEAN_NORMALIZATION_FAILURE,
        f"Could not normalize value '{raw}' to boolean for field '{field_name}'.",
        source_files=[source_file],
        canonical_target=canonical_file,
        field_name=field_name,
        suggested_action="Use Yes/No or True/False values.",
    )
    return None, "bool_normalize_failure", issue


# ---------------------------------------------------------------------------
# Integer / float normalization
# ---------------------------------------------------------------------------

def normalize_int(
    raw: Any,
    canonical_file: str,
    field_name: str,
    source_file: str,
) -> NormalizeResult:
    if is_placeholder(raw):
        return None, "placeholder_detected", None
    try:
        cleaned = re.sub(r"[,\s]", "", str(raw).strip())
        return int(float(cleaned)), "int_normalize", None
    except (ValueError, TypeError):
        issue = make_issue(
            Severity.MINOR,
            IssueCode.ENUM_NORMALIZATION_FAILURE,
            f"Could not normalize value '{raw}' to integer for field '{field_name}'.",
            source_files=[source_file],
            canonical_target=canonical_file,
            field_name=field_name,
        )
        return None, "int_normalize_failure", issue


def normalize_float(
    raw: Any,
    canonical_file: str,
    field_name: str,
    source_file: str,
) -> NormalizeResult:
    if is_placeholder(raw):
        return None, "placeholder_detected", None
    try:
        cleaned = re.sub(r"[,\s]", "", str(raw).strip().rstrip("%"))
        return float(cleaned), "float_normalize", None
    except (ValueError, TypeError):
        issue = make_issue(
            Severity.MINOR,
            IssueCode.ENUM_NORMALIZATION_FAILURE,
            f"Could not normalize value '{raw}' to float for field '{field_name}'.",
            source_files=[source_file],
            canonical_target=canonical_file,
            field_name=field_name,
        )
        return None, "float_normalize_failure", issue


# ---------------------------------------------------------------------------
# Enum normalization
# ---------------------------------------------------------------------------

def normalize_enum(
    raw: Any,
    enum_values: Tuple[str, ...],
    canonical_file: str,
    field_name: str,
    source_file: str,
) -> NormalizeResult:
    if is_placeholder(raw):
        return None, "placeholder_detected", None

    s = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
    # Direct match
    for ev in enum_values:
        if s == ev.lower():
            return ev, "enum_exact", None
    # Partial match
    for ev in enum_values:
        if ev.lower() in s or s in ev.lower():
            return ev, "enum_partial", None

    issue = make_issue(
        Severity.MINOR,
        IssueCode.ENUM_NORMALIZATION_FAILURE,
        f"Value '{raw}' does not match canonical enum for field '{field_name}'. "
        f"Accepted values: {', '.join(enum_values)}.",
        source_files=[source_file],
        canonical_target=canonical_file,
        field_name=field_name,
        suggested_action=f"Use one of: {', '.join(enum_values)}.",
    )
    return str(raw).strip(), "enum_passthrough", issue


# ---------------------------------------------------------------------------
# Country normalization
# ---------------------------------------------------------------------------

_COUNTRY_ALIASES: Dict[str, str] = {
    # ISO 3166-1 alpha-2 and common aliases -> canonical country name
    "gb": "United Kingdom", "uk": "United Kingdom", "united kingdom": "United Kingdom",
    "great britain": "United Kingdom", "england": "United Kingdom",
    "de": "Germany", "germany": "Germany", "deutschland": "Germany",
    "fr": "France", "france": "France",
    "it": "Italy", "italy": "Italy", "italia": "Italy",
    "es": "Spain", "spain": "Spain", "espana": "Spain",
    "nl": "Netherlands", "netherlands": "Netherlands", "holland": "Netherlands",
    "be": "Belgium", "belgium": "Belgium",
    "se": "Sweden", "sweden": "Sweden",
    "no": "Norway", "norway": "Norway",
    "dk": "Denmark", "denmark": "Denmark",
    "fi": "Finland", "finland": "Finland",
    "pt": "Portugal", "portugal": "Portugal",
    "at": "Austria", "austria": "Austria",
    "ch": "Switzerland", "switzerland": "Switzerland",
    "ie": "Ireland", "ireland": "Ireland",
    "pl": "Poland", "poland": "Poland",
    "cz": "Czech Republic", "czech republic": "Czech Republic", "czechia": "Czech Republic",
    "hu": "Hungary", "hungary": "Hungary",
    "ro": "Romania", "romania": "Romania",
    "gr": "Greece", "greece": "Greece",
    "us": "United States", "usa": "United States", "united states": "United States",
    "united states of america": "United States",
    "ca": "Canada", "canada": "Canada",
    "au": "Australia", "australia": "Australia",
    "nz": "New Zealand", "new zealand": "New Zealand",
    "jp": "Japan", "japan": "Japan",
    "cn": "China", "china": "China",
    "in": "India", "india": "India",
    "br": "Brazil", "brazil": "Brazil",
    "mx": "Mexico", "mexico": "Mexico",
    "za": "South Africa", "south africa": "South Africa",
}

_COUNTRY_TO_REGION: Dict[str, str] = {
    "United Kingdom": "UK",
    "Germany": "EU", "France": "EU", "Italy": "EU", "Spain": "EU",
    "Netherlands": "EU", "Belgium": "EU", "Sweden": "EU", "Norway": "EU",
    "Denmark": "EU", "Finland": "EU", "Portugal": "EU", "Austria": "EU",
    "Switzerland": "ROW", "Ireland": "EU", "Poland": "EU", "Czech Republic": "EU",
    "Hungary": "EU", "Romania": "EU", "Greece": "EU",
    "United States": "USA", "Canada": "North America",
    "Australia": "APAC", "New Zealand": "APAC", "Japan": "APAC", "China": "APAC",
    "India": "APAC", "Brazil": "LatAm", "Mexico": "LatAm",
    "South Africa": "ROW",
}


def normalize_country(
    raw: Any,
    canonical_file: str,
    field_name: str,
    source_file: str,
) -> NormalizeResult:
    if is_placeholder(raw):
        return None, "placeholder_detected", None
    s = str(raw).strip().lower()
    canonical = _COUNTRY_ALIASES.get(s)
    if canonical:
        return canonical, "country_normalize", None
    # Title-case passthrough with warning
    normalized = str(raw).strip()
    issue = make_issue(
        Severity.MINOR,
        IssueCode.COUNTRY_NORMALIZATION_FAILURE,
        f"Country value '{raw}' could not be normalized to a known canonical country name. "
        f"Passed through as-is.",
        source_files=[source_file],
        canonical_target=canonical_file,
        field_name=field_name,
        suggested_action="Use ISO 3166-1 country name or 2-letter code.",
    )
    return normalized, "country_passthrough", issue


def country_to_region(country: str) -> Optional[str]:
    return _COUNTRY_TO_REGION.get(country)


# ---------------------------------------------------------------------------
# Status normalization
# ---------------------------------------------------------------------------

_CAPA_STATUS_ALIASES: Dict[str, str] = {
    "open": "open", "opened": "open", "active": "open", "new": "open",
    "closed": "closed", "complete": "closed", "completed": "closed", "done": "closed",
    "in progress": "in_progress", "in_progress": "in_progress", "wip": "in_progress",
    "in work": "in_progress", "ongoing": "in_progress",
    "pending verification": "pending_verification", "pending_verification": "pending_verification",
    "awaiting verification": "pending_verification", "verification pending": "pending_verification",
    "cancelled": "cancelled", "canceled": "cancelled", "voided": "cancelled",
    "on hold": "on_hold", "on_hold": "on_hold", "hold": "on_hold",
}

_FSCA_STATUS_ALIASES: Dict[str, str] = {
    "open": "open", "opened": "open", "active": "open",
    "closed": "closed", "complete": "closed", "completed": "closed",
    "ongoing": "ongoing", "in progress": "ongoing", "in_progress": "ongoing",
    "completed": "completed",
}


def normalize_capa_status(
    raw: Any,
    canonical_file: str,
    field_name: str,
    source_file: str,
) -> NormalizeResult:
    if is_placeholder(raw):
        return None, "placeholder_detected", None
    s = str(raw).strip().lower()
    normalized = _CAPA_STATUS_ALIASES.get(s)
    if normalized:
        return normalized, "status_normalize", None
    return normalize_enum(
        raw,
        ("open", "closed", "in_progress", "pending_verification", "cancelled", "on_hold"),
        canonical_file, field_name, source_file,
    )


# ---------------------------------------------------------------------------
# String normalization (whitespace/case cleanup)
# ---------------------------------------------------------------------------

def normalize_str(raw: Any) -> str:
    if raw is None:
        return ""
    return " ".join(str(raw).split())


# ---------------------------------------------------------------------------
# Dispatch normalizer by dtype
# ---------------------------------------------------------------------------

from .schema_registry import FieldDef  # noqa: E402 (avoid circular at module level)


def normalize_value(
    raw: Any,
    fdef: FieldDef,
    canonical_file: str,
    source_file: str,
) -> NormalizeResult:
    placeholder_result = normalize_placeholder(raw, canonical_file, fdef.name, source_file)
    if placeholder_result is not None:
        return placeholder_result

    dtype = fdef.dtype
    if dtype == "date":
        return normalize_date(raw, canonical_file, fdef.name, source_file)
    if dtype == "bool":
        return normalize_boolean(raw, canonical_file, fdef.name, source_file)
    if dtype == "int":
        return normalize_int(raw, canonical_file, fdef.name, source_file)
    if dtype == "float":
        return normalize_float(raw, canonical_file, fdef.name, source_file)
    if dtype == "enum" and fdef.enum_values:
        return normalize_enum(raw, fdef.enum_values, canonical_file, fdef.name, source_file)
    # str or unknown dtype: whitespace normalize only
    return normalize_str(raw), "str_normalize", None
