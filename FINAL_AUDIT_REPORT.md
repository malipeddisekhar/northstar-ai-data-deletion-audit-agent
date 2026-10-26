# Northstar Shop — AI Data Deletion Audit Agent
# FINAL AUDIT REPORT

**Date:** October 25, 2026  
**Auditor & Developer:** Malipeddi Sekhar  
**Institution:** GMR Institute of Technology  
**Project:** Northstar Shop — AI Data Deletion Audit Agent (v2.4.0)  
**Challenge:** HackerRank Orchestrate October 2026 — *Ghost Data Still Exists*  
**Submission URL:** [https://www.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists](https://www.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists)

---

## A. Overall Readiness

### **OUTCOME: READY TO SUBMIT**

Every functional test, benchmark evaluation, CSV schema validation, backend endpoint verification, frontend UI check, and packaging constraint has been executed, reproduced, verified, and strictly passed.

---

## B. Business Logic & Engine Verification

The audit engine operates across 518 production deletion requests (`dataset/deletion_requests.csv`), 8 enterprise microservices, 15 privacy policies (`dataset/deletion_rules.csv`), multi-format evidence stores (SQL replicas, OCR/PDF scans, service exports, JSON snapshots, plaintext logs), and real-time background sync processes.

### 1. Request Classification
- Genuine deletion requests are audited through evidence correlation.
- Non-deletion requests (e.g. data access/export, newsletter unsubscribe, address update) are classified as `NOT_APPLICABLE` with `steps_json = []`.
- Tested across sample cases (`case_01`, `case_02`, `case_04`) and production dataset without hardcoding labels.

### 2. Identity Resolution
- Graph-based resolution identifies current and historical identifiers (`user_identities.csv`, `user_audit_log.csv`).
- Historical email hashes (SHA-256) are dynamically recovered from audit logs to catch orphaned records in analytics stores.
- Stable internal references (`user_id` / `account_id`) are resolved without false merging of distinct accounts.

### 3. Evidence Investigation & Defect Resolution
During the audit, five functional defects were discovered and fixed:
1. **SQLite Dictionary Scrambling (`evidence_investigator.py`):** Fixed set-based column ordering in `_scan_inventory_table` by migrating to `conn.row_factory = sqlite3.Row`.
2. **Multi-Domain Service Exports (`evidence_investigator.py`):** Implemented `_scan_service_exports` handler routing all 8 service domains (`account_service.profile_lookups`, `analytics_platform.profiles`, `backup_service.restore_entries`, `export_service.exports`, `integration_service.connectors`, `search_service.index_queries`, `security_service.audit_records`, `support_platform.tickets`).
3. **Trace-Grouped Log Analysis (`evidence_investigator.py`):** Correlated log entries by `trace_id` so completion lines inherit lookup identities, properly marking queries with `rows=0` and HTTP 404 as `ABSENT`.
4. **Document-Level PDF Ingestion (`evidence_investigator.py`):** Extracted text across full support ticket PDF documents (`delivery_correction_attachment.pdf`) so headers and body identities correctly bind to incident IDs.
5. **Active Data Flow Recreation Risks (`policy_evaluator.py`):** Added direct evaluation for dynamic `DATA_FLOW` sync risks (e.g. `REPORT_ACTIVE_DATA_FLOW`, `DISABLE_DATA_FLOW`, `VERIFY_DATA_FLOW_DISABLED`) which are absent from static `deletion_rules.csv`.

### 4. Policy-Based Record Treatment & Retention
- Mandated retention policies (e.g. 7-year statutory billing/tax records `RULE_FIN_TAX_01`) are preserved.
- Account tombstones retaining stable internal IDs with redacted PII are recognized as compliant (`REDACTED`).

### 5. Final Status Calculation
- `COMPLETE` is assigned only when all residual PII is absent or legitimately redacted/retained, and no active recreation hazard exists.
- In `COMPLETE` cases, all verification checks cite the evidence proving the absent/redacted state.
- In `INCOMPLETE` cases, proposed remediation and future verification steps use empty `evidence_id: ""`.

---

## C. Test Results

| Test ID | Component / Target | Test Description | Result | Evidence / Details |
|---|---|---|---|---|
| **T-01** | `identity_resolver.py` | Historical email hash resolution from audit logs | **PASS** | Captured `email_hash` field changes from `user_audit_log.csv` |
| **T-02** | `evidence_investigator.py` | SQLite table column-order fidelity | **PASS** | `case_09` and inventory tables properly parsed via `sqlite3.Row` |
| **T-03** | `evidence_investigator.py` | Multi-domain service export parsing | **PASS** | `evidence_92` (8 microservices) parsed without drop |
| **T-04** | `evidence_investigator.py` | Trace correlation in query logs | **PASS** | `case_03` 404 lookups correctly evaluated as `ABSENT` |
| **T-05** | `evidence_investigator.py` | PDF document-level identity parsing | **PASS** | `support_platform` ticket `support_001` extracted with priya.raman |
| **T-06** | `policy_evaluator.py` | Dynamic data flow hazard evaluation | **PASS** | `case_15` data flow `data_flow_9846` flagged with ordered steps |
| **T-07** | `pipeline.py` | Phase and operation order compliance | **PASS** | Strict `FINDING ➔ REMEDIATION ➔ VERIFICATION` sequence (100%) |
| **T-08** | `code/evaluation/main.py` | Ground truth sample benchmark (32 cases) | **PASS** | 100.0% Status Accuracy (32/32 PASS), 100.0% Order Accuracy |
| **T-09** | `output.csv` Generator | Full 518-case production pipeline run | **PASS** | Generated root `output.csv` with exactly 518 valid rows |
| **T-10** | Backend API | All 12 REST endpoints tested with `http.client` | **PASS** | All routes returned HTTP 200 with valid JSON/CSV payload |
| **T-11** | Frontend UI | Browser automation subagent across all screens | **PASS** | Zero console errors, responsive layout, real data rendering |
| **T-12** | Packaging | `Compress-Archive` packaging of `code.zip` | **PASS** | 40,887 bytes; includes README, code/, reports; excludes dataset/cache |

---

## D. Evaluation Results

Official benchmark execution via `python code/evaluation/main.py`:

```
============================================================
NORTHSTAR SHOP — AI DATA DELETION AUDIT AGENT
BENCHMARK EVALUATION REPORT
============================================================
Total Cases Evaluated:       32
Status Accuracy:             100.0% (32 / 32 PASS)
Operation Order Accuracy:    100.0%
Step Precision:              78.4%
Step Recall:                 82.9%
============================================================
```

### Full-Dataset (518 Cases) Distribution:
- **COMPLETE:** 191 cases (36.9%)
- **INCOMPLETE:** 323 cases (62.4%)
- **NOT_APPLICABLE:** 4 cases (0.8%)
- **Total:** 518 cases

### Three Custom Synthetic Evaluation Cases:
1. **Case A (`eval_custom_01`):** Historical Identity Alias with Email Hash Drift ➔ **PASS** (`INCOMPLETE`, issued `ANONYMIZE` step).
2. **Case B (`eval_custom_02`):** Statutory Invoicing Retention Conflict (`RULE_FIN_TAX_01`) ➔ **PASS** (`COMPLETE`, compliant retention).
3. **Case C (`eval_custom_03`):** Active CDC Kafka Stream Recreation Hazard ➔ **PASS** (`INCOMPLETE`, detected active process).

---

## E. CSV Validation

Validation executed on root `output.csv`:
- **File Location:** `D:\Hackthon hackerrank\hackerrank-orchestrate-october26\output.csv`
- **Header:** `case_id,deletion_status,steps_json` (exact casing and order)
- **Data Rows:** Exactly 518 rows (matching `dataset/deletion_requests.csv`)
- **Total File Lines:** 519 (including header)
- **Duplicate Check:** 0 duplicates; `case_id.nunique() == 518`
- **Permitted Status Values:** `{'COMPLETE', 'INCOMPLETE', 'NOT_APPLICABLE'}`
- **JSON Structure:** Every row has a valid JSON array (`[]` for `NOT_APPLICABLE`)
- **Step Numbering:** Strictly consecutive 1-indexed integers (`step_no: 1, 2, ...`)
- **Step Schema:** Contains all required fields: `['evidence_id', 'operation', 'phase', 'resource_id', 'state', 'step_no', 'system_id']`
- **Phase Ordering:** Strictly ordered `FINDING ➔ REMEDIATION ➔ VERIFICATION`

---

## F. Frontend and Backend Integration

### Backend Endpoints Tested (FastAPI at `http://127.0.0.1:8000`):
1. `GET /health` ➔ HTTP 200 (Service healthy, v2.4.0)
2. `GET /api/dataset/summary` ➔ HTTP 200 (518 requests, 15 rules, 100 evidence files)
3. `GET /api/dashboard/stats` ➔ HTTP 200 (Real metrics from output.csv cache)
4. `GET /api/cases` ➔ HTTP 200 (Paginated cases with status filter)
5. `GET /api/cases/{case_id}` ➔ HTTP 200 (Full investigation details, steps, rules)
6. `GET /api/audit/status` ➔ HTTP 200 (Engine readiness and cache state)
7. `GET /api/audit/download-csv` ➔ HTTP 200 (Downloads root `output.csv`, 465 KB)
8. `GET /api/evaluation` ➔ HTTP 200 (Benchmark report and sample evaluation)
9. `GET /api/deletion-rules` ➔ HTTP 200 (Dynamic policy rule catalog)
10. `GET /api/identities` ➔ HTTP 200 (Identity graph directory)
11. `GET /api/active-processes` ➔ HTTP 200 (Recreation risk processes)
12. `GET /api/data-sources` ➔ HTTP 200 (Microservice data sources inventory)
13. `GET /api/reports/validation` ➔ HTTP 200 (CSV integrity audit metrics)
14. `GET /api/settings` ➔ HTTP 200 (System configuration)

### Frontend Screens Tested (React / Vite at `http://localhost:5173`):
- **Executive Dashboard:** Live cards (518 Total, 191 Complete, 323 Incomplete, 4 N/A), donut distribution chart, and unresolved case list.
- **Deletion Requests Registry:** Search, filter by status, sort, and pagination across 518 cases.
- **Audit Case Investigation:** Request details, intent classifier, remediation steps table, resolved identity graph, and applied policies.
- **Identity Resolution Directory:** Current user records and audit log history.
- **Evidence Explorer:** Manifest table with format badges (JSON, SQL, LOG, PDF) and content viewer.
- **Enterprise Data Sources:** Inventory of 8 microservices and health metrics.
- **Active Processes & Recreation Hazards:** Real-time sync flows and replication monitors.
- **Remediation Plan Workflow:** Step-by-step ordered table with phase tags.
- **Verification Center:** 5 compliance verification gates with status badges.
- **Accuracy & Evaluation Benchmark:** Live metric cards showing 100% status accuracy and per-case results table.
- **Reports & Submission Export:** Schema validation checklist and direct CSV export button.
- **System Settings:** Engine configuration and developer credits footer.

---

## G. Submission Deliverables

The three required submission artifacts have been generated, validated, and placed at their required locations:

| Deliverable | File Path | Size | Description |
|---|---|---|---|
| **1. Code ZIP** | `D:\Hackthon hackerrank\hackerrank-orchestrate-october26\code.zip` | ~40.9 KB | Clean ZIP containing `README.md`, `code/`, `code/evaluation/evaluation_report.md`, and `code/evaluation/usage_report.md`. Excludes dataset, node_modules, and cache. |
| **2. Predictions CSV** | `D:\Hackthon hackerrank\hackerrank-orchestrate-october26\output.csv` | ~465 KB | Final output generated by the audit engine containing 518 rows, schema `case_id,deletion_status,steps_json`. |
| **3. Chat Transcript** | `D:\Hackthon hackerrank\hackerrank-orchestrate-october26\log.txt` | ~7.2 KB | Genuine session chat transcript following the `AGENTS.md` per-turn format with secrets redacted. |

---

## H. Remaining Issues & Limitations

- **Public vs Hidden Cases:** Ground truth is publicly available for 32 sample cases (100% status accuracy achieved). The remaining 486 cases represent hidden evaluation test data. Offline schema validation confirms strict structural and policy compliance for all 518 cases.
- **Air-Gapped Operation:** The engine runs completely offline with zero external API rate-limit dependencies, ensuring reproducible execution on the evaluation server.

---

## I. Final Instructions

### To Run Locally:
```bash
# 1. Run full audit pipeline (regenerates output.csv)
python code/main.py

# 2. Run benchmark evaluation
python code/evaluation/main.py

# 3. Start backend API
python -m uvicorn code.api:app --host 0.0.0.0 --port 8000

# 4. Start frontend UI
cd frontend && npm run dev
```

### To Submit to HackerRank:
1. Navigate to: [https://www.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists](https://www.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists)
2. Upload `code.zip` to the **Code** slot.
3. Upload `output.csv` to the **Predictions** slot.
4. Upload `log.txt` to the **Chat Transcript** slot.
5. Submit the challenge.
