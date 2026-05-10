import { ShieldAlert, AlertTriangle } from 'lucide-react';
import { motion } from 'framer-motion';

const Compliance = () => {
  return (
    <div className="space-y-6">
      <div className="mb-8">
        <h1 className="text-2xl font-bold tracking-tight text-foreground">Compliance Alerts</h1>
        <p className="text-muted-foreground mt-1">Real-time regulatory compliance violations and flags.</p>
      </div>

      <motion.div 
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="bg-card rounded-2xl border border-border shadow-sm p-12 text-center"
      >
        <ShieldAlert size={48} className="mx-auto mb-4 text-primary opacity-20" />
        <h3 className="text-lg font-semibold text-foreground">All Clear</h3>
        <p className="text-muted-foreground mt-2 max-w-md mx-auto">
          No system-wide compliance alerts are active. Individual submission findings can be viewed in the queue.
        </p>
      </motion.div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-8">
        <div className="bg-orange-500/5 border border-orange-500/20 rounded-2xl p-6">
          <div className="flex items-center gap-3 mb-4">
            <AlertTriangle className="text-orange-500" size={20} />
            <h4 className="font-semibold text-foreground">Active Policy Monitors</h4>
          </div>
          <ul className="space-y-3 text-sm text-muted-foreground">
            <li className="flex justify-between"><span>CDSCO Schedule M</span> <span className="text-green-500 font-medium">Active</span></li>
            <li className="flex justify-between"><span>PII Data Protection</span> <span className="text-green-500 font-medium">Active</span></li>
            <li className="flex justify-between"><span>Drug & Cosmetics Act</span> <span className="text-green-500 font-medium">Active</span></li>
          </ul>
        </div>
      </div>
    </div>
  );
};

export default Compliance;
