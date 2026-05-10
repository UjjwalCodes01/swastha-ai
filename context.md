# SwasthaAI — Full Project Context

> **CDSCO-IndiaAI Health Innovation Hackathon Solution**
> AI-powered regulatory document processing platform for India's Central Drugs Standard Control Organisation

---

## 1. What Is This Project?

SwasthaAI is an **end-to-end AI platform** that automates the processing of regulatory submissions for CDSCO (India's FDA equivalent). It ingests documents from government portals (SUGAM, MD Online), processes them through an AI pipeline (anonymisation, summarisation, classification, comparison), and delivers results to CDSCO reviewers via a dashboard and API.

The platform is designed for the **CDSCO-IndiaAI Health Innovation Hackathon** — a two-stage competition where Stage 2 requires an **on-premises deployment** (no internet).

---

## 2. Architecture Overview (7 Layers)

The architecture SVG ([cdsco_indiaai_full_architecture.svg](file:///c:/Users/sriva/Desktop/swastha-ai/cdsco_indiaai_full_architecture.svg)) defines these layers:

| Layer | Name | Purpose | Status |
|-------|------|---------|--------|
| **0** | Portal Ingestion | Accept docs from SUGAM, MD Online, manual upload, SAE feed | ✅ **BUILT** |
| **1** | Pre-processing Engine | OCR, format parsing, chunking, embedding generation | ✅ **BUILT** |
| **2** | Message Bus (Kafka) | Async event-driven pipeline with retry/DLQ | ✅ **BUILT** |
| **3** | AI Core (4 Modules) | Anonymisation, Summarisation, Classification, Comparison | ❌ **NOT BUILT** |
| **4** | Compliance & Governance | DPDP Act, ICMR, NDHM checks, XAI audit | ❌ **NOT BUILT** |
| **5** | Data Storage | PostgreSQL, MinIO, ChromaDB, Redis (partially built) | 🟡 **PARTIAL** |
| **6** | Output & Delivery | Reviewer dashboard, PDF reports, REST/webhook API, notifications | ❌ **NOT BUILT** |

**Cross-cutting (Footer):** Infrastructure (FastAPI, Docker, K8s, Nginx), Security (JWT, TLS, RBAC, VAPT), Observability (Prometheus, Grafana, ELK, Sentry)

---

## 3. Folder Structure

```
swastha-ai/                              # Git repo root
├── cdsco_indiaai_full_architecture.svg  # Full system architecture diagram (SVG)
├── dependencies                         # List of ALL dependencies/tools needed
├── layers                               # Detailed description of each architectural layer
│
└── swastha-ai/                          # Python application root
    ├── .env.example                     # Environment variable template (all config)
    ├── .gitignore
    ├── Dockerfile                       # Single-stage Python container
    ├── README.md                        # Project README
    ├── requirements.txt                 # All Python dependencies (Layers 0-2)
    ├── pytest.ini                       # Pytest configuration
    ├── alembic.ini                      # Alembic migration config
    │
    ├── docker-compose.yml               # Layer 0 infra (Postgres, MinIO, Kafka, Redis, Keycloak, App)
    ├── docker-compose.layer1.yml        # Layer 1 infra (preprocessing workers)
    ├── docker-compose.layer2.yml        # Layer 2 infra (KRaft Kafka, Schema Registry, Kafka-UI, Prometheus, Grafana)
    ├── grafana_dashboard.json           # Pre-built Grafana dashboard
    ├── prometheus_alerts.yml            # Prometheus alerting rules
    │
    ├── alembic/                         # Database migrations
    │   ├── env.py                       # Alembic environment (async support)
    │   └── versions/
    │       ├── 0001_initial_schema.py          # Users, submissions, audit_log, rate_limit tables
    │       ├── 0002_layer1_preprocessing_tables.py  # document_chunks, document_processing_log
    │       └── 0003_layer2_messagebus_tables.py     # event_store, dlq_events, consumer_state
    │
    ├── kafka/                           # Kafka configuration & setup
    │   ├── config/
    │   │   ├── broker.properties        # KRaft broker config
    │   │   └── topic_configs.py         # Topic definitions (partitions, retention, cleanup)
    │   ├── schemas/                     # Avro schemas for ALL events
    │   │   ├── raw_document_ingested.avsc
    │   │   ├── document_preprocessed.avsc
    │   │   ├── document_anonymised.avsc
    │   │   ├── document_summarised.avsc
    │   │   ├── document_classified.avsc
    │   │   ├── document_comparison_requested.avsc
    │   │   ├── document_chunks_ready.avsc
    │   │   ├── report_generated.avsc
    │   │   ├── notification_event.avsc
    │   │   └── dlq_event.avsc
    │   └── setup/
    │       ├── create_topics.py         # Auto-create Kafka topics on startup
    │       ├── create_schemas.py        # Register Avro schemas in Schema Registry
    │       └── verify_setup.py          # Health check after init
    │
    ├── app/                             # Main application code
    │   ├── main.py                      # FastAPI app entry point, lifespan management
    │   ├── config.py                    # Pydantic Settings (all env vars)
    │   ├── dependencies.py              # FastAPI dependency injection (Redis, DB sessions)
    │   │
    │   ├── adapters/                    # Layer 0: Portal adapters
    │   │   ├── base_adapter.py          # Abstract base for all portal adapters
    │   │   ├── sugam_adapter.py         # SUGAM portal poller (drug/CT submissions)
    │   │   └── md_online_adapter.py     # MD Online portal poller (medical devices)
    │   │
    │   ├── ingestion/                   # Layer 0: Document ingestion pipeline
    │   │   ├── router.py                # FastAPI routes (/api/v1/ingest/*)
    │   │   ├── schemas.py               # Pydantic request/response models
    │   │   ├── service.py               # 8-step ingestion pipeline orchestrator
    │   │   └── validators.py            # File validation (MIME, zip bomb, XXE, PDF encryption)
    │   │
    │   ├── auth/                        # Authentication & authorization
    │   │   ├── jwt_handler.py           # Keycloak JWT verification, JWKS caching
    │   │   └── rbac.py                  # Role-based access control decorators
    │   │
    │   ├── audit/                       # Tamper-evident audit logging
    │   │   └── logger.py                # Chain-hashed audit log (SHA-256 linked entries)
    │   │
    │   ├── db/                          # Database layer
    │   │   ├── connection.py            # Async SQLAlchemy engine + session factory
    │   │   └── models.py                # ORM models (User, Submission, AuditLog, RateLimitViolation)
    │   │
    │   ├── storage/                     # Object storage
    │   │   ├── document_store.py        # High-level document storage abstraction
    │   │   └── minio_client.py          # MinIO/S3 client (buckets, SSE-S3, retention)
    │   │
    │   ├── queue/                       # Layer 0 Kafka producer
    │   │   ├── kafka_producer.py        # Simple Kafka producer with local queue fallback
    │   │   └── topics.py                # Topic name constants
    │   │
    │   ├── middleware/                   # HTTP middleware
    │   │   ├── request_id.py            # X-Request-ID injection (contextvar-based)
    │   │   └── security_headers.py      # Security headers (CSP, HSTS, X-Frame-Options)
    │   │
    │   ├── rate_limit/                  # Rate limiting
    │   │   └── limiter.py               # Sliding window rate limiter (Redis-backed)
    │   │
    │   ├── messagebus/                  # Layer 2: Full message bus infrastructure
    │   │   ├── __init__.py
    │   │   ├── producer.py              # Resilient producer (circuit breaker, Redis buffer, fallback file)
    │   │   ├── consumer.py              # Base consumer (manual commit, Avro deser, retry/DLQ)
    │   │   ├── circuit_breaker.py       # Circuit breaker pattern implementation
    │   │   ├── retry_handler.py         # Exponential backoff retry logic
    │   │   ├── dlq_handler.py           # Dead letter queue management + replay
    │   │   ├── event_store.py           # PostgreSQL event store (every message recorded)
    │   │   ├── schema_registry.py       # Avro schema registry client (serialize/deserialize)
    │   │   ├── consumer_monitor.py      # Consumer lag monitoring + Prometheus metrics
    │   │   ├── health.py                # Message bus health API (FastAPI sub-app)
    │   │   └── admin.py                 # Admin API (topic management, DLQ replay, consumer state)
    │   │
    │   └── preprocessing/              # Layer 1: Document preprocessing
    │       ├── __init__.py
    │       ├── pipeline.py              # 12-step preprocessing orchestrator
    │       ├── consumer.py              # Kafka consumer that triggers preprocessing
    │       ├── publisher.py             # Publishes "document_preprocessed" events
    │       ├── ocr/
    │       │   ├── ocr_engine.py        # Tesseract OCR with async concurrency
    │       │   └── image_preprocessor.py # Image cleanup before OCR (deskew, denoise, threshold)
    │       ├── extractors/
    │       │   ├── base_extractor.py    # Abstract extractor + ExtractionResult dataclass
    │       │   ├── pdf_extractor.py     # PyMuPDF-based PDF text/table/image extraction
    │       │   ├── docx_extractor.py    # python-docx DOCX extractor
    │       │   ├── xml_extractor.py     # defusedxml XML extractor
    │       │   ├── csv_extractor.py     # pandas CSV/TSV extractor with PII detection
    │       │   └── tika_extractor.py    # Apache Tika fallback extractor
    │       ├── chunker/
    │       │   ├── semantic_chunker.py  # Section-aware semantic chunking
    │       │   └── table_chunker.py     # Table-specific chunking
    │       ├── embedder/
    │       │   ├── embedding_service.py # sentence-transformers embedding generation
    │       │   └── chroma_store.py      # ChromaDB vector storage
    │       ├── metadata/
    │       │   ├── extractor.py         # Regulatory metadata extraction (dates, IDs, form fields)
    │       │   └── language_detector.py # Language detection (langdetect)
    │       └── normaliser/
    │           └── text_normaliser.py   # Unicode normalization, whitespace cleanup, encoding fixes
    │
    └── tests/                           # Test suite
        ├── conftest.py                  # Shared fixtures (mock DB, MinIO, Kafka, Redis)
        ├── test_adapters.py             # Portal adapter tests
        ├── test_auth.py                 # JWT + RBAC tests
        ├── test_ingestion.py            # Ingestion pipeline tests
        ├── test_validators.py           # File validation tests
        ├── preprocessing/
        │   ├── test_extractors.py       # Extractor tests
        │   ├── test_metadata_extractor.py
        │   ├── test_pipeline.py         # Full pipeline tests
        │   └── test_table_chunker.py
        └── messagebus/
            ├── conftest.py              # Messagebus test fixtures
            ├── test_producer.py
            ├── test_consumer.py
            ├── test_circuit_breaker.py
            ├── test_consumer_monitor.py
            ├── test_dlq_handler.py
            ├── test_event_store.py
            ├── test_retry_handler.py
            └── test_schema_registry.py
```

---

## 4. What Has Been Built (Detailed)

### ✅ Layer 0 — Portal Ingestion (COMPLETE)

The entire ingestion layer is production-grade:

- **8-step ingestion pipeline** (`service.py`): validate → checksum → virus scan → generate doc_id → store MinIO → write DB → publish Kafka → audit log
- **File validation** (`validators.py`): MIME type via magic bytes, zip bomb detection, XXE injection prevention, PDF encryption check, filename sanitization
- **Portal adapters**: SUGAM (`sugam_adapter.py`) and MD Online (`md_online_adapter.py`) background pollers with abstract base class
- **Bulk upload**: ZIP manifest-based batch ingestion
- **Auth**: Keycloak JWT verification with JWKS caching + API key auth for M2M
- **RBAC**: 4 roles — admin, reviewer, portal_operator, api_client
- **Audit log**: Chain-hashed (SHA-256 linked) append-only log in PostgreSQL
- **Object storage**: MinIO with SSE-S3 encryption, versioning, 10-year retention
- **Rate limiting**: Redis sliding window per IP + per user, progressive IP blocking
- **Middleware**: Request ID injection, security headers (CSP, HSTS, X-Frame-Options)
- **Docker**: Full compose with PostgreSQL 15, MinIO, Kafka, Redis, Keycloak

### ✅ Layer 1 — Pre-processing Engine (COMPLETE)

Full 12-step preprocessing pipeline:

- **Format detection**: Magic bytes MIME detection
- **5 extractors**: PDF (PyMuPDF), DOCX (python-docx), XML (defusedxml), CSV (pandas), Tika (fallback)
- **OCR engine**: Tesseract with async concurrency, image preprocessing (deskew, denoise, binarize)
- **Language detection**: langdetect with multilingual flag
- **Text normalization**: Unicode NFC, whitespace cleanup, encoding fixes, change logging
- **Metadata extraction**: Dates, IDs, form fields, regulatory-specific fields
- **Table extraction**: PDF tables via PyMuPDF, CSV tables via pandas
- **Semantic chunking**: Section-aware chunking with linked-list pointers between chunks
- **Embedding generation**: sentence-transformers (all-mpnet-base-v2)
- **ChromaDB storage**: Vector embeddings stored per collection
- **Processed JSON in MinIO**: Full extraction results persisted
- **Quality assessment**: Confidence scoring, human review flags

### ✅ Layer 2 — Message Bus (COMPLETE)

Enterprise-grade Kafka infrastructure:

- **KRaft mode**: Zookeeper-free Kafka 3.6 (single-node dev, 3-node prod ready)
- **Avro schemas**: 10 event types defined (ingested, preprocessed, anonymised, summarised, classified, comparison, chunks_ready, report, notification, DLQ)
- **Schema Registry**: Confluent Schema Registry with BACKWARD compatibility
- **Resilient producer**: Circuit breaker, Redis buffering (1000 msg), local file fallback
- **Base consumer**: Manual commits, Avro deserialization, retry topics, DLQ
- **Retry handler**: Exponential backoff with configurable max retries (3)
- **DLQ handler**: Dead letter queue with PostgreSQL persistence + replay API
- **Event store**: Every message recorded in PostgreSQL
- **Consumer monitor**: Lag tracking + Prometheus metrics
- **Health API**: Kafka cluster health endpoint
- **Admin API**: Topic management, DLQ replay, consumer state
- **Kafka-UI**: Web UI for dev (Provectus kafka-ui)
- **Docker compose**: Schema Registry, Kafka-UI, init container, Prometheus, Grafana
- **Alerting**: Prometheus alert rules defined

### 🟡 Layer 5 — Data Storage (PARTIAL)

- ✅ **PostgreSQL**: Users, submissions, audit_log, rate_limit_violations, document_chunks, document_processing_log, event_store, dlq_events, consumer_state — all via Alembic migrations
- ✅ **MinIO**: Raw document storage + processed JSON storage
- ✅ **ChromaDB**: Vector embeddings for chunks
- ✅ **Redis**: Rate limiting, session caching, Kafka buffer, retry counters
- ❌ **Audit log immutability at DB level**: UPDATE/DELETE revocation not yet applied via migration

---

## 5. What Remains To Be Built

### ❌ Layer 3 — AI Core (4 Modules) — THE BIGGEST GAP

> [!IMPORTANT]
> This is the most critical remaining work. The Kafka events and Avro schemas are already defined — these modules just need to consume events and produce results.

#### Module 1: Data Anonymisation
- Microsoft Presidio integration for PII/PHI detection
- Custom recognizers for Indian identifiers (Aadhaar, CIN, Indian phone)
- spaCy NER (en_core_web_lg) for names and locations
- Consistent pseudonymization (not just redaction)
- Audit log of what was redacted
- **Consumes**: `swastha.documents.preprocessed`
- **Produces**: `swastha.documents.anonymised`

#### Module 2: Document Summarisation
- LLM integration (Claude API primary, Ollama/Llama 3 offline fallback)
- Pydantic schema enforcement per document type
- Map-reduce for large documents
- Prompt templates for drug, device, SAE submissions
- RAG retriever using ChromaDB/FAISS
- **Consumes**: `swastha.documents.anonymised`
- **Produces**: `swastha.documents.summarised`

#### Module 3: Assessment & Classification
- **Completeness checker**: Rule engine per form type (deterministic, not AI)
- **SAE severity classifier**: Fine-tuned BERT on CDSCO severity categories
- **Duplicate detector**: FAISS similarity search against stored embeddings
- **Scorecard generator**: Per-submission quality report
- **Consumes**: `swastha.documents.anonymised`
- **Produces**: `swastha.documents.classified`

#### Module 4: Comparison & Reporting
- **Version diff**: Deterministic text diff between document versions
- **Change narrator**: LLM explains regulatory significance of changes
- **Report builder**: CDSCO-format inspection report generation
- **Template library**: CDSCO standard form templates
- **Consumes**: `swastha.documents.comparison_requested`
- **Produces**: `swastha.reports.generated`

### ❌ Layer 4 — Compliance & Governance

- DPDP Act 2023 compliance checker (data privacy validation)
- ICMR guidelines validator (clinical trial rules)
- NDHM standards checker (health data protocol)
- XAI (Explainable AI) logger — every AI decision logged with confidence, model used, input/output
- Post-processing PII leak detection (verify anonymiser didn't miss anything)
- Mandatory human review routing (death/life-threatening SAEs)
- Immutable audit log chain verification

### ❌ Layer 6 — Output & Delivery

- **Reviewer Dashboard** (React frontend):
  - Case management queue (like Jira for submissions)
  - AI analysis display with reviewer override capability
  - Override logging for model improvement feedback loop
  - Analytics charts (Recharts/ECharts): submission volumes, SAE trends, completeness distributions
  - TanStack Query for API integration
- **PDF Report Generation**: WeasyPrint HTML→PDF with CDSCO templates
- **REST/Webhook API**: Callbacks to SUGAM/MD Online when cases are processed
- **Notifications**: Email (SendGrid/AWS SES), SMS (MSG91), Push (FCM)

### ❌ Infrastructure Gaps

- **Keycloak realm setup**: Realm, clients, roles not configured (only Docker container runs)
- **Nginx reverse proxy**: Not configured
- **CI/CD**: GitHub Actions pipeline not created
- **Kubernetes manifests**: No K8s deployment files
- **ELK Stack**: Not deployed (Elasticsearch, Logstash, Kibana)
- **Sentry**: Not integrated
- **HashiCorp Vault**: Secrets still in env vars, not Vault
- **Let's Encrypt/Certbot**: No TLS setup
- **VAPT testing**: Not performed

### ❌ External Integrations

- SUGAM Portal API (real credentials needed from CDSCO)
- MD Online Portal API (real credentials needed)
- DigiLocker API (verified document submission)
- Aadhaar Offline KYC API
- India Post Pincode API (address validation)

### ❌ Training & Fine-tuning

- BERT SAE classifier fine-tuning (needs labelled data from CDSCO)
- Embedding model fine-tuning on PubMed corpus
- W&B experiment tracking setup

---

## 6. Technology Stack Summary

| Category | Tools | Status |
|----------|-------|--------|
| **Backend** | FastAPI, Uvicorn, Pydantic | ✅ In use |
| **Database** | PostgreSQL 15, SQLAlchemy 2.0, Alembic, asyncpg | ✅ In use |
| **Object Storage** | MinIO (S3-compatible), aiobotocore | ✅ In use |
| **Message Bus** | Apache Kafka (KRaft), Avro, Schema Registry | ✅ In use |
| **Cache** | Redis 7 | ✅ In use |
| **Auth** | Keycloak 24, python-jose, JWT | ✅ In use (container only) |
| **Document Processing** | PyMuPDF, Tesseract, pdfplumber, python-docx, defusedxml | ✅ In use |
| **NLP/ML** | sentence-transformers, langdetect, ChromaDB | ✅ In use |
| **LLM** | Anthropic Claude, Ollama (Llama 3) | ❌ Not integrated |
| **Anonymisation** | Microsoft Presidio, spaCy | ❌ Not integrated |
| **Classification** | HuggingFace Transformers (BERT), FAISS | ❌ Not integrated |
| **Orchestration** | LangChain | ❌ Not integrated |
| **Frontend** | React, TanStack Query, Recharts | ❌ Not built |
| **PDF Reports** | WeasyPrint | ❌ Not integrated |
| **Monitoring** | Prometheus, Grafana | 🟡 Config exists, not fully wired |
| **Logging** | Structured JSON logging | ✅ In use |
| **Containers** | Docker, Docker Compose | ✅ In use |
| **Testing** | pytest, pytest-asyncio, factory-boy | ✅ In use |

---

## 7. Build Priority Roadmap

> [!TIP]
> The Kafka schemas for ALL downstream events already exist. Each AI module is a new Kafka consumer that processes messages and publishes results to the next topic in the chain.

### Phase 1 — Core AI Modules (Highest Priority)
1. **Module 1: Anonymisation** — Presidio + spaCy + Indian PII recognizers
2. **Module 2: Summarisation** — Claude/Ollama + prompt templates + schema enforcement
3. **Module 3: Classification** — Rule engine + BERT classifier + FAISS dedup
4. **Module 4: Comparison** — Text diff + LLM narrator + report templates

### Phase 2 — Compliance & Output
5. **Layer 4: Compliance checks** — DPDP, ICMR, XAI logging
6. **PDF report generation** — WeasyPrint templates
7. **Reviewer Dashboard** — React frontend

### Phase 3 — Production Hardening
8. Keycloak realm configuration
9. Nginx + TLS
10. CI/CD pipeline
11. Kubernetes manifests
12. ELK + Sentry integration
13. HashiCorp Vault for secrets

---

## 8. Key Design Decisions

- **Event-driven architecture**: Every module communicates via Kafka events — no direct module-to-module calls
- **Offline-first**: Ollama fallback for LLM when internet unavailable (Stage 2 on-premises)
- **Append-only audit**: Chain-hashed logs with DB-level UPDATE/DELETE revocation
- **Consistent pseudonyms**: Anonymisation produces readable pseudonyms, not blank redactions
- **Schema enforcement**: LLM outputs validated against Pydantic schemas with retry on failure
- **Supabase-compatible**: Database URL configured for both Supabase pooler (port 6543) and direct connection (port 5432)
