"""
Main pipeline for the PSUR Input Standardizer.

Orchestrates: discovery -> classification -> extraction -> mapping ->
              readiness gate -> output writing.

This module is the integration point — it wires together every other module
but contains no domain logic of its own.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from .classifier import discover_and_classify
from .contracts import CanonicalType, ExtractionResult, FileClassification, MapperOutput
from .extractors.csv_excel import read_csv, read_excel
from .extractors.docx_pdf import read_document
from .extractors.json_loader import read_json
from .issue_model import IssueCode, PreparerIssue, Severity, make_issue
from .manifest_writer import write_all_outputs
from .mappers import (
    capa_mapper,
    coding_dictionary_mapper,
    complaints_mapper,
    device_context_mapper,
    external_db_mapper,
    fsca_mapper,
    literature_mapper,
    pmcf_mapper,
    pms_plan_mapper,
    previous_psur_mapper,
    ract_mapper,
    sales_mapper,
)
from .readiness_gate import evaluate as evaluate_readiness
from .schema_registry import ALL_SCHEMAS, CanonicalSchema
from .traceability_model import TraceabilityStore

logger = logging.getLogger(__name__)

# Canonical files that must be written as CSV (all others as JSON)
_CSV_FILES: Set[str] = {
    "sales.csv",
    "complaints.csv",
    "capa.csv",
    "fsca.csv",
    "external_events.csv",
}

# Canonical types that map to document-type extractions (not structured data)
_DOCUMENT_TYPES: Set[CanonicalType] = {
    CanonicalType.CER,
    CanonicalType.IFU,
    CanonicalType.RMF,
}


def _extract(clf: FileClassification) -> List[ExtractionResult]:
    """Extract structured content from a classified file."""
    ext = clf.extension.lower()
    ctype = clf.detected_type
    results: List[ExtractionResult] = []

    try:
        if ext == "csv":
            results.append(read_csv(clf.source_path, ctype))
        elif ext in ("xlsx", "xls"):
            results.extend(read_excel(clf.source_path, ctype))
        elif ext == "json":
            results.append(read_json(clf.source_path, ctype))
        elif ext in ("docx", "doc", "pdf"):
            results.append(read_document(clf.source_path, ctype))
        elif ext == "txt":
            extraction = read_json(clf.source_path, ctype)
            if extraction.rows and not any("ERROR" in n for n in extraction.extraction_notes):
                results.append(extraction)
            else:
                results.append(read_document(clf.source_path, ctype))
        else:
            logger.warning(f"No extractor for extension '{ext}': {clf.source_path}")
    except Exception as e:
        logger.error(f"Extraction failed for {clf.source_path}: {e}")

    return results


def _group_by_type(
    classifications: List[FileClassification],
) -> Dict[CanonicalType, List[FileClassification]]:
    groups: Dict[CanonicalType, List[FileClassification]] = {}
    for clf in classifications:
        groups.setdefault(clf.detected_type, []).append(clf)
    return groups


def _warn_duplicates(
    groups: Dict[CanonicalType, List[FileClassification]],
) -> List[PreparerIssue]:
    issues: List[PreparerIssue] = []
    for ctype, clfs in groups.items():
        if ctype in (CanonicalType.UNKNOWN, CanonicalType.CER,
                     CanonicalType.IFU, CanonicalType.RMF):
            continue
        if len(clfs) > 1:
            issues.append(make_issue(
                Severity.MAJOR,
                IssueCode.DUPLICATE_CANDIDATE,
                f"Multiple source files classified as '{ctype.value}': "
                f"{[c.source_path for c in clfs]}. "
                f"All will be merged; verify there are no conflicts.",
                source_files=[c.source_path for c in clfs],
                canonical_target=ALL_SCHEMAS.get(ctype.value, type('', (), {'filename': ctype.value})()).filename  # type: ignore
                    if ctype.value in ALL_SCHEMAS else ctype.value,
                suggested_action="Provide a single authoritative source file for each data type.",
            ))
    return issues


def _warn_unknowns(classifications: List[FileClassification]) -> List[PreparerIssue]:
    issues: List[PreparerIssue] = []
    for clf in classifications:
        if clf.detected_type == CanonicalType.UNKNOWN:
            issues.append(make_issue(
                Severity.MINOR,
                IssueCode.UNKNOWN_FILE,
                f"File '{clf.source_path}' could not be classified. "
                f"It will be ignored in the canonical package.",
                source_files=[clf.source_path],
                suggested_action="Rename the file with a recognisable name, or add it to the alias registry.",
            ))
        elif clf.confidence < 0.6:
            issues.append(make_issue(
                Severity.MINOR,
                IssueCode.LOW_CONFIDENCE_CLASSIFICATION,
                f"File '{clf.source_path}' classified as '{clf.detected_type.value}' "
                f"with low confidence ({clf.confidence:.2f}). "
                f"Method: {clf.classifier_method.value}.",
                source_files=[clf.source_path],
                suggested_action="Verify classification is correct; rename file if possible.",
            ))
    return issues


def run(
    input_dir: str,
    output_dir: str,
    use_llm: bool = True,
    run_id: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Execute the full standardizer pipeline.

    Returns:
        (ready_for_psur_pipeline, output_dir)
    """
    if run_id is None:
        run_id = str(uuid.uuid4())[:8]

    logger.info(f"=== PSUR Input Standardizer [run_id={run_id}] ===")
    logger.info(f"Input:  {input_dir}")
    logger.info(f"Output: {output_dir}")

    store = TraceabilityStore()
    all_issues: List[PreparerIssue] = []

    # -----------------------------------------------------------------------
    # 1. Discovery and classification
    # -----------------------------------------------------------------------
    logger.info("Phase 1: Discovery and classification")
    classifications = discover_and_classify(input_dir, use_llm=use_llm)
    logger.info(f"  Discovered {len(classifications)} supported file(s).")

    groups = _group_by_type(classifications)
    all_issues.extend(_warn_unknowns(classifications))
    all_issues.extend(_warn_duplicates(groups))

    # -----------------------------------------------------------------------
    # 2. Extraction
    # -----------------------------------------------------------------------
    logger.info("Phase 2: Extraction")
    extractions_by_type: Dict[CanonicalType, List[ExtractionResult]] = {}
    for ctype, clfs in groups.items():
        all_extractions: List[ExtractionResult] = []
        for clf in clfs:
            logger.info(f"  Extracting: {clf.source_path}")
            all_extractions.extend(_extract(clf))
        extractions_by_type[ctype] = all_extractions

    # -----------------------------------------------------------------------
    # 3. Mapping
    # -----------------------------------------------------------------------
    logger.info("Phase 3: Mapping")
    mapper_outputs: List[MapperOutput] = []

    def _get(ct: CanonicalType) -> List[ExtractionResult]:
        return extractions_by_type.get(ct, [])

    # Sales
    out = sales_mapper.run(_get(CanonicalType.SALES), store, use_llm)
    mapper_outputs.append(out)
    all_issues.extend(_collect_issues_from_output(out))

    # Complaints
    out = complaints_mapper.run(_get(CanonicalType.COMPLAINTS), store, use_llm)
    mapper_outputs.append(out)
    all_issues.extend(_collect_issues_from_output(out))

    # CAPA
    out = capa_mapper.run(_get(CanonicalType.CAPA), store, use_llm)
    mapper_outputs.append(out)
    all_issues.extend(_collect_issues_from_output(out))

    # FSCA
    out = fsca_mapper.run(_get(CanonicalType.FSCA), store, use_llm)
    mapper_outputs.append(out)
    all_issues.extend(_collect_issues_from_output(out))

    # Device context — structured sources + documents
    structured_dc = _get(CanonicalType.DEVICE_CONTEXT)
    doc_sources = (
        _get(CanonicalType.CER) +
        _get(CanonicalType.IFU) +
        _get(CanonicalType.RMF)
    )
    out = device_context_mapper.run(structured_dc, doc_sources, store, use_llm)
    mapper_outputs.append(out)
    all_issues.extend(_collect_issues_from_output(out))

    # RACT
    out = ract_mapper.run(_get(CanonicalType.RACT), store, use_llm)
    mapper_outputs.append(out)
    all_issues.extend(_collect_issues_from_output(out))

    # Previous PSUR
    out = previous_psur_mapper.run(_get(CanonicalType.PREVIOUS_PSUR), store, use_llm)
    mapper_outputs.append(out)
    all_issues.extend(_collect_issues_from_output(out))

    # PMS Plan
    pms_candidates = (
        _get(CanonicalType.PMS_PLAN) +
        _get(CanonicalType.CER)   # CER docs may contain PMS plan references
    )
    out = pms_plan_mapper.run(pms_candidates, store, use_llm)
    mapper_outputs.append(out)
    all_issues.extend(_collect_issues_from_output(out))

    # PMCF
    pmcf_candidates = _get(CanonicalType.PMCF) + _get(CanonicalType.CER)
    out = pmcf_mapper.run(pmcf_candidates, store, use_llm)
    mapper_outputs.append(out)
    all_issues.extend(_collect_issues_from_output(out))

    # Literature
    out = literature_mapper.run(_get(CanonicalType.LITERATURE), store, use_llm)
    mapper_outputs.append(out)
    all_issues.extend(_collect_issues_from_output(out))

    # External DB
    out = external_db_mapper.run(_get(CanonicalType.EXTERNAL_DB), store, use_llm)
    mapper_outputs.append(out)
    all_issues.extend(_collect_issues_from_output(out))

    # Coding dictionary
    out = coding_dictionary_mapper.run(_get(CanonicalType.CODING_DICTIONARY), store, use_llm)
    mapper_outputs.append(out)
    all_issues.extend(_collect_issues_from_output(out))

    # -----------------------------------------------------------------------
    # 3b. Stub outputs for missing canonical targets
    # -----------------------------------------------------------------------
    produced_files_pre = {o.canonical_file for o in mapper_outputs if o.rows}
    stubbed_files: Set[str] = set()
    for schema in ALL_SCHEMAS.values():
        if schema.filename not in produced_files_pre:
            stub = _make_stub_output(schema)
            stubbed_files.add(schema.filename)
            existing = next(
                (o for o in mapper_outputs if o.canonical_file == schema.filename), None
            )
            if existing is not None:
                existing.rows = stub.rows
                existing.completeness = stub.completeness
                existing.notes = stub.notes
            else:
                mapper_outputs.append(stub)

    if stubbed_files:
        all_issues = [
            i for i in all_issues
            if not (i.code == IssueCode.MISSING_REQUIRED_TARGET
                    and i.canonical_target in stubbed_files)
        ]
        for fname in sorted(stubbed_files):
            all_issues.append(make_issue(
                Severity.INFO,
                IssueCode.MISSING_STRONGLY_RECOMMENDED_TARGET,
                f"No source data found for '{fname}'. "
                f"Stub output with 'Information not provided' was generated.",
                canonical_target=fname,
                suggested_action=f"Provide source data for '{fname}' for a complete PSUR package.",
            ))

    # -----------------------------------------------------------------------
    # 4. Readiness gate
    # -----------------------------------------------------------------------
    logger.info("Phase 4: Readiness gate")
    produced_files = {o.canonical_file for o in mapper_outputs if o.rows}
    completeness = {o.canonical_file: o.completeness for o in mapper_outputs}
    readiness = evaluate_readiness(all_issues, produced_files, completeness)
    logger.info(f"  {readiness.summary}")

    # -----------------------------------------------------------------------
    # 5. Write outputs
    # -----------------------------------------------------------------------
    logger.info("Phase 5: Writing canonical package")
    written = write_all_outputs(
        output_dir=output_dir,
        run_id=run_id,
        input_dir=input_dir,
        classifications=classifications,
        mapper_outputs=mapper_outputs,
        readiness=readiness,
        issues=all_issues,
        store=store,
        csv_files=_CSV_FILES,
    )
    for name, path in sorted(written.items()):
        logger.info(f"  Wrote: {path}")

    return readiness.ready_for_psur_pipeline, output_dir


_STUB_PLACEHOLDER = "Information not provided"


def _make_stub_output(schema: CanonicalSchema) -> MapperOutput:
    """Create a single-row stub output for a schema with no source data."""
    row = {f.name: _STUB_PLACEHOLDER for f in schema.fields}
    return MapperOutput(
        canonical_file=schema.filename,
        rows=[row],
        mapping_decisions=[],
        traces=[],
        completeness=1.0,
        notes=[f"No source data found for '{schema.filename}'. "
               f"Stub row with placeholder values generated."],
    )


def _collect_issues_from_output(output: MapperOutput) -> List[PreparerIssue]:
    """Return all PreparerIssue objects embedded in a MapperOutput."""
    return list(output.issues) if output.issues else []
