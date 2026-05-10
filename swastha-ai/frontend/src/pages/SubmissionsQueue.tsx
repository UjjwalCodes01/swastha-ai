import { useState, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { motion, AnimatePresence } from 'framer-motion';
import { Search, FileText, CheckCircle2, Clock, AlertCircle, Loader2, AlertTriangle, Upload, X, Check } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { fetchSubmissions, ingestSubmission } from '../lib/api';

const statusOptions = [
  { label: 'All Statuses', value: '' },
  { label: 'Pending Review', value: 'ingested' },
  { label: 'Processed', value: 'processed' },
  { label: 'Reviewed', value: 'reviewed' },
  { label: 'Rejected', value: 'rejected' },
];

const submissionTypes = [
  { label: 'Drug / Pharma', value: 'drug' },
  { label: 'Medical Device', value: 'medical_device' },
  { label: 'Clinical Trial', value: 'clinical_trial' },
  { label: 'SAE Report', value: 'sae' },
];

const SubmissionsQueue = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
  const pageSize = 20;

  // Upload Form State
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [selectedType, setSelectedType] = useState<'drug' | 'medical_device' | 'clinical_trial' | 'sae'>('drug');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { data, isLoading, isError, isFetching } = useQuery({
    queryKey: ['submissions', page, statusFilter],
    queryFn: () => fetchSubmissions(page, pageSize, statusFilter || undefined),
    placeholderData: (prev) => prev, 
  });

  const uploadMutation = useMutation({
    mutationFn: () => {
      if (!selectedFile) throw new Error('No file selected');
      return ingestSubmission(selectedFile, selectedType);
    },
    onSuccess: (data) => {
      setIsUploadModalOpen(false);
      setSelectedFile(null);
      queryClient.invalidateQueries({ queryKey: ['submissions'] });
      // Navigate to the new document after a short delay
      setTimeout(() => navigate(`/submissions/${data.doc_id}`), 500);
    },
  });

  const totalPages = data ? Math.ceil(data.total / pageSize) : 1;

  // Client-side search filter (for ID/applicant name)
  const filteredItems = (data?.items ?? []).filter(
    (sub) =>
      !search ||
      sub.id.toLowerCase().includes(search.toLowerCase()) ||
      sub.applicant.toLowerCase().includes(search.toLowerCase())
  );

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'processed':
      case 'reviewed':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium bg-green-500/10 text-green-600 dark:text-green-400">
            <CheckCircle2 size={14} /> {status === 'reviewed' ? 'Reviewed' : 'Auto-Approved'}
          </span>
        );
      case 'review_required':
      case 'ingested':
      case 'preprocessing':
      case 'queued':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium bg-orange-500/10 text-orange-600 dark:text-orange-400">
            <Clock size={14} /> Needs Review
          </span>
        );
      case 'blocked':
      case 'failed':
      case 'rejected':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium bg-red-500/10 text-red-600 dark:text-red-400">
            <AlertCircle size={14} /> {status === 'rejected' ? 'Rejected' : 'Blocked'}
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium bg-muted text-muted-foreground">
            {status}
          </span>
        );
    }
  };

  const getPriorityBadge = (priority: string) => {
    switch (priority) {
      case 'critical': return <span className="px-2 py-1 rounded text-xs font-bold bg-red-500 text-white shadow-sm">CRITICAL</span>;
      case 'high': return <span className="px-2 py-1 rounded text-xs font-bold bg-orange-500 text-white shadow-sm">HIGH</span>;
      case 'medium': return <span className="px-2 py-1 rounded text-xs font-bold bg-blue-500 text-white shadow-sm">MED</span>;
      case 'low': return <span className="px-2 py-1 rounded text-xs font-bold bg-slate-500 text-white shadow-sm">LOW</span>;
      default: return <span className="px-2 py-1 rounded text-xs font-bold bg-muted text-muted-foreground">{priority}</span>;
    }
  };

  return (
    <div className="space-y-6 pb-10">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight text-foreground">Submissions Queue</h1>
          <p className="text-muted-foreground mt-1 text-base">
            Manage and review AI-processed regulatory submissions in real-time.
            {data && <span className="ml-3 text-xs bg-primary/10 text-primary border border-primary/20 px-2.5 py-1 rounded-full font-bold uppercase tracking-wider">{data.total} total</span>}
          </p>
        </div>
        <button
          onClick={() => setIsUploadModalOpen(true)}
          className="flex items-center justify-center gap-2 px-6 py-3 bg-primary text-primary-foreground rounded-2xl font-bold text-sm hover:bg-primary/90 transition-all shadow-xl shadow-primary/25 active:scale-95 border border-primary"
        >
          <Upload size={20} strokeWidth={2.5} />
          Upload Document
        </button>
      </div>

      <AnimatePresence>
        {isUploadModalOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => !uploadMutation.isPending && setIsUploadModalOpen(false)}
              className="absolute inset-0 bg-background/80 backdrop-blur-sm"
            />
            <motion.div
              initial={{ opacity: 0, scale: 0.95, y: 20 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 20 }}
              className="relative w-full max-w-lg bg-card border border-border rounded-2xl shadow-2xl overflow-hidden"
            >
              <div className="p-6 border-b border-border flex items-center justify-between bg-muted/20">
                <h2 className="text-xl font-bold text-foreground flex items-center gap-2">
                  <Upload className="text-primary" size={20} />
                  Ingest New Document
                </h2>
                <button
                  onClick={() => setIsUploadModalOpen(false)}
                  disabled={uploadMutation.isPending}
                  className="p-2 hover:bg-muted rounded-lg text-muted-foreground transition-colors"
                >
                  <X size={20} />
                </button>
              </div>

              <div className="p-8 space-y-6">
                <div className="space-y-2">
                  <label className="text-sm font-semibold text-foreground">Submission Type</label>
                  <div className="grid grid-cols-2 gap-2">
                    {submissionTypes.map((type) => (
                      <button
                        key={type.value}
                        onClick={() => setSelectedType(type.value as any)}
                        className={`px-4 py-3 rounded-xl text-sm font-medium border transition-all ${
                          selectedType === type.value
                            ? 'bg-primary/10 border-primary text-primary shadow-sm'
                            : 'bg-muted/40 border-transparent text-muted-foreground hover:bg-muted'
                        }`}
                      >
                        {type.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-semibold text-foreground">Document File</label>
                  <div
                    onClick={() => fileInputRef.current?.click()}
                    className={`group cursor-pointer border-2 border-dashed rounded-2xl p-10 text-center transition-all ${
                      selectedFile ? 'border-primary bg-primary/5' : 'border-border hover:border-primary/50 hover:bg-muted/30'
                    }`}
                  >
                    <input
                      type="file"
                      ref={fileInputRef}
                      onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
                      className="hidden"
                      accept=".pdf,.docx,.xml,.csv,.json,.zip"
                    />
                    {selectedFile ? (
                      <div className="space-y-2">
                        <div className="mx-auto w-12 h-12 rounded-full bg-primary/20 flex items-center justify-center text-primary">
                          <Check size={24} />
                        </div>
                        <p className="text-sm font-semibold text-foreground truncate max-w-xs mx-auto">{selectedFile.name}</p>
                        <p className="text-xs text-muted-foreground">{(selectedFile.size / 1024).toFixed(1)} KB</p>
                        <button 
                          onClick={(e) => { e.stopPropagation(); setSelectedFile(null); }}
                          className="text-xs text-red-500 font-medium hover:underline mt-2"
                        >
                          Remove file
                        </button>
                      </div>
                    ) : (
                      <div className="space-y-2">
                        <div className="mx-auto w-12 h-12 rounded-full bg-muted flex items-center justify-center text-muted-foreground group-hover:scale-110 transition-transform">
                          <Upload size={24} />
                        </div>
                        <p className="text-sm font-semibold text-foreground">Click to select or drag and drop</p>
                        <p className="text-xs text-muted-foreground">Supported: PDF, DOCX, XML, CSV, JSON, ZIP</p>
                      </div>
                    )}
                  </div>
                </div>

                {uploadMutation.isError && (
                  <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-red-600 text-xs flex items-start gap-3">
                    <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                    <span>Upload failed: {(uploadMutation.error as any)?.response?.data?.detail || 'Network error'}</span>
                  </div>
                )}
              </div>

              <div className="p-6 bg-muted/20 border-t border-border flex gap-3">
                <button
                  disabled={uploadMutation.isPending}
                  onClick={() => setIsUploadModalOpen(false)}
                  className="flex-1 px-4 py-2.5 border border-border rounded-xl text-sm font-semibold text-foreground hover:bg-muted transition-all disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  disabled={!selectedFile || uploadMutation.isPending}
                  onClick={() => uploadMutation.mutate()}
                  className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-primary text-primary-foreground rounded-xl text-sm font-semibold hover:bg-primary/90 transition-all shadow-lg shadow-primary/20 disabled:opacity-50"
                >
                  {uploadMutation.isPending ? (
                    <>
                      <Loader2 size={18} className="animate-spin" />
                      Ingesting...
                    </>
                  ) : (
                    'Start Analysis'
                  )}
                </button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {isError && (

        <div className="bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400 rounded-xl p-4 text-sm flex items-center gap-2">
          <AlertTriangle size={16} />
          <span>Failed to load submissions. Is the backend running?</span>
        </div>
      )}

      <div className="bg-card rounded-2xl border border-border shadow-sm flex flex-col overflow-hidden">
        {/* Toolbar */}
        <div className="p-4 border-b border-border flex flex-col sm:flex-row items-center gap-4 justify-between bg-muted/20">
          <div className="flex items-center gap-2 w-full sm:w-auto">
            <div className="relative flex-1 sm:w-80">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search doc ID, applicant..."
                className="w-full pl-9 pr-4 py-2 bg-card border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all shadow-sm"
              />
            </div>
            <select
              value={statusFilter}
              onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
              className="p-2 border border-border rounded-lg bg-card hover:bg-muted text-foreground text-sm shadow-sm transition-colors focus:outline-none focus:ring-2 focus:ring-primary/20"
            >
              {statusOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2 text-sm">
            {isFetching && <Loader2 size={14} className="animate-spin text-muted-foreground" />}
            <span className="text-muted-foreground">
              Showing {((page - 1) * pageSize) + 1}–{Math.min(page * pageSize, data?.total ?? 0)} of {data?.total ?? 0}
            </span>
            <div className="flex gap-1 ml-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1 || isLoading}
                className="px-3 py-1.5 border border-border rounded-md bg-card hover:bg-muted transition-colors disabled:opacity-50"
              >
                Prev
              </button>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages || isLoading}
                className="px-3 py-1.5 border border-border rounded-md bg-card hover:bg-muted transition-colors disabled:opacity-50"
              >
                Next
              </button>
            </div>
          </div>
        </div>

        {/* Table */}
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm whitespace-nowrap">
            <thead className="bg-muted/50 text-muted-foreground border-b border-border">
              <tr>
                <th className="px-6 py-5 font-bold uppercase tracking-wider text-[10px]">Document ID</th>
                <th className="px-6 py-5 font-bold uppercase tracking-wider text-[10px]">Applicant</th>
                <th className="px-6 py-5 font-bold uppercase tracking-wider text-[10px]">Type</th>
                <th className="px-6 py-5 font-bold uppercase tracking-wider text-[10px]">Priority</th>
                <th className="px-6 py-5 font-bold uppercase tracking-wider text-[10px]">AI Status</th>
                <th className="px-6 py-5 font-bold uppercase tracking-wider text-[10px]">Received</th>
                <th className="px-6 py-5 font-bold uppercase tracking-wider text-[10px] text-right">AI Score</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {isLoading
                ? Array.from({ length: 5 }).map((_, i) => (
                    <tr key={i} className="animate-pulse">
                      {Array.from({ length: 7 }).map((_, j) => (
                        <td key={j} className="px-6 py-5">
                          <div className="h-4 bg-muted rounded w-3/4" />
                        </td>
                      ))}
                    </tr>
                  ))
                : filteredItems.map((sub, i) => (
                    <motion.tr
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: i * 0.03 }}
                      key={sub.id}
                      className="hover:bg-primary/[0.02] transition-colors cursor-pointer group"
                      onClick={() => navigate(`/submissions/${sub.id}`)}
                    >
                      <td className="px-6 py-5">
                        <div className="flex items-center gap-3">
                          <div className="p-2.5 rounded-xl bg-primary/5 text-primary group-hover:bg-primary group-hover:text-primary-foreground transition-all duration-300 shadow-sm border border-primary/10">
                            <FileText size={18} />
                          </div>
                          <span className="font-bold text-foreground text-sm tracking-tight">{sub.id}</span>
                        </div>
                      </td>
                      <td className="px-6 py-5 text-foreground font-semibold text-sm">{sub.applicant}</td>
                      <td className="px-6 py-5 text-muted-foreground capitalize font-medium text-xs tracking-tight">{sub.type.replace(/_/g, ' ')}</td>
                      <td className="px-6 py-5">{getPriorityBadge(sub.priority)}</td>
                      <td className="px-6 py-5">{getStatusBadge(sub.status)}</td>
                      <td className="px-6 py-5 text-muted-foreground font-medium text-xs">{sub.received}</td>
                      <td className="px-6 py-5 text-right">
                        <div className="inline-flex items-center justify-end bg-muted/30 px-2 py-1 rounded-lg border border-border/50 shadow-inner">
                          <span className={`font-mono text-xs font-black ${sub.score >= 0.85 ? 'text-green-600' : sub.score >= 0.6 ? 'text-orange-500' : 'text-red-500'}`}>
                            {(sub.score * 100).toFixed(0)}%
                          </span>
                        </div>
                      </td>
                    </motion.tr>
                  ))}

              {!isLoading && filteredItems.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-6 py-20 text-center">
                    <div className="max-w-xs mx-auto">
                      <div className="w-16 h-16 bg-muted rounded-full flex items-center justify-center mx-auto mb-4 opacity-50 shadow-inner">
                        <FileText size={32} className="text-muted-foreground" />
                      </div>
                      <h3 className="text-lg font-bold text-foreground mb-1">No submissions found</h3>
                      <p className="text-sm text-muted-foreground mb-6">
                        {search ? "No matches for your current search filter." : "Get started by uploading your first document for AI analysis."}
                      </p>
                      {search && (
                        <button 
                          onClick={() => setSearch('')}
                          className="text-primary font-bold text-xs hover:underline uppercase tracking-widest"
                        >
                          Clear Search
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default SubmissionsQueue;
