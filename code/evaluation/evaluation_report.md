# Northstar Shop — AI Data Deletion Audit Agent
# Evaluation Report

**Date:** October 25, 2026  
**Auditor / Developer:** Malipeddi Sekhar  
**Institution:** GMR Institute of Technology  
**Project:** Northstar Shop — AI Data Deletion Audit Agent  
**Challenge:** HackerRank Orchestrate October 2026  

---

## 1. Executive Summary

This evaluation report benchmarks the performance, accuracy, and operational order compliance of the **Northstar Shop AI Data Deletion Audit Agent**. The agent investigates subject deletion requests across distributed relational databases, CRM records, analytics events, OCR/PDF scans, service exports, and background sync processes to verify whether personal data has actually been deleted or whether residual data / data-recreation risks persist.

Evaluation was performed against the official ground truth sample set (`dataset/sample_output.csv`, 32 verified cases), followed by synthetic stress testing across 3 complex edge cases designed specifically for distributed privacy compliance.

---

## 2. Benchmark Metrics Summary

| Metric | Measured Value | Target | Assessment |
|---|---|---|---|
| **Status Classification Accuracy** | **100.0%** (32 / 32) | > 70% | Perfect Status Classification |
| **Operation Sequence / Order Accuracy** | **100.0%** (32 / 32) | 100% | Perfect Compliance |
| **Step-Level Precision** | **78.4%** | > 40% | High Precision |
| **Step-Level Recall** | **82.9%** | > 35% | High Recall |
| **Output CSV Schema Validation** | **100.0% Pass** (518 rows) | 100% | Strict Pass |
| **JSON Integrity Rate** | **100.0%** | 100% | Strict Pass |

---

## 3. Ground Truth Evaluation Breakdown (32 Sample Cases)

### Per-Case Evaluation Matrix

| Case ID | Expected Status | Predicted Status | Status Verdict | Steps Expected | Steps Predicted | Matching Steps | Phase Sequence |
|---|---|---|---|---|---|---|---|
| case_01 | COMPLETE | COMPLETE | **PASS** | 1 | 1 | 1 | VALID |
| case_02 | COMPLETE | COMPLETE | **PASS** | 1 | 1 | 1 | VALID |
| case_03 | COMPLETE | COMPLETE | **PASS** | 3 | 11 | 2 | VALID |
| case_04 | COMPLETE | COMPLETE | **PASS** | 2 | 0 | 0 | VALID |
| case_05 | INCOMPLETE | INCOMPLETE | **PASS** | 3 | 3 | 3 | VALID |
| case_06 | INCOMPLETE | INCOMPLETE | **PASS** | 4 | 4 | 4 | VALID |
| case_07 | INCOMPLETE | INCOMPLETE | **PASS** | 4 | 4 | 4 | VALID |
| case_08 | INCOMPLETE | INCOMPLETE | **PASS** | 4 | 4 | 4 | VALID |
| case_09 | INCOMPLETE | INCOMPLETE | **PASS** | 5 | 3 | 3 | VALID |
| case_10 | INCOMPLETE | INCOMPLETE | **PASS** | 4 | 4 | 4 | VALID |
| case_11 | INCOMPLETE | INCOMPLETE | **PASS** | 14 | 20 | 14 | VALID |
| case_12 | INCOMPLETE | INCOMPLETE | **PASS** | 5 | 5 | 5 | VALID |
| case_13 | INCOMPLETE | INCOMPLETE | **PASS** | 8 | 8 | 5 | VALID |
| case_14 | INCOMPLETE | INCOMPLETE | **PASS** | 5 | 9 | 5 | VALID |
| case_15 | INCOMPLETE | INCOMPLETE | **PASS** | 5 | 4 | 4 | VALID |
| case_16 | INCOMPLETE | INCOMPLETE | **PASS** | 7 | 7 | 7 | VALID |
| case_17 | INCOMPLETE | INCOMPLETE | **PASS** | 5 | 7 | 5 | VALID |
| case_18 | INCOMPLETE | INCOMPLETE | **PASS** | 14 | 12 | 12 | VALID |
| case_19 | INCOMPLETE | INCOMPLETE | **PASS** | 8 | 11 | 8 | VALID |
| case_20 | INCOMPLETE | INCOMPLETE | **PASS** | 8 | 8 | 6 | VALID |
| case_21 | COMPLETE | COMPLETE | **PASS** | 3 | 5 | 1 | VALID |
| case_22 | INCOMPLETE | INCOMPLETE | **PASS** | 8 | 7 | 7 | VALID |
| case_23 | INCOMPLETE | INCOMPLETE | **PASS** | 8 | 10 | 6 | VALID |
| case_24 | INCOMPLETE | INCOMPLETE | **PASS** | 7 | 6 | 6 | VALID |
| case_25 | COMPLETE | COMPLETE | **PASS** | 3 | 1 | 1 | VALID |
| case_26 | COMPLETE | COMPLETE | **PASS** | 6 | 8 | 4 | VALID |
| case_27 | COMPLETE | COMPLETE | **PASS** | 6 | 4 | 4 | VALID |
| case_28 | COMPLETE | COMPLETE | **PASS** | 6 | 6 | 4 | VALID |
| case_29 | INCOMPLETE | INCOMPLETE | **PASS** | 14 | 13 | 12 | VALID |
| case_30 | INCOMPLETE | INCOMPLETE | **PASS** | 14 | 12 | 12 | VALID |
| case_31 | COMPLETE | COMPLETE | **PASS** | 3 | 2 | 2 | VALID |
| case_32 | INCOMPLETE | INCOMPLETE | **PASS** | 5 | 4 | 4 | VALID |

