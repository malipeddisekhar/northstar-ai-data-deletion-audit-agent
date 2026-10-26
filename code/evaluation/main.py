#!/usr/bin/env python3
"""
Northstar Shop — AI Data Deletion Audit Agent
Evaluation Entry Point: python code/evaluation/main.py
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Add code/ dir to sys.path
code_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(code_dir))

from audit_engine.dataset_loader import load_dataset, load_sample_outputs
from audit_engine.evaluator import evaluate_against_samples, format_evaluation_report
from audit_engine.output_writer import validate_output_csv
from audit_engine.pipeline import run_audit

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("evaluation.main")


def main() -> None:
    repo_root = code_dir.parent
    output_path = repo_root / "output.csv"

    logger.info("Loading dataset and sample outputs…")
    bundle = load_dataset()

    logger.info("Evaluating against sample cases…")
    sample_bundle = load_dataset()
    sample_bundle.deletion_requests = sample_bundle.sample_requests
    results = run_audit(sample_bundle)
    report = evaluate_against_samples(results)

    print("\n" + "=" * 60)
    print("NORTHSTAR SHOP — AI DATA DELETION AUDIT AGENT")
    print("BENCHMARK EVALUATION REPORT")
    print("=" * 60)
    print(format_evaluation_report(report))
    print("-" * 60)
    print(f"Total Cases Evaluated:       {report.total_cases}")
    print(f"Status Accuracy:             {report.status_accuracy:.1%}")
    print(f"Operation Order Accuracy:    {report.operation_order_accuracy:.1%}")
    print(f"Step Precision:              {report.step_precision:.1%}")
    print(f"Step Recall:                 {report.step_recall:.1%}")
    print("=" * 60 + "\n")

    if output_path.exists():
        expected_ids = [r.case_id for r in bundle.deletion_requests]
        validation = validate_output_csv(output_path, expected_ids)
        print(f"Root output.csv exists: {output_path.name}")
        print(f"Schema Validation: {'PASSED' if validation['valid'] else 'FAILED'}")
        print(f"Total Row Count: {validation.get('row_count', 0)} / 518")


if __name__ == "__main__":
    main()
