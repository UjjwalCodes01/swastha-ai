import { motion, type Variants } from 'framer-motion';
import {
  Upload, Cpu, ShieldCheck, FileText, GitCompare,
  Brain, Database, Layout, Zap, ArrowRight,
  CheckCircle2, Eye, Lock, Activity,
} from 'lucide-react';

const fadeUp: Variants = {
  hidden: { opacity: 0, y: 24 },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.08, duration: 0.5, ease: [0.25, 0.1, 0.25, 1] },
  }),
};

// ── Architecture layers ────────────────────────────────────────────────────────
const layers = [
  {
    num: '0',
    name: 'Portal Ingestion',
    icon: Upload,
    color: 'text-blue-500',
    bg: 'bg-blue-500/10 border-blue-500/20',
    description:
      'Accepts documents from SUGAM & MD Online portals, manual uploads, and SAE feeds. Validates MIME type, detects zip-bombs, computes SHA-256 checksum, and stores raw files in MinIO.',
  },
  {
    num: '1',
    name: 'Preprocessing Engine',
    icon: Cpu,
    color: 'text-violet-500',
    bg: 'bg-violet-500/10 border-violet-500/20',
    description:
      'Extracts text from PDFs (PyMuPDF), DOCX, XML, and CSV. Performs Tesseract OCR on scanned images, detects language, normalises Unicode, and generates semantic embeddings via sentence-transformers stored in ChromaDB.',
  },
  {
    num: '2',
    name: 'Message Bus (Kafka)',
    icon: Zap,
    color: 'text-yellow-500',
    bg: 'bg-yellow-500/10 border-yellow-500/20',
    description:
      'Decouples every layer via Apache Kafka (KRaft mode). Implements circuit breakers, dead-letter queues (DLQ), Redis buffering, and Avro schema validation so no message is ever lost.',
  },
  {
    num: '3',
    name: 'AI Core',
    icon: Brain,
    color: 'text-primary',
    bg: 'bg-primary/10 border-primary/20',
    description:
      'Runs four AI modules in sequence: Anonymisation (Presidio + custom Indian PII regex), Summarisation (Gemini 1.5 / Groq / Ollama), Classification (completeness + SAE severity + dedup), and Comparison (version diff + change narration).',
  },
  {
    num: '4',
    name: 'Compliance & Governance',
    icon: ShieldCheck,
    color: 'text-green-500',
    bg: 'bg-green-500/10 border-green-500/20',
    description:
      'Runs deterministic rule engines against DPDP Act 2023, ICMR guidelines, and NDHM standards. Every AI decision is logged in the XAI ledger with model ID, confidence, and rationale for full regulatory transparency.',
  },
  {
    num: '5',
    name: 'Data Storage',
    icon: Database,
    color: 'text-cyan-500',
    bg: 'bg-cyan-500/10 border-cyan-500/20',
    description:
      'PostgreSQL (Supabase) stores submissions, audit logs, compliance assessments, and XAI records. MinIO holds raw and processed documents. ChromaDB stores vector embeddings for semantic search and deduplication.',
  },
  {
    num: '6',
    name: 'Output & Delivery',
    icon: Layout,
    color: 'text-rose-500',
    bg: 'bg-rose-500/10 border-rose-500/20',
    description:
      'The Reviewer Dashboard (this app) surfaces AI analysis for human review. CDSCO reviewers can approve/reject submissions, download PDF reports, and view XAI rationale. Reviewer decisions create immutable audit entries and publish Kafka events.',
  },
];

