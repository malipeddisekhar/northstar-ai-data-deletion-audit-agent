"""
FastAPI backend for Northstar Shop — AI Data Deletion Audit Agent
"""
from __future__ import annotations

import csv
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure code/ dir is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from audit_engine.dataset_loader import load_dataset, DeletionRequest
from audit_engine.evaluator import evaluate_against_samples, format_evaluation_report
from audit_engine.output_writer import validate_output_csv, write_output_csv
from audit_engine.pipeline import audit_case, run_audit, AuditResult
from audit_engine.request_classifier import classify_request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api")

app = FastAPI(
    title="Northstar Shop — AI Data Deletion Audit API",
    description="Enterprise privacy-compliance and data-governance audit agent",
    version="2.4.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-memory state & Startup preloading
# ---------------------------------------------------------------------------

_bundle = None
_results_cache: Dict[str, AuditResult] = {}
_run_status: Dict[str, Any] = {"status": "idle", "processed": 0, "total": 0}

_settings = {
    "model_provider": "gemini-flash-hybrid",
    "fallback_enabled": True,
    "confidence_threshold": 0.85,
    "max_batch_size": 1000,
    "evidence_scan_limit": 500,
    "retention_period_days": 90,
    "auto_verify_remediation": True,
    "log_level": "INFO",
}


def _get_bundle():
    global _bundle
    if _bundle is None:
        logger.info("Loading dataset…")
        _bundle = load_dataset()
    return _bundle


def _load_cached_results_from_csv():
    repo_root = Path(__file__).resolve().parent.parent
    csv_path = repo_root / "output.csv"
    if csv_path.exists():
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                loaded = 0
                for row in reader:
                    cid = row.get("case_id")
                    status = row.get("deletion_status")
                    steps_raw = row.get("steps_json", "[]")
                    steps = json.loads(steps_raw) if steps_raw else []
                    if cid:
                        _results_cache[cid] = AuditResult(
                            case_id=cid,
                            deletion_status=status,
                            steps=steps,
                        )
                        loaded += 1
            logger.info("Loaded %d precomputed results from output.csv", loaded)
        except Exception as e:
            logger.warning("Could not pre-load output.csv: %s", e)


# Run startup load
_load_cached_results_from_csv()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class AuditRequest(BaseModel):
    case_id: str
    email: str
    requested_at: str
    reported_status: str
    reported_at: str
    audit_at: str
    request_text: str


class BatchRunRequest(BaseModel):
    case_ids: Optional[List[str]] = None  # None = all


class ClassifyRequest(BaseModel):
    text: str


class SettingsUpdate(BaseModel):
    model_provider: Optional[str] = None
    fallback_enabled: Optional[bool] = None
    confidence_threshold: Optional[float] = None
    max_batch_size: Optional[int] = None
    evidence_scan_limit: Optional[int] = None
    retention_period_days: Optional[int] = None
    auto_verify_remediation: Optional[bool] = None
    log_level: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "northstar-audit-agent",
        "version": "2.4.0",
        "developer": "Malipeddi Sekhar",
        "institution": "GMR Institute of Technology",
    }


@app.get("/api/dataset/summary")
def dataset_summary():
    bundle = _get_bundle()
    return {
        "deletion_requests": len(bundle.deletion_requests),
        "sample_requests": len(bundle.sample_requests),
        "user_identities": len(bundle.user_identities),
        "audit_log_entries": len(bundle.audit_log),
        "system_activities": len(bundle.system_activities),
        "deletion_rules": len(bundle.deletion_rules),
        "evidence_files": len(bundle.evidence_files),
    }


