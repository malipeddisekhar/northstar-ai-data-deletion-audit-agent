"""
Identity Resolver — resolves an email or identifier to a stable user_id and
collects all current and historical identifiers.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .dataset_loader import AuditLogEntry, UserIdentity

logger = logging.getLogger(__name__)


@dataclass
class ResolvedIdentity:
    user_id: Optional[str]
    current_email: Optional[str]
    current_phone: Optional[str]
    current_email_hash: Optional[str]
    current_name: Optional[str]
    historical_emails: List[str] = field(default_factory=list)
    historical_phones: List[str] = field(default_factory=list)
    all_emails: List[str] = field(default_factory=list)   # current + historical, deduped
    all_phones: List[str] = field(default_factory=list)
    all_email_hashes: List[str] = field(default_factory=list)
    identity_sources: List[str] = field(default_factory=list)
    matched: bool = False


def _normalize_email(email: str) -> str:
    return email.strip().upper()


def _build_email_index(identities: List[UserIdentity]) -> Dict[str, UserIdentity]:
    """Build a case-insensitive email → identity lookup."""
    index: Dict[str, UserIdentity] = {}
    for ident in identities:
        if ident.email:
            index[_normalize_email(ident.email)] = ident
    return index


def _build_historical_email_index(
    audit_log: List[AuditLogEntry],
) -> Dict[str, str]:
    """Build a case-insensitive historical email → user_id lookup from audit log."""
    index: Dict[str, str] = {}
    for entry in audit_log:
        if entry.field_name == "email":
            if entry.old_value:
                index[_normalize_email(entry.old_value)] = entry.user_id
            if entry.new_value:
                index[_normalize_email(entry.new_value)] = entry.user_id
    return index


def resolve_identity(
    email: str,
    identities: List[UserIdentity],
    audit_log: List[AuditLogEntry],
) -> ResolvedIdentity:
    """
    Resolve an email to a user identity using current identities and audit history.

    Algorithm:
    1. Try exact (case-insensitive) match in current user_identities.
    2. If not found, try historical email changes in user_audit_log.
    3. Collect all known identifiers (current + historical).
    """
    norm_email = _normalize_email(email)

    email_index = _build_email_index(identities)
    hist_email_index = _build_historical_email_index(audit_log)

    identity_by_id: Dict[str, UserIdentity] = {i.user_id: i for i in identities}

    user: Optional[UserIdentity] = None
    source: Optional[str] = None

    # 1. Current email match
    if norm_email in email_index:
        user = email_index[norm_email]
        source = "current_identity"

    # 2. Historical email match
    if user is None and norm_email in hist_email_index:
        user_id = hist_email_index[norm_email]
        user = identity_by_id.get(user_id)
        source = "historical_audit_log"

    if user is None:
        return ResolvedIdentity(
            user_id=None,
            current_email=email,
            current_phone=None,
            current_email_hash=None,
            current_name=None,
            matched=False,
        )

    # Collect all identifiers across history
    historical_emails: List[str] = []
    historical_phones: List[str] = []
    historical_email_hashes: List[str] = []

    for entry in audit_log:
        if entry.user_id != user.user_id:
            continue
        if entry.field_name == "email":
            if entry.old_value and entry.old_value not in historical_emails:
                historical_emails.append(entry.old_value)
        elif entry.field_name == "phone":
            if entry.old_value and entry.old_value not in historical_phones:
                historical_phones.append(entry.old_value)
        elif entry.field_name == "email_hash":
            if entry.old_value and entry.old_value not in historical_email_hashes:
                historical_email_hashes.append(entry.old_value)

    all_emails: List[str] = []
    seen_emails: Set[str] = set()
    for e in [user.email] + historical_emails:
        if e and _normalize_email(e) not in seen_emails:
            all_emails.append(e)
            seen_emails.add(_normalize_email(e))

    all_phones: List[str] = []
    seen_phones: Set[str] = set()
    for p in [user.phone] + historical_phones:
        if p and p not in seen_phones:
            all_phones.append(p)
            seen_phones.add(p)

    all_email_hashes: List[str] = []
    seen_hashes: Set[str] = set()
    for h in [user.email_hash] + historical_email_hashes:
        if h and h.strip().upper() not in seen_hashes:
            all_email_hashes.append(h.strip())
            seen_hashes.add(h.strip().upper())

    return ResolvedIdentity(
        user_id=user.user_id,
        current_email=user.email,
        current_phone=user.phone,
        current_email_hash=user.email_hash,
        current_name=user.name,
        historical_emails=historical_emails,
        historical_phones=historical_phones,
        all_emails=all_emails,
        all_phones=all_phones,
        all_email_hashes=all_email_hashes,
        identity_sources=[source] if source else [],
        matched=True,
    )