// ── AI pipeline steps ──────────────────────────────────────────────────────────
const pipeline = [
  {
    icon: Lock,
    title: 'Anonymisation',
    color: 'text-blue-500',
    ring: 'ring-blue-500/30',
    points: [
      'Custom regex for Aadhaar, PAN, CIN, Indian phone numbers, emails',
      'Microsoft Presidio integration for standard PII types',
      'spaCy NER for PERSON, ORG, LOC entities',
      'Stable pseudonyms (e.g. <AADHAAR_1_F4A2C>) — not blank redactions',
      'Every entity logged in the immutable audit trail',
    ],
  },
  {
    icon: FileText,
    title: 'Summarisation',
    color: 'text-primary',
    ring: 'ring-primary/30',
    points: [
      'Gemini 1.5 Flash as primary LLM (handles 38-page PDFs)',
      'Groq / Llama 3 70B as fallback — enables offline Stage 2 use',
      'Pydantic schema-enforced output (retries on malformed JSON)',
      'Per-type prompt templates: Drug, Medical Device, Clinical Trial, SAE',
      'Map-reduce chunking for very large documents',
    ],
  },
  {
    icon: Activity,
    title: 'Classification',
    color: 'text-violet-500',
    ring: 'ring-violet-500/30',
    points: [
      'Completeness checker: rule engine per form type',
      'SAE severity classification: death, life-threatening, disability, hospitalisation',
      'Duplicate detection via FAISS similarity search on stored embeddings',
      'Risk scoring with priority labels: critical / high / medium / low',
    ],
  },
  {
    icon: GitCompare,
    title: 'Comparison',
    color: 'text-green-500',
    ring: 'ring-green-500/30',
    points: [
      'Deterministic text diff between document versions',
      'AI-narrated explanation of regulatory significance of each change',
      'CDSCO-format Markdown inspection reports',
      'Comparison reports stored in MinIO, accessible via API',
    ],
  },
];

// ── XAI explanation ────────────────────────────────────────────────────────────
const xaiPoints = [
  { icon: Eye, label: 'Every decision recorded', desc: 'Module name, model ID, model version, and timestamp are stored for every AI call.' },
  { icon: Activity, label: 'Confidence scores', desc: 'Each AI output includes a 0–1 confidence score so reviewers can prioritise low-confidence cases.' },
  { icon: FileText, label: 'Rationale bullets', desc: 'The LLM is instructed to output step-by-step reasoning alongside its conclusion, not just a result.' },
  { icon: CheckCircle2, label: 'Human override log', desc: 'When a reviewer overrides an AI decision, the reason is appended to the immutable audit chain.' },
  { icon: Lock, label: 'Tamper-evident audit', desc: 'Each audit entry is SHA-256 linked to the previous entry, making retroactive modification detectable.' },
];

// ── LLM fallback chain ────────────────────────────────────────────────────────
const llmChain = [
  { name: 'Google Gemini 1.5 Flash', tag: 'Primary (Cloud)', color: 'bg-blue-500' },
  { name: 'Groq / Llama 3 70B', tag: 'Fallback (Cloud)', color: 'bg-violet-500' },
  { name: 'Anthropic Claude', tag: 'Optional (Cloud)', color: 'bg-primary' },
  { name: 'Ollama (Llama 3)', tag: 'Offline / Stage 2', color: 'bg-green-500' },
];

// ── Component ─────────────────────────────────────────────────────────────────

