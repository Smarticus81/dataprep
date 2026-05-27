"""
Manifest writer for the PSUR Input Standardizer.

Writes three always-present audit files to the canonical output directory:
  - preparer_manifest.json  (run metadata, classification summary, outputs)
  - preparer_issues.json    (all PreparerIssue objects)
  - source_traceability.json (all FieldTrace records)

Also writes domain CSV and JSON files produced by mappers.

All outputs use deterministic ordering (sorted keys/rows) for stable diffs.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from .contracts import FileClassification, MapperOutput, ReadinessResult
from .issue_model import PreparerIssue, Severity
from .traceability_model import TraceabilityStore


def _sorted_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, default=str)


def write_manifest(
    output_dir: str,
    run_id: str,
    input_dir: str,
    classifications: List[FileClassification],
    mapper_outputs: List[MapperOutput],
    readiness: ReadinessResult,
    issues: List[PreparerIssue],
    store: TraceabilityStore,
) -> str:
    """Write preparer_manifest.json. Returns the path written."""
    issue_counts = {
        "CRITICAL": sum(1 for i in issues if i.severity == Severity.CRITICAL),
        "MAJOR": sum(1 for i in issues if i.severity == Severity.MAJOR),
        "MINOR": sum(1 for i in issues if i.severity == Severity.MINOR),
        "INFO": sum(1 for i in issues if i.severity == Severity.INFO),
    }

    total_fields = store.total_field_count()
    llm_fields = store.llm_field_count()
    low_conf_fields = store.low_confidence_count()

    manifest = {
        "run_id": run_id,
        "run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "input_directory": input_dir,
        "output_directory": output_dir,
        "source_files_discovered": [
            {
                "path": c.source_path,
                "extension": c.extension,
                "detected_type": c.detected_type.value,
                "confidence": round(c.confidence, 3),
                "method": c.classifier_method.value,
            }
            for c in sorted(classifications, key=lambda c: c.source_path)
        ],
        "outputs_written": sorted(
            list(readiness.canonical_files_produced) + [
                "preparer_manifest.json",
                "preparer_issues.json",
                "source_traceability.json",
            ]
        ),
        "readiness": {
            "ready_for_psur_pipeline": readiness.ready_for_psur_pipeline,
            "summary": readiness.summary,
            "blocking_issue_count": len(readiness.blocking_issues),
            "major_issue_count": len(readiness.major_issues),
        },
        "issue_counts_by_severity": issue_counts,
        "confidence_summary": {
            "total_traced_fields": total_fields,
            "llm_assisted_fields": llm_fields,
            "llm_fraction": round(llm_fields / total_fields, 3) if total_fields else 0.0,
            "low_confidence_fields": low_conf_fields,
            "low_confidence_fraction": round(low_conf_fields / total_fields, 3) if total_fields else 0.0,
        },
        "mapper_completeness": {
            mo.canonical_file: round(mo.completeness, 3)
            for mo in sorted(mapper_outputs, key=lambda m: m.canonical_file)
        },
    }

    path = os.path.join(output_dir, "preparer_manifest.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_sorted_json(manifest))
    return path


def write_issues(
    output_dir: str,
    issues: List[PreparerIssue],
) -> str:
    """Write preparer_issues.json. Returns path."""
    severity_order = {Severity.CRITICAL: 0, Severity.MAJOR: 1, Severity.MINOR: 2, Severity.INFO: 3}
    sorted_issues = sorted(issues, key=lambda i: (severity_order.get(i.severity, 9), i.code.value))
    payload = {
        "total_issues": len(sorted_issues),
        "issues": [i.to_dict() for i in sorted_issues],
    }
    path = os.path.join(output_dir, "preparer_issues.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_sorted_json(payload))
    return path


def write_traceability(
    output_dir: str,
    store: TraceabilityStore,
) -> str:
    """Write source_traceability.json. Returns path."""
    path = os.path.join(output_dir, "source_traceability.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_sorted_json(store.to_dict()))
    return path


def write_canonical_csv(
    output_dir: str,
    output: MapperOutput,
) -> Optional[str]:
    """Write a canonical CSV file from a MapperOutput. Returns path or None if no rows."""
    if not output.rows:
        return None
    path = os.path.join(output_dir, output.canonical_file)
    # Collect all keys across rows for a stable column order
    all_keys: List[str] = []
    seen: Set[str] = set()
    for row in output.rows:
        for k in row:
            if k not in seen:
                all_keys.append(k)
                seen.add(k)

    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=all_keys, extrasaction="ignore")
        writer.writeheader()
        for row in output.rows:
            writer.writerow({k: ("" if row.get(k) is None else row[k]) for k in all_keys})
    return path


def write_canonical_json(
    output_dir: str,
    output: MapperOutput,
) -> Optional[str]:
    """Write a canonical JSON file from a MapperOutput. Returns path or None."""
    if not output.rows:
        return None
    path = os.path.join(output_dir, output.canonical_file)
    payload = output.rows[0] if len(output.rows) == 1 else output.rows
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_sorted_json(payload))
    return path


def write_all_outputs(
    output_dir: str,
    run_id: str,
    input_dir: str,
    classifications: List[FileClassification],
    mapper_outputs: List[MapperOutput],
    readiness: ReadinessResult,
    issues: List[PreparerIssue],
    store: TraceabilityStore,
    csv_files: Set[str],
) -> Dict[str, str]:
    """
    Write all canonical outputs and audit files to output_dir.

    Args:
        csv_files: Set of canonical filenames that should be written as CSV
                   (all others written as JSON).

    Returns:
        Dict mapping output type -> path written.
    """
    os.makedirs(output_dir, exist_ok=True)
    written: Dict[str, str] = {}

    # Domain files
    for output in mapper_outputs:
        if not output.rows:
            continue
        if output.canonical_file in csv_files:
            path = write_canonical_csv(output_dir, output)
        else:
            path = write_canonical_json(output_dir, output)
        if path:
            written[output.canonical_file] = path

    # Audit files (always written)
    written["source_traceability.json"] = write_traceability(output_dir, store)
    written["preparer_issues.json"] = write_issues(output_dir, issues)
    written["preparer_manifest.json"] = write_manifest(
        output_dir, run_id, input_dir, classifications,
        mapper_outputs, readiness, issues, store,
    )

    return written
