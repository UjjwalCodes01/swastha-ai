FROM python:3.11-slim

# Install system dependencies for python-magic, PyMuPDF, and ClamAV client
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    curl \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create log directory
RUN mkdir -p /var/log/swastha-ai && chmod 755 /var/log/swastha-ai

# Run Alembic migrations then start the app
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --log-config /dev/null"]
