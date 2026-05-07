"""
Embedding Service using sentence-transformers.

Uses: sentence-transformers/all-mpnet-base-v2
Generates 768-dimensional normalised vectors.
Loaded once at startup, CPU-bound batched inference via thread pool.
Redis caching for generated vectors to avoid re-computing on reprocessing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from functools import partial
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover
    SentenceTransformer = None  # type: ignore[assignment, misc]

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]


_MODEL_NAME = "sentence-transformers/all-mpnet-base-v2"
_EMBEDDING_DIM = 768
_CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 days


class EmbeddingService:
    """
    Singleton-style service for document chunk embedding.
    """

    def __init__(self, batch_size: int = 32) -> None:
        self.batch_size = batch_size
        self._model: Any = None
        self._redis: Any = None
        self._lock = asyncio.Lock()

    async def initialize(self, redis_client: Any) -> None:
        """
        Load model weights into memory (once). Run a warmup inference.
        """
        self._redis = redis_client
        if self._model is not None:
            return

        if SentenceTransformer is None:
            raise ImportError("sentence-transformers not installed")

        logger.info(f"Loading embedding model {_MODEL_NAME}...")
        loop = asyncio.get_event_loop()
        self._model = await loop.run_in_executor(
            None, partial(SentenceTransformer, _MODEL_NAME)
        )
        logger.info("Embedding model loaded")

        # Warmup
        await self._encode_sync_in_executor(["warmup"])

    async def generate_embeddings(
        self, texts: list[str]
    ) -> list[list[float]]:
        """
        Generate normalise embeddings for a list of texts.
        Uses Redis cache. CPU inference runs in thread pool in batches.
        """
        if not texts:
            return []

        if self._model is None:
            raise RuntimeError("EmbeddingService not initialized")

        results: list[list[float] | None] = [None] * len(texts)
        missing_indices: list[int] = []
        missing_texts: list[str] = []

        # 1. Check Cache
        for i, text in enumerate(texts):
            cached = await self._get_cached_embedding(text)
            if cached is not None:
                results[i] = cached
            else:
                missing_indices.append(i)
                missing_texts.append(text)

        if not missing_texts:
            return results  # type: ignore[return-value]

        # 2. Batch Encode
        loop = asyncio.get_event_loop()
        encoded = []
        for i in range(0, len(missing_texts), self.batch_size):
            batch = missing_texts[i : i + self.batch_size]
            batch_vectors = await loop.run_in_executor(
                None, partial(self._encode_sync, batch)
            )
            encoded.extend(batch_vectors)

        # 3. Update Cache & Results
        for idx, original_index in enumerate(missing_indices):
            vector = encoded[idx]
            results[original_index] = vector
            # Fire and forget cache set
            asyncio.create_task(
                self._set_cached_embedding(missing_texts[idx], vector)
            )

        return results  # type: ignore[return-value]

    async def _encode_sync_in_executor(self, texts: list[str]) -> list[list[float]]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(self._encode_sync, texts))

    def _encode_sync(self, texts: list[str]) -> list[list[float]]:
        """Run sentence-transformers encode (sync)."""
        if self._model is None:
            return []
        try:
            # normalize_embeddings=True is critical for dot-product cosine similarity
            vectors = self._model.encode(
                texts,
                batch_size=self.batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
            if np is not None and isinstance(vectors, np.ndarray):
                return vectors.tolist()  # type: ignore[no-any-return]
            return vectors  # type: ignore[return-value]
        except Exception as exc:
            logger.error("Embedding generation failed", extra={"error": str(exc)})
            # Return zero vectors as fallback so pipeline doesn't crash completely
            return [[0.0] * _EMBEDDING_DIM for _ in texts]

    def _get_cache_key(self, text: str) -> str:
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return f"embedding:{h}"

    async def _get_cached_embedding(self, text: str) -> list[float] | None:
        if not self._redis:
            return None
        try:
            key = self._get_cache_key(text)
            cached = await self._redis.get(key)
            if cached:
                return json.loads(cached)  # type: ignore[no-any-return]
        except Exception:
            pass
        return None

    async def _set_cached_embedding(self, text: str, vector: list[float]) -> None:
        if not self._redis:
            return
        try:
            key = self._get_cache_key(text)
            await self._redis.setex(key, _CACHE_TTL_SECONDS, json.dumps(vector))
        except Exception:
            pass
