#!/bin/bash

echo "Starting Redis..."
redis-server --daemonize yes --requirepass swastha_ai_redis_secret

echo "Starting MinIO..."
mkdir -p /data/minio
export MINIO_ROOT_USER=swastha_ai_minio
export MINIO_ROOT_PASSWORD=swastha_ai_minio_secret
minio server /data/minio --address "127.0.0.1:9000" --console-address "127.0.0.1:9001" &

# Wait for databases to boot up
sleep 5

echo "Starting SwasthaAI FastAPI..."
# Note: Hugging Face exposes port 7860 to the outside world
uvicorn app.main:app --host 0.0.0.0 --port 7860