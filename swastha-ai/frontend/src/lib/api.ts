/**
 * SwasthaAI API client.
 * All requests go to the FastAPI backend via the /api/v1 prefix.
 * The base URL is configurable via VITE_API_BASE_URL env var (defaults to same origin).
 */

import axios from 'axios';

const BASE_URL = (import.meta as any).env?.VITE_API_BASE_URL ?? '';

export const api = axios.create({
  baseURL: `${BASE_URL}/api/v1/output`,
  headers: {
    'Content-Type': 'application/json',
    'X-API-Key': '4ed29acd03b88585355fb1d0be28a9d5',
  },
  timeout: 30_000,
});

// ── Types matching backend schemas ─────────────────────────────────────────────

export interface DashboardMetrics {
  total_processed: number;
  pending_review: number;
  critical_saes: number;
  auto_approved: number;
  throughput: Array<{ name: string; submissions: number }>;
  priority_breakdown: Array<{ name: string; critical: number; processed: number }>;
}

export interface SubmissionSummary {
  id: string;
  applicant: string;
  type: string;
  received: string;
  status: 'processed' | 'review_required' | 'blocked' | string;
  priority: 'critical' | 'high' | 'medium' | 'low' | 'informational';
  score: number;
}

export interface SubmissionQueueResponse {
  items: SubmissionSummary[];
  total: number;
  page: number;
  size: number;
}

export interface ComplianceFinding {
  rule_id: string;
  framework: string;
  severity: 'info' | 'warning' | 'critical';
  message: string;
  evidence: Record<string, string>;
}

export interface XAIRecord {
  module: string;
  model_id: string;
  model_version: string;
  confidence: number;
  summary: string;
  rationale: string[];
  created_at: string;
}

export interface SubmissionDetail {
  id: string;
  submission_type: string;
  filename: string;
  status: string;
  checksum_sha256: string;
  portal_source: string;
  received_at: string;
  file_size_bytes: number;

  // AI analysis
  executive_summary: string;
  key_findings: string[];
  risks: string[];
  missing_information: string[];
  recommended_next_steps: string[];
  summary_model_id: string;
  summary_confidence: number;

  // Classification
  classification: Record<string, any>;
  completeness_score: number;
  missing_required_fields: string[];
  duplicate_candidates: Array<{ doc_id: string; similarity: number }>;

  // Anonymisation
  pii_entities_removed: number;
  anonymisation_method: string;

  // Compliance & XAI
  compliance_findings: Array<{
    findings: ComplianceFinding[];
    decision: string;
    human_review_required: boolean;
    blocked: boolean;
    confidence: number;
    assessed_at: string;
  }>;
  xai_log: XAIRecord[];
}

export interface ReviewerAction {
  action: 'approve' | 'reject';
  notes?: string;
  override_reason?: string;
}

// ── Ingestion types ──────────────────────────────────────────────────────────

export interface IngestResponse {
  doc_id: string;
  status: string;
  message: string;
  checksum_sha256: string;
  raw_storage_path: string;
  created_at: string;
}

// ── API functions ──────────────────────────────────────────────────────────────

export const ingestSubmission = async (
  file: File,
  submissionType: 'drug' | 'medical_device' | 'clinical_trial' | 'sae',
  portalSource = 'manual',
  externalId?: string
): Promise<IngestResponse> => {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('submission_type', submissionType);
  formData.append('portal_source', portalSource);
  if (externalId) {
    formData.append('external_id', externalId);
  }

  const { data } = await axios.post<IngestResponse>(
    `${BASE_URL}/api/v1/ingest/submission`,
    formData,
    {
      headers: {
        'Content-Type': 'multipart/form-data',
        'X-API-Key': '4ed29acd03b88585355fb1d0be28a9d5',
      },
    }
  );
  return data;
};

export const fetchDashboardMetrics = async (): Promise<DashboardMetrics> => {
  const { data } = await api.get<DashboardMetrics>('/dashboard/metrics');
  return data;
};

export const fetchSubmissions = async (
  page = 1,
  size = 20,
  status?: string
): Promise<SubmissionQueueResponse> => {
  const params: Record<string, any> = { page, size };
  if (status) params.status = status;
  const { data } = await api.get<SubmissionQueueResponse>('/submissions', { params });
  return data;
};

export const fetchSubmissionDetail = async (docId: string): Promise<SubmissionDetail> => {
  const { data } = await api.get<SubmissionDetail>(`/submissions/${docId}`);
  return data;
};

export const submitReviewerAction = async (
  docId: string,
  action: ReviewerAction
): Promise<{ status: string; message: string }> => {
  const { data } = await api.post(`/submissions/${docId}/review`, action);
  return data;
};

export const downloadPdfReport = (docId: string): string => {
  return `${BASE_URL}/api/v1/output/submissions/${docId}/report.pdf`;
};
