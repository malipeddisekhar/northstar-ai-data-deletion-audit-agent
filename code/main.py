#!/usr/bin/env python3
"""
Northstar Shop — AI Data Deletion Audit Agent
Entry point: python code/main.py

Runs the full audit pipeline and writes output.csv to the repository root.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure code/ is on sys.path when run directly
sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_engine.dataset_loader import load_dataset
from audit_engine.evaluator import evaluate_against_samples, format_evaluation_report
from audit_engine.output_writer import validate_output_csv, write_output_csv
from audit_engine.pipeline import run_audit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Northstar Shop — AI Data Deletion Audit Agent"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path for output.csv (default: repo root/output.csv)",
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Run evaluation against sample outputs after generating output.csv",
    )
    parser.add_argument(
        "--sample-only",
        action="store_true",
        help="Only process sample requests (for faster testing)",
    )
    parser.add_argument(
        "--case",
        type=str,
        default=None,
        help="Process a single case_id (for debugging)",
    )
    args = parser.parse_args()

    logger.info("Loading dataset …")
    bundle = load_dataset()

    requests = bundle.deletion_requests
    if args.sample_only:
        sample_ids = {r.case_id for r in bundle.sample_requests}
        requests = [r for r in bundle.deletion_requests if r.case_id in sample_ids]
        if not requests:
            # fall back to using sample_requests directly
            requests = bundle.sample_requests
        logger.info("Sample-only mode: %d requests", len(requests))

    if args.case:
        requests = [r for r in requests if r.case_id == args.case]
        if not requests:
            logger.error("Case %s not found", args.case)
            sys.exit(1)
        logger.info("Single-case mode: %s", args.case)

    logger.info("Running audit on %d requests …", len(requests))

    # Temporarily patch bundle.deletion_requests so run_audit uses filtered list
    original = bundle.deletion_requests
    bundle.deletion_requests = requests

    results = run_audit(bundle)

    bundle.deletion_requests = original

    # Write output
    target_output = args.output
    if target_output is None and args.sample_only:
        target_output = Path(__file__).resolve().parent.parent / "output_sample.csv"
    output_path = write_output_csv(results, target_output)
    logger.info("Output written to: %s", output_path)

    # Validate
    expected_ids = [r.case_id for r in requests]
    validation = validate_output_csv(output_path, expected_ids)
    if validation["valid"]:
        logger.info("Output validation PASSED (%d rows)", validation["row_count"])
    else:
        logger.warning("Output validation FAILED:")
        for err in validation["errors"]:
            logger.warning("  %s", err)

    # Evaluation
    if args.evaluate or args.sample_only:
        logger.info("Running evaluation against sample outputs …")
        report = evaluate_against_samples(results)
        print(format_evaluation_report(report))
        print(f"\nStatus accuracy:  {report.status_accuracy:.1%}")
        print(f"Step precision:   {report.step_precision:.1%}")
        print(f"Step recall:      {report.step_recall:.1%}")
        print(f"Order accuracy:   {report.operation_order_accuracy:.1%}")

    # Print summary
    status_counts: dict[str, int] = {}
    for r in results:
        status_counts[r.deletion_status] = status_counts.get(r.deletion_status, 0) + 1
    print("\n=== Audit Summary ===")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")
    print(f"  Total: {len(results)}")


if __name__ == "__main__":
    main()