const Explainability = () => {
  return (
    <div className="space-y-16 pb-16">

      {/* Hero */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="text-center pt-4"
      >
        <span className="inline-block px-4 py-1.5 rounded-full bg-primary/10 border border-primary/20 text-primary text-xs font-bold uppercase tracking-widest mb-4">
          How SwasthaAI Works
        </span>
        <h1 className="text-4xl font-extrabold tracking-tight text-foreground mb-4">
          Explainable AI for Regulatory Compliance
        </h1>
        <p className="text-muted-foreground max-w-2xl mx-auto text-base leading-relaxed">
          SwasthaAI is a seven-layer, event-driven AI platform built for India's Central Drugs Standard Control Organisation (CDSCO). Every step — from document ingestion to the reviewer's decision — is fully transparent, auditable, and reproducible.
        </p>
      </motion.div>

      {/* 7-Layer Architecture */}
      <section>
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.2 }}
          className="mb-8"
        >
          <h2 className="text-2xl font-bold text-foreground">7-Layer Architecture</h2>
          <p className="text-muted-foreground mt-1">Each layer is independently scalable and communicates exclusively via Kafka events.</p>
        </motion.div>

        <div className="space-y-4">
          {layers.map((layer, i) => {
            const Icon = layer.icon;
            return (
              <motion.div
                key={layer.num}
                custom={i}
                initial="hidden"
                animate="visible"
                variants={fadeUp}
                className={`flex gap-5 p-5 rounded-2xl border bg-card shadow-sm hover:shadow-md transition-shadow`}
              >
                <div className={`shrink-0 w-12 h-12 rounded-xl border flex items-center justify-center ${layer.bg}`}>
                  <Icon size={22} className={layer.color} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-3 mb-1">
                    <span className={`text-xs font-black uppercase tracking-widest ${layer.color}`}>Layer {layer.num}</span>
                    <h3 className="font-bold text-foreground">{layer.name}</h3>
                  </div>
                  <p className="text-sm text-muted-foreground leading-relaxed">{layer.description}</p>
                </div>
              </motion.div>
            );
          })}
        </div>
      </section>

      {/* AI Pipeline */}
      <section>
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="mb-8"
        >
          <h2 className="text-2xl font-bold text-foreground">The AI Core Pipeline (Layer 3)</h2>
          <p className="text-muted-foreground mt-1">Four modules run in sequence, each consuming and producing Kafka events.</p>
        </motion.div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          {pipeline.map((step, i) => {
            const Icon = step.icon;
            return (
              <motion.div
                key={step.title}
                custom={i}
                initial="hidden"
                whileInView="visible"
                viewport={{ once: true }}
                variants={fadeUp}
                className={`bg-card border border-border rounded-2xl shadow-sm p-6 ring-1 ${step.ring}`}
              >
                <div className="flex items-center gap-3 mb-4">
                  <div className={`w-10 h-10 rounded-xl bg-muted flex items-center justify-center`}>
                    <Icon size={20} className={step.color} />
                  </div>
                  <div>
                    <span className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground">Step {i + 1}</span>
                    <h3 className="font-bold text-foreground text-sm">{step.title}</h3>
                  </div>
                </div>
                <ul className="space-y-2">
                  {step.points.map((pt) => (
                    <li key={pt} className="flex items-start gap-2 text-xs text-muted-foreground">
                      <ArrowRight size={12} className={`${step.color} shrink-0 mt-0.5`} />
                      <span>{pt}</span>
                    </li>
                  ))}
                </ul>
              </motion.div>
            );
          })}
        </div>
      </section>

      {/* LLM Fallback Chain */}
      <section>
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="mb-6"
        >
          <h2 className="text-2xl font-bold text-foreground">LLM Fallback Chain</h2>
          <p className="text-muted-foreground mt-1">
            The system gracefully degrades through cloud and offline models, ensuring it works even in CDSCO's Stage 2 air-gapped on-premises environment.
          </p>
        </motion.div>

        <div className="bg-card border border-border rounded-2xl shadow-sm p-6">
          <div className="flex flex-col sm:flex-row gap-3 items-stretch">
            {llmChain.map((model, i) => (
              <div key={model.name} className="flex sm:flex-col items-center gap-3 flex-1">
                <div className="flex-1 w-full bg-muted/40 border border-border rounded-xl p-4 text-center">
                  <div className={`w-3 h-3 rounded-full ${model.color} mx-auto mb-2`} />
                  <p className="text-xs font-bold text-foreground">{model.name}</p>
                  <p className="text-[10px] text-muted-foreground mt-0.5">{model.tag}</p>
                </div>
                {i < llmChain.length - 1 && (
                  <ArrowRight size={16} className="text-muted-foreground shrink-0 sm:hidden" />
                )}
                {i < llmChain.length - 1 && (
                  <ArrowRight size={16} className="text-muted-foreground shrink-0 hidden sm:block self-center sm:-mt-2" />
                )}
              </div>
            ))}
          </div>
          <p className="text-xs text-muted-foreground mt-4 text-center">
            Set <code className="bg-muted px-1.5 py-0.5 rounded font-mono">AI_CORE_MODEL_MODE</code> in <code className="bg-muted px-1.5 py-0.5 rounded font-mono">.env</code> to control which LLM is used: <span className="font-semibold">gemini</span>, <span className="font-semibold">groq</span>, <span className="font-semibold">cloud</span> (Claude), <span className="font-semibold">hybrid</span>, or <span className="font-semibold">offline</span> (Ollama).
          </p>
        </div>
      </section>

      {/* XAI Section */}
      <section>
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="mb-6"
        >
          <h2 className="text-2xl font-bold text-foreground">Explainable AI (XAI) & Audit Trail</h2>
          <p className="text-muted-foreground mt-1">
            Government regulators require more than a result — they need to understand <em>why</em> the AI made a decision and prove it has not been tampered with.
          </p>
        </motion.div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {xaiPoints.map((pt, i) => {
            const Icon = pt.icon;
            return (
              <motion.div
                key={pt.label}
                custom={i}
                initial="hidden"
                whileInView="visible"
                viewport={{ once: true }}
                variants={fadeUp}
                className="bg-card border border-border rounded-2xl p-5 shadow-sm"
              >
                <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center mb-3">
                  <Icon size={18} className="text-primary" />
                </div>
                <h4 className="font-bold text-foreground text-sm mb-1">{pt.label}</h4>
                <p className="text-xs text-muted-foreground leading-relaxed">{pt.desc}</p>
              </motion.div>
            );
          })}
        </div>
      </section>

      {/* CDSCO Compliance Frameworks */}
      <section>
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="mb-6"
        >
          <h2 className="text-2xl font-bold text-foreground">Regulatory Compliance Frameworks</h2>
          <p className="text-muted-foreground mt-1">SwasthaAI's compliance engine validates every document against these frameworks automatically.</p>
        </motion.div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-5">
          {[
            {
              name: 'DPDP Act 2023',
              color: 'border-blue-500/30 bg-blue-500/5',
              badge: 'bg-blue-500',
              items: ['PII/PHI detection and anonymisation before LLM processing', 'Consent requirement checks', 'Cross-border data transfer flags', 'Right to erasure compliance'],
            },
            {
              name: 'ICMR Guidelines',
              color: 'border-green-500/30 bg-green-500/5',
              badge: 'bg-green-500',
              items: ['Clinical trial protocol completeness checks', 'SAE severity classification rules', 'Informed consent form validation', 'Ethics committee approval verification'],
            },
            {
              name: 'NDHM Standards',
              color: 'border-primary/30 bg-primary/5',
              badge: 'bg-primary',
              items: ['Health data interoperability checks', 'ABHA ID linkage validation', 'Health record format compliance', 'De-identification standards'],
            },
          ].map((fw, i) => (
            <motion.div
              key={fw.name}
              custom={i}
              initial="hidden"
              whileInView="visible"
              viewport={{ once: true }}
              variants={fadeUp}
              className={`border rounded-2xl p-5 ${fw.color}`}
            >
              <div className="flex items-center gap-2 mb-4">
                <span className={`w-2.5 h-2.5 rounded-full ${fw.badge}`} />
                <h3 className="font-bold text-foreground text-sm">{fw.name}</h3>
              </div>
              <ul className="space-y-2">
                {fw.items.map((item) => (
                  <li key={item} className="flex items-start gap-2 text-xs text-muted-foreground">
                    <CheckCircle2 size={12} className="text-green-500 shrink-0 mt-0.5" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </motion.div>
          ))}
        </div>
      </section>

      {/* Deployment note */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true }}
        transition={{ duration: 0.5 }}
        className="bg-card border border-border rounded-2xl p-6 shadow-sm"
      >
        <h2 className="text-lg font-bold text-foreground mb-2">Deployment Strategy</h2>
        <p className="text-sm text-muted-foreground leading-relaxed mb-4">
          The platform is deployed as a hybrid-cloud stack to support both Stage 1 (virtual) and Stage 2 (on-premises) of the hackathon.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-sm">
          {[
            { label: 'Backend (AI + API)', detail: 'Hugging Face Docker Space — 16 GB RAM, 2 vCPU', sub: 'satvik-svg/swastha-ai-hf', color: 'bg-yellow-500' },
            { label: 'Database', detail: 'Supabase PostgreSQL 15, asyncpg over IPv4 Session Pooler', sub: 'Session Pooler :5432', color: 'bg-green-500' },
            { label: 'Frontend', detail: 'Vercel (this dashboard) — auto-deploys on every push', sub: 'VITE_API_BASE_URL → HF Space', color: 'bg-primary' },
          ].map((dep) => (
            <div key={dep.label} className="bg-muted/30 border border-border rounded-xl p-4">
              <div className="flex items-center gap-2 mb-2">
                <span className={`w-2 h-2 rounded-full ${dep.color}`} />
                <span className="font-bold text-foreground text-xs uppercase tracking-wider">{dep.label}</span>
              </div>
              <p className="text-xs text-foreground font-medium">{dep.detail}</p>
              <p className="text-[11px] text-muted-foreground mt-0.5 font-mono">{dep.sub}</p>
            </div>
          ))}
        </div>
      </motion.div>

    </div>
  );
};

export default Explainability;
