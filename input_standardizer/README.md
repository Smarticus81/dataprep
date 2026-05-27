# PSUR Input Standardizer

## What it is

The **PSUR Input Standardizer** is an upstream preprocessing component that transforms
messy, inconsistent raw input packages into clean, canonical, auditable input packages
ready for downstream PSUR (Periodic Safety Update Report) generation.

It is **not** a PSUR generator. It stops before report generation begins. Its sole
responsibility is to turn raw source files into a stable, validated canonical package
that a downstream PSUR generator can trust.

---

## Why it exists

Downstream PSUR generation assumes clean, well-structured input. Real-world source
packages are not clean:

- Filenames vary by client
- CSV headers vary ("Complaint #", "complaint no", "Case ID" all mean the same thing)
- Some spreadsheets start headers at row 3
- Dates come in DD/MM/YYYY, MM/DD/YYYY, and ISO formats — sometimes mixed in the same file
- Some required data only exists inside DOCX or PDF documents (CER, IFU, RMF)
- Different clients use different terminology for the same regulatory concept
- Some packages contain multiple files for the same logical data type
- Some files have placeholder values ("N/A", "TBD") where real values are required

The standardizer solves all of this before the PSUR generator ever sees the data.

---

## What goes in

A directory containing any combination of:

| File type       | Examples                                    |
|-----------------|---------------------------------------------|
| CSV             | Sales report, complaints log, CAPA register |
| XLSX            | Multi-sheet workbooks with any of the above |
| JSON            | device_context, ract, pms_plan, pmcf        |
| DOCX            | CER, IFU, RMF, PMS Plan document           |
| PDF             | CER, IFU, published literature             |

File naming does not need to follow any convention. The standardizer classifies files
by content, not by name (though well-named files improve classification confidence).

---

## What comes out

A canonical output directory:

```
canonical_package/
├── sales.csv                  # normalised shipment data
├── complaints.csv             # deduplicated, normalised complaints
├── capa.csv                   # corrective/preventive actions
├── fsca.csv                   # field safety corrective actions
├── device_context.json        # full device regulatory identity
├── ract.json                  # risk acceptability criteria table
├── previous_psur.json         # prior PSUR summary
├── pms_plan.json              # post-market surveillance plan
├── pmcf.json                  # post-market clinical follow-up
├── literature_search.json     # literature search results
├── external_events.csv        # external database events (MAUDE, EUDAMED)
├── coding_dictionary.json     # IMDRF Annex A/F codes
├── preparer_manifest.json     # always present — run metadata
├── preparer_issues.json       # always present — all issues found
└── source_traceability.json   # always present — field-level provenance
```

The three audit files (`preparer_manifest.json`, `preparer_issues.json`,
`source_traceability.json`) are **always** written, even if the package is empty or
the run fails. They are the primary human-readable output.

---

## What "ready for PSUR pipeline" means

The standardizer makes a binary **ready / not-ready** decision at the end of every run.

**Ready** means all of the following are true:

1. All six core canonical inputs were produced:
   `sales.csv`, `complaints.csv`, `device_context.json`, `ract.json`,
   `previous_psur.json`, `pms_plan.json`
2. Each produced core file meets the minimum completeness threshold (≥70% of required
   fields populated)
3. Device identity is sufficiently resolved (key `device_context.json` fields present)
4. No CRITICAL-severity issues remain
5. No canonical schema failures remain

**Not ready** does not mean the data is unusable — it means at least one blocking
issue must be resolved before the downstream PSUR generator should proceed.

Non-blocking (MAJOR, MINOR, INFO) issues do not prevent readiness but are recorded
for human review.

---

## Deterministic vs LLM-assisted

The standardizer uses a **deterministic-first** approach. LLM assistance is a fallback
only — never the primary path.

### Always deterministic

