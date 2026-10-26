"""
Evaluation module — compares generated output against sample_output.csv.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .dataset_loader import load_sample_outputs
from .pipeline import AuditResult

logger = logging.getLogger(__name__)


@dataclass
class CaseEvaluation:
    case_id: str
    expected_status: str
    predicted_status: str
    status_correct: bool
    expected_steps: List[Dict]
    predicted_steps: List[Dict]
    step_count_expected: int
    step_count_predicted: int
    matching_steps: int
    operation_order_correct: bool
    notes: List[str] = field(default_factory=list)


@dataclass
class EvaluationReport:
    total_cases: int
    status_accuracy: float
    step_precision: float
    step_recall: float
    operation_order_accuracy: float
    case_evaluations: List[CaseEvaluation]


def _step_key(step: Dict) -> Tuple:
    return (
        step.get("system_id", ""),
        step.get("resource_id", ""),
        step.get("operation", ""),
        step.get("phase", ""),
    )


def _evaluate_case(
    case_id: str,
    expected: Dict[str, Any],
    predicted: Optional[AuditResult],
) -> CaseEvaluation:
    expected_status = expected["deletion_status"]
    expected_steps = expected.get("steps", [])

    if predicted is None:
        return CaseEvaluation(
            case_id=case_id,
            expected_status=expected_status,
            predicted_status="MISSING",
            status_correct=False,
            expected_steps=expected_steps,
            predicted_steps=[],
            step_count_expected=len(expected_steps),
            step_count_predicted=0,
            matching_steps=0,
            operation_order_correct=False,
            notes=["Predicted result missing"],
        )

    predicted_status = predicted.deletion_status
    predicted_steps = predicted.steps
    status_correct = predicted_status == expected_status

    # Step matching by (system_id, resource_id, operation, phase)
    expected_keys = [_step_key(s) for s in expected_steps]
    predicted_keys = [_step_key(s) for s in predicted_steps]

    expected_set = set(expected_keys)
    predicted_set = set(predicted_keys)
    matching = len(expected_set & predicted_set)

    # Operation order correctness: check that phase ordering is FINDING→REMEDIATION→VERIFICATION
    order_correct = _check_phase_order(predicted_steps)

    notes = []
    if not status_correct:
        notes.append(f"Status: expected={expected_status}, got={predicted_status}")
    if len(predicted_steps) != len(expected_steps):
        notes.append(f"Step count: expected={len(expected_steps)}, got={len(predicted_steps)}")
    extra = predicted_set - expected_set
    missing = expected_set - predicted_set
    if missing:
        notes.append(f"Missing steps: {list(missing)[:3]}")
    if extra:
        notes.append(f"Extra steps: {list(extra)[:3]}")

    return CaseEvaluation(
        case_id=case_id,
        expected_status=expected_status,
        predicted_status=predicted_status,
        status_correct=status_correct,
        expected_steps=expected_steps,
        predicted_steps=predicted_steps,
        step_count_expected=len(expected_steps),
        step_count_predicted=len(predicted_steps),
        matching_steps=matching,
        operation_order_correct=order_correct,
        notes=notes,
    )


def _check_phase_order(steps: List[Dict]) -> bool:
    """Verify that phases appear in FINDING→REMEDIATION→VERIFICATION order."""
    phase_order = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
    last_phase_val = -1
    for step in steps:
        phase = step.get("phase", "")
        val = phase_order.get(phase, -1)
        if val < last_phase_val:
            return False
        last_phase_val = val
    return True


def evaluate_against_samples(
    results: List[AuditResult],
) -> EvaluationReport:
    """Compare results against the public sample outputs."""
    sample_outputs = load_sample_outputs()
    result_by_id = {r.case_id: r for r in results}

    evaluations: List[CaseEvaluation] = []
    for case_id, expected in sample_outputs.items():
        predicted = result_by_id.get(case_id)
        ev = _evaluate_case(case_id, expected, predicted)
        evaluations.append(ev)

    total = len(evaluations)
    if total == 0:
        return EvaluationReport(
            total_cases=0,
            status_accuracy=0.0,
            step_precision=0.0,
            step_recall=0.0,
            operation_order_accuracy=0.0,
            case_evaluations=[],
        )

    status_correct = sum(1 for e in evaluations if e.status_correct)
    total_expected_steps = sum(e.step_count_expected for e in evaluations)
    total_predicted_steps = sum(e.step_count_predicted for e in evaluations)
    total_matching_steps = sum(e.matching_steps for e in evaluations)
    order_correct = sum(1 for e in evaluations if e.operation_order_correct)

    precision = total_matching_steps / max(total_predicted_steps, 1)
    recall = total_matching_steps / max(total_expected_steps, 1)

    return EvaluationReport(
        total_cases=total,
        status_accuracy=status_correct / total,
        step_precision=precision,
        step_recall=recall,
        operation_order_accuracy=order_correct / total,
        case_evaluations=evaluations,
    )


def format_evaluation_report(report: EvaluationReport) -> str:
    """Format an evaluation report as markdown text."""
    lines = [
        "# Evaluation Report",
        "",
        f"**Cases evaluated:** {report.total_cases}",
        f"**Status accuracy:** {report.status_accuracy:.1%}",
        f"**Step precision:** {report.step_precision:.1%}",
        f"**Step recall:** {report.step_recall:.1%}",
        f"**Operation order accuracy:** {report.operation_order_accuracy:.1%}",
        "",
        "## Per-Case Results",
        "",
        "| Case | Expected | Predicted | Status OK | Steps Exp | Steps Pred | Matching |",
        "|------|----------|-----------|-----------|-----------|------------|---------|",
    ]
    for ev in report.case_evaluations:
        ok = "PASS" if ev.status_correct else "FAIL"
        lines.append(
            f"| {ev.case_id} | {ev.expected_status} | {ev.predicted_status} | {ok}"
            f" | {ev.step_count_expected} | {ev.step_count_predicted} | {ev.matching_steps} |"
        )
    return "\n".join(lines)
