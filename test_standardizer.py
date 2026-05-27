"""
Test harness for the PSUR Input Standardizer.

Covers:
- File classification
- Header mapping
- Value normalization
- Readiness gate
- Traceability output population
- End-to-end pipeline (no-LLM mode) against all five fixture packages
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

# Ensure the project root is on sys.path when running directly
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from input_standardizer.classifier import classify_file
from input_standardizer.contracts import CanonicalType, ClassifierMethod
from input_standardizer.header_mapper import HeaderMapper
from input_standardizer.issue_model import IssueCode, Severity
from input_standardizer.readiness_gate import evaluate as evaluate_readiness
from input_standardizer.schema_registry import (
    COMPLAINTS_SCHEMA,
    SALES_SCHEMA,
    ALL_SCHEMAS,
)
from input_standardizer.traceability_model import TraceabilityStore, make_trace
from input_standardizer.value_normalizers import (
    normalize_boolean,
    normalize_country,
    normalize_date,
    normalize_enum,
)

FIXTURES = os.path.join(_HERE, "input_standardizer", "fixtures", "inputs")


# ---------------------------------------------------------------------------
# Classification tests
# ---------------------------------------------------------------------------

class TestClassification(unittest.TestCase):

    def _fixture(self, scenario: str, filename: str) -> str:
        return os.path.join(FIXTURES, scenario, filename)

    def test_sales_csv_classified_by_filename(self):
        path = self._fixture("messy_package", "SurgiFlow_Sales_Data_Q1Q2_2024.csv")
        if not os.path.exists(path):
            self.skipTest("Fixture not found")
        clf = classify_file(path, use_llm=False)
        self.assertEqual(clf.detected_type, CanonicalType.SALES)
        self.assertIn(clf.classifier_method, (
            ClassifierMethod.FILENAME, ClassifierMethod.HEADER_SIGNATURE
        ))

    def test_complaints_csv_classified_by_filename(self):
        path = self._fixture("messy_package", "CustomerComplaints_Report_2024.csv")
        if not os.path.exists(path):
            self.skipTest("Fixture not found")
        clf = classify_file(path, use_llm=False)
        self.assertEqual(clf.detected_type, CanonicalType.COMPLAINTS)

    def test_device_context_json_classified_by_keys(self):
        path = self._fixture("clean_package", "device_context.json")
        if not os.path.exists(path):
            self.skipTest("Fixture not found")
        clf = classify_file(path, use_llm=False)
        self.assertEqual(clf.detected_type, CanonicalType.DEVICE_CONTEXT)

    def test_clean_sales_csv_headers(self):
        path = self._fixture("clean_package", "sales.csv")
        if not os.path.exists(path):
            self.skipTest("Fixture not found")
        clf = classify_file(path, use_llm=False)
        self.assertEqual(clf.detected_type, CanonicalType.SALES)

    def test_ract_json_classified(self):
        path = self._fixture("clean_package", "ract.json")
        if not os.path.exists(path):
            self.skipTest("Fixture not found")
        clf = classify_file(path, use_llm=False)
        # RACT JSON doesn't have a top-level key that signals type directly,
        # but 'ract.json' filename should match
        self.assertNotEqual(clf.detected_type, CanonicalType.UNKNOWN)


# ---------------------------------------------------------------------------
# Header mapping tests
# ---------------------------------------------------------------------------

class TestHeaderMapping(unittest.TestCase):

    def test_exact_canonical_match(self):
        mapper = HeaderMapper(COMPLAINTS_SCHEMA)
        result, decisions, issues = mapper.map_headers(
            ["complaint_number", "date", "description", "serious"],
            source_file="test.csv",
        )
        self.assertEqual(result["complaint_number"], "complaint_number")
        self.assertEqual(result["date"], "date")
        self.assertTrue(all(d.confidence >= 0.95 for d in decisions))

    def test_alias_match(self):
        mapper = HeaderMapper(COMPLAINTS_SCHEMA)
        result, decisions, issues = mapper.map_headers(
            ["complaint no", "date received", "event description", "serious event"],
            source_file="test.csv",
        )
        self.assertEqual(result.get("complaint no"), "complaint_number")
        self.assertEqual(result.get("date received"), "date")
        self.assertEqual(result.get("event description"), "description")
        self.assertEqual(result.get("serious event"), "serious")

    def test_normalized_alias_match(self):
        mapper = HeaderMapper(COMPLAINTS_SCHEMA)
        # "Complaint #" should normalize and match
        result, decisions, issues = mapper.map_headers(
            ["Complaint #"],
            source_file="test.csv",
        )
        self.assertIn("Complaint #", result)
        self.assertEqual(result["Complaint #"], "complaint_number")

    def test_sales_header_aliases(self):
        mapper = HeaderMapper(SALES_SCHEMA)
        result, decisions, issues = mapper.map_headers(
            ["Ship Date", "Qty", "Country of Destination", "Sales Region"],
            source_file="test.csv",
        )
        self.assertEqual(result.get("Ship Date"), "date")
        self.assertEqual(result.get("Qty"), "quantity")
        self.assertEqual(result.get("Country of Destination"), "country")
        self.assertEqual(result.get("Sales Region"), "region")

    def test_unresolved_header_emits_issue(self):
        mapper = HeaderMapper(SALES_SCHEMA)
        _, _, issues = mapper.map_headers(
            ["totally_unknown_column_xyz"],
            source_file="test.csv",
        )
        codes = [i.code for i in issues]
        self.assertIn(IssueCode.UNRESOLVED_HEADER, codes)

    def test_ambiguous_header_emits_issue(self):
        mapper = HeaderMapper(COMPLAINTS_SCHEMA)
        # Two headers that both map to 'complaint_number'
        _, _, issues = mapper.map_headers(
            ["complaint_number", "complaint no"],
            source_file="test.csv",
        )
        codes = [i.code for i in issues]
        self.assertIn(IssueCode.AMBIGUOUS_HEADER, codes)


# ---------------------------------------------------------------------------
# Value normalization tests
# ---------------------------------------------------------------------------

class TestNormalization(unittest.TestCase):

    def test_date_iso(self):
        val, transform, issue = normalize_date("15/01/2024", "sales.csv", "date", "test.csv")
        self.assertEqual(val, "2024-01-15")
        self.assertEqual(transform, "date_iso")
        self.assertIsNone(issue)

    def test_date_already_iso(self):
        val, transform, issue = normalize_date("2024-01-15", "sales.csv", "date", "test.csv")
        self.assertEqual(val, "2024-01-15")
        self.assertIsNone(issue)

    def test_date_dmy_with_dots(self):
        val, transform, issue = normalize_date("15.01.2024", "sales.csv", "date", "test.csv")
        self.assertEqual(val, "2024-01-15")

    def test_date_unparseable_emits_issue(self):
        val, transform, issue = normalize_date("not-a-date", "sales.csv", "date", "test.csv")
        self.assertIsNotNone(issue)
        self.assertEqual(issue.code, IssueCode.DATE_PARSE_FAILURE)

    def test_boolean_yes(self):
        for raw in ("Yes", "YES", "y", "Y", "true", "1"):
            val, _, issue = normalize_boolean(raw, "complaints.csv", "serious", "test.csv")
            self.assertTrue(val, f"Expected True for {raw!r}")

    def test_boolean_no(self):
        for raw in ("No", "NO", "n", "N", "false", "0"):
            val, _, issue = normalize_boolean(raw, "complaints.csv", "serious", "test.csv")
            self.assertFalse(val, f"Expected False for {raw!r}")

    def test_boolean_unknown_emits_issue(self):
        val, _, issue = normalize_boolean("maybe", "complaints.csv", "serious", "test.csv")
        self.assertIsNotNone(issue)
        self.assertEqual(issue.code, IssueCode.BOOLEAN_NORMALIZATION_FAILURE)

    def test_country_normalize_uk(self):
        val, transform, issue = normalize_country("uk", "sales.csv", "country", "test.csv")
        self.assertEqual(val, "United Kingdom")
        self.assertIsNone(issue)

    def test_country_normalize_de(self):
        val, _, issue = normalize_country("Deutschland", "sales.csv", "country", "test.csv")
        self.assertEqual(val, "Germany")
        self.assertIsNone(issue)

    def test_country_unknown_emits_minor_issue(self):
        val, _, issue = normalize_country("Ruritania", "sales.csv", "country", "test.csv")
        self.assertIsNotNone(issue)
        self.assertEqual(issue.severity, Severity.MINOR)

    def test_enum_exact(self):
        val, transform, issue = normalize_enum(
            "open", ("open", "closed", "in_progress"), "capa.csv", "status", "test.csv"
        )
        self.assertEqual(val, "open")
        self.assertIsNone(issue)

    def test_enum_case_insensitive(self):
        val, _, _ = normalize_enum(
            "CLOSED", ("open", "closed", "in_progress"), "capa.csv", "status", "test.csv"
        )
        self.assertEqual(val, "closed")

    def test_enum_unknown_emits_issue(self):
        _, _, issue = normalize_enum(
            "gibberish", ("open", "closed"), "capa.csv", "status", "test.csv"
        )
        self.assertIsNotNone(issue)

    def test_placeholder_returns_none(self):
        val, transform, issue = normalize_date("N/A", "sales.csv", "date", "test.csv")
        self.assertIsNone(val)


# ---------------------------------------------------------------------------
# Readiness gate tests
# ---------------------------------------------------------------------------

class TestReadinessGate(unittest.TestCase):

    def _core_files(self):
        return {
            "sales.csv", "complaints.csv", "device_context.json",
            "ract.json", "previous_psur.json", "pms_plan.json",
        }

    def test_ready_with_no_issues_and_all_core_files(self):
        result = evaluate_readiness(
            issues=[],
            produced_files=self._core_files(),
            completeness_by_file={f: 1.0 for f in self._core_files()},
        )
        self.assertTrue(result.ready_for_psur_pipeline)
        self.assertEqual(result.blocking_issues, [])

    def test_not_ready_when_sales_missing(self):
        files = self._core_files() - {"sales.csv"}
        result = evaluate_readiness(
            issues=[],
            produced_files=files,
            completeness_by_file={f: 1.0 for f in files},
        )
        self.assertFalse(result.ready_for_psur_pipeline)
        self.assertTrue(any("sales.csv" in b for b in result.blocking_issues))

    def test_not_ready_when_device_context_missing(self):
        files = self._core_files() - {"device_context.json"}
        result = evaluate_readiness(
            issues=[],
            produced_files=files,
            completeness_by_file={f: 1.0 for f in files},
        )
        self.assertFalse(result.ready_for_psur_pipeline)

    def test_not_ready_when_completeness_too_low(self):
        from input_standardizer.readiness_gate import MIN_COMPLETENESS_REQUIRED
        files = self._core_files()
        completeness = {f: 1.0 for f in files}
        completeness["complaints.csv"] = MIN_COMPLETENESS_REQUIRED - 0.1
        result = evaluate_readiness(
            issues=[],
            produced_files=files,
            completeness_by_file=completeness,
        )
        self.assertFalse(result.ready_for_psur_pipeline)

    def test_strongly_recommended_missing_goes_to_major_not_blocking(self):
        files = self._core_files()
        result = evaluate_readiness(
            issues=[],
            produced_files=files,
            completeness_by_file={f: 1.0 for f in files},
        )
        # No capa/fsca/pmcf/literature -> major issues but still ready
        self.assertTrue(result.ready_for_psur_pipeline)
        self.assertTrue(len(result.major_issues) > 0)


# ---------------------------------------------------------------------------
# Traceability tests
# ---------------------------------------------------------------------------

class TestTraceability(unittest.TestCase):

    def test_tabular_trace_stored_and_serialised(self):
        store = TraceabilityStore()
        trace = make_trace(
            canonical_file="sales.csv",
            canonical_field="date",
            source_file="raw_sales.csv",
            source_location="row 2, col 'Ship Date'",
            source_key_or_excerpt="15/01/2024",
            transform_applied="date_iso",
            confidence=1.0,
            llm_used=False,
        )
        store.add(trace, row_index=0)
        d = store.to_dict()
        self.assertIn("sales.csv", d)
        self.assertIn("rows", d["sales.csv"])
        row_entry = d["sales.csv"]["rows"][0]
        self.assertEqual(row_entry["row_index"], 0)
        self.assertIn("date", row_entry["fields"])
        self.assertEqual(row_entry["fields"]["date"]["source_key_or_excerpt"], "15/01/2024")

    def test_document_trace_stored_and_serialised(self):
        store = TraceabilityStore()
        trace = make_trace(
            canonical_file="device_context.json",
            canonical_field="intended_purpose",
            source_file="CER_v3.pdf",
            source_location="section 2",
            source_key_or_excerpt="intended to provide vascular access",
            transform_applied="llm_document_extraction",
            confidence=0.85,
            llm_used=True,
        )
        store.add(trace)
        d = store.to_dict()
        self.assertIn("device_context.json", d)
        self.assertIn("fields", d["device_context.json"])
        self.assertTrue(d["device_context.json"]["fields"]["intended_purpose"]["llm_used"])

    def test_llm_field_count(self):
        store = TraceabilityStore()
        store.add(make_trace("f.json", "field_a", "src", "loc", "val",
                              llm_used=False, confidence=1.0))
        store.add(make_trace("f.json", "field_b", "src", "loc", "val",
                              llm_used=True, confidence=0.8))
        self.assertEqual(store.llm_field_count(), 1)
        self.assertEqual(store.total_field_count(), 2)


# ---------------------------------------------------------------------------
# End-to-end pipeline tests (no LLM)
# ---------------------------------------------------------------------------

class TestEndToEnd(unittest.TestCase):

    def _run(self, fixture_name: str) -> dict:
        from input_standardizer.service import standardize
        input_dir = os.path.join(FIXTURES, fixture_name)
        if not os.path.isdir(input_dir):
            self.skipTest(f"Fixture directory not found: {input_dir}")
        with tempfile.TemporaryDirectory() as tmp:
            result = standardize(input_dir=input_dir, output_dir=tmp, use_llm=False)
            manifest_path = os.path.join(tmp, "preparer_manifest.json")
            issues_path = os.path.join(tmp, "preparer_issues.json")
            trace_path = os.path.join(tmp, "source_traceability.json")

            with open(manifest_path, encoding="utf-8") as fh:
                manifest = json.load(fh)
            with open(issues_path, encoding="utf-8") as fh:
                issues_data = json.load(fh)
            with open(trace_path, encoding="utf-8") as fh:
                trace_data = json.load(fh)

        return {
            "result": result,
            "manifest": manifest,
            "issues": issues_data,
            "trace": trace_data,
        }

    def test_clean_package_is_ready(self):
        data = self._run("clean_package")
        self.assertTrue(
            data["result"].ready,
            f"Expected clean package to be ready. Summary: "
            f"{data['manifest']['readiness']['summary']}"
        )

    def test_clean_package_produces_audit_files(self):
        data = self._run("clean_package")
        outputs = data["manifest"]["outputs_written"]
        self.assertIn("preparer_manifest.json", outputs)
        self.assertIn("preparer_issues.json", outputs)
        self.assertIn("source_traceability.json", outputs)

    def test_clean_package_sales_written(self):
        data = self._run("clean_package")
        self.assertIn("sales.csv", data["manifest"]["outputs_written"])

    def test_clean_package_traceability_populated(self):
        data = self._run("clean_package")
        self.assertTrue(len(data["trace"]) > 0, "Traceability should not be empty")

    def test_messy_package_classifies_and_maps(self):
        data = self._run("messy_package")
        outputs = data["manifest"]["outputs_written"]
        # Should produce at least sales and complaints despite messy headers
        self.assertIn("sales.csv", outputs)
        self.assertIn("complaints.csv", outputs)

    def test_partial_package_is_not_ready(self):
        data = self._run("partial_package")
        self.assertFalse(
            data["result"].ready,
            "Partial package should NOT be ready (missing required fields)"
        )

    def test_partial_package_has_major_issues(self):
        data = self._run("partial_package")
        severity_counts = data["manifest"]["issue_counts_by_severity"]
        total_significant = (
            severity_counts.get("CRITICAL", 0) + severity_counts.get("MAJOR", 0)
        )
        self.assertGreater(total_significant, 0)

    def test_conflicting_package_has_conflict_issues(self):
        data = self._run("conflicting_package")
        issue_codes = [i["code"] for i in data["issues"]["issues"]]
        # Should flag duplicate sales candidates or conflict in device context
        conflict_codes = {IssueCode.DUPLICATE_CANDIDATE.value,
                          IssueCode.CONFLICTING_FIELD_VALUES.value,
                          IssueCode.DUPLICATE_COMPLAINT_IDENTITY.value}
        found = set(issue_codes) & conflict_codes
        self.assertTrue(len(found) > 0,
                        f"Expected conflict issues, got codes: {issue_codes}")

    def test_audit_files_always_written(self):
        """Even a completely empty input should produce the three audit files."""
        from input_standardizer.service import standardize
        with tempfile.TemporaryDirectory() as empty_in:
            with tempfile.TemporaryDirectory() as tmp_out:
                result = standardize(input_dir=empty_in, output_dir=tmp_out, use_llm=False)
                for fname in ("preparer_manifest.json", "preparer_issues.json",
                              "source_traceability.json"):
                    path = os.path.join(tmp_out, fname)
                    self.assertTrue(os.path.exists(path),
                                    f"Audit file missing: {fname}")
        self.assertFalse(result.ready)

    def test_cli_defaults_to_data_and_output_folders(self):
        """No-argument CLI runs against ./Data and writes audit files to ./Output."""
        from input_standardizer.cli import main

        old_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                with redirect_stdout(StringIO()):
                    exit_code = main(["--no-llm"])
                self.assertEqual(exit_code, 1)
                self.assertTrue(os.path.isdir("Data"))
                self.assertTrue(os.path.isdir("Output"))
                for fname in ("preparer_manifest.json", "preparer_issues.json",
                              "source_traceability.json"):
                    self.assertTrue(os.path.exists(os.path.join("Output", fname)))
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
