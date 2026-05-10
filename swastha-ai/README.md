# SwasthaAI Layer 0 - Portal Ingestion Layer

Production-grade document ingestion for the CDSCO regulatory AI platform. This layer runs inside the `swastha-ai` project workspace under `swastha-ai/`.

## Prerequisites

- Python 3.11+
- Docker and Docker Compose
- `libmagic` installed on the host for MIME detection
- Enough local disk for PostgreSQL, MinIO, Kafka, Redis, and Keycloak volumes

## Start The Stack

```bash
cd swastha-ai
cp .env.example .env
docker compose up --build
```

The FastAPI app listens on `http://localhost:8000`.

Useful consoles:

- FastAPI docs: `http://localhost:8000/docs`
- MinIO console: `http://localhost:9001`
- Keycloak admin: `http://localhost:8080`

## Run Tests

Lightweight unit tests use `requirements-test.txt` so contributors can run them
on Windows, macOS, or Linux without installing the full OCR/ML stack.

Windows PowerShell:

```powershell
cd swastha-ai
python scripts\bootstrap_test_env.py
python scripts\run_tests.py tests\ai_core tests\compliance
```

macOS/Linux:

```bash
cd swastha-ai
python3 scripts/bootstrap_test_env.py
python3 scripts/run_tests.py tests/ai_core tests/compliance
```

The tests use mocked MinIO, Kafka, Redis, and JWT verification plus an async SQLite database.

## API Endpoints

- `POST /api/v1/ingest/submission` - upload one document. Requires `admin`, `portal_operator`, or `api_client`.
- `POST /api/v1/ingest/bulk` - upload a ZIP with `manifest.json`. Requires `admin`.
- `GET /api/v1/ingest/status/{doc_id}` - view submission status. Requires any authenticated role.
- `DELETE /api/v1/ingest/submission/{doc_id}` - soft reject a submission. Requires `admin`.
- `GET /api/v1/ingest/health` - public dependency health check.

Authentication supports Keycloak Bearer JWTs and `X-API-Key` for machine clients.

## Environment Variables

All runtime configuration is loaded from environment variables. See `.env.example` for the full list and explanations, including:

- `DATABASE_URL`
- `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET_RAW`
- `KAFKA_BOOTSTRAP_SERVERS`
- `REDIS_URL`
- `KEYCLOAK_URL`, `KEYCLOAK_REALM`, `KEYCLOAK_CLIENT_ID`
- `MAX_UPLOAD_SIZE_MB`, `ALLOWED_MIME_TYPES`
- `RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_USER_PER_MINUTE`, `RATE_LIMIT_API_CLIENT_PER_MINUTE`
- `SECRET_KEY`
- `API_KEYS`
- `SUGAM_API_BASE_URL`, `SUGAM_API_KEY`
- `MD_ONLINE_API_BASE_URL`, `MD_ONLINE_API_KEY`

Do not commit `.env`.

## Keycloak First User

1. Start the stack with `docker compose up --build`.
2. Open `http://localhost:8080`.
3. Sign in with `KEYCLOAK_ADMIN_USER` and `KEYCLOAK_ADMIN_PASSWORD` from `.env`.
4. Create the `swastha-ai` realm if it is not already present.
5. Create a client named `swastha-ai-api`.
6. Add client roles: `admin`, `reviewer`, `portal_operator`, `api_client`.
7. Create the first user, set credentials, and assign the `admin` client role.

For machine-to-machine portal ingestion, configure `API_KEYS` as:

```text
some-long-random-key:api_client:sugam-portal
```
