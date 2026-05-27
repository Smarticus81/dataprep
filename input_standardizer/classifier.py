"""
File classifier for the PSUR Input Standardizer.

Classification priority:
1. Exact filename pattern matching
2. Schema / header signature matching
3. Sheet-name signatures (XLSX)
4. JSON key structure
5. Document content heuristics (DOCX/PDF)
6. LLM-assisted (last resort)

Returns a FileClassification for every file in the input directory.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .contracts import CanonicalType, ClassifierMethod, FileClassification
from .extractors.csv_excel import get_excel_sheet_names, read_csv, read_excel
from .extractors.json_loader import read_json
from .schema_registry import ALL_SCHEMAS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Filename patterns (exact or prefix/suffix keyword match)
# ---------------------------------------------------------------------------

_FILENAME_PATTERNS: List[Tuple[re.Pattern, CanonicalType]] = [
    # Sales
    (re.compile(r"\bsales?\b", re.I), CanonicalType.SALES),
    (re.compile(r"\bshipment[s]?\b", re.I), CanonicalType.SALES),
    (re.compile(r"\bunits?[_\s]?sold\b", re.I), CanonicalType.SALES),
    (re.compile(r"\bmarket[_\s]?data\b", re.I), CanonicalType.SALES),
    # Complaints
    (re.compile(r"\bcomplaints?\b", re.I), CanonicalType.COMPLAINTS),
    (re.compile(r"\bmdrs?\b", re.I), CanonicalType.COMPLAINTS),
    (re.compile(r"\bvigilance\b", re.I), CanonicalType.COMPLAINTS),
    (re.compile(r"\badverse[_\s]?event[s]?\b", re.I), CanonicalType.COMPLAINTS),
    # CAPA
    (re.compile(r"\bcapa[s]?\b", re.I), CanonicalType.CAPA),
    (re.compile(r"\bcorrective[_\s]?action\b", re.I), CanonicalType.CAPA),
    (re.compile(r"\bpreventive[_\s]?action\b", re.I), CanonicalType.CAPA),
    # FSCA
    (re.compile(r"\bfsca[s]?\b", re.I), CanonicalType.FSCA),
    (re.compile(r"\bfield[_\s]?safety\b", re.I), CanonicalType.FSCA),
    (re.compile(r"\brecall[s]?\b", re.I), CanonicalType.FSCA),
    (re.compile(r"\bfield[_\s]?action\b", re.I), CanonicalType.FSCA),
    # Device context
    (re.compile(r"\bdevice[_\s]?context\b", re.I), CanonicalType.DEVICE_CONTEXT),
    (re.compile(r"\bdevice[_\s]?info\b", re.I), CanonicalType.DEVICE_CONTEXT),
    (re.compile(r"\bproduct[_\s]?profile\b", re.I), CanonicalType.DEVICE_CONTEXT),
    (re.compile(r"\bdevice[_\s]?profile\b", re.I), CanonicalType.DEVICE_CONTEXT),
    (re.compile(r"^context$", re.I), CanonicalType.DEVICE_CONTEXT),
    (re.compile(r"\btechnical[_\s]?doc(umentation)?\b", re.I), CanonicalType.DEVICE_CONTEXT),
    # RACT
    (re.compile(r"\bract\b", re.I), CanonicalType.RACT),
    (re.compile(r"\brisk[_\s]?acceptability\b", re.I), CanonicalType.RACT),
    (re.compile(r"\brisk[_\s]?assessment\b", re.I), CanonicalType.RACT),
    (re.compile(r"\brisk[_\s]?table\b", re.I), CanonicalType.RACT),
    (re.compile(r"\bhazard[_\s]?table\b", re.I), CanonicalType.RACT),
    # Previous PSUR
    (re.compile(r"\bprevious[_\s]?psur\b", re.I), CanonicalType.PREVIOUS_PSUR),
    (re.compile(r"\bprior[_\s]?psur\b", re.I), CanonicalType.PREVIOUS_PSUR),
    (re.compile(r"\blast[_\s]?psur\b", re.I), CanonicalType.PREVIOUS_PSUR),
    (re.compile(r"\bpsur[_\s]?prior\b", re.I), CanonicalType.PREVIOUS_PSUR),
    # PMS Plan
    (re.compile(r"\bpms[_\s]?plan\b", re.I), CanonicalType.PMS_PLAN),
    (re.compile(r"\bpost[_\s]?market[_\s]?surv(eillance)?[_\s]?plan\b", re.I), CanonicalType.PMS_PLAN),
    # PMCF
    (re.compile(r"\bpmcf\b", re.I), CanonicalType.PMCF),
    (re.compile(r"\bpost[_\s]?market[_\s]?clinical[_\s]?follow[_\s]?up\b", re.I), CanonicalType.PMCF),
    # Literature
    (re.compile(r"\bliterature[_\s]?(search|review|survey)\b", re.I), CanonicalType.LITERATURE),
    (re.compile(r"\blit[_\s]?search\b", re.I), CanonicalType.LITERATURE),
    (re.compile(r"\bpublished[_\s]?data\b", re.I), CanonicalType.LITERATURE),
    # External DB
    (re.compile(r"\bexternal[_\s]?(db|database|events?)\b", re.I), CanonicalType.EXTERNAL_DB),
    (re.compile(r"\bmaude\b", re.I), CanonicalType.EXTERNAL_DB),
    (re.compile(r"\beudamed\b", re.I), CanonicalType.EXTERNAL_DB),
    (re.compile(r"\bdatabase[_\s]?events?\b", re.I), CanonicalType.EXTERNAL_DB),
    # Coding dictionary
    (re.compile(r"\bcoding[_\s]?dict(ionary)?\b", re.I), CanonicalType.CODING_DICTIONARY),
    (re.compile(r"\bimdrf[_\s]?(codes?|dict|annex)\b", re.I), CanonicalType.CODING_DICTIONARY),
    # CER
    (re.compile(r"\bcer\b", re.I), CanonicalType.CER),
    (re.compile(r"\bclinical[_\s]?evaluation[_\s]?report\b", re.I), CanonicalType.CER),
    # IFU
    (re.compile(r"\bifu\b", re.I), CanonicalType.IFU),
    (re.compile(r"\binstructions?[_\s]?for[_\s]?use\b", re.I), CanonicalType.IFU),
    # RMF
    (re.compile(r"\brmf\b", re.I), CanonicalType.RMF),
    (re.compile(r"\brisk[_\s]?management[_\s]?file\b", re.I), CanonicalType.RMF),
    (re.compile(r"\brisk[_\s]?management[_\s]?plan\b", re.I), CanonicalType.RMF),
]


# Header signature sets (must match ≥N of these to classify)
_HEADER_SIGNATURES: List[Tuple[Set[str], CanonicalType, int]] = [
    # (required_terms_lower, type, min_overlap)
    ({"complaint_number", "complaint number", "complaint no", "complaint id"}, CanonicalType.COMPLAINTS, 1),
    ({"complaint", "description", "serious"}, CanonicalType.COMPLAINTS, 2),
    ({"capa_number", "capa number", "capa id", "capa no"}, CanonicalType.CAPA, 1),
    ({"capa", "status", "open_date", "open date"}, CanonicalType.CAPA, 2),
    ({"action_id", "action id", "fsca", "fsca id", "fsca no"}, CanonicalType.FSCA, 1),
    ({"hazard_id", "hazard id", "severity", "risk_level", "risk level"}, CanonicalType.RACT, 2),
    ({"quantity", "country", "date", "sales"}, CanonicalType.SALES, 2),
    ({"quantity", "units", "country"}, CanonicalType.SALES, 2),
    ({"event_id", "event id", "external_source", "external source"}, CanonicalType.EXTERNAL_DB, 2),
    ({"annexa", "annex a", "annexf", "annex f", "imdrf"}, CanonicalType.CODING_DICTIONARY, 1),
]

# Sheet name signatures
_SHEET_SIGNATURES: Dict[str, CanonicalType] = {
    "sales": CanonicalType.SALES,
    "shipments": CanonicalType.SALES,
    "complaints": CanonicalType.COMPLAINTS,
    "complaint": CanonicalType.COMPLAINTS,
    "vigilance": CanonicalType.COMPLAINTS,
    "capa": CanonicalType.CAPA,
    "capas": CanonicalType.CAPA,
    "fsca": CanonicalType.FSCA,
    "recalls": CanonicalType.FSCA,
    "ract": CanonicalType.RACT,
    "risk": CanonicalType.RACT,
    "hazards": CanonicalType.RACT,
    "pmcf": CanonicalType.PMCF,
    "literature": CanonicalType.LITERATURE,
    "coding": CanonicalType.CODING_DICTIONARY,
    "dictionary": CanonicalType.CODING_DICTIONARY,
    "imdrf": CanonicalType.CODING_DICTIONARY,
    "external": CanonicalType.EXTERNAL_DB,
    "maude": CanonicalType.EXTERNAL_DB,
    "eudamed": CanonicalType.EXTERNAL_DB,
}


def _match_filename(stem: str) -> Optional[Tuple[CanonicalType, float]]:
    # Split CamelCase ("CustomerComplaints" -> "Customer Complaints")
    stem_norm = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)
    # Normalise underscores/dashes/dots to spaces so \b word boundaries fire correctly
    stem_norm = re.sub(r"[_\-.]", " ", stem_norm)
    for pattern, ct in _FILENAME_PATTERNS:
        if pattern.search(stem_norm):
            return ct, 0.90
    return None


def _match_headers(headers: List[str]) -> Optional[Tuple[CanonicalType, float]]:
    lower_headers = {h.strip().lower() for h in headers if h}
    for sig_set, ct, min_overlap in _HEADER_SIGNATURES:
        overlap = len(sig_set & lower_headers)
        if overlap >= min_overlap:
            confidence = min(0.95, 0.70 + 0.10 * overlap)
            return ct, confidence
    return None


def _match_sheet_names(sheet_names: List[str]) -> Optional[Tuple[CanonicalType, float]]:
    for name in sheet_names:
        norm = name.strip().lower()
        if norm in _SHEET_SIGNATURES:
            return _SHEET_SIGNATURES[norm], 0.85
    return None


def classify_file(
    path: str,
    use_llm: bool = True,
) -> FileClassification:
    stem = Path(path).stem
    ext = Path(path).suffix.lstrip(".").lower()
    evidence: List[str] = []

    # 1. Filename patterns
    result = _match_filename(stem)
    if result:
        ct, conf = result
        return FileClassification(
            source_path=path,
            extension=ext,
            detected_type=ct,
            confidence=conf,
            classifier_method=ClassifierMethod.FILENAME,
            evidence=[f"Filename '{stem}' matched pattern for {ct.value}"],
        )

    # 2. Extension-based extraction + header/key analysis
    if ext in ("csv",):
        try:
            extraction = read_csv(path)
            if extraction.headers:
                evidence.append(f"Headers: {extraction.headers[:10]}")
                result = _match_headers(extraction.headers)
                if result:
                    ct, conf = result
                    return FileClassification(
                        source_path=path,
                        extension=ext,
                        detected_type=ct,
                        confidence=conf,
                        classifier_method=ClassifierMethod.HEADER_SIGNATURE,
                        evidence=evidence,
                        candidate_headers=extraction.headers[:20],
                    )
        except Exception as e:
            evidence.append(f"CSV read error: {e}")

    elif ext in ("xlsx", "xls"):
        sheet_names = get_excel_sheet_names(path)
        if sheet_names:
            evidence.append(f"Sheets: {sheet_names}")
            # 3. Sheet name signatures
            result = _match_sheet_names(sheet_names)
            if result:
                ct, conf = result
                return FileClassification(
                    source_path=path,
                    extension=ext,
                    detected_type=ct,
                    confidence=conf,
                    classifier_method=ClassifierMethod.SHEET_NAME,
                    evidence=evidence,
                    sheet_names=sheet_names,
                )
            # Try first sheet headers
            try:
                results = read_excel(path, target_sheet=sheet_names[0])
                if results and results[0].headers:
                    evidence.append(f"First-sheet headers: {results[0].headers[:10]}")
                    result = _match_headers(results[0].headers)
                    if result:
                        ct, conf = result
                        return FileClassification(
                            source_path=path,
                            extension=ext,
                            detected_type=ct,
                            confidence=conf,
                            classifier_method=ClassifierMethod.HEADER_SIGNATURE,
                            evidence=evidence,
                            sheet_names=sheet_names,
                            candidate_headers=results[0].headers[:20],
                        )
            except Exception as e:
                evidence.append(f"XLSX read error: {e}")

    elif ext in ("json", "txt"):
        try:
            extraction = read_json(path)
            if extraction.source_type != CanonicalType.UNKNOWN:
                evidence.append(f"JSON keys matched {extraction.source_type.value}")
                return FileClassification(
                    source_path=path,
                    extension=ext,
                    detected_type=extraction.source_type,
                    confidence=0.85,
                    classifier_method=ClassifierMethod.JSON_KEY_STRUCTURE,
                    evidence=evidence,
                )
            if extraction.headers:
                evidence.append(f"Top-level JSON keys: {extraction.headers[:10]}")
        except Exception as e:
            if ext == "json":
                evidence.append(f"JSON read error: {e}")

        if ext == "txt" and not any("JSON keys matched" in e for e in evidence):
            from .extractors.docx_pdf import read_document
            doc = read_document(path)
            if doc.raw_text_snippets:
                sections = list(doc.raw_text_snippets.keys())
                evidence.append(f"Document sections detected: {sections}")
                if "pmcf_details" in sections:
                    ct = CanonicalType.PMCF
                elif "literature_review" in sections:
                    ct = CanonicalType.LITERATURE
                elif any(s in sections for s in ("device_description", "intended_purpose", "notified_body")):
                    ct = CanonicalType.CER
                elif "rmf_reference" in sections and len(sections) < 4:
                    ct = CanonicalType.RMF
                elif "ifu_reference" in sections:
                    ct = CanonicalType.IFU
                else:
                    ct = CanonicalType.CER
                return FileClassification(
                    source_path=path,
                    extension=ext,
                    detected_type=ct,
                    confidence=0.60,
                    classifier_method=ClassifierMethod.CONTENT_HEURISTIC,
                    evidence=evidence,
                )

    elif ext in ("docx", "doc", "pdf"):
        # 5. Document content heuristics
        from .extractors.docx_pdf import read_document
        doc = read_document(path)
        if doc.raw_text_snippets:
            sections = list(doc.raw_text_snippets.keys())
            evidence.append(f"Document sections detected: {sections}")
            # Guess type from sections present
            if "pmcf_details" in sections:
                ct = CanonicalType.PMCF
            elif "literature_review" in sections:
                ct = CanonicalType.LITERATURE
            elif any(s in sections for s in ("device_description", "intended_purpose", "notified_body")):
                ct = CanonicalType.CER
            elif "rmf_reference" in sections and len(sections) < 4:
                ct = CanonicalType.RMF
            elif "ifu_reference" in sections:
                ct = CanonicalType.IFU
            else:
                ct = CanonicalType.CER  # default document assumption
            return FileClassification(
                source_path=path,
                extension=ext,
                detected_type=ct,
                confidence=0.60,
                classifier_method=ClassifierMethod.CONTENT_HEURISTIC,
                evidence=evidence,
            )

    # 6. LLM-assisted
    if use_llm:
        try:
            from . import llm_assist
            if llm_assist.is_available():
                known_types = [ct.value for ct in CanonicalType]
                sample_text = evidence[0] if evidence else ""
                llm_type, llm_conf = llm_assist.classify_file(
                    filename=Path(path).name,
                    extension=ext,
                    sample_headers=[],
                    sample_text=sample_text,
                    known_types=known_types,
                )
                try:
                    ct = CanonicalType(llm_type)
                except ValueError:
                    ct = CanonicalType.UNKNOWN
                evidence.append(f"LLM classified as {llm_type} (confidence={llm_conf:.2f})")
                return FileClassification(
                    source_path=path,
                    extension=ext,
                    detected_type=ct,
                    confidence=llm_conf,
                    classifier_method=ClassifierMethod.LLM_ASSISTED,
                    evidence=evidence,
                )
        except Exception as e:
            evidence.append(f"LLM classification failed: {e}")

    return FileClassification(
        source_path=path,
        extension=ext,
        detected_type=CanonicalType.UNKNOWN,
        confidence=0.0,
        classifier_method=ClassifierMethod.UNRESOLVED,
        evidence=evidence,
        notes="Could not classify file by any method.",
    )


def discover_and_classify(
    input_dir: str,
    use_llm: bool = True,
) -> List[FileClassification]:
    """Recursively scan input_dir and classify every supported file."""
    supported_extensions = {
        "csv", "xlsx", "xls", "json", "docx", "doc", "pdf", "txt",
    }
    classifications: List[FileClassification] = []

    for root, _, files in os.walk(input_dir):
        for fname in sorted(files):
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            if ext not in supported_extensions:
                logger.debug(f"Skipping unsupported file: {fname}")
                continue
            full_path = os.path.join(root, fname)
            clf = classify_file(full_path, use_llm=use_llm)
            classifications.append(clf)
            logger.info(
                f"  {fname} -> {clf.detected_type.value} "
                f"(conf={clf.confidence:.2f}, method={clf.classifier_method.value})"
            )

    return classifications
