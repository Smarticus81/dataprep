"""
CLI entry point for the PSUR Input Standardizer.

Usage:
    python -m input_standardizer.cli
    python -m input_standardizer.cli --input <raw_dir> --output <canonical_dir>

Options:
    --input   PATH      Raw source package directory (default: Data)
    --output  PATH      Canonical output directory (default: Output)
    --no-llm            Disable LLM-assisted classification and extraction
    --run-id  STRING    Optional run identifier for traceability
    --verbose           Enable DEBUG logging
    --quiet             Suppress all output except errors

Exit codes:
    0  — package is ready for downstream PSUR generation
    1  — package is NOT ready (blocking issues remain)
    2  — fatal error (missing arguments, unreadable directory, etc.)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from .service import standardize

DEFAULT_INPUT_DIR = "Data"
DEFAULT_OUTPUT_DIR = "Output"


def _configure_logging(verbose: bool, quiet: bool) -> None:
    if quiet:
        level = logging.ERROR
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO
    logging.basicConfig(
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
        level=level,
    )


def _print_readiness_summary(output_dir: str) -> None:
    manifest_path = os.path.join(output_dir, "preparer_manifest.json")
    issues_path = os.path.join(output_dir, "preparer_issues.json")

    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        readiness = manifest.get("readiness", {})
        print("\n" + "=" * 60)
        print("PSUR INPUT STANDARDIZER - READINESS SUMMARY")
        print("=" * 60)
        status = "READY" if readiness.get("ready_for_psur_pipeline") else "NOT READY"
        print(f"Status:  {status}")
        print(f"Summary: {readiness.get('summary', 'N/A')}")
        counts = manifest.get("issue_counts_by_severity", {})
        print(
            f"Issues:  CRITICAL={counts.get('CRITICAL', 0)}  "
            f"MAJOR={counts.get('MAJOR', 0)}  "
            f"MINOR={counts.get('MINOR', 0)}  "
            f"INFO={counts.get('INFO', 0)}"
        )
        conf = manifest.get("confidence_summary", {})
        print(
            f"Fields:  {conf.get('total_traced_fields', 0)} traced, "
            f"{conf.get('llm_assisted_fields', 0)} LLM-assisted "
            f"({conf.get('llm_fraction', 0):.0%})"
        )
        outputs = manifest.get("outputs_written", [])
        print(f"Outputs: {len(outputs)} canonical file(s) written to {output_dir}")
        print("=" * 60)

    if os.path.exists(issues_path):
        with open(issues_path, encoding="utf-8") as fh:
            issues_data = json.load(fh)
        blocking = [
            i for i in issues_data.get("issues", [])
            if i.get("severity") == "CRITICAL"
        ]
        if blocking:
            print("\nBlocking issues requiring attention:")
            for issue in blocking[:10]:
                print(f"  [{issue['code']}] {issue['title']}")
                print(f"    {issue['detail']}")
                if issue.get("suggested_action"):
                    print(f"    -> {issue['suggested_action']}")
            if len(blocking) > 10:
                print(f"  ... and {len(blocking) - 10} more.")

    print(f"\nFull details:")
    print(f"  Issues:       {os.path.join(output_dir, 'preparer_issues.json')}")
    print(f"  Traceability: {os.path.join(output_dir, 'source_traceability.json')}")
    print(f"  Manifest:     {os.path.join(output_dir, 'preparer_manifest.json')}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m input_standardizer.cli",
        description="PSUR Input Standardizer - transforms raw source packages into "
                    "canonical PSUR input packages.",
    )
    parser.add_argument(
        "--input", default=DEFAULT_INPUT_DIR, metavar="DIR",
        help=f"Raw source package directory to process (default: {DEFAULT_INPUT_DIR}).",
    )
    parser.add_argument(
        "--output", default=DEFAULT_OUTPUT_DIR, metavar="DIR",
        help=f"Output directory for the canonical package (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Disable LLM-assisted classification and extraction.",
    )
    parser.add_argument(
        "--run-id", default=None, metavar="ID",
        help="Optional run identifier for traceability (default: auto-generated).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG logging.",
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress output except errors.",
    )

    args = parser.parse_args(argv)
    _configure_logging(args.verbose, args.quiet)

    if args.input == DEFAULT_INPUT_DIR:
        os.makedirs(args.input, exist_ok=True)
    if args.output == DEFAULT_OUTPUT_DIR:
        os.makedirs(args.output, exist_ok=True)

    if not os.path.isdir(args.input):
        print(f"ERROR: Input directory does not exist: {args.input}", file=sys.stderr)
        return 2

    try:
        result = standardize(
            input_dir=args.input,
            output_dir=args.output,
            use_llm=not args.no_llm,
            run_id=args.run_id,
        )
    except Exception as exc:
        logging.error(f"Fatal error during standardization: {exc}", exc_info=True)
        return 2

    if not args.quiet:
        _print_readiness_summary(result.output_dir)

    return 0 if result.ready else 1


if __name__ == "__main__":
    sys.exit(main())
