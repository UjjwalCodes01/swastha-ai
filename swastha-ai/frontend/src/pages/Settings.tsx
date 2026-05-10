import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { User, Bell, Shield, Database, Check, ChevronRight } from 'lucide-react';

// ── Types ─────────────────────────────────────────────────────────────────────

type SettingsTab = 'profile' | 'notifications' | 'security' | 'api';

interface ToggleProps {
  id: string;
  enabled: boolean;
  onChange: () => void;
}

const Toggle = ({ id, enabled, onChange }: ToggleProps) => (
  <button
    id={id}
    role="switch"
    aria-checked={enabled}
    onClick={onChange}
    className={`relative w-11 h-6 rounded-full border-2 transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-primary/40 ${
      enabled ? 'bg-primary border-primary' : 'bg-muted border-border'
    }`}
  >
    <span
      className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-transform duration-200 ${
        enabled ? 'translate-x-5' : 'translate-x-0'
      }`}
    />
  </button>
);

// ── Saved toast ───────────────────────────────────────────────────────────────

const SavedToast = ({ show }: { show: boolean }) => (
  <AnimatePresence>
    {show && (
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: 10 }}
        className="fixed bottom-6 right-6 z-50 flex items-center gap-2 bg-green-600 text-white text-sm font-medium px-4 py-3 rounded-xl shadow-lg"
      >
        <Check size={16} />
        Settings saved
      </motion.div>
    )}
  </AnimatePresence>
);

// ── Main component ────────────────────────────────────────────────────────────

const Settings = () => {
  const [activeTab, setActiveTab] = useState<SettingsTab>('profile');
  const [saved, setSaved] = useState(false);

  // Platform settings state
  const [autoAnon, setAutoAnon] = useState(true);
  const [enableAiCore, setEnableAiCore] = useState(true);
  const [enableCompliance, setEnableCompliance] = useState(true);
  const [xaiVerbosity, setXaiVerbosity] = useState('standard');
  const [llmMode, setLlmMode] = useState('groq');

  // Notification settings state
  const [emailAlerts, setEmailAlerts] = useState(true);
  const [criticalOnly, setCriticalOnly] = useState(false);
  const [digestEmail, setDigestEmail] = useState(true);

  const tabs: { id: SettingsTab; name: string; icon: typeof User }[] = [
    { id: 'profile', name: 'Profile', icon: User },
    { id: 'notifications', name: 'Notifications', icon: Bell },
    { id: 'security', name: 'Security', icon: Shield },
    { id: 'api', name: 'API & Model', icon: Database },
  ];

  const handleSave = () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  };

  return (
    <div className="space-y-6 pb-16">
      <div className="mb-8">
        <h1 className="text-2xl font-bold tracking-tight text-foreground">Settings</h1>
        <p className="text-muted-foreground mt-1">Configure your SwasthaAI Reviewer Dashboard experience.</p>
      </div>

      <SavedToast show={saved} />

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-8">
        {/* Sidebar nav */}
        <aside className="space-y-1">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all ${
                  isActive
                    ? 'bg-primary/10 text-primary border border-primary/20 shadow-sm'
                    : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                }`}
              >
                <Icon size={18} />
                {tab.name}
                {isActive && <ChevronRight size={14} className="ml-auto" />}
              </button>
            );
          })}
        </aside>

        {/* Content */}
        <div className="lg:col-span-3 space-y-6">
          <AnimatePresence mode="wait">

            {/* Profile tab */}
            {activeTab === 'profile' && (
              <motion.div
                key="profile"
                initial={{ opacity: 0, x: 16 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -16 }}
                transition={{ duration: 0.2 }}
                className="space-y-6"
              >
                <div className="bg-card rounded-2xl border border-border shadow-sm p-6">
                  <h3 className="text-base font-bold text-foreground mb-5">Reviewer Profile</h3>
                  <div className="flex items-center gap-4 mb-6">
                    <div className="w-16 h-16 rounded-full bg-gradient-to-tr from-primary to-orange-400 flex items-center justify-center text-white text-xl font-bold shadow-lg">
                      RC
                    </div>
                    <div>
                      <p className="font-bold text-foreground">Reviewer — CDSCO</p>
                      <p className="text-sm text-muted-foreground">reviewer@cdsco.gov.in</p>
                      <span className="inline-block mt-1 text-[10px] font-bold uppercase tracking-wider bg-primary/10 text-primary border border-primary/20 px-2 py-0.5 rounded-full">
                        Senior Reviewer
                      </span>
                    </div>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    {[
                      { label: 'Full Name', placeholder: 'Dr. Rani Chaudhary', defaultValue: 'CDSCO Reviewer' },
                      { label: 'Email', placeholder: 'reviewer@cdsco.gov.in', defaultValue: 'reviewer@cdsco.gov.in' },
                      { label: 'Department', placeholder: 'Drug Division', defaultValue: 'Drug Division' },
                      { label: 'Employee ID', placeholder: 'CDSCO-0001', defaultValue: 'CDSCO-0001' },
                    ].map((field) => (
                      <div key={field.label}>
                        <label className="text-xs font-semibold text-foreground block mb-1.5">{field.label}</label>
                        <input
                          type="text"
                          defaultValue={field.defaultValue}
                          placeholder={field.placeholder}
                          className="w-full px-3 py-2 bg-muted/40 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all"
                        />
                      </div>
                    ))}
                  </div>
                  <button onClick={handleSave} className="mt-5 px-5 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-semibold hover:bg-primary/90 transition-colors shadow-sm">
                    Save Profile
                  </button>
                </div>
              </motion.div>
            )}

            {/* Notifications tab */}
            {activeTab === 'notifications' && (
              <motion.div
                key="notifications"
                initial={{ opacity: 0, x: 16 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -16 }}
                transition={{ duration: 0.2 }}
              >
                <div className="bg-card rounded-2xl border border-border shadow-sm p-6">
                  <h3 className="text-base font-bold text-foreground mb-5">Notification Preferences</h3>
                  <div className="space-y-5">
                    {[
                      { label: 'Email Alerts', desc: 'Receive email when a new submission is flagged for review.', value: emailAlerts, onChange: () => setEmailAlerts((v) => !v), id: 'email-alerts' },
                      { label: 'Critical SAE Only', desc: 'Only notify for death or life-threatening SAE classifications.', value: criticalOnly, onChange: () => setCriticalOnly((v) => !v), id: 'critical-only' },
                      { label: 'Daily Digest', desc: 'Receive a daily summary of all processed submissions.', value: digestEmail, onChange: () => setDigestEmail((v) => !v), id: 'digest-email' },
                    ].map((setting) => (
                      <div key={setting.id} className="flex items-center justify-between p-4 bg-muted/30 rounded-xl border border-border">
                        <div>
                          <p className="font-medium text-foreground text-sm">{setting.label}</p>
                          <p className="text-xs text-muted-foreground mt-0.5">{setting.desc}</p>
                        </div>
                        <Toggle id={setting.id} enabled={setting.value} onChange={setting.onChange} />
                      </div>
                    ))}
                  </div>
                  <button onClick={handleSave} className="mt-5 px-5 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-semibold hover:bg-primary/90 transition-colors shadow-sm">
                    Save Preferences
                  </button>
                </div>
              </motion.div>
            )}

            {/* Security tab */}
            {activeTab === 'security' && (
              <motion.div
                key="security"
                initial={{ opacity: 0, x: 16 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -16 }}
                transition={{ duration: 0.2 }}
              >
                <div className="bg-card rounded-2xl border border-border shadow-sm p-6">
                  <h3 className="text-base font-bold text-foreground mb-5">Security</h3>
                  <div className="space-y-4">
                    <div className="p-4 bg-green-500/5 border border-green-500/20 rounded-xl flex items-start gap-3">
                      <Shield size={18} className="text-green-600 mt-0.5 shrink-0" />
                      <div>
                        <p className="font-semibold text-foreground text-sm">Immutable Audit Trail Active</p>
                        <p className="text-xs text-muted-foreground mt-0.5">
                          All reviewer actions are chain-hashed (SHA-256) and stored in an append-only PostgreSQL table. <code className="bg-muted px-1 rounded font-mono">UPDATE</code> and <code className="bg-muted px-1 rounded font-mono">DELETE</code> are revoked at database level.
                        </p>
                      </div>
                    </div>
                    <div className="p-4 bg-blue-500/5 border border-blue-500/20 rounded-xl flex items-start gap-3">
                      <Shield size={18} className="text-blue-500 mt-0.5 shrink-0" />
                      <div>
                        <p className="font-semibold text-foreground text-sm">API Key Authentication</p>
                        <p className="text-xs text-muted-foreground mt-0.5">
                          All requests use <code className="bg-muted px-1 rounded font-mono">X-API-Key</code> header authentication. JWT / Keycloak bearer token support is pre-configured for production deployment.
                        </p>
                      </div>
                    </div>
                    <div className="p-4 bg-muted/30 border border-border rounded-xl">
                      <p className="font-medium text-foreground text-sm mb-3">Change Session API Key</p>
                      <input
                        type="password"
                        placeholder="••••••••••••••••••••••••••••••••"
                        className="w-full px-3 py-2 bg-card border border-border rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all"
                      />
                      <button onClick={handleSave} className="mt-3 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-semibold hover:bg-primary/90 transition-colors shadow-sm">
                        Update Key
                      </button>
                    </div>
                  </div>
                </div>
              </motion.div>
            )}

            {/* API & Model tab */}
            {activeTab === 'api' && (
              <motion.div
                key="api"
                initial={{ opacity: 0, x: 16 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -16 }}
                transition={{ duration: 0.2 }}
                className="space-y-5"
              >
                <div className="bg-card rounded-2xl border border-border shadow-sm p-6">
                  <h3 className="text-base font-bold text-foreground mb-5">AI Core Configuration</h3>
                  <div className="space-y-5">
                    {[
                      { label: 'Auto-Anonymisation', desc: 'Automatically remove PII/PHI from all ingested documents before LLM processing.', value: autoAnon, onChange: () => setAutoAnon((v) => !v), id: 'auto-anon' },
                      { label: 'AI Core Pipeline', desc: 'Enable the Layer 3 AI Core pipeline (anonymisation, summarisation, classification).', value: enableAiCore, onChange: () => setEnableAiCore((v) => !v), id: 'enable-ai' },
                      { label: 'Compliance Checks', desc: 'Run DPDP / ICMR / NDHM compliance rules after every AI analysis.', value: enableCompliance, onChange: () => setEnableCompliance((v) => !v), id: 'enable-compliance' },
                    ].map((setting) => (
                      <div key={setting.id} className="flex items-center justify-between p-4 bg-muted/30 rounded-xl border border-border">
                        <div className="mr-4">
                          <p className="font-medium text-foreground text-sm">{setting.label}</p>
                          <p className="text-xs text-muted-foreground mt-0.5">{setting.desc}</p>
                        </div>
                        <Toggle id={setting.id} enabled={setting.value} onChange={setting.onChange} />
                      </div>
                    ))}

                    <div className="p-4 bg-muted/30 rounded-xl border border-border">
                      <div className="flex items-center justify-between mb-3">
                        <div>
                          <p className="font-medium text-foreground text-sm">LLM Mode</p>
                          <p className="text-xs text-muted-foreground mt-0.5">Controls which language model is used by the AI Core.</p>
                        </div>
                        <select
                          value={llmMode}
                          onChange={(e) => setLlmMode(e.target.value)}
                          className="bg-card border border-border rounded-lg text-sm px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/20"
                        >
                          <option value="gemini">Gemini 1.5 Flash</option>
                          <option value="groq">Groq / Llama 3</option>
                          <option value="cloud">Anthropic Claude</option>
                          <option value="hybrid">Hybrid (Claude → Ollama)</option>
                          <option value="offline">Offline (Ollama only)</option>
                        </select>
                      </div>
                    </div>

                    <div className="p-4 bg-muted/30 rounded-xl border border-border">
                      <div className="flex items-center justify-between">
                        <div>
                          <p className="font-medium text-foreground text-sm">XAI Verbosity</p>
                          <p className="text-xs text-muted-foreground mt-0.5">Level of detail in AI decision rationale logs.</p>
                        </div>
                        <select
                          value={xaiVerbosity}
                          onChange={(e) => setXaiVerbosity(e.target.value)}
                          className="bg-card border border-border rounded-lg text-sm px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/20"
                        >
                          <option value="standard">Standard</option>
                          <option value="detailed">Detailed</option>
                          <option value="debug">Debug</option>
                        </select>
                      </div>
                    </div>
                  </div>

                  <button onClick={handleSave} className="mt-5 px-5 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-semibold hover:bg-primary/90 transition-colors shadow-sm">
                    Save Configuration
                  </button>
                </div>

                {/* Backend info */}
                <div className="bg-card rounded-2xl border border-border shadow-sm p-6">
                  <h3 className="text-base font-bold text-foreground mb-4">Backend Connection</h3>
                  <div className="space-y-3">
                    {[
                      { label: 'API Base URL', value: (import.meta as any).env?.VITE_API_BASE_URL || 'http://localhost:8000', mono: true },
                      { label: 'HuggingFace Backend Repo', value: 'satvik-svg/swastha-ai-hf', mono: true },
                      { label: 'Database', value: 'Supabase PostgreSQL 15 (Session Pooler :5432)', mono: false },
                    ].map((item) => (
                      <div key={item.label} className="flex items-center justify-between py-2 border-b border-border last:border-0">
                        <span className="text-xs text-muted-foreground">{item.label}</span>
                        <span className={`text-xs font-medium text-foreground ${item.mono ? 'font-mono bg-muted px-2 py-0.5 rounded' : ''}`}>
                          {item.value}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </motion.div>
            )}

          </AnimatePresence>
        </div>
      </div>
    </div>
  );
};

export default Settings;
