"""
Header mapper — maps source column headers to canonical field names.

Decision order per field:
1. Exact canonical name match
2. Exact alias match (from schema registry)
3. Normalized alias match (lower + strip + compress whitespace)
4. Fuzzy match above threshold
5. LLM-assisted (deferred; caller must inject llm_assist if desired)

Returns a MappingDecision for every resolved header and emits issues for
unresolved or ambiguous headers.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from .contracts import MappingDecision, MappingMethod
from .issue_model import IssueCode, PreparerIssue, Severity, make_issue
from .schema_registry import CanonicalSchema


_FUZZY_THRESHOLD = 0.78
_LOW_CONFIDENCE_THRESHOLD = 0.70


def _normalize_key(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[_\-./]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _fuzzy_score(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


class HeaderMapper:
    def __init__(self, schema: CanonicalSchema) -> None:
        self.schema = schema
        self._alias_map: Dict[str, str] = schema.all_aliases()  # lower alias -> canonical name
        self._norm_alias_map: Dict[str, str] = {
            _normalize_key(k): v for k, v in self._alias_map.items()
        }

    def map_headers(
        self,
        source_headers: List[str],
        source_file: str,
        llm_assist_fn=None,  # Optional[Callable[[str, CanonicalSchema], Tuple[str, float]]]
    ) -> Tuple[Dict[str, str], List[MappingDecision], List[PreparerIssue]]:
        """
        Returns:
          header_to_canonical: {source_header -> canonical_field_name}
          decisions: one MappingDecision per resolved source_header
          issues: issues for unresolved / ambiguous / low-confidence headers
        """
        header_to_canonical: Dict[str, str] = {}
        decisions: List[MappingDecision] = []
        issues: List[PreparerIssue] = []

        for src_header in source_headers:
            if not src_header or not src_header.strip():
                continue
            result = self._resolve_single(src_header, source_file, llm_assist_fn)
            if result is None:
                issues.append(make_issue(
                    Severity.MINOR,
                    IssueCode.UNRESOLVED_HEADER,
                    f"Header '{src_header}' in '{source_file}' could not be mapped "
                    f"to any canonical field in {self.schema.filename}.",
                    source_files=[source_file],
                    canonical_target=self.schema.filename,
                    suggested_action="Add an alias mapping or rename the column.",
                ))
                continue
            canonical_field, decision = result
            # Ambiguity check: multiple source headers mapping to same canonical field
            if canonical_field in header_to_canonical.values():
                existing_src = next(
                    k for k, v in header_to_canonical.items() if v == canonical_field
                )
                issues.append(make_issue(
                    Severity.MINOR,
                    IssueCode.AMBIGUOUS_HEADER,
                    f"Both '{src_header}' and '{existing_src}' map to canonical field "
                    f"'{canonical_field}' in {self.schema.filename}. "
                    f"Using first match ('{existing_src}').",
                    source_files=[source_file],
                    canonical_target=self.schema.filename,
                    field_name=canonical_field,
                    suggested_action="Remove the duplicate column or rename one of them.",
                ))
            else:
                header_to_canonical[src_header] = canonical_field
                decisions.append(decision)
                if decision.confidence < _LOW_CONFIDENCE_THRESHOLD:
                    issues.append(make_issue(
                        Severity.MINOR,
                        IssueCode.LOW_CONFIDENCE_MAPPING,
                        f"Low-confidence mapping ({decision.confidence:.2f}): "
                        f"'{src_header}' -> '{canonical_field}' via {decision.method.value}.",
                        source_files=[source_file],
                        canonical_target=self.schema.filename,
                        field_name=canonical_field,
                        suggested_action="Verify this mapping is correct.",
                    ))

        return header_to_canonical, decisions, issues

    def _resolve_single(
        self,
        src_header: str,
        source_file: str,
        llm_assist_fn=None,
    ) -> Optional[Tuple[str, MappingDecision]]:
        s = src_header.strip()
        s_lower = s.lower()
        s_norm = _normalize_key(s)

        # 1. Exact canonical name
        if s_lower in {f.name.lower() for f in self.schema.fields}:
            canonical = next(f.name for f in self.schema.fields if f.name.lower() == s_lower)
            return canonical, MappingDecision(
                canonical_field=canonical,
                mapped_from=s,
                method=MappingMethod.EXACT_CANONICAL,
                confidence=1.0,
                llm_used=False,
            )

        # 2. Exact alias match
        if s_lower in self._alias_map:
            canonical = self._alias_map[s_lower]
            return canonical, MappingDecision(
                canonical_field=canonical,
                mapped_from=s,
                method=MappingMethod.EXACT_ALIAS,
                confidence=0.98,
                llm_used=False,
            )

        # 3. Normalized alias match
        if s_norm in self._norm_alias_map:
            canonical = self._norm_alias_map[s_norm]
            return canonical, MappingDecision(
                canonical_field=canonical,
                mapped_from=s,
                method=MappingMethod.NORMALIZED_ALIAS,
                confidence=0.92,
                llm_used=False,
            )

        # 4. Fuzzy match
        best_canonical: Optional[str] = None
        best_score = 0.0
        alternatives: List[str] = []

        for alias_lower, canonical in self._norm_alias_map.items():
            score = _fuzzy_score(s_norm, alias_lower)
            if score > best_score:
                best_score = score
                best_canonical = canonical
            if score >= _FUZZY_THRESHOLD and canonical != best_canonical:
                alternatives.append(f"{canonical} ({score:.2f})")

        if best_canonical and best_score >= _FUZZY_THRESHOLD:
            return best_canonical, MappingDecision(
                canonical_field=best_canonical,
                mapped_from=s,
                method=MappingMethod.FUZZY,
                confidence=best_score * 0.9,
                llm_used=False,
                alternatives_considered=alternatives[:3],
                notes=f"Fuzzy score={best_score:.2f}",
            )

        # 5. LLM-assisted
        if llm_assist_fn is not None:
            try:
                llm_canonical, llm_confidence = llm_assist_fn(s, self.schema)
                if llm_canonical:
                    return llm_canonical, MappingDecision(
                        canonical_field=llm_canonical,
                        mapped_from=s,
                        method=MappingMethod.LLM_ASSISTED,
                        confidence=llm_confidence,
                        llm_used=True,
                        notes="Resolved via LLM header mapping.",
                    )
            except Exception:
                pass  # fall through to unresolved

        return None