| Task                              | Method                                      |
|-----------------------------------|---------------------------------------------|
| Filename-based classification     | Regex pattern matching                      |
| Header-to-field mapping (known)   | Exact match → alias match → normalized match |
| Date normalization                | Multi-format parser (ISO, DD/MM/YYYY, etc.) |
| Boolean normalization             | Yes/No/Y/N/1/0 lookup                       |
| Country normalization             | ISO 3166-1 lookup table                     |
| Region derivation from country    | Static country→region lookup                |
| Enum normalization                | Case-insensitive canonical set matching     |
| Placeholder detection             | Token set: "N/A", "TBD", "-", "", etc.      |
| Uniqueness checking               | Exact key comparison                        |
| Readiness gate                    | Rule-based, no ML                           |

### LLM-assisted (only when deterministic fails)

| Task                              | Trigger condition                           |
|-----------------------------------|---------------------------------------------|
| File classification               | Filename + headers not sufficient           |
| Header-to-field mapping           | Alias + fuzzy match both fail               |
| Document field extraction         | Structured data absent; only DOCX/PDF present |

The LLM (Claude via Anthropic SDK) is explicitly instructed:
- Never fabricate absent regulatory values
- Return `null` when a field is not present in the source text
- Assign a confidence score to every extraction

Every LLM-assisted field is flagged in `source_traceability.json` with `"llm_used": true`.

To disable LLM entirely: use `--no-llm` on the CLI.

---

## How to run

### Prerequisites

```bash
pip install openpyxl python-docx pdfplumber anthropic
```

Required: `openpyxl` (XLSX reading).  
Optional: `python-docx` (DOCX text extraction), `pdfplumber` or `pymupdf` (PDF extraction).  
Optional: `anthropic` (LLM-assisted classification/extraction; requires `ANTHROPIC_API_KEY`).

### CLI

By default, the project uses the top-level folders `Data/` and `Output/`.
Drop the raw source files into `Data/`, then run:

```bash
python -m input_standardizer.cli
```

The canonical package and audit files will be written to `Output/`.

You can still override either folder when needed:

```bash
python -m input_standardizer.cli \
  --input  path/to/raw_source_package/ \
  --output path/to/canonical_package/

# Disable LLM (fully deterministic)
python -m input_standardizer.cli \
  --input  path/to/raw/ \
  --output path/to/out/ \
  --no-llm

# Verbose mode
python -m input_standardizer.cli \
  --input  path/to/raw/ \
  --output path/to/out/ \
  --verbose
```

**Exit codes:**
- `0` — package is ready for downstream PSUR generation
- `1` — package is NOT ready (blocking issues exist)
- `2` — fatal error (bad arguments, unreadable directory)

### Python API

```python
from input_standardizer.service import standardize

result = standardize(
    input_dir="raw_source_package/",
    output_dir="canonical_package/",
    use_llm=True,          # default
    run_id="Q1-2024-run",  # optional, for traceability
)

print(result.ready)       # True / False
print(result.output_dir)  # path to canonical package
```

---

## How to review issues

Open `canonical_package/preparer_issues.json`. Issues are sorted by severity.

```json
{
  "total_issues": 3,
  "issues": [
    {
      "severity": "CRITICAL",
      "code": "IS-020",
      "title": "Required canonical field is missing",
      "detail": "Required field 'serious' is missing or empty in 'complaints.csv'.",
      "source_files": ["CustomerComplaints_Export.xlsx"],
      "canonical_target": "complaints.csv",
      "field_name": "serious",
      "suggested_action": "Ensure a 'Serious (Y/N)' column is present in the source data."
    }
  ]
}
```

**Severity levels:**

| Severity | Meaning                                                  |
|----------|----------------------------------------------------------|
| CRITICAL | Blocks PSUR generation — must be resolved               |
| MAJOR    | Significant gap — PSUR may be incomplete without it     |
| MINOR    | Data quality concern — investigate but not blocking      |
| INFO     | Informational — LLM used, field inferred, etc.          |

---

## How to review traceability

Open `canonical_package/source_traceability.json`. Every field of regulatory
significance carries full provenance.

```json
{
  "device_context.json": {
    "fields": {
      "eu_mdr_classification_and_rule": {
        "source_file": "device_context.json",
        "source_location": "key 'eu_mdr_classification_and_rule'",
        "source_key_or_excerpt": "Class IIb, Rule 8",
        "transform_applied": "str_normalize",
        "confidence": 0.98,
        "llm_used": false
      },
      "intended_purpose": {
        "source_file": "CER_SurgiFlow_v3.1.pdf",
        "source_location": "document text",
        "source_key_or_excerpt": "intended to provide vascular access...",
        "transform_applied": "llm_document_extraction",
        "confidence": 0.88,
        "llm_used": true
      }
    }
  }
}
```