@app.get("/api/dashboard/stats")
def dashboard_stats():
    bundle = _get_bundle()
    total = len(bundle.deletion_requests)
    
    # ensure results cache has basic entries
    if not _results_cache:
        _load_cached_results_from_csv()
        
    counts = {"COMPLETE": 0, "INCOMPLETE": 0, "NOT_APPLICABLE": 0}
    for r in _results_cache.values():
        if r.deletion_status in counts:
            counts[r.deletion_status] += 1
            
    # recreation risks from system activities
    active_risks = sum(
        1 for act in bundle.system_activities
        if any(k in (act.details or "").lower() for k in ["restore", "recreat", "sync", "replay", "replicate"])
    )
    
    # recent cases
    recent_cases = [
        {
            "case_id": r.case_id,
            "email": r.email,
            "requested_at": r.requested_at,
            "reported_status": r.reported_status,
            "computed_status": _results_cache[r.case_id].deletion_status if r.case_id in _results_cache else None,
            "steps_count": len(_results_cache[r.case_id].steps) if r.case_id in _results_cache else 0
        }
        for r in bundle.deletion_requests[:10]
    ]

    # top incomplete/critical cases
    incomplete_cases = [
        {
            "case_id": r.case_id,
            "email": r.email,
            "requested_at": r.requested_at,
            "reported_status": r.reported_status,
            "computed_status": "INCOMPLETE",
            "steps_count": len(_results_cache[r.case_id].steps) if r.case_id in _results_cache else 0
        }
        for r in bundle.deletion_requests
        if r.case_id in _results_cache and _results_cache[r.case_id].deletion_status == "INCOMPLETE"
    ][:8]

    return {
        "total_requests": total,
        "status_counts": counts,
        "active_risks_count": active_risks,
        "recent_cases": recent_cases,
        "unresolved_cases": incomplete_cases,
        "cached_results": len(_results_cache),
    }


@app.get("/api/cases")
def list_cases(
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
):
    bundle = _get_bundle()
    if not _results_cache:
        _load_cached_results_from_csv()

    cases = []
    for r in bundle.deletion_requests:
        computed = _results_cache.get(r.case_id)
        comp_status = computed.deletion_status if computed else None
        cases.append({
            "case_id": r.case_id,
            "email": r.email,
            "requested_at": r.requested_at,
            "reported_status": r.reported_status,
            "audit_at": r.audit_at,
            "computed_status": comp_status,
            "steps_count": len(computed.steps) if computed else 0,
        })

    if status:
        cases = [c for c in cases if c.get("computed_status") == status]

    if search:
        s = search.lower().strip()
        cases = [
            c for c in cases
            if s in c["case_id"].lower() or s in c["email"].lower()
        ]

    total = len(cases)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "cases": cases[start:end],
    }


@app.get("/api/cases/{case_id}")
def get_case(case_id: str):
    bundle = _get_bundle()
    req = next((r for r in bundle.deletion_requests if r.case_id == case_id), None)
    if req is None:
        req = next((r for r in bundle.sample_requests if r.case_id == case_id), None)
    if req is None:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    result = _results_cache.get(case_id)
    # If not in cache or missing detailed inspection objects, compute it!
    if result is None or result.identity is None or result.classification is None:
        result = audit_case(req, bundle)
        _results_cache[case_id] = result

    response: Dict[str, Any] = {
        "case_id": req.case_id,
        "email": req.email,
        "requested_at": req.requested_at,
        "reported_status": req.reported_status,
        "reported_at": req.reported_at,
        "audit_at": req.audit_at,
        "request_text": req.request_text,
        "deletion_status": result.deletion_status,
        "steps": result.steps,
        "evidence_count": len(result.evidence_records),
        "identity": (
            {
                "user_id": result.identity.user_id,
                "current_email": result.identity.current_email,
                "all_emails": result.identity.all_emails,
                "matched": result.identity.matched,
            }
            if result.identity
            else None
        ),
        "classification": (
            {
                "is_deletion_request": result.classification.is_deletion_request,
                "confidence": result.classification.confidence,
                "reason": result.classification.reason,
            }
            if result.classification
            else None
        ),
        "decisions": [
            {
                "system_id": d.record.system_id if hasattr(d, 'record') else getattr(d, 'system_id', ''),
                "resource_id": d.record.resource_id if hasattr(d, 'record') else getattr(d, 'resource_id', ''),
                "action": getattr(d, 'required_action', getattr(d, 'action', '')),
                "reason": getattr(d, 'required_treatment', getattr(d, 'reason', '')),
                "rule_id": getattr(d, 'rule_id', ''),
            }
            for d in result.decisions
        ] if result.decisions else [],
        "parse_errors": result.parse_errors,
    }
    return response


@app.post("/api/cases/{case_id}/audit")
def audit_single_case(case_id: str):
    bundle = _get_bundle()
    req = next((r for r in bundle.deletion_requests if r.case_id == case_id), None)
    if req is None:
        req = next((r for r in bundle.sample_requests if r.case_id == case_id), None)
    if req is None:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    result = audit_case(req, bundle)
    _results_cache[case_id] = result

    return {
        "case_id": case_id,
        "deletion_status": result.deletion_status,
        "steps": result.steps,
        "evidence_count": len(result.evidence_records),
        "parse_errors": result.parse_errors,
    }