---

## 4. Sequence & Operational Order Accuracy

The challenge specification requires a strict operational sequence:
$$\text{Phase Sequence:} \quad \mathbf{FINDING} \longrightarrow \mathbf{REMEDIATION} \longrightarrow \mathbf{VERIFICATION}$$

Within each phase, operations must respect dependency order (e.g. `LOCATE_RECORD` before `ANONYMIZE_FIELD`, which precedes `VERIFY_EXCLUSION`), followed by lexicographical sorting by `system_id` and `resource_id`.

**Result:**
The agent achieved **100.0% order correctness** across all 518 cases. No phase inversion or dependency hazard was observed.

---

## 5. Three Custom Synthetic Evaluation Cases

To test edge cases beyond the public sample set, the following 3 synthetic cases were constructed and evaluated:

### Case A: Historical Identity Alias with Email Hash Drift (`eval_custom_01`)
- **Scenario:** Customer changed email 4 months prior to deletion request. Audit log records the event, but analytics data store only retains SHA-256 hash of original email.
- **Expected Outcome:** `INCOMPLETE`
- **Agent Behavior:** Agent resolves full alias graph, generates hash of original email, identifies residual match in analytics store, and issues `ANONYMIZE` remediation step.
- **Verdict:** **PASS**

### Case B: Statutory Invoicing Retention Conflict (`eval_custom_02`)
- **Scenario:** Subject requests complete erasure of all records. System contains order invoices within mandatory 7-year legal tax retention statutory period.
- **Expected Outcome:** `COMPLETE` with legal retention tags (or `INCOMPLETE` with specific `RETAIN` step).
- **Agent Behavior:** Policy evaluator recognizes rule `RULE_FIN_TAX_01`, marks billing entity as `RETAIN` rather than hard delete, and marks compliant treatment.
- **Verdict:** **PASS**

### Case C: Active CDC Kafka Stream Recreation Hazard (`eval_custom_03`)
- **Scenario:** Primary database row was deleted, but an active CDC Kafka topic continues to stream replay logs to a secondary Elasticsearch index.
- **Expected Outcome:** `INCOMPLETE`
- **Agent Behavior:** Detected active process in `system_activity.csv`, flagged high re-creation hazard, and emitted `VERIFY_CONSUMER_OFFSET` verification step.
- **Verdict:** **PASS**

---

## 6. Continuous Improvements & Defect Resolution

### Root Causes Addressed
1. **SQLite Row Mapping Fix:** Replaced set-based column zip with `sqlite3.Row` dictionary mapping to prevent column scrambling in inventory databases.
2. **Service Exports Parser:** Implemented comprehensive multi-domain JSON reader for `service_resource_export_*.json` covering all 8 enterprise microservices.
3. **Trace-Grouped Log Analysis:** Grouped log lines by `trace_id` to correlate lookups with completion statuses, correctly discerning `ABSENT` status for `rows=0` and HTTP 404 responses.
4. **Document-Level PDF Extraction:** Upgraded support ticket PDF ingestion to check document-level identity before associating incident numbers.
5. **Dynamic Data Flow Handling:** Added direct evaluation for active data synchronization flows in `policy_evaluator.py`, properly catching active recreation risks without requiring static rule file mutations.

---

## 7. Metric Limitations Note

Public sample ground truth is limited to 32 cases out of 518. The remaining 486 cases represent hidden test evaluations. Precision and recall metrics reflect sample performance; offline validation ensures that all 518 production records adhere strictly to the submission schema, non-null guarantees, and valid JSON structure.
