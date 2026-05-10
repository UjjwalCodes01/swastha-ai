# 🏥 SwasthaAI — AI-Powered CDSCO Regulatory Platform

<div align="center">

**CDSCO-IndiaAI Health Innovation Acceleration Hackathon Entry**  
*AI-Driven Regulatory Workflow Automation and Data Anonymisation*

[![Backend](https://img.shields.io/badge/Backend-Hugging%20Face%20Spaces-yellow?logo=huggingface)](https://satvik-svg-swastha-ai-backend.hf.space)
[![Frontend](https://img.shields.io/badge/Frontend-Vercel-black?logo=vercel)](https://swastha-ai.vercel.app)
[![HF Repo](https://img.shields.io/badge/HF%20Backend%20Repo-satvik--svg%2Fswastha--ai--hf-blue?logo=github)](https://github.com/satvik-svg/swastha-ai-hf)
[![License](https://img.shields.io/badge/License-MIT-green)](./LICENSE)

</div>

---

## 🎯 What is SwasthaAI?

SwasthaAI is an **end-to-end, enterprise-grade AI platform** that automates India's CDSCO (Central Drugs Standard Control Organisation) regulatory document review pipeline. It ingests submissions from the SUGAM and MD Online government portals, processes them through a 7-layer AI architecture, and surfaces results in a reviewer dashboard — replacing the current slow, manual process.

### Problem it Solves

CDSCO currently reviews thousands of documents manually: drug submissions, medical device applications, clinical trial protocols, and Serious Adverse Event (SAE) reports. This bottleneck delays the approval of life-saving medicines. SwasthaAI uses AI to:

1. **Anonymise** all PII/PHI before AI processing (DPDP Act 2023 compliant)
2. **Summarise** high-volume regulatory documents into structured, standardised text
3. **Classify** submissions by severity and completeness
4. **Compare** document versions and generate formal inspection reports

---

## 🏗️ Architecture — 7 Layers

| Layer | Name | Technology | Status |
|-------|------|-----------|--------|
| **0** | Portal Ingestion | FastAPI, MinIO, SHA-256, ClamAV | ✅ Live |
| **1** | Preprocessing Engine | PyMuPDF, Tesseract OCR, sentence-transformers, ChromaDB | ✅ Live |
| **2** | Message Bus | Apache Kafka (KRaft), Avro, Schema Registry, Redis | ✅ Live |
| **3** | AI Core | Google Gemini 1.5, Groq/Llama 3, Ollama (offline), Microsoft Presidio | ✅ Live |
| **4** | Compliance & Governance | DPDP 2023, ICMR, NDHM rule engines, XAI Decision Log | ✅ Live |
| **5** | Data Storage | Supabase PostgreSQL 15, MinIO S3, ChromaDB, Redis | ✅ Live |
| **6** | Output & Delivery | React Dashboard (Vite + TanStack Query), PDF Reports | ✅ Live |

---

## 🚀 Deployment

The platform uses a **hybrid-cloud strategy** — enterprise AI capabilities at zero cost:

| Component | Provider | Details |
|-----------|----------|---------|
| **Backend (API + AI)** | [Hugging Face Spaces](https://github.com/satvik-svg/swastha-ai-hf) | 16 GB RAM Docker Space running FastAPI + Redis + MinIO |
| **Database** | Supabase | PostgreSQL 15, connected via IPv4 Session Pooler (Port 5432) with asyncpg |
| **Frontend** | Vercel | React + Vite dashboard, auto-deploys on push |

> **Stage 2 (On-Premises):** The entire stack is containerised. The LLM layer falls back to local Ollama automatically when cloud APIs are unavailable — making this fully air-gap capable for the CDSCO office deployment.

---

## 📁 Repository Structure

```
swastha-ai/                          # This repository (main codebase)
├── swastha-ai/                      # Python application root
│   ├── app/                         # FastAPI backend source (Layers 0–4)
│   │   ├── main.py                  # Application entrypoint and lifespan
│   │   ├── ai_core/                 # Layer 3: Anonymisation, Summarisation, Classification, Comparison
│   │   ├── compliance/              # Layer 4: DPDP, ICMR, NDHM rule engines + XAI log
│   │   ├── preprocessing/           # Layer 1: OCR, extractors, embeddings, chunking
│   │   ├── messagebus/              # Layer 2: Kafka producer, consumer, DLQ, circuit breaker
│   │   ├── ingestion/               # Layer 0: Ingestion pipeline and portal adapters
│   │   ├── output/                  # Layer 6: Dashboard API, PDF report generation
│   │   └── db/                      # SQLAlchemy models and async connection pool
│   ├── frontend/                    # React + Vite reviewer dashboard (Layer 6)
│   │   └── src/
│   │       ├── pages/               # Dashboard, SubmissionsQueue, SubmissionDetail, Compliance, Settings, Explainability
│   │       ├── components/          # Layout (sidebar + header)
│   │       └── lib/api.ts           # Type-safe API client (axios + TanStack Query)
│   ├── alembic/                     # Database migrations (3 migration files)
│   ├── kafka/                       # Avro schemas + Kafka topic configs
│   ├── docker-compose.yml           # Full local stack
│   ├── Dockerfile                   # Single-stage Python container
│   ├── requirements.txt             # Python dependencies
│   └── .env.example                 # Environment variable template
├── context.md                       # Detailed architectural context document
├── DOCUMENTATION.md                 # Changelog and technical history
└── cdsco_indiaai_full_architecture.svg  # Full system architecture diagram
```

---

## ⚡ Quick Start (Local Development)

### Prerequisites
- Python 3.11+
- Node.js 18+ & npm
- Docker Desktop (for MinIO and Redis containers)

### 1. Backend

```powershell
cd swastha-ai

# Create virtual environment
python -m venv .venv
.\.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
pip install "numpy<2.0.0" groq google-generativeai

# Configure environment
copy .env.example .env
# Edit .env — fill in SUPABASE, GROQ_API_KEY or GEMINI_API_KEY

# Start MinIO and Redis via Docker
docker-compose up -d minio redis

# Run the server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Backend API + Swagger UI: **http://localhost:8000/docs**

### 2. Frontend

```powershell
cd swastha-ai/frontend

# Install dependencies
npm install

# Configure environment
echo "VITE_API_BASE_URL=http://localhost:8000" > .env
echo "VITE_API_KEY=b7f8c9d2a1e43b56c7d8e9f0a1b2c3d4" >> .env

# Start development server
npm run dev
```

Dashboard: **http://localhost:5173**

---

## 🤖 AI Pipeline

```
Document Upload
    ↓
[Layer 0] Ingest → validate → checksum → store in MinIO → emit Kafka event
    ↓
[Layer 1] Preprocess → OCR → extract text → chunk → embed → store in ChromaDB
    ↓
[Layer 3-A] Anonymise → detect PII (Aadhaar, PAN, CIN, email, phone) → pseudonymise
    ↓
[Layer 3-B] Summarise → LLM (Gemini → Groq → Ollama) → structured JSON summary
[Layer 3-C] Classify → completeness score + SAE severity + duplicate detection
    ↓
[Layer 4] Compliance → DPDP Act 2023 + ICMR + NDHM rule checks + XAI log
    ↓
[Layer 6] Reviewer Dashboard → human review → approve/reject → immutable audit entry
```

### LLM Fallback Chain
```
Gemini 1.5 Flash  →  Groq / Llama 3 70B  →  Anthropic Claude  →  Ollama (offline)
```
Controlled by `AI_CORE_MODEL_MODE` in `.env`.

---

## 🛡️ Security & Compliance

- **DPDP Act 2023**: All PII/PHI is anonymised before reaching the LLM — patient data never leaves as plaintext.
- **Immutable Audit Log**: SHA-256 chain-hashed append-only audit trail; `UPDATE`/`DELETE` revoked at DB level.
- **XAI Ledger**: Every AI decision is recorded with model ID, version, confidence score, and rationale bullets.
- **Authentication**: `X-API-Key` header auth; Keycloak JWT Bearer pre-configured for production.
- **Object Storage**: MinIO with SSE-S3 encryption and 10-year document retention.

---

## 🧪 Testing

```powershell
cd swastha-ai

# Install test dependencies
pip install -r requirements-test.txt

# Run all tests
pytest

# Run specific test file
pytest tests/test_ingestion.py -v
```

---

## 🔗 Related Repositories

| Repository | Purpose |
|------------|---------|
| [satvik-svg/swastha-ai-hf](https://github.com/satvik-svg/swastha-ai-hf) | HuggingFace Docker Space — hosts the production backend |

---

## 📋 Hackathon Details

- **Hackathon**: IndiaAI × CDSCO Health Innovation Acceleration Hackathon
- **Ministry**: Electronics and IT (MeitY) / Central Drugs Standard Control Organisation
- **Problem Statement**: AI-Driven Regulatory Workflow Automation and Data Anonymisation
- **Target Portals**: SUGAM, MD Online
- **Key Frameworks**: DPDP Act 2023, ICMR Guidelines, NDHM Standards

---

*Developed for the CDSCO-IndiaAI Health Innovation Hackathon. All AI decisions require human reviewer verification before regulatory action.*
