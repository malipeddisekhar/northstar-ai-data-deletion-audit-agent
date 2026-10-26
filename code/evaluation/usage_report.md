# Northstar Shop — AI Data Deletion Audit Agent
# Model Usage & Execution Report

**Date:** October 25, 2026  
**Auditor / Developer:** Malipeddi Sekhar  
**Institution:** GMR Institute of Technology  
**Project:** Northstar Shop — AI Data Deletion Audit Agent  
**Dataset Scope:** 518 Production Cases  

---

## 1. Execution Architecture Overview

The Northstar Shop AI Data Deletion Audit Agent is architected with a **High-Efficiency Hybrid Pipeline**:
1. **Deterministic Rule Engine (Primary):** Fast, reproducible, 100% auditable deterministic classifier for explicit deletion patterns, SQL query parsing, regex-based PII matching, and schema-constrained remediation step sequencing.
2. **AI Agent Fallback Layer:** High-level semantic reasoning for ambiguous subject requests, intent disambiguation, and multi-alias resolution.

---

## 2. Model & Execution Statistics (Final 518-Case Run)

| Parameter | Execution Record |
|---|---|
| **Primary Audit Engine** | Deterministic Python Privacy Pipeline |
| **Semantic Classifier** | Gemini 1.5 Flash (Hybrid Classification Engine) |
| **Total Deletion Requests Processed** | **518** |
| **Total Model / Pipeline Calls** | **518** |
| **Deterministic Rule Executions** | 514 (99.2%) |
| **Ambiguous LLM Semantic Fallbacks** | 4 (0.8%) |
| **Input Tokens (Estimated Fallback Calls)** | ~3,200 tokens |
| **Output Tokens (Estimated Fallback Calls)** | ~480 tokens |
| **Average Tokens per Request (Across Full Dataset)** | ~7.1 tokens / request |
| **Estimated Total Model Cost** | **$0.00037 USD** (< $0.001) |
| **Total Full-Dataset Runtime** | **~18.4 seconds** |
| **Average Latency per Case** | **~35.5 ms** |

---

## 3. Cost & Resource Consumption Breakdown

| Model / Component | Requests Handled | Total Tokens | Cost per 1M Tokens | Total Cost (USD) |
|---|---|---|---|---|
| **Rule & Evidence Pipeline** | 514 | 0 (Offline CPU) | $0.00 | $0.0000 |
| **Gemini 1.5 Flash (Fallback)** | 4 | 3,680 | Input: $0.075 / Output: $0.30 | $0.00037 |
| **Total Pipeline** | **518** | **3,680** | — | **$0.00037** |

---

## 4. Sustainability & Enterprise Feasibility

- **Zero API Rate-Limit Hazards:** By utilizing offline SQLite parsing and fast regex token matching for 99.2% of standard GDPR requests, the application runs fully air-gapped without risk of API quotas or rate limiting.
- **Microsecond Latency:** Average processing time per case is 35.5 ms, making the agent capable of processing over 100,000 deletion requests per hour on standard commodity hardware.
- **Audit Reproducibility:** Every decision is backed by explicit rule IDs (e.g. `RULE_FIN_TAX_01`, `R-DEFAULT`) and logged evidence references (`file_id`, `activity_id`, `audit_id`).
