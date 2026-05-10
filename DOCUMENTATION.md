# SwasthaAI — Project Documentation & History

> **CDSCO-IndiaAI Health Innovation Hackathon Solution**  
> AI-powered regulatory document processing platform for India's Central Drugs Standard Control Organisation.

---

## 1. Project Overview
SwasthaAI is an enterprise-grade platform designed to automate the ingestion, analysis, and compliance-checking of CDSCO regulatory documents (Drug Submissions, Medical Device Applications, Clinical Trial Protocols, and SAE Reports).

The system uses an **"Offline-First"** approach, capable of running on local infrastructure or high-performance cloud environments, while utilizing cutting-edge LLMs (Gemini, Llama 3) for intelligence.

## 2. Technical Architecture
The project follows a modular **7-Layer Architecture**:

*   **Layer 0 (Ingestion):** FastAPI portal handling virus scanning, checksumming, and ingestion.
*   **Layer 1 (Preprocessing):** OCR (Tesseract), semantic chunking, and vector embedding generation (PyTorch/sentence-transformers).
*   **Layer 2 (Message Bus):** Kafka-driven orchestration (simulated via in-process queue in cloud deployment).
*   **Layer 3 (AI Core):** PII Anonymisation (Microsoft Presidio), LLM Summarisation (Gemini/Groq), and Risk Classification.
*   **Layer 4 (Compliance):** Automated assessment against DPDP Act 2023, ICMR guidelines, and NDHM standards.
*   **Layer 5 (Storage):** Supabase (PostgreSQL), MinIO (S3 Object Storage), and ChromaDB (Vector Store).
*   **Layer 6 (Output):** React Reviewer Dashboard and PDF Report generation.

---

## 3. Deployment Strategy (24/7 Cloud)
To support a high-memory AI backend for free, we use a **Hybrid Cloud Strategy**:

### **A. Backend (Hugging Face Spaces)**
*   **Provider:** Hugging Face (Docker Space).
*   **Resources:** 16GB RAM / 2 vCPU.
*   **Implementation:** A "Super Container" running Redis, MinIO, and FastAPI together.
*   **AI Models:** Connects to Google Gemini 1.5 Flash (via API) for high-token context processing.

### **B. Frontend (Vercel)**
*   **Provider:** Vercel (Production Build).
*   **Integration:** Configured with `VITE_API_BASE_URL` pointing to the Hugging Face Space.

### **C. Database (Supabase)**
*   **Provider:** Supabase (PostgreSQL 15).
*   **Connection:** Uses the **Session Pooler (Port 5432)** to support high-performance `asyncpg` calls over IPv4.

---

## 4. Changelog: Bugs Fixed & Improvements
During the development phase, the following critical issues were resolved:

### **Infrastructure & Backend Fixes**
1.  **Windows Logging Crash:** Fixed `ValueError: Invalid format string` by replacing `time.strftime` with `datetime.strftime` to support microseconds (`%f`) on Windows.
2.  **ChromaDB Client Bug:** Updated the adapter to use the synchronous `HttpClient` wrapped in `asyncio.to_thread`, resolving the removal of `AsyncHttpClient` in ChromaDB 0.5.0.
3.  **NumPy 2.0 Incompatibility:** Downgraded NumPy to `<2.0.0` to fix the `AttributeError: np.float_` crash that was killing the background consumer.
4.  **Supabase IPv6 Routing:** Fixed `[Errno 101] Network is unreachable` on Hugging Face by switching to the Supabase **IPv4 Session Pooler (Port 5432)**.
5.  **Supabase Transaction Error:** Fixed `InvalidSQLStatementNameErr` by bypassing the transaction pooler and using a direct session connection.
6.  **MinIO KMS Failure:** Fixed 500 Error by disabling hardcoded Server-Side Encryption (SSE-S3) in `minio_client.py`, as the free-tier setup lacks an external KMS.

### **AI & Pipeline Improvements**
1.  **Gemini 1.5 Integration:** Added full support for Google Gemini to handle large documents (like 38-page PDFs) that exceeded the token limits of Llama 3/Groq.
2.  **End-to-End Local Flow:** Modified `PreprocessorConsumer` to invoke AI Core and Compliance layers sequentially, allowing the full pipeline to run without a live Kafka broker.
3.  **JSONB Encoding Refactor:** Refactored AI and Compliance persistence to use **SQLAlchemy ORM models** instead of raw SQL, fixing encoding errors with PostgreSQL JSONB columns.

### **Frontend & UX Enhancements**
1.  **Direct Ingestion UI:** Added a prominent "Upload Document" button and Modal to the dashboard, removing the need for Swagger UI uploads.
2.  **Visual Overhaul:** Improved spacing, badge alignments, and added "Glassmorphism" styling to the Reviewer Dashboard.
3.  **Zero-Dependency PDF Export:** Replaced `WeasyPrint` (which required native GTK libraries) with a pure-Python raw PDF generator, enabling PDF downloads on all platforms.
4.  **Secure API Auth:** Implemented `VITE_API_KEY` environment variable support to securely communicate between Vercel and Hugging Face without hardcoding keys.

---

## 5. Information for Developers
*   **Local Setup:** Copy `.env.example` to `.env`. Requires Docker for local ChromaDB/PostgreSQL testing.
*   **Adding AI Models:** To add a new LLM, update `app/ai_core/llm_client.py`. The system defaults to Gemini -> Groq -> Claude -> Offline.
*   **Compliance Rules:** New regulatory checks can be added as classes in `app/compliance/`.
*   **Security:** `SECRET_KEY` must be a 64-char string. Database `UPDATE/DELETE` is revoked on the `audit_log` table for immutability.
