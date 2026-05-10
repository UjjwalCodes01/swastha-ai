import { motion } from 'framer-motion';
import { Search, Filter, MoreVertical, FileText, CheckCircle2, Clock, AlertCircle } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

const mockSubmissions = [
  { id: 'DOC-8A7B9C', applicant: 'PharmaCorp Ltd.', type: 'drug', received: '10 mins ago', status: 'review_required', priority: 'high', score: 0.82 },
  { id: 'DOC-2X9Y4Z', applicant: 'MedTech Devices', type: 'medical_device', received: '1 hr ago', status: 'processed', priority: 'medium', score: 0.95 },
  { id: 'DOC-1A2B3C', applicant: 'Clinical Trials Inc.', type: 'sae', received: '2 hrs ago', status: 'blocked', priority: 'critical', score: 0.45 },
  { id: 'DOC-5P6Q7R', applicant: 'Global Health', type: 'drug', received: '3 hrs ago', status: 'processed', priority: 'low', score: 0.98 },
  { id: 'DOC-9M8N7P', applicant: 'BioGenetics', type: 'clinical_trial', received: '5 hrs ago', status: 'review_required', priority: 'high', score: 0.78 },
];

const SubmissionsQueue = () => {
  const navigate = useNavigate();

  const getStatusBadge = (status: string) => {
    switch(status) {
      case 'processed': return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium bg-green-500/10 text-green-600 dark:text-green-400"><CheckCircle2 size={14}/> Auto-Approved</span>;
      case 'review_required': return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium bg-orange-500/10 text-orange-600 dark:text-orange-400"><Clock size={14}/> Needs Review</span>;
      case 'blocked': return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium bg-red-500/10 text-red-600 dark:text-red-400"><AlertCircle size={14}/> Blocked</span>;
      default: return null;
    }
  };

  const getPriorityBadge = (priority: string) => {
    switch(priority) {
      case 'critical': return <span className="px-2 py-1 rounded text-xs font-bold bg-red-500 text-white shadow-sm">CRITICAL</span>;
      case 'high': return <span className="px-2 py-1 rounded text-xs font-bold bg-orange-500 text-white shadow-sm">HIGH</span>;
      case 'medium': return <span className="px-2 py-1 rounded text-xs font-bold bg-blue-500 text-white shadow-sm">MED</span>;
      case 'low': return <span className="px-2 py-1 rounded text-xs font-bold bg-slate-500 text-white shadow-sm">LOW</span>;
      default: return null;
    }
  };

  return (
    <div className="space-y-6 pb-10">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Submissions Queue</h1>
          <p className="text-muted-foreground mt-1">Manage and review AI-processed regulatory submissions.</p>
        </div>
      </div>

      <div className="bg-card rounded-2xl border border-border shadow-sm flex flex-col overflow-hidden">
        {/* Table Toolbar */}
        <div className="p-4 border-b border-border flex flex-col sm:flex-row items-center gap-4 justify-between bg-muted/20">
          <div className="flex items-center gap-2 w-full sm:w-auto">
            <div className="relative flex-1 sm:w-80">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <input
                type="text"
                placeholder="Search doc ID, applicant..."
                className="w-full pl-9 pr-4 py-2 bg-card border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all shadow-sm"
              />
            </div>
            <button className="p-2 border border-border rounded-lg bg-card hover:bg-muted text-foreground shadow-sm transition-colors flex items-center justify-center shrink-0">
              <Filter size={18} />
            </button>
          </div>
          
          <div className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">Showing 1-5 of 142</span>
            <div className="flex gap-1 ml-2">
              <button className="px-3 py-1.5 border border-border rounded-md bg-card hover:bg-muted transition-colors disabled:opacity-50" disabled>Prev</button>
              <button className="px-3 py-1.5 border border-border rounded-md bg-card hover:bg-muted transition-colors">Next</button>
            </div>
          </div>
        </div>

        {/* Table */}
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm whitespace-nowrap">
            <thead className="bg-muted/30 text-muted-foreground">
              <tr>
                <th className="px-6 py-4 font-medium">Document ID</th>
                <th className="px-6 py-4 font-medium">Applicant</th>
                <th className="px-6 py-4 font-medium">Type</th>
                <th className="px-6 py-4 font-medium">Priority</th>
                <th className="px-6 py-4 font-medium">AI Status</th>
                <th className="px-6 py-4 font-medium">Received</th>
                <th className="px-6 py-4 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {mockSubmissions.map((sub, i) => (
                <motion.tr 
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.05 }}
                  key={sub.id} 
                  className="hover:bg-muted/30 transition-colors cursor-pointer group"
                  onClick={() => navigate(`/submissions/${sub.id}`)}
                >
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-3">
                      <div className="p-2 rounded-lg bg-primary/10 text-primary group-hover:bg-primary group-hover:text-primary-foreground transition-colors">
                        <FileText size={16} />
                      </div>
                      <span className="font-semibold text-foreground">{sub.id}</span>
                    </div>
                  </td>
                  <td className="px-6 py-4 text-foreground font-medium">{sub.applicant}</td>
                  <td className="px-6 py-4 text-muted-foreground capitalize">{sub.type.replace('_', ' ')}</td>
                  <td className="px-6 py-4">{getPriorityBadge(sub.priority)}</td>
                  <td className="px-6 py-4">{getStatusBadge(sub.status)}</td>
                  <td className="px-6 py-4 text-muted-foreground">{sub.received}</td>
                  <td className="px-6 py-4 text-right">
                    <button 
                      className="p-1.5 rounded-md hover:bg-muted text-muted-foreground transition-colors"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <MoreVertical size={18} />
                    </button>
                  </td>
                </motion.tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default SubmissionsQueue;