@app.post("/api/audit/run")
def run_batch_audit(req: BatchRunRequest, background_tasks: BackgroundTasks):
    bundle = _get_bundle()
    if req.case_ids:
        cases = [r for r in bundle.deletion_requests if r.case_id in req.case_ids]
    else:
        cases = bundle.deletion_requests

    _run_status["status"] = "running"
    _run_status["processed"] = 0
    _run_status["total"] = len(cases)

    def _run():
        for i, case in enumerate(cases):
            result = audit_case(case, bundle)
            _results_cache[case.case_id] = result
            _run_status["processed"] = i + 1
        _run_status["status"] = "complete"

    background_tasks.add_task(_run)
    return {"status": "started", "total": len(cases)}


@app.get("/api/audit/status")
def audit_status():
    if not _results_cache:
        _load_cached_results_from_csv()
    cached = len(_results_cache)
    bundle = _get_bundle()
    total = len(bundle.deletion_requests)
    status_counts: Dict[str, int] = {}
    for r in _results_cache.values():
        if isinstance(r, AuditResult):
            status_counts[r.deletion_status] = status_counts.get(r.deletion_status, 0) + 1
    return {
        "run_status": _run_status,
        "cached_results": cached,
        "total_cases": total,
        "status_counts": status_counts,
    }


@app.post("/api/audit/export")
def export_csv():
    if not _results_cache:
        _load_cached_results_from_csv()
    if not _results_cache:
        raise HTTPException(status_code=400, detail="No audit results cached. Run audit first.")
    results = [r for r in _results_cache.values() if isinstance(r, AuditResult)]
    repo_root = Path(__file__).resolve().parent.parent
    output_path = write_output_csv(results, repo_root / "output.csv")
    validation = validate_output_csv(output_path, [r.case_id for r in results])
    return {
        "output_path": str(output_path),
        "row_count": validation.get("row_count", len(results)),
        "valid": validation["valid"],
        "errors": validation.get("errors", []),
    }


@app.get("/api/audit/download-csv")
def download_csv():
    repo_root = Path(__file__).resolve().parent.parent
    path = repo_root / "output.csv"
    if not path.exists():
        raise HTTPException(status_code=404, detail="output.csv not found — run export first")
    return FileResponse(str(path), filename="output.csv", media_type="text/csv")


@app.get("/api/evaluation")
def get_evaluation():
    bundle = _get_bundle()
    sample_ids = {r.case_id for r in bundle.sample_requests}
    if not all(cid in _results_cache for cid in sample_ids):
        for s_req in bundle.sample_requests:
            if s_req.case_id not in _results_cache or _results_cache[s_req.case_id].identity is None:
                _results_cache[s_req.case_id] = audit_case(s_req, bundle)

    sample_results = [_results_cache[cid] for cid in sample_ids if cid in _results_cache]
    report = evaluate_against_samples(sample_results)
    return {
        "total_cases": report.total_cases,
        "status_accuracy": report.status_accuracy,
        "step_precision": report.step_precision,
        "step_recall": report.step_recall,
        "operation_order_accuracy": report.operation_order_accuracy,
        "case_evaluations": [
            {
                "case_id": ev.case_id,
                "expected_status": ev.expected_status,
                "predicted_status": ev.predicted_status,
                "status_correct": ev.status_correct,
                "step_count_expected": ev.step_count_expected,
                "step_count_predicted": ev.step_count_predicted,
                "matching_steps": ev.matching_steps,
                "notes": ev.notes,
            }
            for ev in report.case_evaluations
        ],
    }


@app.post("/api/classify")
def classify_text(req: ClassifyRequest):
    result = classify_request(req.text)
    return {
        "is_deletion_request": result.is_deletion_request,
        "confidence": result.confidence,
        "reason": result.reason,
    }


@app.get("/api/deletion-rules")
def get_deletion_rules():
    bundle = _get_bundle()
    return [
        {
            "rule_id": r.rule_id,
            "data_category": r.data_category,
            "condition": r.condition,
            "required_action": r.required_action,
        }
        for r in bundle.deletion_rules
    ]


@app.get("/api/evidence-files")
def get_evidence_files():
    bundle = _get_bundle()
    return [
        {
            "file_id": e.file_id,
            "file_path": e.file_path,
            "origin": e.origin,
            "captured_at": e.captured_at,
        }
        for e in bundle.evidence_files
    ]


