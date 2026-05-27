"""
Readiness gate for the PSUR Input Standardizer.

Produces a ReadinessResult that downstream systems can trust as the
authoritative go/no-go signal before PSUR generation begins.

Rules are explicit and easy to adjust — see READINESS_RULES below.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from .contracts import ReadinessResult
from .issue_model import IssueCode, PreparerIssue, Severity
from .schema_registry import (
    CORE_REQUIRED_TYPES,
    STRONGLY_RECOMMENDED_TYPES,
    ALL_SCHEMAS,
)

# ---------------------------------------------------------------------------
# Tunable thresholds
# ---------------------------------------------------------------------------

# Minimum completeness fraction for a produced canonical target to pass
MIN_COMPLETENESS_REQUIRED = 0.70  # required fields only

# Minimum confidence for device identity to be considered resolved
MIN_DEVICE_IDENTITY_CONFIDENCE = 0.60

# Issue codes that unconditionally block the gate
BLOCKING_ISSUE_CODES: Set[IssueCode] = {
    IssueCode.MISSING_REQUIRED_TARGET,
    IssueCode.SCHEMA_VALIDATION_FAILURE,
    IssueCode.READINESS_GATE_FAILED,
}

# Issue codes that are blocking only if severity is CRITICAL
CRITICAL_BLOCKING_CODES: Set[IssueCode] = {
    IssueCode.UNRESOLVED_DEVICE_IDENTITY,
    IssueCode.CONFLICTING_FIELD_VALUES,
    IssueCode.UNIQUENESS_VIOLATION,
}


def evaluate(
    issues: List[PreparerIssue],
    produced_files: Set[str],
    completeness_by_file: Dict[str, float],
) -> ReadinessResult:
    """
    Evaluate readiness for downstream PSUR generation.

    Args:
        issues: All issues emitted by the pipeline run.
        produced_files: Set of canonical filenames that were actually written.
        completeness_by_file: {canonical_filename -> completeness fraction}

    Returns:
        ReadinessResult with categorized issues and ready/not-ready verdict.
    """
    blocking: List[str] = []
    major: List[str] = []
    minor: List[str] = []
    informational: List[str] = []

    # --- Step 1: Categorise all issues ---
    for issue in issues:
        msg = f"[{issue.code.value}] {issue.title}: {issue.detail}"
        if issue.severity == Severity.CRITICAL:
            blocking.append(msg)
        elif issue.severity == Severity.MAJOR:
            major.append(msg)
        elif issue.severity == Severity.MINOR:
            minor.append(msg)
        else:
            informational.append(msg)

    # --- Step 2: Check core required targets are produced ---
    core_schema_filenames = {
        ALL_SCHEMAS[ct].filename for ct in CORE_REQUIRED_TYPES if ct in ALL_SCHEMAS
    }
    for filename in sorted(core_schema_filenames):
        if filename not in produced_files:
            blocking.append(
                f"[GATE-001] Core required canonical file '{filename}' was not produced. "
                f"Downstream PSUR generation cannot proceed without this file."
            )

    # --- Step 3: Check completeness for produced required targets ---
    for ct in CORE_REQUIRED_TYPES:
        schema = ALL_SCHEMAS.get(ct)
        if not schema:
            continue
        if schema.filename not in produced_files:
            continue  # already flagged above
        completeness = completeness_by_file.get(schema.filename, 0.0)
        if completeness < MIN_COMPLETENESS_REQUIRED:
            blocking.append(
                f"[GATE-002] '{schema.filename}' completeness is {completeness:.0%}, "
                f"below minimum {MIN_COMPLETENESS_REQUIRED:.0%}. "
                f"Required fields are missing."
            )

    # --- Step 4: Check strongly recommended targets ---
    rec_schema_filenames = {
        ALL_SCHEMAS[ct].filename for ct in STRONGLY_RECOMMENDED_TYPES if ct in ALL_SCHEMAS
    }
    for filename in sorted(rec_schema_filenames):
        if filename not in produced_files:
            major.append(
                f"[GATE-003] Strongly recommended file '{filename}' was not produced. "
                f"PSUR may be incomplete."
            )

    # --- Step 5: Check device identity resolution ---
    device_context_issues = [
        i for i in issues
        if i.canonical_target == "device_context.json"
        and i.code == IssueCode.MISSING_REQUIRED_FIELD
        and i.field_name in (
            "device_trade_names", "basic_udi_di_or_device_family_name",
            "eu_mdr_classification_and_rule", "intended_purpose",
        )
    ]
    if device_context_issues:
        blocking.append(
            f"[GATE-004] Device identity is not sufficiently resolved. "
            f"Critical device_context.json fields are missing: "
            f"{[i.field_name for i in device_context_issues]}."
        )

    # --- Final verdict ---
    ready = len(blocking) == 0

    if ready:
        summary = (
            f"Package is READY for downstream PSUR generation. "
            f"{len(produced_files)} canonical files produced. "
            f"{len(major)} major issue(s), {len(minor)} minor issue(s)."
        )
    else:
        summary = (
            f"Package is NOT READY for downstream PSUR generation. "
            f"{len(blocking)} blocking issue(s) must be resolved. "
            f"{len(major)} major, {len(minor)} minor, {len(informational)} informational."
        )

    return ReadinessResult(
        ready_for_psur_pipeline=ready,
        blocking_issues=blocking,
        major_issues=major,
        minor_issues=minor,
        informational_issues=informational,
        summary=summary,
        canonical_files_produced=sorted(produced_files),
        missing_strongly_recommended=[
            filename for filename in sorted(rec_schema_filenames)
            if filename not in produced_files
        ],
    )
