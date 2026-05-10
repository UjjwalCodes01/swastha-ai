"""
Async MinIO client built on aiobotocore (boto3-compatible async wrapper).

Handles:
- Bucket creation with versioning and lifecycle policy on startup
- Server-side encryption (SSE-S3)
- Object upload with custom metadata
- Presigned URL generation (15-minute expiry)
- Health check
- Graceful shutdown
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import aiobotocore.session
import aiobotocore.config
from botocore.exceptions import ClientError, EndpointResolutionError

from app.config import get_settings

logger = logging.getLogger(__name__)

# Lifecycle policy: raw documents retained for 10 years (3650 days)
_LIFECYCLE_POLICY = {
    "Rules": [
        {
            "ID": "swastha-ai-raw-retention",
            "Status": "Enabled",
            "Filter": {"Prefix": "raw/"},
            "Expiration": {"Days": 3650},
            "NoncurrentVersionExpiration": {"NoncurrentDays": 3650},
        }
    ]
}

# SSE-S3 server-side encryption configuration
_SSE_CONFIG: dict[str, Any] = {
    "ServerSideEncryptionConfiguration": {
        "Rules": [
            {
                "ApplyServerSideEncryptionByDefault": {
                    "SSEAlgorithm": "AES256"
                },
                "BucketKeyEnabled": True,
            }
        ]
    }
}


class MinIOClient:
    """
    Async MinIO/S3 client wrapper.

    Usage:
        client = MinIOClient()
        await client.startup()   # call in FastAPI lifespan
        ...
        await client.shutdown()  # call on shutdown
    """

    def __init__(self) -> None:
        self._session = aiobotocore.session.get_session()
        self._settings = get_settings()
        self._client_context: Any = None
        self._client: Any = None

    async def startup(self) -> None:
        """
        Initialise the MinIO client, create buckets, and configure policies.
        Called once from the FastAPI lifespan handler.
        """
        endpoint_url = (
            f"{'https' if self._settings.minio_use_ssl else 'http'}://"
            f"{self._settings.minio_endpoint}"
        )
        self._client_context = self._session.create_client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=self._settings.minio_access_key,
            aws_secret_access_key=self._settings.minio_secret_key,
            region_name="us-east-1",  # MinIO ignores region, but boto3 needs one
            config=aiobotocore.config.AioConfig(  # type: ignore[attr-defined]
                connect_timeout=10,
                read_timeout=60,
                retries={"max_attempts": 3},
            ),
        )
        self._client = await self._client_context.__aenter__()

        bucket = self._settings.minio_bucket_raw
        await self._ensure_bucket(bucket)
        await self._enable_versioning(bucket)
        await self._set_lifecycle_policy(bucket)
        await self._enable_encryption(bucket)

        logger.info("MinIO client initialised", extra={"bucket": bucket})

    async def shutdown(self) -> None:
        """Gracefully close the MinIO client."""
        if self._client_context:
            try:
                await self._client_context.__aexit__(None, None, None)
            except Exception as exc:
                logger.error("MinIO client shutdown error", extra={"error": str(exc)})
        logger.info("MinIO client shutdown complete")

    async def _ensure_bucket(self, bucket: str) -> None:
        """Create the bucket if it does not already exist."""
        try:
            await self._client.head_bucket(Bucket=bucket)
            logger.debug("MinIO bucket exists", extra={"bucket": bucket})
        except ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            if error_code in ("404", "NoSuchBucket"):
                await self._client.create_bucket(Bucket=bucket)
                logger.info("MinIO bucket created", extra={"bucket": bucket})
            else:
                raise

    async def _enable_versioning(self, bucket: str) -> None:
        """Enable versioning on the bucket."""
        try:
            await self._client.put_bucket_versioning(
                Bucket=bucket,
                VersioningConfiguration={"Status": "Enabled"},
            )
            logger.info("MinIO versioning enabled", extra={"bucket": bucket})
        except ClientError as exc:
            logger.warning(
                "Could not enable MinIO versioning",
                extra={"bucket": bucket, "error": str(exc)},
            )

    async def _set_lifecycle_policy(self, bucket: str) -> None:
        """Set the 10-year retention lifecycle policy."""
        try:
            await self._client.put_bucket_lifecycle_configuration(
                Bucket=bucket,
                LifecycleConfiguration=_LIFECYCLE_POLICY,
            )
            logger.info("MinIO lifecycle policy set", extra={"bucket": bucket})
        except ClientError as exc:
            logger.warning(
                "Could not set MinIO lifecycle policy",
                extra={"bucket": bucket, "error": str(exc)},
            )

    async def _enable_encryption(self, bucket: str) -> None:
        """Enable SSE-S3 server-side encryption."""
        try:
            await self._client.put_bucket_encryption(
                Bucket=bucket,
                ServerSideEncryptionConfiguration=_SSE_CONFIG[
                    "ServerSideEncryptionConfiguration"
                ],
            )
            logger.info("MinIO SSE-S3 encryption enabled", extra={"bucket": bucket})
        except ClientError as exc:
            logger.warning(
                "Could not enable MinIO encryption",
                extra={"bucket": bucket, "error": str(exc)},
            )

    async def upload_object(
        self,
        *,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str,
        metadata: dict[str, str],
    ) -> str:
        """
        Upload a file to MinIO with metadata and SSE-S3 encryption.

        Returns the object key (storage path).
        If the key already exists, appends a counter suffix.
        """
        final_key = await self._resolve_unique_key(bucket, key)

        # Build put_object params
        put_params: dict[str, Any] = {
            "Bucket": bucket,
            "Key": final_key,
            "Body": data,
            "ContentType": content_type,
            "Metadata": metadata,
        }

        await self._client.put_object(**put_params)
        logger.info(
            "Object uploaded to MinIO",
            extra={"bucket": bucket, "key": final_key, "size_bytes": len(data)},
        )
        return final_key

    async def _resolve_unique_key(self, bucket: str, key: str) -> str:
        """
        Ensure the storage key is unique — never overwrite an existing object.

        If `key` exists, appends `_2`, `_3`, etc. before the extension.
        """
        try:
            await self._client.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "404":
                return key  # Key does not exist — safe to use
            raise

        # Key exists — find the next available suffix
        base, _, ext = key.rpartition(".")
        if not base:
            base, ext = key, ""

        counter = 2
        while True:
            candidate = f"{base}_{counter}.{ext}" if ext else f"{base}_{counter}"
            try:
                await self._client.head_object(Bucket=bucket, Key=candidate)
                counter += 1
            except ClientError as exc:
                if exc.response["Error"]["Code"] == "404":
                    logger.warning(
                        "MinIO key collision — using suffixed key",
                        extra={"original_key": key, "resolved_key": candidate},
                    )
                    return candidate
                raise

    async def generate_presigned_url(
        self,
        bucket: str,
        key: str,
        expires_in_seconds: int = 900,  # 15 minutes
    ) -> str:
        """
        Generate a presigned download URL valid for 15 minutes.

        The presigned URL does not expose credentials or require the caller
        to have MinIO access — it's safe to pass to authenticated users.
        """
        url: str = await self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in_seconds,
        )
        return url

    async def health_check(self) -> bool:
        """
        Return True if MinIO is reachable and the raw bucket exists.
        Used by the /health endpoint.
        """
        try:
            await self._client.head_bucket(Bucket=self._settings.minio_bucket_raw)
            return True
        except Exception as exc:
            logger.warning("MinIO health check failed", extra={"error": str(exc)})
            return False


# Module-level singleton
_minio_client: MinIOClient | None = None


async def get_minio_client() -> MinIOClient:
    global _minio_client
    if _minio_client is None:
        raise RuntimeError("MinIO client not initialised. Call startup() first.")
    return _minio_client


async def init_minio() -> MinIOClient:
    global _minio_client
    _minio_client = MinIOClient()
    await _minio_client.startup()
    return _minio_client


async def close_minio() -> None:
    global _minio_client
    if _minio_client:
        await _minio_client.shutdown()
        _minio_client = None
