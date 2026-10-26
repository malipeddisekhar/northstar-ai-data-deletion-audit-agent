"""
Evidence Investigator — searches all evidence files for records relating to
a resolved identity and returns structured findings.

Handles the actual evidence schemas found in the dataset:
- SQL snapshots: customer_data_* tables with embedded JSON record_json field
                 + older inventory tables (customer_profiles, etc.)
                 + failover replica tables with user_id field
- JSON bundles: snapshots[] and flat record arrays
- Log/text files: line-by-line pattern matching
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .dataset_loader import (
    EvidenceFile,
    dataset_dir,
    load_json_evidence,
    load_text_evidence,
)
from .identity_resolver import ResolvedIdentity

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class EvidenceRecord:
    """A single record found in an evidence file."""
    file_id: str
    captured_at: str
    system_id: str
    resource_id: str
    data_category: str      # maps to deletion_rules data_category
    state: str              # PRESENT / REDACTED / ANONYMIZED / ABSENT / ACTIVE / BLOCKED / DISABLED
    details: Dict[str, Any] = field(default_factory=dict)
    identifiers_found: List[str] = field(default_factory=list)


@dataclass
class InvestigationResult:
    records: List[EvidenceRecord] = field(default_factory=list)
    parse_errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    ts = ts.strip().rstrip("Z")
    try:
        return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _is_before_cutoff(captured_at: str, audit_at: str) -> bool:
    cap = _parse_iso(captured_at)
    cut = _parse_iso(audit_at)
    if cap is None or cut is None:
        return False
    return cap <= cut


def _matches_identity(value: Optional[str], identity: ResolvedIdentity) -> bool:
    if value is None:
        return False
    v = value.strip().upper()
    for e in identity.all_emails:
        if v == e.strip().upper():
            return True
    for p in identity.all_phones:
        if v == p.strip().upper():
            return True
    for h in identity.all_email_hashes:
        if h and v == h.strip().upper():
            return True
    if identity.user_id and v == identity.user_id.strip().upper():
        return True
    return False


def _any_matches(values: List[Optional[str]], identity: ResolvedIdentity) -> bool:
    return any(_matches_identity(v, identity) for v in values if v)


def _found_ids(values: List[Optional[str]], identity: ResolvedIdentity) -> List[str]:
    return [v for v in values if v and _matches_identity(v, identity)]


# ---------------------------------------------------------------------------
# System / category mapping
# ---------------------------------------------------------------------------

def _system_from_collection(origin_system: str, collection_name: str) -> Tuple[str, str]:
    """Map origin_system + collection_name → (system_id, data_category)."""
    col = (collection_name or "").lower()
    sys = (origin_system or "").lower()

    if "account" in sys or "customer_profiles" in col:
        return "account_service", "USER_PROFILE"
    if "search" in sys or "search_documents" in col or "search" in col:
        return "search_service", "SEARCH_INDEX_ENTRY"
    if "analytics" in sys or "analytics_reports" in col or "analytics" in col:
        return "analytics_platform", "ANALYTICS_PROFILE"
    if "support" in sys:
        if "attachment" in col:
            return "support_platform", "SUPPORT_ATTACHMENT"
        return "support_platform", "SUPPORT_MESSAGE"
    if "export" in sys or "export" in col:
        return "export_service", "USER_EXPORT"
    if "integration" in sys or "connector" in col or "data_flow" in col:
        return "integration_service", "DATA_FLOW"
    if "backup" in sys or "backup" in col:
        return "backup_service", "BACKUP_ARCHIVE"
    if "security" in sys or "audit" in col:
        return "security_service", "SECURITY_AUDIT_LOG"
    if "marketing" in sys:
        return "marketing_platform", "MARKETING_PROFILE"
    if "email_delivery" in sys or "delivery" in col:
        return "email_delivery_service", "EMAIL_DELIVERY_LOG"
    if "identity" in sys or "tombstone" in col:
        return "identity_service", "ACCOUNT_TOMBSTONE"
    return origin_system or "unknown", "USER_PROFILE"


def _infer_state_from_record(record: Dict[str, Any], data_category: str) -> str:
    """Determine record state from JSON content."""
    if data_category == "ACCOUNT_TOMBSTONE":
        pii = [record.get("email"), record.get("phone"), record.get("name"),
               record.get("full_name"), record.get("email_hash")]
        if all(v is None for v in pii):
            return "REDACTED"
        return "PRESENT"

    # Explicit state fields
    for key in ("account_status", "status", "state", "observed_state"):
        val = (record.get(key) or "").upper()
        if val == "DELETED":
            return "ABSENT"
        if val in ("ACTIVE", "DELETION_PENDING"):
            return "PRESENT" if val == "DELETION_PENDING" else "ACTIVE"

    # support message with [REDACTED] text
    if data_category in ("SUPPORT_MESSAGE", "SUPPORT_ATTACHMENT"):
        msg = record.get("message_text", "") or ""
        if "[REDACTED]" in msg.upper():
            return "REDACTED"
        # Check for null PII fields
        pii = [record.get("requester_name"), record.get("requester_email"),
               record.get("requester_phone")]
        if all(v is None for v in pii) and not record.get("customer_reference"):
            return "REDACTED"
        return "PRESENT"

    if data_category == "ANALYTICS_PROFILE":
        pii = [record.get("user_id"), record.get("email"), record.get("email_hash"),
               record.get("phone")]
        if all(v is None for v in pii):
            return "ANONYMIZED"
        return "PRESENT"

    if data_category == "DATA_FLOW":
        enabled = record.get("enabled")
        if enabled is False or enabled == 0:
            return "DISABLED"
        return "ACTIVE"

    if data_category == "BACKUP_ARCHIVE":
        restore = record.get("restore_allowed")
        if restore is False or restore == 0:
            return "BLOCKED"
        return "ACTIVE"

    return "PRESENT"


def _get_record_identity_fields(record: Dict[str, Any]) -> List[Optional[str]]:
    """Extract all potentially identity-bearing fields from a record dict."""
    return [
        record.get("email"),
        record.get("customer_reference"),
        record.get("owner_email"),
        record.get("requester_email"),
        record.get("customer_email"),
        record.get("user_id"),
        record.get("email_hash"),
        record.get("phone"),
        record.get("subject_reference"),
        record.get("uploaded_by"),
        record.get("subject_selector"),
        # document sub-object fields
        (record.get("document") or {}).get("email") if isinstance(record.get("document"), dict) else None,
        (record.get("document") or {}).get("customer_reference") if isinstance(record.get("document"), dict) else None,
    ]


# ---------------------------------------------------------------------------
# SQL evidence readers
# ---------------------------------------------------------------------------

def _read_sql_evidence(
    ev: EvidenceFile,
    identity: ResolvedIdentity,
    audit_at: str,
) -> List[EvidenceRecord]:
    if not _is_before_cutoff(ev.captured_at, audit_at):
        return []

    full_path = dataset_dir() / ev.file_path
    try:
        with open(full_path, encoding="utf-8", errors="replace") as fh:
            sql_script = fh.read()
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(sql_script)
    except Exception as exc:
        logger.warning("SQL load error %s: %s", ev.file_id, exc)
        return []

    records: List[EvidenceRecord] = []
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    ]

    for table in tables:
        try:
            records.extend(
                _scan_sql_table(conn, table, ev, identity, audit_at)
            )
        except Exception as exc:
            logger.debug("SQL table scan error %s.%s: %s", ev.file_id, table, exc)

    conn.close()
    return records


def _scan_sql_table(
    conn: sqlite3.Connection,
    table: str,
    ev: EvidenceFile,
    identity: ResolvedIdentity,
    audit_at: str,
) -> List[EvidenceRecord]:
    """Scan a single SQL table for identity matches."""
    records: List[EvidenceRecord] = []

    # Get column names
    cur = conn.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in cur.fetchall()]

    # --- Pattern 1: record_json column (the main snapshot format) ---
    if "record_json" in cols and "origin_system" in cols:
        for row in conn.execute(f"SELECT resource_id, origin_system, collection_name, record_json, updated_at FROM {table}"):
            resource_id = row[0]
            origin_system = row[1]
            collection_name = row[2] or ""
            updated_at = row[4] or ev.captured_at

            if not _is_before_cutoff(updated_at, audit_at):
                continue

            try:
                rec = json.loads(row[3])
            except Exception:
                continue

            fields = _get_record_identity_fields(rec)
            if not _any_matches(fields, identity):
                continue

            system_id, data_category = _system_from_collection(origin_system, collection_name)
            state = _infer_state_from_record(rec, data_category)

            records.append(EvidenceRecord(
                file_id=ev.file_id,
                captured_at=updated_at,
                system_id=system_id,
                resource_id=resource_id,
                data_category=data_category,
                state=state,
                details=rec,
                identifiers_found=_found_ids(fields, identity),
            ))
        return records

    # --- Pattern 2: Failover replica table (user_id col, no record_json) ---
    # e.g. customer_profiles_failover_replica, customer_profiles_primary
    if "user_id" in cols and "email" in cols and "account_status" in cols:
        captured_at = ev.captured_at
        system_id = "account_service"
        data_category = "USER_PROFILE"

        for row in conn.execute(f"SELECT resource_id, user_id, full_name, email, phone, account_status, updated_at FROM {table}"):
            resource_id = row[0]
            user_id_val = row[1]
            email_val = row[3]
            phone_val = row[4]
            status = row[5]
            updated_at = row[6] or captured_at

            if not _is_before_cutoff(updated_at, audit_at):
                continue

            fields = [email_val, user_id_val, phone_val]
            if not _any_matches(fields, identity):
                continue

            # Failover replica data that's ACTIVE = still PRESENT (replica drift)
            state = "ABSENT" if (status or "").upper() == "DELETED" else "PRESENT"

            records.append(EvidenceRecord(
                file_id=ev.file_id,
                captured_at=updated_at,
                system_id=system_id,
                resource_id=resource_id,
                data_category=data_category,
                state=state,
                details={"user_id": user_id_val, "email": email_val, "status": status,
                         "table": table, "topology": "failover_replica"},
                identifiers_found=_found_ids(fields, identity),
            ))
        return records

    # --- Pattern 3: Search primary table (search_documents_primary) ---
    if "display_name" in cols and "email" in cols and "source_resource_id" in cols:
        for row in conn.execute(f"SELECT resource_id, display_name, email, source_resource_id, indexed_at FROM {table}"):
            resource_id = row[0]
            email_val = row[2]
            indexed_at = row[4] or ev.captured_at

            if not _is_before_cutoff(indexed_at, audit_at):
                continue

            fields = [email_val]
            if not _any_matches(fields, identity):
                continue

            records.append(EvidenceRecord(
                file_id=ev.file_id,
                captured_at=indexed_at,
                system_id="search_service",
                resource_id=resource_id,
                data_category="SEARCH_INDEX_ENTRY",
                state="PRESENT",
                details={"email": email_val, "table": table},
                identifiers_found=_found_ids(fields, identity),
            ))
        return records

    # --- Pattern 4: Inventory tables (customer_profiles, customer_analytics_profiles, etc.) ---
    _scan_inventory_table(conn, table, ev, identity, audit_at, records)
    return records


_INVENTORY_TABLE_MAP = {
    "customer_profiles": ("account_service", "USER_PROFILE"),
    "customer_search_documents": ("search_service", "SEARCH_INDEX_ENTRY"),
    "customer_analytics_profiles": ("analytics_platform", "ANALYTICS_PROFILE"),
    "support_records": ("support_platform", "SUPPORT_MESSAGE"),
    "customer_exports": ("export_service", "USER_EXPORT"),
    "connector_subscriptions": ("integration_service", "DATA_FLOW"),
    "backup_restore_entries": ("backup_service", "BACKUP_ARCHIVE"),
    "security_audit_records": ("security_service", "SECURITY_AUDIT_LOG"),
    "account_tombstones": ("identity_service", "ACCOUNT_TOMBSTONE"),
}


def _scan_inventory_table(
    conn: sqlite3.Connection,
    table: str,
    ev: EvidenceFile,
    identity: ResolvedIdentity,
    audit_at: str,
    records: List[EvidenceRecord],
) -> None:
    """Handle the older inventory SQL format."""
    mapping = None
    for key, val in _INVENTORY_TABLE_MAP.items():
        if table == key or table.startswith(key):
            mapping = val
            break

    if mapping is None:
        return

    system_id, data_category = mapping

    try:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(f"SELECT * FROM {table}"):
            row_dict = dict(row)
            resource_id = row_dict.get("resource_id", "")
            updated_at = row_dict.get("updated_at", ev.captured_at)

            if not _is_before_cutoff(updated_at, audit_at):
                continue

            id_fields = [
                row_dict.get("email"),
                row_dict.get("customer_email"),
                row_dict.get("owner_email"),
                row_dict.get("requester_email"),
                row_dict.get("customer_reference"),
                row_dict.get("subject_reference"),
                row_dict.get("phone"),
                row_dict.get("requester_phone"),
                row_dict.get("owner_phone"),
                row_dict.get("email_hash"),
            ]
            if not _any_matches(id_fields, identity):
                continue

            state = _infer_inventory_state(table, row_dict, data_category)
            records.append(EvidenceRecord(
                file_id=ev.file_id,
                captured_at=updated_at,
                system_id=system_id,
                resource_id=resource_id,
                data_category=data_category,
                state=state,
                details=dict(row_dict),
                identifiers_found=_found_ids(id_fields, identity),
            ))
    except Exception as exc:
        logger.debug("Inventory table %s error: %s", table, exc)


def _infer_inventory_state(table: str, row: Dict, data_category: str) -> str:
    if table == "connector_subscriptions":
        return "ACTIVE" if row.get("enabled") == 1 else "DISABLED"
    if table == "backup_restore_entries":
        return "ACTIVE" if row.get("restore_allowed") == 1 else "BLOCKED"
    if table == "customer_profiles":
        status = (row.get("account_status") or "").upper()
        return "ABSENT" if status == "DELETED" else "PRESENT"
    if table == "customer_analytics_profiles":
        pii = [row.get("email"), row.get("phone"), row.get("email_hash"),
               row.get("customer_reference")]
        return "ANONYMIZED" if all(v is None for v in pii) else "PRESENT"
    if table == "support_records":
        pii = [row.get("requester_name"), row.get("requester_email"),
               row.get("requester_phone")]
        return "REDACTED" if all(v is None for v in pii) else "PRESENT"
    if table == "account_tombstones":
        pii = [row.get("email"), row.get("phone"), row.get("name"), row.get("email_hash")]
        return "REDACTED" if all(v is None for v in pii) else "PRESENT"
    return "PRESENT"


# ---------------------------------------------------------------------------
# JSON evidence readers
# ---------------------------------------------------------------------------

def _read_json_evidence(
    ev: EvidenceFile,
    identity: ResolvedIdentity,
    audit_at: str,
) -> List[EvidenceRecord]:
    if not _is_before_cutoff(ev.captured_at, audit_at):
        return []

    data = load_json_evidence(ev)
    if data is None:
        return []

    records: List[EvidenceRecord] = []

    if isinstance(data, dict):
        _scan_json_dict(data, ev, identity, audit_at, records)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                _scan_json_dict(item, ev, identity, audit_at, records)

    return records


def _scan_json_dict(
    data: Dict[str, Any],
    ev: EvidenceFile,
    identity: ResolvedIdentity,
    audit_at: str,
    records: List[EvidenceRecord],
) -> None:
    """Route JSON document to the right handler."""
    # snapshot bundles
    if "snapshots" in data:
        for snapshot in data.get("snapshots", []):
            _scan_snapshot(snapshot, ev, identity, audit_at, records)
        return

    # service exports
    if "service_exports" in data:
        _scan_service_exports(data["service_exports"], ev, identity, audit_at, records, data.get("exported_at", ev.captured_at))
        return

    # security audit ledger
    if "audit_records" in data:
        exported_at = data.get("exported_at", ev.captured_at)
        if _is_before_cutoff(exported_at, audit_at):
            for rec in data.get("audit_records", []):
                if _matches_identity(rec.get("subject_reference"), identity):
                    records.append(EvidenceRecord(
                        file_id=ev.file_id,
                        captured_at=exported_at,
                        system_id="security_service",
                        resource_id=rec.get("record_id", "unknown"),
                        data_category="SECURITY_AUDIT_LOG",
                        state="PRESENT",
                        details=rec,
                        identifiers_found=_found_ids([rec.get("subject_reference")], identity),
                    ))
        return

    # analytics reports
    if "analytics_records" in data:
        exported_at = data.get("exported_at", ev.captured_at)
        if _is_before_cutoff(exported_at, audit_at):
            for rec in data.get("analytics_records", []):
                fields = [rec.get("user_id"), rec.get("email"), rec.get("email_hash")]
                if _any_matches(fields, identity):
                    pii_cleared = all(v is None for v in fields)
                    records.append(EvidenceRecord(
                        file_id=ev.file_id,
                        captured_at=exported_at,
                        system_id="analytics_platform",
                        resource_id=rec.get("record_id", "unknown"),
                        data_category="ANALYTICS_PROFILE",
                        state="ANONYMIZED" if pii_cleared else "PRESENT",
                        details=rec,
                        identifiers_found=_found_ids(fields, identity),
                    ))
        return

    # connector configurations
    if "connectors" in data:
        exported_at = data.get("exported_at", ev.captured_at)
        if not _is_before_cutoff(exported_at, audit_at):
            return
        for conn_data in data.get("connectors", []):
            for sub in conn_data.get("active_subscriptions", []):
                fields = [sub.get("customer_email"), sub.get("customer_reference")]
                if _any_matches(fields, identity):
                    enabled = sub.get("enabled", True)
                    records.append(EvidenceRecord(
                        file_id=ev.file_id,
                        captured_at=exported_at,
                        system_id="integration_service",
                        resource_id=sub.get("subscription_id", "unknown"),
                        data_category="DATA_FLOW",
                        state="ACTIVE" if enabled else "DISABLED",
                        details=sub,
                        identifiers_found=_found_ids(fields, identity),
                    ))
        return

    # export manifests
    if "exports" in data:
        exported_at = data.get("exported_at", ev.captured_at)
        if not _is_before_cutoff(exported_at, audit_at):
            return
        for exp in data.get("exports", []):
            fields = [exp.get("owner_email"), exp.get("customer_reference")]
            if _any_matches(fields, identity):
                records.append(EvidenceRecord(
                    file_id=ev.file_id,
                    captured_at=exported_at,
                    system_id="export_service",
                    resource_id=exp.get("export_id", "unknown"),
                    data_category="USER_EXPORT",
                    state="PRESENT",
                    details=exp,
                    identifiers_found=_found_ids(fields, identity),
                ))
        return

    # export jobs (flat records array with source/collection context)
    if "records" in data and "source" in data:
        captured_at = data.get("captured_at", ev.captured_at)
        if not _is_before_cutoff(captured_at, audit_at):
            return
        source = data.get("source", "")
        collection = data.get("collection", "")
        system_id, data_category = _system_from_collection(source, collection)
        for rec in data.get("records", []):
            fields = _get_record_identity_fields(rec)
            if not _any_matches(fields, identity):
                continue
            state = _infer_state_from_record(rec, data_category)
            records.append(EvidenceRecord(
                file_id=ev.file_id,
                captured_at=captured_at,
                system_id=system_id,
                resource_id=rec.get("resource_id", "unknown"),
                data_category=data_category,
                state=state,
                details=rec,
                identifiers_found=_found_ids(fields, identity),
            ))
        return

    # service resource exports (resources array)
    if "resources" in data:
        exported_at = data.get("exported_at", ev.captured_at)
        for res in data.get("resources", []):
            _classify_service_resource(res, ev, identity, audit_at, records, exported_at)
        return

    # export jobs
    if "export_jobs" in data:
        exported_at = data.get("exported_at", ev.captured_at)
        for job in data.get("export_jobs", []):
            fields = [job.get("owner_email"), job.get("customer_reference")]
            if _any_matches(fields, identity):
                if not _is_before_cutoff(exported_at, audit_at):
                    continue
                records.append(EvidenceRecord(
                    file_id=ev.file_id,
                    captured_at=exported_at,
                    system_id="export_service",
                    resource_id=job.get("export_id", "unknown"),
                    data_category="USER_EXPORT",
                    state="PRESENT" if job.get("download_available", True) else "ABSENT",
                    details=job,
                    identifiers_found=_found_ids(fields, identity),
                ))
        return


def _scan_snapshot(
    snapshot: Dict[str, Any],
    ev: EvidenceFile,
    identity: ResolvedIdentity,
    audit_at: str,
    records: List[EvidenceRecord],
) -> None:
    """Process a single snapshot block from a bundle."""
    captured_at = snapshot.get("captured_at", ev.captured_at)
    if not _is_before_cutoff(captured_at, audit_at):
        return

    data_store = snapshot.get("data_store", "")
    collection = snapshot.get("collection", "")
    system_id, data_category = _system_from_collection(data_store, collection)

    for rec in snapshot.get("records", []):
        fields = _get_record_identity_fields(rec)
        if not _any_matches(fields, identity):
            continue

        state = _infer_state_from_record(rec, data_category)
        records.append(EvidenceRecord(
            file_id=ev.file_id,
            captured_at=captured_at,
            system_id=system_id,
            resource_id=rec.get("resource_id", "unknown"),
            data_category=data_category,
            state=state,
            details=rec,
            identifiers_found=_found_ids(fields, identity),
        ))


def _scan_service_exports(
    se: Dict[str, Any],
    ev: EvidenceFile,
    identity: ResolvedIdentity,
    audit_at: str,
    records: List[EvidenceRecord],
    exported_at: str,
) -> None:
    """Handle service_resource_export JSON bundles."""
    if not _is_before_cutoff(exported_at, audit_at):
        return

    # 1. account_service
    for lk in se.get("account_service", {}).get("profile_lookups", []):
        res_id = lk.get("resource_id", "")
        prof = lk.get("profile")
        status = lk.get("status_code", 200)
        obs_at = lk.get("observed_at", exported_at)
        if not _is_before_cutoff(obs_at, audit_at):
            continue
        fields = []
        if isinstance(prof, dict):
            fields = [prof.get("email"), prof.get("customer_reference"), prof.get("phone"), prof.get("email_hash")]
        matched = _any_matches(fields, identity)
        if not matched and identity.user_id:
            uid = identity.user_id.upper()
            if uid.startswith("USER_"):
                num = uid[5:]
                try:
                    n = int(num)
                    if res_id.upper() in (f"ACCOUNT_{num}", f"ACCOUNT_{n:03d}", f"ACCOUNT_{n}"):
                        matched = True
                except ValueError:
                    pass
        if matched:
            state = "ABSENT" if (status == 404 or prof is None) else "PRESENT"
            records.append(EvidenceRecord(
                file_id=ev.file_id,
                captured_at=obs_at,
                system_id="account_service",
                resource_id=res_id,
                data_category="USER_PROFILE",
                state=state,
                details=lk,
                identifiers_found=_found_ids(fields, identity),
            ))

    # 2. analytics_platform
    for pr in se.get("analytics_platform", {}).get("profiles", []):
        res_id = pr.get("resource_id", "")
        obs_at = pr.get("observed_at", exported_at)
        if not _is_before_cutoff(obs_at, audit_at):
            continue
        fields = [pr.get("email"), pr.get("phone"), pr.get("email_hash"), pr.get("customer_reference")]
        if _any_matches(fields, identity):
            pii_null = all(v is None for v in fields)
            records.append(EvidenceRecord(
                file_id=ev.file_id,
                captured_at=obs_at,
                system_id="analytics_platform",
                resource_id=res_id,
                data_category="ANALYTICS_PROFILE",
                state="ANONYMIZED" if pii_null else "PRESENT",
                details=pr,
                identifiers_found=_found_ids(fields, identity),
            ))

    # 3. backup_service
    for bk in se.get("backup_service", {}).get("restore_entries", []):
        res_id = bk.get("resource_id", "")
        obs_at = bk.get("observed_at", exported_at)
        if not _is_before_cutoff(obs_at, audit_at):
            continue
        fields = [bk.get("customer_reference"), bk.get("customer_email")]
        if _any_matches(fields, identity):
            allowed = bk.get("restore_allowed", False)
            records.append(EvidenceRecord(
                file_id=ev.file_id,
                captured_at=obs_at,
                system_id="backup_service",
                resource_id=res_id,
                data_category="BACKUP_ARCHIVE",
                state="ACTIVE" if allowed else "BLOCKED",
                details=bk,
                identifiers_found=_found_ids(fields, identity),
            ))

    # 4. export_service
    for ex in se.get("export_service", {}).get("exports", []):
        res_id = ex.get("resource_id", "")
        obs_at = ex.get("observed_at", exported_at)
        if not _is_before_cutoff(obs_at, audit_at):
            continue
        fields = [ex.get("owner_email"), ex.get("customer_reference"), ex.get("owner_phone")]
        if _any_matches(fields, identity):
            records.append(EvidenceRecord(
                file_id=ev.file_id,
                captured_at=obs_at,
                system_id="export_service",
                resource_id=res_id,
                data_category="USER_EXPORT",
                state="PRESENT",
                details=ex,
                identifiers_found=_found_ids(fields, identity),
            ))

    # 5. integration_service
    for cn in se.get("integration_service", {}).get("connectors", []):
        res_id = cn.get("resource_id", "")
        obs_at = cn.get("observed_at", exported_at)
        if not _is_before_cutoff(obs_at, audit_at):
            continue
        fields = [cn.get("customer_email"), cn.get("customer_reference")]
        if _any_matches(fields, identity):
            enabled = cn.get("enabled", False)
            records.append(EvidenceRecord(
                file_id=ev.file_id,
                captured_at=obs_at,
                system_id="integration_service",
                resource_id=res_id,
                data_category="DATA_FLOW",
                state="ACTIVE" if enabled else "DISABLED",
                details=cn,
                identifiers_found=_found_ids(fields, identity),
            ))

    # 6. search_service
    for sq in se.get("search_service", {}).get("index_queries", []):
        res_id = sq.get("resource_id", "")
        obs_at = sq.get("observed_at", exported_at)
        if not _is_before_cutoff(obs_at, audit_at):
            continue
        query = sq.get("query", "")
        matched = _matches_identity(query, identity)
        hits = sq.get("hits", {}).get("documents", [])
        for doc in hits:
            doc_fields = [doc.get("email"), doc.get("customer_reference"), doc.get("phone"), doc.get("email_hash")]
            if _any_matches(doc_fields, identity):
                matched = True
                res_id = doc.get("resource_id") or res_id
                break
        if matched:
            records.append(EvidenceRecord(
                file_id=ev.file_id,
                captured_at=obs_at,
                system_id="search_service",
                resource_id=res_id,
                data_category="SEARCH_INDEX_ENTRY",
                state="PRESENT",
                details=sq,
                identifiers_found=_found_ids([query], identity),
            ))

    # 7. security_service
    for ar in se.get("security_service", {}).get("audit_records", []):
        res_id = ar.get("resource_id", "")
        obs_at = ar.get("observed_at", exported_at)
        if not _is_before_cutoff(obs_at, audit_at):
            continue
        fields = [ar.get("subject_reference")]
        if _any_matches(fields, identity):
            records.append(EvidenceRecord(
                file_id=ev.file_id,
                captured_at=obs_at,
                system_id="security_service",
                resource_id=res_id,
                data_category="SECURITY_AUDIT_LOG",
                state="PRESENT",
                details=ar,
                identifiers_found=_found_ids(fields, identity),
            ))

    # 8. support_platform
    for tk in se.get("support_platform", {}).get("tickets", []):
        res_id = tk.get("resource_id", "")
        obs_at = tk.get("observed_at", exported_at)
        if not _is_before_cutoff(obs_at, audit_at):
            continue
        fields = [tk.get("customer_reference"), tk.get("requester_email"), tk.get("requester_phone"), tk.get("requester_name")]
        if _any_matches(fields, identity):
            pii_fields = [tk.get("requester_email"), tk.get("requester_phone"), tk.get("requester_name")]
            is_redacted = all(v is None for v in pii_fields)
            records.append(EvidenceRecord(
                file_id=ev.file_id,
                captured_at=obs_at,
                system_id="support_platform",
                resource_id=res_id,
                data_category="SUPPORT_MESSAGE",
                state="REDACTED" if is_redacted else "PRESENT",
                details=tk,
                identifiers_found=_found_ids(fields, identity),
            ))


# ---------------------------------------------------------------------------
# Log/text evidence readers
# ---------------------------------------------------------------------------

_LOG_RESOURCE_PATTERNS = [
    (r"\baccount_\d+\b", "account_service", "USER_PROFILE"),
    (r"\bsearch_\d+\b", "search_service", "SEARCH_INDEX_ENTRY"),
    (r"\banalytics_\d+\b", "analytics_platform", "ANALYTICS_PROFILE"),
    (r"\bsupport_\d+\b", "support_platform", "SUPPORT_MESSAGE"),
    (r"\bexport_\d+\b", "export_service", "USER_EXPORT"),
    (r"\bdata_flow_\d+\b", "integration_service", "DATA_FLOW"),
    (r"\bbackup_\d+\b", "backup_service", "BACKUP_ARCHIVE"),
    (r"\baudit_record_\d+\b", "security_service", "SECURITY_AUDIT_LOG"),
    (r"\bemail_delivery_\d+\b", "email_delivery_service", "EMAIL_DELIVERY_LOG"),
    (r"\btombstone_\d+\b", "identity_service", "ACCOUNT_TOMBSTONE"),
]

_LOG_STATE_PATTERNS = [
    (r"\b(ANONYMIZED|anonymized)\b", "ANONYMIZED"),
    (r"\b(REDACTED|redacted)\b", "REDACTED"),
    (r"\b(ABSENT|absent|NOT_FOUND|not.found)\b", "ABSENT"),
    (r"\b(BLOCKED|blocked)\b", "BLOCKED"),
    (r"\b(DISABLED|disabled)\b", "DISABLED"),
    (r"\b(ACTIVE|active|ENABLED|enabled|IDENTIFIABLE|identifiable|FOUND|found)\b", "PRESENT"),
]

_compiled_resource = [(re.compile(p, re.IGNORECASE), s, c) for p, s, c in _LOG_RESOURCE_PATTERNS]
_compiled_state = [(re.compile(p, re.IGNORECASE), s) for p, s in _LOG_STATE_PATTERNS]


def _read_log_evidence(
    ev: EvidenceFile,
    identity: ResolvedIdentity,
    audit_at: str,
) -> List[EvidenceRecord]:
    if not _is_before_cutoff(ev.captured_at, audit_at):
        return []

    text = load_text_evidence(ev)
    if not text:
        return []

    records: List[EvidenceRecord] = []

    # Group lines by trace_id if present
    trace_re = re.compile(r"trace_id=([^\s|]+)")
    traces: Dict[str, List[str]] = {}
    standalone_lines: List[str] = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = trace_re.search(line)
        if m:
            tid = m.group(1).strip('"\'')
            traces.setdefault(tid, []).append(line)
        else:
            standalone_lines.append(line)

    units: List[Tuple[str, List[str]]] = [(tid, lines) for tid, lines in traces.items()]
    for s_line in standalone_lines:
        units.append(("", [s_line]))

    for tid, unit_lines in units:
        combined_text = "\n".join(unit_lines)
        if not _line_mentions_identity(combined_text, identity):
            continue

        ts_match = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?", combined_text)
        line_ts = ts_match.group(0) if ts_match else ev.captured_at
        if not _is_before_cutoff(line_ts, audit_at):
            continue

        for pattern, system_id, data_category in _compiled_resource:
            for match in pattern.finditer(combined_text):
                resource_id = match.group(0)
                state = _infer_log_unit_state(combined_text, system_id)
                cat = data_category
                if "contact_lookup" in combined_text or "email_opt_in" in combined_text:
                    cat = "SECONDARY_IDENTIFIER"
                elif "legacy_contact_index" in combined_text or "support_replica" in ev.file_path:
                    cat = "SECONDARY_IDENTIFIER"

                records.append(EvidenceRecord(
                    file_id=ev.file_id,
                    captured_at=line_ts,
                    system_id=system_id,
                    resource_id=resource_id,
                    data_category=cat,
                    state=state,
                    details={"trace_id": tid, "line": unit_lines[0]},
                    identifiers_found=_extract_ids_from_line(combined_text, identity),
                ))

    return records


def _infer_log_unit_state(text: str, system_id: str) -> str:
    lower = text.lower()
    if "rows=0" in lower or 'rows="0"' in lower or "http_status=404" in lower or 'http_status="404"' in lower or "total_hits=0" in lower or "result=miss" in lower:
        return "ABSENT"
    if "restore_allowed=false" in lower or 'restore_allowed="false"' in lower:
        return "BLOCKED"
    if "enabled=false" in lower or 'enabled="false"' in lower or "last_delivery_status=stopped" in lower:
        return "DISABLED"
    if "restore_allowed=true" in lower or 'restore_allowed="true"' in lower:
        return "ACTIVE"
    if "enabled=true" in lower or 'enabled="true"' in lower:
        return "ACTIVE"

    if system_id == "support_platform":
        if "requester_email=null" in lower or "[redacted]" in lower:
            return "REDACTED"
    if system_id == "analytics_platform":
        if "email=null" in lower and "phone=null" in lower:
            return "ANONYMIZED"
    if system_id == "identity_service":
        if "email=null" in lower:
            return "REDACTED"

    for pattern, state in _compiled_state:
        if pattern.search(text):
            return state

    return "PRESENT"


def _line_mentions_identity(line: str, identity: ResolvedIdentity) -> bool:
    upper = line.upper()
    for e in identity.all_emails:
        if e.upper() in upper:
            return True
    for p in identity.all_phones:
        if len(p) > 6 and p.upper() in upper:
            return True
    for h in identity.all_email_hashes:
        if h and h.upper() in upper:
            return True
    if identity.user_id and identity.user_id.upper() in upper:
        return True
    return False


def _extract_ids_from_line(line: str, identity: ResolvedIdentity) -> List[str]:
    found = []
    upper = line.upper()
    for e in identity.all_emails:
        if e.upper() in upper:
            found.append(e)
    if identity.user_id and identity.user_id.upper() in upper:
        found.append(identity.user_id)
    return found


def _infer_log_state(line: str) -> str:
    return _infer_log_unit_state(line, "")


# ---------------------------------------------------------------------------
# Main investigation entry point
# ---------------------------------------------------------------------------

def investigate_evidence(
    identity: ResolvedIdentity,
    evidence_files: List[EvidenceFile],
    audit_at: str,
) -> InvestigationResult:
    """
    Search all eligible evidence files for records relating to the resolved identity.
    Respects the audit_at cutoff. Deduplicates by (system_id, resource_id) keeping
    the record from the latest-captured file.
    """
    result = InvestigationResult()
    best: Dict[Tuple[str, str], EvidenceRecord] = {}

    for ev in evidence_files:
        path_lower = ev.file_path.lower()
        new_records: List[EvidenceRecord] = []

        try:
            if path_lower.endswith(".sql"):
                new_records = _read_sql_evidence(ev, identity, audit_at)
            elif path_lower.endswith(".json"):
                new_records = _read_json_evidence(ev, identity, audit_at)
            elif path_lower.endswith((".log", ".txt")):
                new_records = _read_log_evidence(ev, identity, audit_at)
            elif path_lower.endswith(".pdf"):
                new_records = _read_pdf_evidence(ev, identity, audit_at)
            # Images: skip
        except Exception as exc:
            msg = f"Error processing {ev.file_id}: {exc}"
            logger.warning(msg)
            result.parse_errors.append(msg)
            continue

        for rec in new_records:
            key = (rec.system_id, rec.resource_id)
            existing = best.get(key)
            if existing is None:
                best[key] = rec
            else:
                existing_dt = _parse_iso(existing.captured_at)
                new_dt = _parse_iso(rec.captured_at)
                if new_dt and existing_dt and new_dt > existing_dt:
                    best[key] = rec

    result.records = list(best.values())
    return result


def _read_pdf_evidence(
    ev: EvidenceFile,
    identity: ResolvedIdentity,
    audit_at: str,
) -> List[EvidenceRecord]:
    """Extract text from PDF evidence files and scan."""
    if not _is_before_cutoff(ev.captured_at, audit_at):
        return []
    try:
        import pypdf
        full_path = dataset_dir() / ev.file_path
        reader = pypdf.PdfReader(str(full_path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        logger.debug("PDF read error %s: %s", ev.file_id, exc)
        return []
    if not text:
        return []

    if not _line_mentions_identity(text, identity):
        return []

    records: List[EvidenceRecord] = []
    seen_res: Set[str] = set()
    for pattern, system_id, data_category in _compiled_resource:
        for match in pattern.finditer(text):
            resource_id = match.group(0)
            if resource_id in seen_res:
                continue
            seen_res.add(resource_id)
            cat = "SUPPORT_ATTACHMENT" if (system_id == "support_platform" or "documents" in ev.file_path) else data_category
            records.append(EvidenceRecord(
                file_id=ev.file_id,
                captured_at=ev.captured_at,
                system_id=system_id,
                resource_id=resource_id,
                data_category=cat,
                state="PRESENT",
                details={"source": "pdf"},
                identifiers_found=_extract_ids_from_line(text, identity),
            ))
    return records
