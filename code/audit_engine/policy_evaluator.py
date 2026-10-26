"""
Policy Evaluator — applies deletion_rules.csv to each evidence record
and determines the required treatment and findings.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .dataset_loader import DeletionRule
from .evidence_investigator import EvidenceRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mapping from data_category to required action
# ---------------------------------------------------------------------------

# Actions from deletion_rules.csv
ACTION_DELETE = "DELETE"
ACTION_REDACT = "REDACT"
ACTION_ANONYMIZE = "ANONYMIZE"
ACTION_RETAIN = "RETAIN"

# States that mean the record is already in the required state
_COMPLIANT_STATES: Dict[str, List[str]] = {
    ACTION_DELETE: ["ABSENT"],
    ACTION_REDACT: ["REDACTED"],
    ACTION_ANONYMIZE: ["ANONYMIZED"],
    ACTION_RETAIN: ["PRESENT"],
}


@dataclass
class PolicyDecision:
    record: EvidenceRecord
    required_action: str        # DELETE / REDACT / ANONYMIZE / RETAIN
    rule_id: str
    is_compliant: bool
    required_treatment: str     # operation to apply if non-compliant
    finding_operation: str      # REPORT_REMAINING_RECORD / REPORT_EXPOSED_PII / etc.
    finding_state: str          # PRESENT / ACTIVE
    remediation_operation: str  # DELETE_RECORD / REDACT_RECORD / ANONYMIZE_RECORD
    remediation_state: str      # ABSENT / REDACTED / ANONYMIZED
    verify_operation: str       # VERIFY_RECORD_ABSENT / etc.
    verify_state: str           # ABSENT / PRESENT / REDACTED / ANONYMIZED


@dataclass
class FlowRiskDecision:
    """Decision for data flow or restore risks."""
    record: EvidenceRecord
    rule_id: str
    is_compliant: bool


def build_rule_index(rules: List[DeletionRule]) -> Dict[str, DeletionRule]:
    return {r.data_category: r for r in rules}


def evaluate_record(
    record: EvidenceRecord,
    rule_index: Dict[str, DeletionRule],
) -> Optional[PolicyDecision]:
    """
    Apply the correct deletion rule to a single evidence record.
    Returns None if no applicable rule exists.
    """
    # Handle data flow risks (not in deletion_rules.csv as a static rule)
    if record.data_category == "DATA_FLOW":
        is_compliant = record.state in ("DISABLED",)
        return PolicyDecision(
            record=record,
            required_action="DISABLE",
            rule_id="data_flow_policy",
            is_compliant=is_compliant,
            required_treatment="DISABLE_DATA_FLOW",
            finding_operation="REPORT_ACTIVE_DATA_FLOW",
            finding_state="ACTIVE",
            remediation_operation="DISABLE_DATA_FLOW",
            remediation_state="DISABLED",
            verify_operation="VERIFY_DATA_FLOW_DISABLED",
            verify_state="DISABLED",
        )

    rule = rule_index.get(record.data_category)
    if rule is None:
        logger.debug("No rule for category %s", record.data_category)
        return None

    required_action = rule.required_action
    compliant_states = _COMPLIANT_STATES.get(required_action, [])
    is_compliant = record.state in compliant_states

    if record.data_category == "BACKUP_ARCHIVE":
        return _evaluate_backup(record, rule, is_compliant)

    # Standard record evaluations
    return _evaluate_standard(record, rule, required_action, is_compliant)


def _evaluate_standard(
    record: EvidenceRecord,
    rule: DeletionRule,
    required_action: str,
    is_compliant: bool,
) -> PolicyDecision:
    if required_action == ACTION_DELETE:
        return PolicyDecision(
            record=record,
            required_action=required_action,
            rule_id=rule.rule_id,
            is_compliant=is_compliant,
            required_treatment="DELETE_RECORD",
            finding_operation="REPORT_REMAINING_RECORD",
            finding_state="PRESENT",
            remediation_operation="DELETE_RECORD",
            remediation_state="ABSENT",
            verify_operation="VERIFY_RECORD_ABSENT",
            verify_state="ABSENT",
        )
    if required_action == ACTION_REDACT:
        return PolicyDecision(
            record=record,
            required_action=required_action,
            rule_id=rule.rule_id,
            is_compliant=is_compliant,
            required_treatment="REDACT_RECORD",
            finding_operation="REPORT_EXPOSED_PII",
            finding_state="PRESENT",
            remediation_operation="REDACT_RECORD",
            remediation_state="REDACTED",
            verify_operation="VERIFY_RECORD_REDACTED",
            verify_state="REDACTED",
        )
    if required_action == ACTION_ANONYMIZE:
        return PolicyDecision(
            record=record,
            required_action=required_action,
            rule_id=rule.rule_id,
            is_compliant=is_compliant,
            required_treatment="ANONYMIZE_RECORD",
            finding_operation="REPORT_EXPOSED_PII" if record.state == "PRESENT" else "REPORT_REMAINING_RECORD",
            finding_state="PRESENT",
            remediation_operation="ANONYMIZE_RECORD",
            remediation_state="ANONYMIZED",
            verify_operation="VERIFY_RECORD_ANONYMIZED",
            verify_state="ANONYMIZED",
        )
    if required_action == ACTION_RETAIN:
        return PolicyDecision(
            record=record,
            required_action=required_action,
            rule_id=rule.rule_id,
            is_compliant=is_compliant,
            required_treatment="RETAIN_RECORD",
            finding_operation="REPORT_REMAINING_RECORD",  # shouldn't be found as a violation
            finding_state="PRESENT",
            remediation_operation="",
            remediation_state="",
            verify_operation="VERIFY_RECORD_PRESENT",
            verify_state="PRESENT",
        )
    # fallback
    return PolicyDecision(
        record=record,
        required_action=required_action,
        rule_id=rule.rule_id,
        is_compliant=is_compliant,
        required_treatment="DELETE_RECORD",
        finding_operation="REPORT_REMAINING_RECORD",
        finding_state="PRESENT",
        remediation_operation="DELETE_RECORD",
        remediation_state="ABSENT",
        verify_operation="VERIFY_RECORD_ABSENT",
        verify_state="ABSENT",
    )


def _evaluate_data_flow(
    record: EvidenceRecord,
    rule: DeletionRule,
    is_compliant: bool,
) -> PolicyDecision:
    """Data flows that are ACTIVE are risks; DISABLED flows are compliant."""
    is_compliant = record.state in ("DISABLED",)
    return PolicyDecision(
        record=record,
        required_action="DISABLE",
        rule_id=rule.rule_id,
        is_compliant=is_compliant,
        required_treatment="DISABLE_DATA_FLOW",
        finding_operation="REPORT_ACTIVE_DATA_FLOW",
        finding_state="ACTIVE",
        remediation_operation="DISABLE_DATA_FLOW",
        remediation_state="DISABLED",
        verify_operation="VERIFY_DATA_FLOW_DISABLED",
        verify_state="DISABLED",
    )


def _evaluate_backup(
    record: EvidenceRecord,
    rule: DeletionRule,
    is_compliant: bool,
) -> PolicyDecision:
    """Backup archives with ACTIVE restore are risks; BLOCKED are compliant."""
    is_compliant = record.state in ("BLOCKED",)
    return PolicyDecision(
        record=record,
        required_action="BLOCK_RESTORE",
        rule_id=rule.rule_id,
        is_compliant=is_compliant,
        required_treatment="BLOCK_RESTORE",
        finding_operation="REPORT_RESTORE_RISK",
        finding_state="ACTIVE",
        remediation_operation="BLOCK_RESTORE",
        remediation_state="BLOCKED",
        verify_operation="VERIFY_RESTORE_BLOCKED",
        verify_state="BLOCKED",
    )


def evaluate_all(
    records: List[EvidenceRecord],
    rules: List[DeletionRule],
) -> List[PolicyDecision]:
    """Evaluate all evidence records against deletion rules."""
    rule_index = build_rule_index(rules)
    decisions: List[PolicyDecision] = []
    for record in records:
        decision = evaluate_record(record, rule_index)
        if decision is not None:
            decisions.append(decision)
    return decisions
