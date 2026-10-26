// API client for Northstar Audit Backend
const BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

async function fetchJSON(path: string, opts?: RequestInit) {
  const res = await fetch(`${BASE}${path}`, {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...opts?.headers },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  health: () => fetchJSON('/health'),
  datasetSummary: () => fetchJSON('/api/dataset/summary'),
  dashboardStats: () => fetchJSON('/api/dashboard/stats'),
  listCases: (params: { status?: string; search?: string; page?: number; page_size?: number }) => {
    const q = new URLSearchParams();
    if (params.status) q.set('status', params.status);
    if (params.search) q.set('search', params.search);
    if (params.page) q.set('page', String(params.page));
    if (params.page_size) q.set('page_size', String(params.page_size));
    return fetchJSON(`/api/cases?${q}`);
  },
  getCase: (caseId: string) => fetchJSON(`/api/cases/${caseId}`),
  auditCase: (caseId: string) => fetchJSON(`/api/cases/${caseId}/audit`, { method: 'POST' }),
  runBatch: (caseIds?: string[]) =>
    fetchJSON('/api/audit/run', {
      method: 'POST',
      body: JSON.stringify({ case_ids: caseIds ?? null }),
    }),
  auditStatus: () => fetchJSON('/api/audit/status'),
  exportCsv: () => fetchJSON('/api/audit/export', { method: 'POST' }),
  downloadCsv: () => `${BASE}/api/audit/download-csv`,
  evaluation: () => fetchJSON('/api/evaluation'),
  classify: (text: string) =>
    fetchJSON('/api/classify', { method: 'POST', body: JSON.stringify({ text }) }),
  deletionRules: () => fetchJSON('/api/deletion-rules'),
  evidenceFiles: () => fetchJSON('/api/evidence-files'),
  evidenceContent: (fileId: string) => fetchJSON(`/api/evidence/${fileId}/content`),
  listIdentities: () => fetchJSON('/api/identities'),
  listActiveProcesses: () => fetchJSON('/api/active-processes'),
  listDataSources: () => fetchJSON('/api/data-sources'),
  validationReport: () => fetchJSON('/api/reports/validation'),
  getSettings: () => fetchJSON('/api/settings'),
  updateSettings: (settings: Record<string, any>) =>
    fetchJSON('/api/settings', { method: 'POST', body: JSON.stringify(settings) }),
};

export type CaseRow = {
  case_id: string;
  email: string;
  requested_at: string;
  reported_status: string;
  audit_at: string;
  computed_status: string | null;
  steps_count?: number;
};

export type CaseDetail = CaseRow & {
  reported_at: string;
  request_text: string;
  deletion_status: string | null;
  steps: AuditStep[] | null;
  evidence_count: number;
  identity: IdentityInfo | null;
  classification: Classification | null;
  decisions?: PolicyDecision[];
  parse_errors: string[];
};

export type PolicyDecision = {
  system_id: string;
  resource_id: string;
  action: string;
  reason: string;
  rule_id?: string;
};

export type AuditStep = {
  step_no: number;
  phase: 'FINDING' | 'REMEDIATION' | 'VERIFICATION';
  system_id: string;
  resource_id: string;
  operation: string;
  state: string;
  evidence_id: string;
};

export type IdentityInfo = {
  user_id: string | null;
  current_email: string | null;
  all_emails: string[];
  matched: boolean;
};

export type UserIdentityRecord = {
  user_id: string;
  name: string;
  email: string;
  phone: string;
  email_hash: string;
  updated_at: string;
  history_count: number;
  history: Array<{
    audit_id: string;
    event_time: string;
    field_name: string;
    old_value: string;
    new_value: string;
  }>;
};

export type ActiveProcess = {
  activity_id: string;
  event_time: string;
  event_type: string;
  source_system: string;
  target_system: string;
  resource_id: string;
  reported_result: string;
  details: string;
  is_restore_risk: boolean;
  risk_level: 'HIGH' | 'LOW';
};

export type DataSourceItem = {
  name: string;
  file_count: number;
  supported_types: string[];
  sample_files: Array<{ file_id: string; file_path: string; captured_at: string }>;
  status: string;
  health: string;
  last_audit_sync: string;
};

export type Classification = {
  is_deletion_request: boolean;
  confidence: 'high' | 'medium' | 'low';
  reason: string;
};

export type AuditStatusResponse = {
  run_status: { status: string; processed: number; total: number };
  cached_results: number;
  total_cases: number;
  status_counts: Record<string, number>;
};

export type DashboardStatsResponse = {
  total_requests: number;
  status_counts: Record<string, number>;
  active_risks_count: number;
  recent_cases: CaseRow[];
  unresolved_cases: CaseRow[];
  cached_results: number;
};

export type EvaluationReport = {
  total_cases: number;
  status_accuracy: number;
  step_precision: number;
  step_recall: number;
  operation_order_accuracy: number;
  case_evaluations: CaseEvaluation[];
};

export type CaseEvaluation = {
  case_id: string;
  expected_status: string;
  predicted_status: string;
  status_correct: boolean;
  step_count_expected: number;
  step_count_predicted: number;
  matching_steps: number;
  notes: string[];
};
