"""
Remediation Planner — generates ordered remediation and verification steps
from policy decisions, following the official operation ordering rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .policy_evaluator import PolicyDecision


# ---------------------------------------------------------------------------
# Step structure
# ---------------------------------------------------------------------------

@dataclass
class AuditStep:
    step_no: int
    phase: str          # FINDING | REMEDIATION | VERIFICATION
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str    # file_id, activity_id, or audit_id; empty for proposed steps


# ---------------------------------------------------------------------------
# Operation ordering
# ---------------------------------------------------------------------------

# Official operation order within each phase (problem_statement.md §Allowed Operations table)
_FINDING_ORDER = [
    "REPORT_REMAINING_RECORD",
    "REPORT_EXPOSED_PII",
    "REPORT_ACTIVE_DATA_FLOW",
    "REPORT_RESTORE_RISK",
]

_REMEDIATION_ORDER = [
    "DISABLE_DATA_FLOW",
    "BLOCK_RESTORE",
    "DELETE_RECORD",
    "REDACT_RECORD",
    "ANONYMIZE_RECORD",
]

_VERIFICATION_ORDER = [
    "VERIFY_RECORD_ABSENT",
    "VERIFY_RECORD_PRESENT",
    "VERIFY_RECORD_REDACTED",
    "VERIFY_RECORD_ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED",
    "VERIFY_RESTORE_BLOCKED",
]


def _op_order(operation: str, order_list: List[str]) -> int:
    try:
        return order_list.index(operation)
    except ValueError:
        return 999


def _sort_key(step: AuditStep, order_list: List[str]) -> Tuple:
    return (_op_order(step.operation, order_list), step.system_id, step.resource_id)


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

def generate_steps_for_incomplete(
    decisions: List[PolicyDecision],
    compliant_decisions: List[PolicyDecision],
    security_records: List[Tuple[str, str, str]],   # (system_id, resource_id, evidence_id)
    account_records: List[Tuple[str, str, str]],     # (system_id, resource_id, evidence_id) - absent accounts
) -> List[AuditStep]:
    """
    Generate the complete ordered step list for an INCOMPLETE case.
    Order: FINDING → REMEDIATION → VERIFICATION
    """
    findings: List[AuditStep] = []
    remediations: List[AuditStep] = []
    verifications: List[AuditStep] = []

    # Non-compliant decisions → findings + remediation + verification
    for dec in decisions:
        if dec.is_compliant:
            continue
        if dec.required_action == "RETAIN":
            continue

        findings.append(AuditStep(
            step_no=0,
            phase="FINDING",
            system_id=dec.record.system_id,
            resource_id=dec.record.resource_id,
            operation=dec.finding_operation,
            state=dec.finding_state,
            evidence_id=dec.record.file_id,
        ))

        if dec.remediation_operation:
            remediations.append(AuditStep(
                step_no=0,
                phase="REMEDIATION",
                system_id=dec.record.system_id,
                resource_id=dec.record.resource_id,
                operation=dec.remediation_operation,
                state=dec.remediation_state,
                evidence_id="",
            ))

        verifications.append(AuditStep(
            step_no=0,
            phase="VERIFICATION",
            system_id=dec.record.system_id,
            resource_id=dec.record.resource_id,
            operation=dec.verify_operation,
            state=dec.verify_state,
            evidence_id="",
        ))

    # Add absent account verifications (for records the system says are gone)
    for system_id, resource_id, evidence_id in account_records:
        verifications.append(AuditStep(
            step_no=0,
            phase="VERIFICATION",
            system_id=system_id,
            resource_id=resource_id,
            operation="VERIFY_RECORD_ABSENT",
            state="ABSENT",
            evidence_id="",
        ))

    # Compliant retained records → verification that they still exist
    for dec in compliant_decisions:
        if dec.required_action == "RETAIN":
            verifications.append(AuditStep(
                step_no=0,
                phase="VERIFICATION",
                system_id=dec.record.system_id,
                resource_id=dec.record.resource_id,
                operation="VERIFY_RECORD_PRESENT",
                state="PRESENT",
                evidence_id="",
            ))

    # Security audit records → always verify they are retained
    for system_id, resource_id, evidence_id in security_records:
        verifications.append(AuditStep(
            step_no=0,
            phase="VERIFICATION",
            system_id=system_id,
            resource_id=resource_id,
            operation="VERIFY_RECORD_PRESENT",
            state="PRESENT",
            evidence_id="",
        ))

    # Sort each phase
    findings.sort(key=lambda s: _sort_key(s, _FINDING_ORDER))
    remediations.sort(key=lambda s: _sort_key(s, _REMEDIATION_ORDER))
    verifications.sort(key=lambda s: _sort_key(s, _VERIFICATION_ORDER))

    # Deduplicate
    findings = _dedup(findings)
    remediations = _dedup(remediations)
    verifications = _dedup(verifications)

    # Number steps consecutively
    all_steps = findings + remediations + verifications
    for i, step in enumerate(all_steps, start=1):
        step.step_no = i

    return all_steps


def generate_steps_for_complete(
    all_decisions: List[PolicyDecision],
    security_records: List[Tuple[str, str, str]],
    account_records: List[Tuple[str, str, str]],
) -> List[AuditStep]:
    """
    Generate verification-only steps for a COMPLETE case.
    All steps must cite actual evidence (evidence_id from the decision's file_id).
    """
    verifications: List[AuditStep] = []

    for dec in all_decisions:
        verifications.append(AuditStep(
            step_no=0,
            phase="VERIFICATION",
            system_id=dec.record.system_id,
            resource_id=dec.record.resource_id,
            operation=dec.verify_operation,
            state=dec.verify_state,
            evidence_id=dec.record.file_id,
        ))

    # Account absent verifications
    for system_id, resource_id, evidence_id in account_records:
        verifications.append(AuditStep(
            step_no=0,
            phase="VERIFICATION",
            system_id=system_id,
            resource_id=resource_id,
            operation="VERIFY_RECORD_ABSENT",
            state="ABSENT",
            evidence_id=evidence_id,
        ))

    # Security records retained
    for system_id, resource_id, evidence_id in security_records:
        verifications.append(AuditStep(
            step_no=0,
            phase="VERIFICATION",
            system_id=system_id,
            resource_id=resource_id,
            operation="VERIFY_RECORD_PRESENT",
            state="PRESENT",
            evidence_id=evidence_id,
        ))

    verifications.sort(key=lambda s: _sort_key(s, _VERIFICATION_ORDER))
    verifications = _dedup(verifications)

    for i, step in enumerate(verifications, start=1):
        step.step_no = i

    return verifications


def _dedup(steps: List[AuditStep]) -> List[AuditStep]:
    """Remove duplicate (system_id, resource_id, operation) entries, keeping first."""
    seen = set()
    result = []
    for step in steps:
        key = (step.phase, step.system_id, step.resource_id, step.operation)
        if key not in seen:
            seen.add(key)
            result.append(step)
    return result


def steps_to_dicts(steps: List[AuditStep]) -> List[Dict]:
    return [
        {
            "step_no": s.step_no,
            "phase": s.phase,
            "system_id": s.system_id,
            "resource_id": s.resource_id,
            "operation": s.operation,
            "state": s.state,
            "evidence_id": s.evidence_id,
        }
        for s in steps
    ]