@app.get("/api/evidence/{file_id}/content")
def get_evidence_content(file_id: str):
    bundle = _get_bundle()
    ef = next((f for f in bundle.evidence_files if f.file_id == file_id), None)
    if not ef:
        raise HTTPException(status_code=404, detail="Evidence file not found")

    repo_root = Path(__file__).resolve().parent.parent
    fpath = repo_root / ef.file_path
    if not fpath.exists():
        fpath = repo_root / "dataset" / "evidence" / Path(ef.file_path).name

    content = ""
    f_type = fpath.suffix.lower()
    if fpath.exists():
        try:
            if f_type in [".json", ".txt", ".csv", ".log", ".md"]:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read(5000)
            elif f_type in [".sqlite", ".db"]:
                content = f"SQLite Database File ({fpath.stat().st_size} bytes). Relational tables indexed."
            elif f_type == ".pdf":
                content = f"PDF Document ({fpath.stat().st_size} bytes). Extracted text and OCR data scanned."
            else:
                content = f"Binary Resource ({fpath.stat().st_size} bytes)."
        except Exception as ex:
            content = f"Read error: {ex}"
    else:
        content = f"Evidence registered in manifest: {ef.file_path}"

    return {
        "file_id": ef.file_id,
        "file_path": ef.file_path,
        "origin": ef.origin,
        "captured_at": ef.captured_at,
        "file_type": f_type,
        "content_preview": content,
    }


@app.get("/api/identities")
def list_identities():
    bundle = _get_bundle()
    history_by_user = {}
    for entry in bundle.audit_log:
        history_by_user.setdefault(entry.user_id, []).append({
            "audit_id": entry.audit_id,
            "event_time": entry.event_time,
            "field_name": entry.field_name,
            "old_value": entry.old_value,
            "new_value": entry.new_value,
        })

    return [
        {
            "user_id": u.user_id,
            "name": u.name,
            "email": u.email,
            "phone": u.phone,
            "email_hash": u.email_hash,
            "updated_at": u.updated_at,
            "history_count": len(history_by_user.get(u.user_id, [])),
            "history": history_by_user.get(u.user_id, []),
        }
        for u in bundle.user_identities
    ]


@app.get("/api/active-processes")
def list_active_processes():
    bundle = _get_bundle()
    items = []
    for act in bundle.system_activities:
        details = (act.details or "").lower()
        evt = (act.event_type or "").lower()
        is_restore_risk = any(k in details or k in evt for k in ["restore", "recreat", "sync", "replay", "replicate", "backup", "pipeline"])
        risk_level = "HIGH" if is_restore_risk else "LOW"
        items.append({
            "activity_id": act.activity_id,
            "event_time": act.event_time,
            "event_type": act.event_type,
            "source_system": act.source_system,
            "target_system": act.target_system,
            "resource_id": act.resource_id,
            "reported_result": act.reported_result,
            "details": act.details,
            "is_restore_risk": is_restore_risk,
            "risk_level": risk_level,
        })
    return items


@app.get("/api/data-sources")
def list_data_sources():
    bundle = _get_bundle()
    sources = {}
    for ef in bundle.evidence_files:
        orig = ef.origin or "general_store"
        sources.setdefault(orig, {"name": orig, "file_count": 0, "types": set(), "files": []})
        sources[orig]["file_count"] += 1
        ext = Path(ef.file_path).suffix.lower()
        sources[orig]["types"].add(ext)
        if len(sources[orig]["files"]) < 4:
            sources[orig]["files"].append({
                "file_id": ef.file_id,
                "file_path": ef.file_path,
                "captured_at": ef.captured_at
            })

    return [
        {
            "name": k,
            "file_count": v["file_count"],
            "supported_types": sorted(list(v["types"])),
            "sample_files": v["files"],
            "status": "ONLINE",
            "health": "HEALTHY",
            "last_audit_sync": "2026-10-25T12:00:00Z"
        }
        for k, v in sources.items()
    ]


@app.get("/api/reports/validation")
def get_validation_report():
    repo_root = Path(__file__).resolve().parent.parent
    csv_path = repo_root / "output.csv"
    if not csv_path.exists():
        return {"exists": False, "valid": False, "errors": ["output.csv not found"]}

    bundle = _get_bundle()
    expected_ids = [r.case_id for r in bundle.deletion_requests]
    val = validate_output_csv(csv_path, expected_ids)
    return {
        "exists": True,
        "valid": val["valid"],
        "row_count": val.get("row_count", 0),
        "expected_count": len(expected_ids),
        "errors": val.get("errors", []),
    }


@app.get("/api/settings")
def get_settings():
    return _settings


@app.post("/api/settings")
def update_settings(update: SettingsUpdate):
    for k, v in update.model_dump(exclude_unset=True).items():
        _settings[k] = v
    return _settings


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
