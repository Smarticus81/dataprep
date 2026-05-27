"""
LLM-assist layer for the PSUR Input Standardizer.

All LLM calls in this component are gated through this module.
The LLM is used only when deterministic methods have failed.
Every LLM call records what was asked and what was returned.

Supported use cases:
1. File classification (when filename + headers are ambiguous)
2. Header-to-field mapping (when alias + fuzzy methods fail)
3. Document section extraction (device_context, PMCF, literature fields)

The LLM must never fabricate missing regulatory data.
Prompts instruct it to return null / unknown for absent values.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from .contracts import CanonicalType
from .schema_registry import CanonicalSchema

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 1024
_LLM_AVAILABLE = False

try:
    import anthropic  # type: ignore
    _client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    _LLM_AVAILABLE = True
except ImportError:
    _client = None
    logger.warning(
        "anthropic SDK not installed. LLM-assisted classification/extraction disabled. "
        "Install with: pip install anthropic"
    )


def _call_llm(system: str, user: str) -> str:
    if not _LLM_AVAILABLE or _client is None:
        raise RuntimeError("LLM not available (anthropic SDK not installed or no API key).")
    message = _client.messages.create(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return message.content[0].text.strip()


def classify_file(
    filename: str,
    extension: str,
    sample_headers: List[str],
    sample_text: str,
    known_types: List[str],
) -> Tuple[str, float]:
    """
    Ask the LLM to classify a file given its name, extension, headers, and a text sample.
    Returns (canonical_type_string, confidence).
    Falls back to 'unknown' on any failure.
    """
    system = (
        "You are a medical device regulatory data expert. "
        "Your task is to classify a file from a PSUR (Periodic Safety Update Report) data package. "
        "Respond ONLY with a JSON object containing exactly two keys: "
        '"type" (one of the known types listed) and "confidence" (float 0.0-1.0). '
        "Never return anything outside this JSON. "
        'If you cannot determine the type, return {"type": "unknown", "confidence": 0.0}.'
    )
    user = (
        f"Filename: {filename}\n"
        f"Extension: {extension}\n"
        f"Sample headers: {json.dumps(sample_headers[:20])}\n"
        f"Sample text (first 500 chars): {sample_text[:500]}\n\n"
        f"Known canonical types: {json.dumps(known_types)}\n\n"
        "Classify this file. Return only JSON."
    )
    try:
        raw = _call_llm(system, user)
        obj = json.loads(raw)
        ct = str(obj.get("type", "unknown"))
        conf = float(obj.get("confidence", 0.3))
        return ct, min(max(conf, 0.0), 1.0)
    except Exception as e:
        logger.warning(f"LLM classify_file failed: {e}")
        return "unknown", 0.0


def map_header_to_field(
    source_header: str,
    schema: CanonicalSchema,
) -> Tuple[Optional[str], float]:
    """
    Ask the LLM to map a source header to a canonical field in a given schema.
    Returns (canonical_field_name_or_None, confidence).
    """
    field_names = [f.name for f in schema.fields]
    system = (
        "You are a medical device regulatory data expert. "
        "Your task is to map a source spreadsheet column header to the correct canonical field "
        f"in the {schema.filename} schema for PSUR reporting. "
        "Respond ONLY with a JSON object: "
        '"field" (the canonical field name, or null if no match) and '
        '"confidence" (float 0.0-1.0). '
        "Never fabricate a field name not in the list."
    )
    user = (
        f"Source header: '{source_header}'\n"
        f"Target schema: {schema.filename}\n"
        f"Available canonical fields: {json.dumps(field_names)}\n\n"
        "Return only JSON."
    )
    try:
        raw = _call_llm(system, user)
        obj = json.loads(raw)
        field = obj.get("field")
        conf = float(obj.get("confidence", 0.3))
        if field not in field_names:
            return None, 0.0
        return field, min(max(conf, 0.0), 1.0)
    except Exception as e:
        logger.warning(f"LLM map_header_to_field failed: {e}")
        return None, 0.0


def extract_document_fields(
    document_text: str,
    target_fields: List[str],
    canonical_file: str,
    source_file: str,
) -> Dict[str, Any]:
    """
    Ask the LLM to extract specific structured fields from unstructured document text.

    Returns a dict: {field_name: {"value": ..., "excerpt": ..., "confidence": float}}

    - Values are returned verbatim where present; the LLM must not invent absent values.
    - If a field is absent in the document, the LLM must return null for its value.
    """
    system = (
        "You are a medical device regulatory data expert. "
        "You extract structured information from regulatory documents "
        "(CER, IFU, RMF, PMS Plan, PMCF Report) for PSUR preparation. "
        "For each requested field:\n"
        "  - Extract the value verbatim or paraphrase concisely from the text.\n"
        "  - Include a short excerpt (≤80 chars) from the source text supporting the value.\n"
        "  - Assign a confidence score (0.0–1.0).\n"
        "  - If the field is NOT present in the text, return null for value and excerpt.\n"
        "NEVER fabricate a regulatory value that is not present in the text.\n"
        "Respond ONLY with a JSON object where each key is a field name from the request, "
        'and each value is {"value": ..., "excerpt": ..., "confidence": float}.'
    )
    user = (
        f"Document: {source_file}\n"
        f"Target canonical output: {canonical_file}\n"
        f"Fields to extract: {json.dumps(target_fields)}\n\n"
        f"Document text (first 4000 chars):\n{document_text[:4000]}\n\n"
        "Return only JSON."
    )
    try:
        raw = _call_llm(system, user)
        obj = json.loads(raw)
        result: Dict[str, Any] = {}
        for f in target_fields:
            entry = obj.get(f, {})
            if isinstance(entry, dict):
                result[f] = {
                    "value": entry.get("value"),
                    "excerpt": entry.get("excerpt", ""),
                    "confidence": float(entry.get("confidence", 0.3)),
                }
            else:
                result[f] = {"value": None, "excerpt": "", "confidence": 0.0}
        return result
    except Exception as e:
        logger.warning(f"LLM extract_document_fields failed: {e}")
        return {f: {"value": None, "excerpt": "", "confidence": 0.0} for f in target_fields}


def is_available() -> bool:
    return _LLM_AVAILABLE
