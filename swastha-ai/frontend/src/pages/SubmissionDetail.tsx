import { motion } from 'framer-motion';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Check, X, FileText, AlertTriangle, Info, Download, Maximize2 } from 'lucide-react';

const SubmissionDetail = () => {
  const { id } = useParams();
  const navigate = useNavigate();

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
              <h1 className="text-2xl font-bold tracking-tight text-foreground">{id}</h1>
              <span className="px-2.5 py-1 rounded text-xs font-bold bg-orange-500 text-white shadow-sm">HIGH PRIORITY</span>
            </div>
            <p className="text-muted-foreground text-sm mt-1">PharmaCorp Ltd. • Drug Submission • Received 10 mins ago</p>
          </div>
        </div>
        <div className="flex gap-3">
          <button className="px-4 py-2 bg-red-500 text-white rounded-lg text-sm font-medium hover:bg-red-600 transition-colors shadow-sm flex items-center gap-2">
            <X size={16} /> Reject
          </button>
          <button className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors shadow-sm flex items-center gap-2">
            <Check size={16} /> Approve
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Column: AI Analysis */}
        <div className="lg:col-span-2 space-y-6">
          
          {/* Executive Summary */}
          <motion.div 
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="bg-card rounded-2xl border border-border shadow-sm overflow-hidden"
          >
            <div className="p-5 border-b border-border bg-muted/20 flex items-center justify-between">
              <h3 className="font-semibold text-foreground flex items-center gap-2">
                <FileText size={18} className="text-primary" /> AI Executive Summary
              </h3>
              <span className="text-xs font-medium text-muted-foreground bg-card border border-border px-2 py-1 rounded">Claude 3.5 Sonnet</span>
            </div>
            <div className="p-6">
              <p className="text-foreground leading-relaxed text-sm mb-6">
                This submission requests approval for a new indication of Paracetamol 500mg. The provided clinical trial data demonstrates efficacy in treating mild to moderate osteoarthritis pain. However, there are missing required fields in the manufacturing section, and safety data lacks long-term stability testing results.
              </p>
              
              <h4 className="text-sm font-semibold text-foreground mb-3">Key Findings</h4>
              <ul className="space-y-2 mb-6">
                {['Clinical endpoints met for osteoarthritis indication.', 'No severe adverse events reported in Phase III.', 'Manufacturing site inspection certificate is expired.'].map((finding, i) => (
                  <li key={i} className="flex items-start gap-3 text-sm text-muted-foreground">
                    <div className="mt-1 w-1.5 h-1.5 rounded-full bg-primary shrink-0" />
                    <span>{finding}</span>
                  </li>
                ))}
              </ul>

              <h4 className="text-sm font-semibold text-foreground mb-3">Recommended Next Steps</h4>
              <div className="bg-primary/5 border border-primary/20 rounded-lg p-4">
                <p className="text-sm text-primary font-medium">Request applicant clarification for missing mandatory fields: manufacturing_site_cert, long_term_stability_data. Route to Senior Reviewer for final assessment.</p>
              </div>
            </div>
          </motion.div>

          {/* Compliance & Governance */}
          <motion.div 
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
            className="bg-card rounded-2xl border border-border shadow-sm overflow-hidden"
          >
            <div className="p-5 border-b border-border bg-muted/20 flex items-center justify-between">
              <h3 className="font-semibold text-foreground flex items-center gap-2">
                <AlertTriangle size={18} className="text-orange-500" /> Compliance Assessments
              </h3>
              <span className="text-xs font-medium text-orange-600 bg-orange-500/10 px-2 py-1 rounded">2 Flags</span>
            </div>
            <div className="p-0 divide-y divide-border">
              <div className="p-5 flex gap-4">
                <div className="shrink-0 mt-0.5 text-orange-500"><AlertTriangle size={18}/></div>
                <div>
                  <h4 className="text-sm font-semibold text-foreground">ICMR-HIGH-RISK-REVIEW</h4>
                  <p className="text-sm text-muted-foreground mt-1">High-priority AI classification should require reviewer validation. AI confidence: 0.86</p>
                </div>
              </div>
              <div className="p-5 flex gap-4">
                <div className="shrink-0 mt-0.5 text-red-500"><AlertTriangle size={18}/></div>
                <div>
                  <h4 className="text-sm font-semibold text-foreground">DPDP-LINEAGE-MISSING</h4>
                  <p className="text-sm text-muted-foreground mt-1">Anonymised artefact is missing source lineage to the processed document. This is a critical DPDP violation.</p>
                </div>
              </div>
            </div>
          </motion.div>
        </div>

        {/* Right Column: Metadata & Tools */}
        <div className="space-y-6">
          <motion.div 
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.2 }}
            className="bg-card rounded-2xl border border-border shadow-sm p-5"
          >
            <h3 className="font-semibold text-foreground mb-4">Submission Details</h3>
            <div className="space-y-4">
              <div>
                <span className="text-xs text-muted-foreground block mb-1">Applicant</span>
                <span className="text-sm font-medium text-foreground">PharmaCorp Ltd. (ID: PC-8923)</span>
              </div>
              <div>
                <span className="text-xs text-muted-foreground block mb-1">Document Hash (SHA-256)</span>
                <span className="text-xs font-mono text-muted-foreground break-all bg-muted p-2 rounded block">e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855</span>
              </div>
              <div>
                <span className="text-xs text-muted-foreground block mb-1">Extracted Entities (Anonymised)</span>
                <div className="flex flex-wrap gap-2 mt-2">
                  <span className="text-xs font-medium bg-muted text-muted-foreground px-2 py-1 rounded border border-border">&lt;PAN_1_A3F2&gt;</span>
                  <span className="text-xs font-medium bg-muted text-muted-foreground px-2 py-1 rounded border border-border">&lt;PHONE_1_B9C4&gt;</span>
                </div>
              </div>
            </div>
          </motion.div>

          <motion.div 
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3 }}
            className="bg-card rounded-2xl border border-border shadow-sm overflow-hidden flex flex-col"
          >
             <div className="p-4 border-b border-border flex items-center justify-between bg-muted/20">
              <h3 className="font-semibold text-sm text-foreground">Source Document</h3>
              <div className="flex gap-2">
                <button className="p-1.5 hover:bg-muted rounded text-muted-foreground"><Download size={16}/></button>
                <button className="p-1.5 hover:bg-muted rounded text-muted-foreground"><Maximize2 size={16}/></button>
              </div>
            </div>
            <div className="h-64 bg-muted/30 flex items-center justify-center flex-col text-muted-foreground p-6 text-center">
              <Info size={32} className="mb-3 opacity-50" />
              <p className="text-sm">Document preview is available in the Anonymised View only.</p>
              <button className="mt-4 px-4 py-2 border border-border bg-card rounded-lg text-sm font-medium hover:bg-muted transition-colors text-foreground shadow-sm">
                Open Secure Viewer
              </button>
            </div>
          </motion.div>
        </div>
      </div>
    </div>
  );
};

export default SubmissionDetail;