For any LLM-assisted field (`"llm_used": true`), the `source_key_or_excerpt` shows the
actual text from which the value was extracted. Review these fields during QA.

---

## How it integrates with the downstream PSUR generator

The downstream PSUR generator should:

1. Check `preparer_manifest.json` → `readiness.ready_for_psur_pipeline` is `true`
2. Read canonical CSV and JSON files from the output directory
3. Optionally read `source_traceability.json` to annotate generated PSUR sections
   with source provenance
4. Optionally read `preparer_issues.json` to include a preparation notes section

The canonical package format is the stable contract between this component and the
downstream generator. The downstream generator must never depend on raw source files
directly.

---

## Running the tests

```bash
python test_standardizer.py
```

Or with pytest:

```bash
pip install pytest
pytest test_standardizer.py -v
```

The test suite covers:
- Classification of all five fixture scenarios
- Header alias mapping (exact, alias, normalized, ambiguous)
- Value normalization (dates, booleans, countries, enums, placeholders)
- Readiness gate (pass/fail conditions)
- Traceability store serialization
- End-to-end pipeline against all five fixture packages

---

## Fixture scenarios

| Scenario              | Location                              | Tests                                      |
|-----------------------|---------------------------------------|--------------------------------------------|
| `clean_package`       | `fixtures/inputs/clean_package/`      | Happy path — all files present, well-formed |
| `messy_package`       | `fixtures/inputs/messy_package/`      | Headers at row 5, alias headers, messy country codes |
| `partial_package`     | `fixtures/inputs/partial_package/`    | Missing required fields — NOT READY        |
| `docheavy_package`    | `fixtures/inputs/docheavy_package/`   | device_context enriched from CER text      |
| `conflicting_package` | `fixtures/inputs/conflicting_package/`| Duplicate files, conflicting field values  |

---

## Module structure

```
input_standardizer/
├── __init__.py             # package version
├── cli.py                  # CLI entry point
├── service.py              # programmatic API
├── pipeline.py             # orchestration (no domain logic)
├── classifier.py           # file discovery and classification
├── schema_registry.py      # canonical schemas — single source of truth
├── contracts.py            # typed data structures
├── header_mapper.py        # source header → canonical field mapping
├── row_locator.py          # header row detection in spreadsheets
├── value_normalizers.py    # date/bool/country/enum normalization
├── readiness_gate.py       # go/no-go decision
├── issue_model.py          # PreparerIssue, severity, issue codes
├── traceability_model.py   # TraceabilityStore, FieldTrace
├── manifest_writer.py      # output writing
├── llm_assist.py           # LLM integration (explicit boundary)
├── extractors/
│   ├── csv_excel.py        # CSV and XLSX extraction
│   ├── json_loader.py      # JSON extraction
│   └── docx_pdf.py         # DOCX/PDF text extraction
└── mappers/
    ├── _base.py            # shared mapping helpers
    ├── sales_mapper.py
    ├── complaints_mapper.py
    ├── capa_mapper.py
    ├── fsca_mapper.py
    ├── device_context_mapper.py
    ├── ract_mapper.py
    ├── previous_psur_mapper.py
    ├── pms_plan_mapper.py
    ├── pmcf_mapper.py
    ├── literature_mapper.py
    ├── external_db_mapper.py
    └── coding_dictionary_mapper.py
```

---

## Regulatory context

This component supports PSUR preparation under:
- **EU MDR 2017/745, Article 86** — periodic safety update reports for Class IIa, IIb, III devices
- **MDCG 2022-21** — guidance on PSUR content and format
- **UK MDR 2002 (as amended 2024)** — UK PMS and PSUR requirements

The standardizer does not generate regulatory conclusions. All benefit-risk assessment,
complaint-rate interpretation, and PSUR prose remain the responsibility of the
downstream PSUR generator and the qualified regulatory professional reviewing the output.
