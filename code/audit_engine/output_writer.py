"""
Output writer — serialises AuditResult list to the required output.csv format.
"""
from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import List

from .pipeline import AuditResult

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def write_output_csv(results: List[AuditResult], output_path: Path | None = None) -> Path:
    """
    Write output.csv at the repo root (or specified path).
    Schema: case_id,deletion_status,steps_json
    Sorted by case_id.
    """
    if output_path is None:
        output_path = _repo_root() / "output.csv"

    # Sort by case_id
    sorted_results = sorted(results, key=lambda r: r.case_id)

    with open(output_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(["case_id", "deletion_status", "steps_json"])
        for result in sorted_results:
            steps_json = json.dumps(result.steps, ensure_ascii=False, separators=(",", ":"))
            writer.writerow([result.case_id, result.deletion_status, steps_json])

    logger.info("Wrote %d rows to %s", len(sorted_results), output_path)
    return output_path


def validate_output_csv(output_path: Path, expected_case_ids: List[str]) -> dict:
    """Validate the output CSV against expected schema and case IDs."""
    errors = []
    warnings = []

    if not output_path.exists():
        return {"valid": False, "errors": ["output.csv not found"], "warnings": []}

    try:
        with open(output_path, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
    except Exception as exc:
        return {"valid": False, "errors": [f"CSV read error: {exc}"], "warnings": []}

    # Check header
    expected_cols = {"case_id", "deletion_status", "steps_json"}
    actual_cols = set(reader.fieldnames or [])
    if not expected_cols.issubset(actual_cols):
        errors.append(f"Missing columns: {expected_cols - actual_cols}")

    # Check row count
    if len(rows) != len(expected_case_ids):
        errors.append(
            f"Row count mismatch: got {len(rows)}, expected {len(expected_case_ids)}"
        )

    # Check each row
    found_case_ids = set()
    for row in rows:
        case_id = row.get("case_id", "")
        status = row.get("deletion_status", "")
        steps_raw = row.get("steps_json", "")

        found_case_ids.add(case_id)

        if status not in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"):
            errors.append(f"{case_id}: invalid deletion_status '{status}'")

        try:
            steps = json.loads(steps_raw)
            if not isinstance(steps, list):
                errors.append(f"{case_id}: steps_json is not a JSON array")
                continue
            # Check step structure
            for step in steps:
                for field in ("step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"):
                    if field not in step:
                        errors.append(f"{case_id}: step missing field '{field}'")
                        break
        except json.JSONDecodeError as exc:
            errors.append(f"{case_id}: invalid steps_json JSON: {exc}")

    # Check all expected case IDs present
    missing = set(expected_case_ids) - found_case_ids
    if missing:
        errors.append(f"Missing case_ids: {sorted(missing)[:10]}")

    return {
        "valid": len(errors) == 0,
        "row_count": len(rows),
        "errors": errors,
        "warnings": warnings,
    }
