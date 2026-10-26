"""
Dataset Loader — reads and validates all participant-facing CSV and evidence files.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    """Return the repository root (parent of code/)."""
    return Path(__file__).resolve().parent.parent.parent


def dataset_dir() -> Path:
    return _repo_root() / "dataset"


def evidence_dir() -> Path:
    return dataset_dir() / "evidence"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DeletionRequest:
    case_id: str
    email: str
    requested_at: str
    reported_status: str
    reported_at: str
    audit_at: str
    request_text: str


@dataclass
class UserIdentity:
    user_id: str
    name: str
    email: str
    phone: str
    email_hash: str
    updated_at: str


@dataclass
class AuditLogEntry:
    audit_id: str
    user_id: str
    event_time: str
    field_name: str
    old_value: str
    new_value: str


@dataclass
class SystemActivity:
    activity_id: str
    event_time: str
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str
    details: str


@dataclass
class DeletionRule:
    rule_id: str
    data_category: str
    condition: str
    required_action: str


@dataclass
class EvidenceFile:
    file_id: str
    file_path: str
    origin: str
    captured_at: str


@dataclass
class DatasetBundle:
    deletion_requests: List[DeletionRequest]
    sample_requests: List[DeletionRequest]
    sample_outputs: Dict[str, Any]  # case_id -> parsed output
    user_identities: List[UserIdentity]
    audit_log: List[AuditLogEntry]
    system_activities: List[SystemActivity]
    deletion_rules: List[DeletionRule]
    evidence_files: List[EvidenceFile]


# ---------------------------------------------------------------------------
# CSV readers
# ---------------------------------------------------------------------------

def _read_csv(path: Path) -> List[Dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return [dict(row) for row in reader]


def load_deletion_requests(path: Optional[Path] = None) -> List[DeletionRequest]:
    path = path or (dataset_dir() / "deletion_requests.csv")
    rows = _read_csv(path)
    return [
        DeletionRequest(
            case_id=r["case_id"],
            email=r["email"].strip(),
            requested_at=r["requested_at"].strip(),
            reported_status=r["reported_status"].strip(),
            reported_at=r["reported_at"].strip(),
            audit_at=r["audit_at"].strip(),
            request_text=r["request_text"],
        )
        for r in rows
    ]


def load_sample_outputs() -> Dict[str, Any]:
    path = dataset_dir() / "sample_output.csv"
    rows = _read_csv(path)
    result: Dict[str, Any] = {}
    for r in rows:
        case_id = r["case_id"]
        deletion_status = r["deletion_status"]
        steps_raw = r["steps_json"]
        try:
            steps = json.loads(steps_raw)
        except json.JSONDecodeError:
            steps = []
        result[case_id] = {"deletion_status": deletion_status, "steps": steps}
    return result


def load_user_identities() -> List[UserIdentity]:
    path = dataset_dir() / "user_identities.csv"
    rows = _read_csv(path)
    return [
        UserIdentity(
            user_id=r["user_id"],
            name=r.get("name", ""),
            email=r.get("email", "").strip(),
            phone=r.get("phone", "").strip(),
            email_hash=r.get("email_hash", "").strip(),
            updated_at=r.get("updated_at", "").strip(),
        )
        for r in rows
    ]


def load_audit_log() -> List[AuditLogEntry]:
    path = dataset_dir() / "user_audit_log.csv"
    rows = _read_csv(path)
    return [
        AuditLogEntry(
            audit_id=r["audit_id"],
            user_id=r["user_id"],
            event_time=r["event_time"],
            field_name=r["field_name"],
            old_value=r.get("old_value", ""),
            new_value=r.get("new_value", ""),
        )
        for r in rows
    ]


def load_system_activity() -> List[SystemActivity]:
    path = dataset_dir() / "system_activity.csv"
    rows = _read_csv(path)
    return [
        SystemActivity(
            activity_id=r["activity_id"],
            event_time=r["event_time"],
            event_type=r["event_type"],
            source_system=r["source_system"],
            target_system=r["target_system"],
            resource_id=r["resource_id"],
            reported_result=r["reported_result"],
            details=r.get("details", ""),
        )
        for r in rows
    ]


def load_deletion_rules() -> List[DeletionRule]:
    path = dataset_dir() / "deletion_rules.csv"
    rows = _read_csv(path)
    return [
        DeletionRule(
            rule_id=r["rule_id"],
            data_category=r["data_category"],
            condition=r["condition"],
            required_action=r["required_action"],
        )
        for r in rows
    ]


def load_evidence_files() -> List[EvidenceFile]:
    path = dataset_dir() / "evidence_files.csv"
    rows = _read_csv(path)
    return [
        EvidenceFile(
            file_id=r["file_id"],
            file_path=r["file_path"],
            origin=r["origin"],
            captured_at=r["captured_at"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Evidence file readers
# ---------------------------------------------------------------------------

def load_json_evidence(evidence_file: EvidenceFile) -> Any:
    """Load a JSON evidence file. Returns parsed object or None on failure."""
    full_path = dataset_dir() / evidence_file.file_path
    try:
        with open(full_path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        logger.warning("Failed to load JSON evidence %s: %s", evidence_file.file_id, exc)
        return None


def load_text_evidence(evidence_file: EvidenceFile) -> str:
    """Load a text/log evidence file. Returns raw string."""
    full_path = dataset_dir() / evidence_file.file_path
    try:
        with open(full_path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception as exc:
        logger.warning("Failed to load text evidence %s: %s", evidence_file.file_id, exc)
        return ""


def query_sql_evidence(evidence_file: EvidenceFile, sql: str) -> List[Dict[str, Any]]:
    """
    Execute a SQL query against a SQLite-compatible SQL snapshot.
    The SQL file is loaded into an in-memory SQLite database.
    Returns list of row dicts.
    """
    full_path = dataset_dir() / evidence_file.file_path
    try:
        with open(full_path, encoding="utf-8", errors="replace") as fh:
            sql_script = fh.read()
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(sql_script)
        cur = conn.execute(sql)
        rows = [dict(row) for row in cur.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        logger.warning("Failed to query SQL evidence %s: %s", evidence_file.file_id, exc)
        return []


def get_snapshot_scope(evidence_file: EvidenceFile) -> Dict[str, str]:
    """Return snapshot_scope table from a SQL evidence file as {origin_system: captured_at}."""
    rows = query_sql_evidence(evidence_file, "SELECT origin_system, captured_at FROM snapshot_scope")
    return {r["origin_system"]: r["captured_at"] for r in rows}


# ---------------------------------------------------------------------------
# Master dataset loader
# ---------------------------------------------------------------------------

def load_dataset() -> DatasetBundle:
    sample_path = dataset_dir() / "sample_deletion_requests.csv"
    return DatasetBundle(
        deletion_requests=load_deletion_requests(),
        sample_requests=load_deletion_requests(sample_path),
        sample_outputs=load_sample_outputs(),
        user_identities=load_user_identities(),
        audit_log=load_audit_log(),
        system_activities=load_system_activity(),
        deletion_rules=load_deletion_rules(),
        evidence_files=load_evidence_files(),
    )
