import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeft, Check, X, FileText, AlertTriangle,
  Download, Loader2, ShieldAlert, Brain, Cpu, Eye,
} from 'lucide-react';
import { fetchSubmissionDetail, submitReviewerAction, downloadPdfReport, type SubmissionDetail } from '../lib/api';

const SeverityDot = ({ severity }: { severity: string }) => {
  const color = severity === 'critical' ? 'bg-red-500' : severity === 'warning' ? 'bg-orange-500' : 'bg-blue-400';
  return <span className={`inline-block w-2 h-2 rounded-full ${color} shrink-0 mt-1.5`} />;
};

const SubmissionDetailPage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [reviewNotes, setReviewNotes] = useState('');
  const [overrideReason, setOverrideReason] = useState('');
  const [activeTab, setActiveTab] = useState<'summary' | 'compliance' | 'xai'>('summary');

  const { data, isLoading, isError } = useQuery<SubmissionDetail>({
    queryKey: ['submission-detail', id],
    queryFn: () => fetchSubmissionDetail(id!),
    enabled: !!id,
  });

  const mutation = useMutation({
    mutationFn: (action: 'approve' | 'reject') =>
      submitReviewerAction(id!, { action, notes: reviewNotes, override_reason: overrideReason || undefined }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['submission-detail', id] });
      queryClient.invalidateQueries({ queryKey: ['submissions'] });
      queryClient.invalidateQueries({ queryKey: ['dashboard-metrics'] });
    },
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-muted-foreground">
        <Loader2 size={32} className="animate-spin mr-3" />
        <span>Loading AI analysis...</span>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="bg-red-500/10 border border-red-500/20 rounded-2xl p-8 text-center">
        <AlertTriangle size={32} className="mx-auto mb-3 text-red-500" />
        <p className="font-medium text-foreground">Submission not found or failed to load.</p>
        <button onClick={() => navigate('/submissions')} className="mt-4 px-4 py-2 border border-border rounded-lg text-sm hover:bg-muted transition-colors">
          ← Back to Queue
        </button>
      </div>
    );
  }

  const allCompliance = data.compliance_findings.flatMap((c) => c.findings ?? []);
  const isBlocked = data.compliance_findings.some((c) => c.blocked);
  const requiresReview = data.compliance_findings.some((c) => c.human_review_required);

  const priorityFromClassification = (() => {
    const priority = data.classification?.priority ?? 'informational';
    const colors: Record<string, string> = {
      critical: 'bg-red-500',
      high: 'bg-orange-500',
      medium: 'bg-blue-500',
      low: 'bg-slate-500',
    };
    return { label: priority.toUpperCase(), color: colors[priority] ?? 'bg-muted' };
  })();

  return (
    <div className="space-y-6 pb-20">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/submissions')}
            className="p-2 rounded-lg hover:bg-muted text-muted-foreground transition-colors"
          >
            <ArrowLeft size={20} />
          </button>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold tracking-tight text-foreground">{data.id}</h1>
              <span className={`px-2.5 py-1 rounded text-xs font-bold ${priorityFromClassification.color} text-white shadow-sm`}>
                {priorityFromClassification.label}
              </span>
              {isBlocked && (
                <span className="px-2.5 py-1 rounded text-xs font-bold bg-red-700 text-white shadow-sm flex items-center gap-1">
                  <ShieldAlert size={12} /> BLOCKED
                </span>
              )}
            </div>
            <p className="text-muted-foreground text-sm mt-1">
              {data.filename} • {data.submission_type.replace('_', ' ').replace(/\b\w/g, (c) => c.toUpperCase())} • {data.portal_source} • {data.received_at.split('T')[0]}
            </p>
          </div>
        </div>
        <div className="flex gap-3">
          <a
            href={downloadPdfReport(data.id)}
            target="_blank"
            rel="noopener noreferrer"
            className="px-4 py-2 bg-card border border-border rounded-lg text-sm font-medium text-foreground hover:bg-muted transition-colors shadow-sm flex items-center gap-2"
          >
            <Download size={16} /> Download PDF
          </a>
          <button
            onClick={() => mutation.mutate('reject')}
            disabled={mutation.isPending}
            className="px-4 py-2 bg-red-500 text-white rounded-lg text-sm font-medium hover:bg-red-600 transition-colors shadow-sm flex items-center gap-2 disabled:opacity-50"
          >
            {mutation.isPending ? <Loader2 size={16} className="animate-spin" /> : <X size={16} />} Reject
          </button>
          <button
            onClick={() => mutation.mutate('approve')}
            disabled={mutation.isPending}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors shadow-sm flex items-center gap-2 disabled:opacity-50"
          >
            {mutation.isPending ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />} Approve
          </button>
        </div>
      </div>

      {mutation.isSuccess && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="bg-green-500/10 border border-green-500/20 text-green-600 rounded-xl p-4 text-sm flex items-center gap-2"
        >
          <Check size={16} /> {(mutation.data as any)?.message}
        </motion.div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left column — AI analysis tabs */}
        <div className="lg:col-span-2 space-y-6">
          {/* Tab bar */}
          <div className="flex gap-1 bg-muted/40 p-1 rounded-xl">
            {[
              { key: 'summary', label: 'AI Summary', icon: Brain },
              { key: 'compliance', label: `Compliance (${allCompliance.length})`, icon: ShieldAlert },
              { key: 'xai', label: `XAI Log (${data.xai_log.length})`, icon: Cpu },
            ].map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                onClick={() => setActiveTab(key as any)}
                className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-all ${
                  activeTab === key ? 'bg-card shadow-sm text-foreground' : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                <Icon size={16} /> {label}
              </button>
            ))}
          </div>

          {/* Summary tab */}
          {activeTab === 'summary' && (
            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="bg-card rounded-2xl border border-border shadow-sm overflow-hidden">
              <div className="p-5 border-b border-border bg-muted/20 flex items-center justify-between">
                <h3 className="font-semibold text-foreground flex items-center gap-2">
                  <FileText size={18} className="text-primary" /> AI Executive Summary
                </h3>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground bg-card border border-border px-2 py-1 rounded">
                    {data.summary_model_id}
                  </span>
                  <span className="text-xs font-medium text-primary bg-primary/10 px-2 py-1 rounded">
                    {(data.summary_confidence * 100).toFixed(0)}% conf.
                  </span>
                </div>
              </div>
              <div className="p-6 space-y-5">
                <p className="text-foreground leading-relaxed text-sm">
                  {data.executive_summary || 'No summary available — AI processing may still be in progress.'}
                </p>

                {data.key_findings.length > 0 && (
                  <>
                    <h4 className="text-sm font-semibold text-foreground">Key Findings</h4>
                    <ul className="space-y-2">
                      {data.key_findings.map((finding, i) => (
                        <li key={i} className="flex items-start gap-3 text-sm text-muted-foreground">
                          <div className="mt-1 w-1.5 h-1.5 rounded-full bg-primary shrink-0" />
                          <span>{finding}</span>
                        </li>
                      ))}
                    </ul>
                  </>
                )}

                {data.risks.length > 0 && (
                  <>
                    <h4 className="text-sm font-semibold text-foreground">Identified Risks</h4>
                    <ul className="space-y-2">
                      {data.risks.map((risk, i) => (
                        <li key={i} className="flex items-start gap-3 text-sm text-orange-600 dark:text-orange-400">
                          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                          <span>{risk}</span>
                        </li>
                      ))}
                    </ul>
                  </>
                )}

                {data.missing_information.length > 0 && (
                  <div className="bg-orange-500/5 border border-orange-500/20 rounded-lg p-4">
                    <h4 className="text-sm font-semibold text-orange-600 dark:text-orange-400 mb-2">Missing Information</h4>
                    <ul className="space-y-1 text-sm text-muted-foreground">
                      {data.missing_information.map((m, i) => (
                        <li key={i}>• {m.replace(/_/g, ' ')}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {data.recommended_next_steps.length > 0 && (
                  <div className="bg-primary/5 border border-primary/20 rounded-lg p-4">
                    <h4 className="text-sm font-semibold text-primary mb-2">Recommended Next Steps</h4>
                    <ul className="space-y-1 text-sm text-primary/80">
                      {data.recommended_next_steps.map((s, i) => (
                        <li key={i}>→ {s}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </motion.div>
          )}

          {/* Compliance tab */}
          {activeTab === 'compliance' && (
            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="bg-card rounded-2xl border border-border shadow-sm overflow-hidden">
              <div className="p-5 border-b border-border bg-muted/20 flex items-center justify-between">
                <h3 className="font-semibold text-foreground flex items-center gap-2">
                  <ShieldAlert size={18} className="text-orange-500" /> Compliance Assessments
                </h3>
                {allCompliance.length > 0 && (
                  <span className="text-xs font-medium text-orange-600 bg-orange-500/10 px-2 py-1 rounded">
                    {allCompliance.length} finding{allCompliance.length !== 1 ? 's' : ''}
                  </span>
                )}
              </div>
              {allCompliance.length === 0 ? (
                <div className="p-8 text-center text-muted-foreground">
                  <Check size={24} className="mx-auto mb-2 text-green-500" />
                  <p className="text-sm">All compliance checks passed.</p>
                </div>
              ) : (
                <div className="divide-y divide-border">
                  {allCompliance.map((finding: any, i: number) => (
                    <div key={i} className="p-5 flex gap-4">
                      <SeverityDot severity={finding.severity} />
                      <div>
                        <h4 className="text-sm font-semibold text-foreground">{finding.rule_id}</h4>
                        <p className="text-sm text-muted-foreground mt-1">{finding.message}</p>
                        <p className="text-xs text-muted-foreground mt-1">Framework: {finding.framework}</p>
                        {Object.keys(finding.evidence ?? {}).length > 0 && (
                          <p className="text-xs font-mono text-muted-foreground mt-1 bg-muted p-1.5 rounded">
                            {JSON.stringify(finding.evidence)}
                          </p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </motion.div>
          )}

          {/* XAI tab */}
          {activeTab === 'xai' && (
            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="bg-card rounded-2xl border border-border shadow-sm overflow-hidden">
              <div className="p-5 border-b border-border bg-muted/20">
                <h3 className="font-semibold text-foreground flex items-center gap-2">
                  <Cpu size={18} className="text-primary" /> XAI Decision Log
                </h3>
                <p className="text-xs text-muted-foreground mt-1">Every AI decision with model, confidence, and rationale.</p>
              </div>
              {data.xai_log.length === 0 ? (
                <div className="p-8 text-center text-muted-foreground text-sm">No XAI records yet.</div>
              ) : (
                <div className="divide-y divide-border">
                  {data.xai_log.map((xai, i) => (
                    <div key={i} className="p-5 space-y-1">
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-semibold text-foreground capitalize">{xai.module}</span>
                        <span className="text-xs text-muted-foreground">{xai.created_at.split('T')[0]}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-xs bg-muted border border-border px-2 py-0.5 rounded font-mono">{xai.model_id}</span>
                        <span className="text-xs text-muted-foreground">v{xai.model_version}</span>
                        <span className={`text-xs font-medium px-2 py-0.5 rounded ${xai.confidence >= 0.8 ? 'bg-green-500/10 text-green-600' : 'bg-orange-500/10 text-orange-600'}`}>
                          {(xai.confidence * 100).toFixed(0)}% conf.
                        </span>
                      </div>
                      <p className="text-sm text-muted-foreground">{xai.summary}</p>
                      {xai.rationale.length > 0 && (
                        <ul className="text-xs text-muted-foreground space-y-0.5 mt-1 pl-3">
                          {xai.rationale.map((r, j) => <li key={j}>• {r}</li>)}
                        </ul>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </motion.div>
          )}

          {/* Reviewer notes */}
          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="bg-card rounded-2xl border border-border shadow-sm p-5">
            <h3 className="font-semibold text-foreground mb-4 flex items-center gap-2">
              <Eye size={16} className="text-primary" /> Reviewer Notes
            </h3>
            <textarea
              value={reviewNotes}
              onChange={(e) => setReviewNotes(e.target.value)}
              rows={3}
              placeholder="Add your review notes here (optional)..."
              className="w-full px-3 py-2 bg-muted/40 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 resize-none"
            />
            {(requiresReview || isBlocked) && (
              <>
                <p className="text-xs text-orange-600 mt-2 mb-1">Override reason required for flagged submissions:</p>
                <input
                  type="text"
                  value={overrideReason}
                  onChange={(e) => setOverrideReason(e.target.value)}
                  placeholder="State clinical/regulatory justification for your decision..."
                  className="w-full px-3 py-2 bg-muted/40 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </>
            )}
          </motion.div>
        </div>

        {/* Right column — metadata */}
        <div className="space-y-6">
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }} className="bg-card rounded-2xl border border-border shadow-sm p-5">
            <h3 className="font-semibold text-foreground mb-4">Submission Details</h3>
            <div className="space-y-4">
              {[
                { label: 'Document ID', value: data.id, mono: true },
                { label: 'Type', value: data.submission_type.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) },
                { label: 'Status', value: data.status },
                { label: 'Portal', value: data.portal_source },
                { label: 'File Size', value: `${(data.file_size_bytes / 1024).toFixed(1)} KB` },
                { label: 'Completeness', value: `${(data.completeness_score * 100).toFixed(0)}%` },
                { label: 'PII Entities Removed', value: String(data.pii_entities_removed) },
                { label: 'Anonymisation Method', value: data.anonymisation_method || '—' },
              ].map(({ label, value, mono }) => (
                <div key={label}>
                  <span className="text-xs text-muted-foreground block mb-0.5">{label}</span>
                  <span className={`text-sm font-medium text-foreground ${mono ? 'font-mono text-xs' : ''}`}>{value}</span>
                </div>
              ))}
              <div>
                <span className="text-xs text-muted-foreground block mb-1">SHA-256</span>
                <span className="text-xs font-mono text-muted-foreground break-all bg-muted p-2 rounded block">{data.checksum_sha256}</span>
              </div>
            </div>
          </motion.div>

          {/* Duplicate candidates */}
          {data.duplicate_candidates.length > 0 && (
            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }} className="bg-orange-500/5 border border-orange-500/20 rounded-2xl p-5">
              <h3 className="font-semibold text-orange-600 dark:text-orange-400 mb-3 flex items-center gap-2">
                <AlertTriangle size={16} /> Potential Duplicates
              </h3>
              {data.duplicate_candidates.map((dup: any, i: number) => (
                <div key={i} className="flex justify-between text-sm text-muted-foreground mb-1">
                  <button
                    onClick={() => navigate(`/submissions/${dup.doc_id}`)}
                    className="font-mono text-primary hover:underline"
                  >
                    {dup.doc_id}
                  </button>
                  <span>{(dup.similarity * 100).toFixed(1)}% similar</span>
                </div>
              ))}
            </motion.div>
          )}

          {/* Classification card */}
          {data.classification && Object.keys(data.classification).length > 0 && (
            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }} className="bg-card rounded-2xl border border-border shadow-sm p-5">
              <h3 className="font-semibold text-foreground mb-3">AI Classification</h3>
              <div className="space-y-2 text-sm">
                {[
                  ['Category', data.classification.category],
                  ['Priority', data.classification.priority],
                  ['Risk Score', `${((data.classification.risk_score ?? 0) * 100).toFixed(0)}%`],
                  ['SAE Severity', data.classification.sae_severity ?? '—'],
                  ['Requires Review', data.classification.requires_review ? 'Yes' : 'No'],
                ].map(([k, v]) => (
                  <div key={k} className="flex justify-between">
                    <span className="text-muted-foreground">{k}</span>
                    <span className="font-medium text-foreground">{v}</span>
                  </div>
                ))}
                {data.classification.labels?.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-2 pt-2 border-t border-border">
                    {data.classification.labels.map((label: string) => (
                      <span key={label} className="text-xs bg-muted border border-border px-1.5 py-0.5 rounded font-mono">
                        {label}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </motion.div>
          )}
        </div>
      </div>
    </div>
  );
};

export default SubmissionDetailPage;
