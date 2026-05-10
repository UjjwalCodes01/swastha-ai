# SwasthaAI — CDSCO Regulatory AI Platform

> **Revolutionising CDSCO regulatory processing with an enterprise-grade, 7-layer AI architecture.**  
> Winner/Entry for the CDSCO-IndiaAI Health Innovation Hackathon.

SwasthaAI automates the ingestion, PII-anonymisation, and intelligent analysis of critical regulatory documents including Drug Submissions, Medical Device Applications, Clinical Trial Protocols, and SAE Reports.

---

## 🚀 Key Features
- **Intelligent Summarisation:** Uses Google Gemini 1.5 & Llama 3 to generate structured executive summaries.
- **Data Privacy First:** Automated Indian PII/PHI scrubbing (Aadhaar, PAN, CIN) via Microsoft Presidio.
- **Automated Compliance:** Instant checks against DPDP Act 2023, ICMR, and NDHM standards.
- **7-Layer Architecture:** Decoupled, scalable design from Ingestion to Output.
- **High-Performance Backend:** FastAPI with asynchronous database operations (Supabase/asyncpg).

---

## 🛠️ Local Development Setup

Follow these steps to get the full platform running on your local machine.

### 1. Prerequisites
- **Python 3.11+**
- **Node.js 18+** & **npm**
- **Docker Desktop** (Required for local MinIO and Redis)

---

### 2. Backend Setup (FastAPI)

Open your terminal and navigate to the backend folder:

```powershell
# 1. Navigate to backend
cd swastha-ai

# 2. Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
pip install "numpy<2.0.0" groq google-generativeai

# 4. Set up environment variables
# Copy .env.example to .env and fill in your Supabase & Gemini keys
cp .env.example .env

# 5. Start supporting services (MinIO/Redis)
docker-compose up -d minio redis

# 6. Run the backend server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Backend is now live at: `http://localhost:8000/docs`

---

### 3. Frontend Setup (React + Vite)

Open a **new** terminal window:

```powershell
# 1. Navigate to frontend
cd swastha-ai/frontend

# 2. Install dependencies
npm install

# 3. Set up environment variables
# Create a .env file and add:
# VITE_API_BASE_URL=http://localhost:8000
# VITE_API_KEY=your_generated_api_key
echo "VITE_API_BASE_URL=http://localhost:8000" > .env
echo "VITE_API_KEY=b7f8c9d2a1e43b56c7d8e9f0a1b2c3d4" >> .env

# 4. Start the development server
npm run dev
```

Frontend is now live at: `http://localhost:5173`

---

## 📂 Project Structure
- `/app`: FastAPI backend source code (Layers 0-4).
- `/frontend`: React dashboard source code (Layer 6).
- `/alembic`: Database migration scripts.
- `/kafka`: Avro schemas and Kafka configuration.
- `DOCUMENTATION.md`: Full technical history and changelog.

---

## 🧪 Testing the AI
1. Login to the dashboard at `http://localhost:5173`.
2. Click **"Upload Document"**.
3. Select **"SAE Report"** or **"Drug Submission"**.
4. Upload a PDF and watch the real-time AI analysis.

---

## 🛡️ Security
- **Immutability:** Audit logs are append-only; `UPDATE` and `DELETE` are revoked at the database level.
- **Authentication:** All requests are secured via `X-API-Key` headers and Keycloak-ready JWT handlers.
- **Anonymisation:** All data is scrubbed of PII before reaching the LLM or being stored for reviewers.

---

**Developed for the CDSCO Health Innovation Hackathon.**
