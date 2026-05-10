"""Storage helpers for Layer 3 JSON artefacts."""

from __future__ import annotations

import json
from typing import Any

PROCESSED_BUCKET = "swastha-ai-processed-documents"


async def load_json(minio_client: Any, path: str, bucket: str = PROCESSED_BUCKET) -> dict[str, Any]:
    """Load a JSON artefact from MinIO/S3."""
    # Support both raw aiobotocore client and our MinIOClient wrapper
    client = getattr(minio_client, '_client', minio_client)
    response = await client.get_object(Bucket=bucket, Key=path)
    raw = await response["Body"].read()
    return json.loads(raw.decode("utf-8"))


async def save_json(
    minio_client: Any,
    path: str,
    payload: dict[str, Any],
    bucket: str = PROCESSED_BUCKET,
) -> str:
    """Persist a JSON artefact and return its object key."""
    # Support both raw aiobotocore client and our MinIOClient wrapper
    client = getattr(minio_client, '_client', minio_client)
    try:
        await client.head_bucket(Bucket=bucket)
    except Exception:
        await client.create_bucket(Bucket=bucket)

    await client.put_object(
        Bucket=bucket,
        Key=path,
        Body=json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
        ContentType="application/json",
    )
    return path


def chunks_to_text(processed_json: dict[str, Any]) -> str:
    """Convert the Layer 1 processed JSON shape into a plain text body."""
    chunks = processed_json.get("chunks") or []
    if chunks:
        return "\n\n".join(str(chunk.get("text", "")).strip() for chunk in chunks if chunk.get("text"))
    return str(processed_json.get("text") or processed_json.get("total_text") or "")
