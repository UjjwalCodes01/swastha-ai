import { Settings as SettingsIcon, User, Bell, Shield, Database } from 'lucide-react';
import { motion } from 'framer-motion';

const Settings = () => {
  return (
    <div className="space-y-6">
      <div className="mb-8">
        <h1 className="text-2xl font-bold tracking-tight text-foreground">Settings</h1>
        <p className="text-muted-foreground mt-1">Configure your SwasthaAI experience and platform integrations.</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-8">
        <aside className="space-y-1">
          {[
            { name: 'Profile', icon: User },
            { name: 'Notifications', icon: Bell },
            { name: 'Security', icon: Shield },
            { name: 'API Keys', icon: Database },
          ].map((item, i) => (
            <button
              key={item.name}
              className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                i === 0 ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-muted hover:text-foreground'
              }`}
            >
              <item.icon size={18} />
              {item.name}
            </button>
          ))}
        </aside>

        <div className="lg:col-span-3 space-y-6">
          <motion.div 
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            className="bg-card rounded-2xl border border-border shadow-sm p-6"
          >
            <h3 className="text-lg font-semibold text-foreground mb-4">Platform Configuration</h3>
            <div className="space-y-4">
              <div className="flex items-center justify-between p-4 bg-muted/30 rounded-xl border border-border">
                <div>
                  <p className="font-medium text-foreground">Auto-Anonymisation</p>
                  <p className="text-xs text-muted-foreground">Automatically remove PII from all ingested documents.</p>
                </div>
                <div className="w-10 h-5 bg-primary rounded-full relative shadow-inner">
                   <div className="absolute right-0.5 top-0.5 w-4 h-4 bg-white rounded-full shadow-sm" />
                </div>
              </div>
              <div className="flex items-center justify-between p-4 bg-muted/30 rounded-xl border border-border">
                <div>
                  <p className="font-medium text-foreground">XAI Verbosity</p>
                  <p className="text-xs text-muted-foreground">Level of detail in decision rationale logs.</p>
                </div>
                <select className="bg-card border border-border rounded-md text-xs px-2 py-1">
                  <option>Standard</option>
                  <option>Detailed</option>
                  <option>Debug</option>
                </select>
              </div>
            </div>
          </motion.div>
        </div>
      </div>
    </div>
  );
};

export default Settings;
