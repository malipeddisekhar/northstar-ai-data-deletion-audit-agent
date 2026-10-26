"""
Main Audit Pipeline — orchestrates all agents and produces AuditResult per case.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .dataset_loader import (
    DatasetBundle,
    DeletionRequest,
    EvidenceFile,
    SystemActivity,
)
from .evidence_investigator import EvidenceRecord, InvestigationResult, investigate_evidence
from .identity_resolver import ResolvedIdentity, resolve_identity
from .policy_evaluator import PolicyDecision, evaluate_all
from .remediation_planner import (
    AuditStep,
    generate_steps_for_complete,
    generate_steps_for_incomplete,
    steps_to_dicts,
)
from .request_classifier import ClassificationResult, classify_request

logger = logging.getLogger(__name__)


@dataclass
class AuditResult:
    case_id: str
    deletion_status: str        # COMPLETE | INCOMPLETE | NOT_APPLICABLE
    steps: List[Dict[str, Any]]
    identity: Optional[ResolvedIdentity] = None
    classification: Optional[ClassificationResult] = None
    evidence_records: List[EvidenceRecord] = field(default_factory=list)
    decisions: List[PolicyDecision] = field(default_factory=list)
    parse_errors: List[str] = field(default_factory=list)


def _activity_mentions_identity(
    act: SystemActivity, identity: ResolvedIdentity
) -> bool:
    details = (act.details or "").upper()
    resource = (act.resource_id or "").upper()
    for e in identity.all_emails:
        if e.upper() in details or e.upper() in resource:
            return True
    if identity.user_id:
        uid = identity.user_id.upper()
        if uid in details or uid in resource:
            return True
        if uid.startswith("USER_"):
            num_part = uid[5:]
            try:
                num = int(num_part)
                if resource in (f"ACCOUNT_{num_part}", f"ACCOUNT_{num:03d}", f"ACCOUNT_{num}"):
                    return True
            except ValueError:
                pass
    return False


def _find_relevant_activity(
    identity: ResolvedIdentity,
    activities: List[SystemActivity],
    audit_at: str,
) -> List[SystemActivity]:
    """Find system_activity rows relevant to this identity, before audit_at."""
    from .evidence_investigator import _is_before_cutoff
    relevant = []
    for act in activities:
        if not _is_before_cutoff(act.event_time, audit_at):
            continue
        if _activity_mentions_identity(act, identity):
            relevant.append(act)
    return relevant


def _latest_activity_for_resource(
    activities: List[SystemActivity],
    resource_id: str,
    audit_at: str,
) -> Optional[SystemActivity]:
    """Return the latest activity row for a given resource_id before audit_at."""
    from .evidence_investigator import _is_before_cutoff
    matches = [
        a for a in activities
        if a.resource_id == resource_id and _is_before_cutoff(a.event_time, audit_at)
    ]
    if not matches:
        return None
    return max(matches, key=lambda a: a.event_time)


def _find_account_records_from_activity(
    identity: ResolvedIdentity,
    activities: List[SystemActivity],
    audit_at: str,
) -> List[Tuple[str, str, str]]:
    """
    Identify user account resources that were deleted per system_activity.
    Returns list of (system_id, resource_id, activity_id).
    """
    deleted = []
    for act in activities:
        if act.event_type not in ("DELETE_RECORD", "DELETION_COMPLETE"):
            continue
        if act.target_system not in ("account_service", "identity_service"):
            continue
        if not _activity_mentions_identity(act, identity):
            continue
        deleted.append((act.target_system, act.resource_id, act.activity_id))
    return deleted


def _extract_security_records(
    evidence_records: List[EvidenceRecord],
    best_evidence_id: str,
) -> List[Tuple[str, str, str]]:
    """Find security audit records to verify they are retained."""
    sec = []
    for rec in evidence_records:
        if rec.data_category == "SECURITY_AUDIT_LOG" and rec.state == "PRESENT":
            sec.append((rec.system_id, rec.resource_id, rec.file_id))
    return sec


def audit_case(
    request: DeletionRequest,
    bundle: DatasetBundle,
) -> AuditResult:
    """Run the full audit pipeline for a single deletion request."""

    # ------------------------------------------------------------------
    # Step 1: Classify the request
    # ------------------------------------------------------------------
    classification = classify_request(request.request_text)

    if not classification.is_deletion_request:
        return AuditResult(
            case_id=request.case_id,
            deletion_status="NOT_APPLICABLE",
            steps=[],
            classification=classification,
        )

    # ------------------------------------------------------------------
    # Step 2: Resolve identity
    # ------------------------------------------------------------------
    identity = resolve_identity(
        request.email,
        bundle.user_identities,
        bundle.audit_log,
    )

    # If we can't find any identity match, we still process evidence
    # using the supplied email as the only identifier
    if not identity.matched:
        identity.all_emails = [request.email]
        identity.current_email = request.email

    # ------------------------------------------------------------------
    # Step 3: Investigate evidence
    # ------------------------------------------------------------------
    inv: InvestigationResult = investigate_evidence(
        identity,
        bundle.evidence_files,
        request.audit_at,
    )
    evidence_records = inv.records

    # ------------------------------------------------------------------
    # Step 4: Find relevant system activity
    # ------------------------------------------------------------------
    relevant_activity = _find_relevant_activity(
        identity, bundle.system_activities, request.audit_at
    )

    # ------------------------------------------------------------------
    # Step 5: Apply deletion rules
    # ------------------------------------------------------------------
    decisions = evaluate_all(evidence_records, bundle.deletion_rules)

    non_compliant = [d for d in decisions if not d.is_compliant and d.required_action != "RETAIN"]
    compliant = [d for d in decisions if d.is_compliant]
    retained = [d for d in decisions if d.required_action == "RETAIN" and d.is_compliant]

    # ------------------------------------------------------------------
    # Step 6: Determine deletion status
    # ------------------------------------------------------------------
    # Security audit records (RETAIN category) don't block COMPLETE
    # Active data flows and backup restores DO block COMPLETE
    blocks_complete = [
        d for d in non_compliant
        if d.required_action not in ("RETAIN",)
    ]

    if blocks_complete:
        deletion_status = "INCOMPLETE"
    else:
        deletion_status = "COMPLETE"

    # ------------------------------------------------------------------
    # Step 7: Collect security/account records for verification
    # ------------------------------------------------------------------
    security_records = _extract_security_records(evidence_records, "")
    # Find account IDs that are ABSENT (deleted)
    absent_accounts: List[Tuple[str, str, str]] = []
    for rec in evidence_records:
        if rec.data_category == "USER_PROFILE" and rec.state == "ABSENT":
            absent_accounts.append((rec.system_id, rec.resource_id, rec.file_id))

    # Also infer from system_activity deletions
    activity_deletions = _find_account_records_from_activity(
        identity, bundle.system_activities, request.audit_at
    )
    seen_resources = {r[1] for r in absent_accounts}
    for sys_id, res_id, act_id in activity_deletions:
        if res_id not in seen_resources:
            absent_accounts.append((sys_id, res_id, act_id))
            seen_resources.add(res_id)

    # ------------------------------------------------------------------
    # Step 8: Generate steps
    # ------------------------------------------------------------------
    if deletion_status == "COMPLETE":
        steps_objs = generate_steps_for_complete(
            all_decisions=compliant + retained,
            security_records=security_records,
            account_records=absent_accounts,
        )
    else:
        steps_objs = generate_steps_for_incomplete(
            decisions=non_compliant,
            compliant_decisions=retained,
            security_records=security_records,
            account_records=absent_accounts,
        )

    steps = steps_to_dicts(steps_objs)

    return AuditResult(
        case_id=request.case_id,
        deletion_status=deletion_status,
        steps=steps,
        identity=identity,
        classification=classification,
        evidence_records=evidence_records,
        decisions=decisions,
        parse_errors=inv.parse_errors,
    )


def run_audit(bundle: DatasetBundle) -> List[AuditResult]:
    """Run the audit pipeline for all deletion requests."""
    results: List[AuditResult] = []
    total = len(bundle.deletion_requests)
    for i, request in enumerate(bundle.deletion_requests):
        logger.info("Processing %s (%d/%d)", request.case_id, i + 1, total)
        try:
            result = audit_case(request, bundle)
        except Exception as exc:
            logger.error("Failed to audit %s: %s", request.case_id, exc, exc_info=True)
            result = AuditResult(
                case_id=request.case_id,
                deletion_status="INCOMPLETE",
                steps=[],
                parse_errors=[str(exc)],
            )
        results.append(result)
    return results
