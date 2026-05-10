import { useQuery } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import { ShieldAlert, AlertTriangle, CheckCircle2, Clock, Loader2, RefreshCw, Ban, Eye } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { fetchSubmissions, type SubmissionSummary } from '../lib/api';

const policyMonitors = [
  { name: 'DPDP Act 2023 — PII Anonymisation', status: 'active' },
  { name: 'ICMR — SAE Classification Rules', status: 'active' },
  { name: 'NDHM — Health Data Interoperability', status: 'active' },
  { name: 'CDSCO Schedule M', status: 'active' },
  { name: 'Drugs & Cosmetics Act', status: 'active' },
];

const Compliance = () => {
  const navigate = useNavigate();

  // Fetch the top 100 submissions and extract flagged ones
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['compliance-alerts'],
    queryFn: () => fetchSubmissions(1, 100),
    refetchInterval: 60_000,
  });

  const blockedItems: SubmissionSummary[] = (data?.items ?? []).filter(
    (s) => s.status === 'blocked' || s.priority === 'critical'
  );
  const reviewRequiredItems: SubmissionSummary[] = (data?.items ?? []).filter(
    (s) => s.status === 'review_required' || s.priority === 'high'
  );
  const allAlertsCount = blockedItems.length + reviewRequiredItems.length;

  const AlertRow = ({ sub, type }: { sub: SubmissionSummary; type: 'blocked' | 'review' }) => (
    <motion.div
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      className={`flex items-center gap-4 p-4 rounded-xl border transition-colors cursor-pointer hover:bg-muted/30 ${
        type === 'blocked'
          ? 'bg-red-500/5 border-red-500/20'
          : 'bg-orange-500/5 border-orange-500/20'
      }`}
      onClick={() => navigate(`/submissions/${sub.id}`)}
    >
      <div className={`shrink-0 w-9 h-9 rounded-lg flex items-center justify-center ${
        type === 'blocked' ? 'bg-red-500/15 text-red-500' : 'bg-orange-500/15 text-orange-500'
      }`}>
        {type === 'blocked' ? <Ban size={18} /> : <AlertTriangle size={18} />}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-foreground font-mono truncate">{sub.id}</span>
          <span className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded ${
            type === 'blocked' ? 'bg-red-500 text-white' : 'bg-orange-500 text-white'
          }`}>
            {type === 'blocked' ? 'BLOCKED' : 'REVIEW REQUIRED'}
          </span>
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">
          {sub.applicant} · {sub.type.replace(/_/g, ' ')} · Received {sub.received}
        </p>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <span className={`text-xs font-mono font-bold ${
          sub.score >= 0.85 ? 'text-green-600' : sub.score >= 0.6 ? 'text-orange-500' : 'text-red-500'
        }`}>
          {(sub.score * 100).toFixed(0)}%
        </span>
        <Eye size={14} className="text-muted-foreground" />
      </div>
    </motion.div>
  );

  return (
    <div className="space-y-6 pb-10">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Compliance Alerts</h1>
          <p className="text-muted-foreground mt-1">
            Real-time regulatory compliance violations and flagged submissions.
            {!isLoading && (
              <span className={`ml-2 text-xs font-bold px-2 py-0.5 rounded-full border ${
                allAlertsCount > 0
                  ? 'bg-red-500/10 text-red-600 border-red-500/20'
                  : 'bg-green-500/10 text-green-600 border-green-500/20'
              }`}>
                {allAlertsCount} alert{allAlertsCount !== 1 ? 's' : ''}
              </span>
            )}
          </p>
        </div>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="flex items-center gap-2 px-4 py-2 bg-card border border-border rounded-lg text-sm font-medium text-foreground hover:bg-muted transition-colors shadow-sm disabled:opacity-50"
        >
          {isFetching ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          Refresh
        </button>
      </div>

      {/* Error state */}
      {isError && (
        <div className="bg-red-500/10 border border-red-500/20 text-red-600 rounded-xl p-4 text-sm flex items-center gap-2">
          <AlertTriangle size={16} />
          <span>Failed to load compliance data. Is the backend running?</span>
        </div>
      )}

      {/* Summary cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0 }}
          className="bg-red-500/5 border border-red-500/20 rounded-2xl p-5 shadow-sm">
          <p className="text-xs font-bold uppercase tracking-widest text-red-500 mb-1">Blocked</p>
          {isLoading
            ? <div className="h-9 w-16 bg-muted animate-pulse rounded-lg" />
            : <p className="text-3xl font-extrabold text-foreground">{blockedItems.length}</p>
          }
          <p className="text-xs text-muted-foreground mt-1">Submissions blocked by compliance engine</p>
        </motion.div>
        <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }}
          className="bg-orange-500/5 border border-orange-500/20 rounded-2xl p-5 shadow-sm">
          <p className="text-xs font-bold uppercase tracking-widest text-orange-500 mb-1">Review Required</p>
          {isLoading
            ? <div className="h-9 w-16 bg-muted animate-pulse rounded-lg" />
            : <p className="text-3xl font-extrabold text-foreground">{reviewRequiredItems.length}</p>
          }
          <p className="text-xs text-muted-foreground mt-1">Human review flagged by AI or compliance rules</p>
        </motion.div>
        <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}
          className="bg-green-500/5 border border-green-500/20 rounded-2xl p-5 shadow-sm">
          <p className="text-xs font-bold uppercase tracking-widest text-green-600 mb-1">Auto-Approved</p>
          {isLoading
            ? <div className="h-9 w-16 bg-muted animate-pulse rounded-lg" />
            : <p className="text-3xl font-extrabold text-foreground">
                {(data?.total ?? 0) - blockedItems.length - reviewRequiredItems.length}
              </p>
          }
          <p className="text-xs text-muted-foreground mt-1">Passed all compliance checks automatically</p>
        </motion.div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Alert feed */}
        <div className="lg:col-span-2 space-y-4">
          {/* Blocked */}
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }}
            className="bg-card rounded-2xl border border-border shadow-sm overflow-hidden">
            <div className="p-4 border-b border-border bg-red-500/5 flex items-center gap-2">
              <Ban size={16} className="text-red-500" />
              <h3 className="font-bold text-foreground text-sm">Blocked Submissions</h3>
              <span className="ml-auto text-xs bg-red-500 text-white font-bold px-2 py-0.5 rounded-full">
                {isLoading ? '…' : blockedItems.length}
              </span>
            </div>
            <div className="p-4 space-y-3">
              {isLoading
                ? Array.from({ length: 2 }).map((_, i) => (
                    <div key={i} className="h-16 bg-muted/40 rounded-xl animate-pulse" />
                  ))
                : blockedItems.length === 0
                  ? (
                    <div className="text-center py-8 text-muted-foreground">
                      <CheckCircle2 size={24} className="mx-auto mb-2 text-green-500" />
                      <p className="text-sm">No blocked submissions — all clear.</p>
                    </div>
                  )
                  : blockedItems.map((sub) => (
                      <AlertRow key={sub.id} sub={sub} type="blocked" />
                    ))
              }
            </div>
          </motion.div>

          {/* Review Required */}
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}
            className="bg-card rounded-2xl border border-border shadow-sm overflow-hidden">
            <div className="p-4 border-b border-border bg-orange-500/5 flex items-center gap-2">
              <Clock size={16} className="text-orange-500" />
              <h3 className="font-bold text-foreground text-sm">Pending Human Review</h3>
              <span className="ml-auto text-xs bg-orange-500 text-white font-bold px-2 py-0.5 rounded-full">
                {isLoading ? '…' : reviewRequiredItems.length}
              </span>
            </div>
            <div className="p-4 space-y-3">
              {isLoading
                ? Array.from({ length: 3 }).map((_, i) => (
                    <div key={i} className="h-16 bg-muted/40 rounded-xl animate-pulse" />
                  ))
                : reviewRequiredItems.length === 0
                  ? (
                    <div className="text-center py-8 text-muted-foreground">
                      <CheckCircle2 size={24} className="mx-auto mb-2 text-green-500" />
                      <p className="text-sm">No submissions awaiting human review.</p>
                    </div>
                  )
                  : reviewRequiredItems.map((sub) => (
                      <AlertRow key={sub.id} sub={sub} type="review" />
                    ))
              }
            </div>
          </motion.div>
        </div>

        {/* Policy monitors sidebar */}
        <div className="space-y-4">
          <motion.div initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.25 }}
            className="bg-card rounded-2xl border border-border shadow-sm p-5">
            <div className="flex items-center gap-2 mb-4">
              <ShieldAlert size={16} className="text-primary" />
              <h3 className="font-bold text-foreground text-sm">Active Policy Monitors</h3>
            </div>
            <ul className="space-y-3">
              {policyMonitors.map((pm) => (
                <li key={pm.name} className="flex justify-between items-center text-sm">
                  <span className="text-muted-foreground text-xs leading-tight">{pm.name}</span>
                  <span className="text-green-500 font-bold text-xs ml-2 shrink-0">● Active</span>
                </li>
              ))}
            </ul>
          </motion.div>

          <motion.div initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.3 }}
            className="bg-primary/5 border border-primary/20 rounded-2xl p-5">
            <h4 className="font-bold text-foreground text-sm mb-2">About Compliance Engine</h4>
            <p className="text-xs text-muted-foreground leading-relaxed">
              SwasthaAI's Layer 4 compliance engine runs deterministic rule checks against DPDP Act 2023, ICMR guidelines, and NDHM standards after every AI Core run. Results are stored in PostgreSQL and surfaced here in real-time.
            </p>
          </motion.div>
        </div>
      </div>
    </div>
  );
};

export default Compliance;
