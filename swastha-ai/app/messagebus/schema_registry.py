"""
Schema Registry wrapper.

Provides:
  - Schema registration on startup
  - In-memory schema ID caching (zero extra network calls after first lookup)
  - Avro serialisation: Python dict → bytes (with Confluent wire format prefix)
  - Avro deserialisation: bytes → Python dict
  - BACKWARD compatibility enforcement
  - Clear startup failure if registry is unreachable

Confluent wire format (5-byte prefix):
  Byte 0:   Magic byte (0x00)
  Bytes 1-4: Schema ID (big-endian int32)
  Bytes 5+: Avro binary payload
"""
from __future__ import annotations

import io
import logging
import struct
from typing import Any

import requests

logger = logging.getLogger(__name__)

_MAGIC_BYTE = 0x00
_SCHEMA_HEADER_SIZE = 5  # 1 magic + 4 schema ID bytes

try:
    import avro.schema
    import avro.io
except ImportError:  # pragma: no cover
    avro = None  # type: ignore[assignment]


class SchemaError(RuntimeError):
    """Raised when schema registration or compatibility check fails."""


class SchemaRegistryClient:
    """
    Thin wrapper around the Confluent Schema Registry REST API.

    Usage:
        sr = SchemaRegistryClient("http://schema-registry:8081")
        await sr.initialize(schema_map)  # register all schemas at startup
        data = sr.serialise("raw.documents.ingested", {"doc_id": "DOC-123", ...})
        payload = sr.deserialise(data)
    """

    def __init__(self, url: str) -> None:
        self._url = url.rstrip("/")
        # Cache: subject → schema_id
        self._id_cache: dict[str, int] = {}
        # Cache: schema_id → avro.schema.Schema
        self._schema_cache: dict[int, Any] = {}
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/vnd.schemaregistry.v1+json"})

    def initialize(self, schema_paths: dict[str, str]) -> None:
        """
        Register all schemas at startup.

        Args:
            schema_paths: {topic_name: path_to_avsc_file}

        Raises:
            SchemaError if the registry is unreachable or any schema fails.
        """
        try:
            r = self._session.get(f"{self._url}/subjects", timeout=5)
            if r.status_code != 200:
                raise SchemaError(f"Schema Registry returned {r.status_code}: {r.text}")
        except requests.ConnectionError as exc:
            raise SchemaError(
                f"Schema Registry unreachable at {self._url}. "
                "Ensure the schema-registry container is running. "
                f"Original error: {exc}"
            ) from exc

        failures: list[str] = []
        for topic, path in schema_paths.items():
            subject = f"{topic}-value"
            try:
                schema_id = self._register_or_get(subject, path)
                self._preload_schema(schema_id, path)
                logger.debug(f"Schema ready: {subject} (id={schema_id})")
            except Exception as exc:
                failures.append(f"{subject}: {exc}")

        if failures:
            raise SchemaError(
                f"Schema registration failed for {len(failures)} subjects:\n"
                + "\n".join(f"  - {f}" for f in failures)
            )

    def serialise(self, topic: str, payload: dict) -> bytes:
        """
        Encode payload dict as Confluent Avro wire format bytes.
        Raises SchemaError if topic is not registered.
        """
        subject = f"{topic}-value"
        schema_id = self._id_cache.get(subject)
        if schema_id is None:
            raise SchemaError(
                f"No schema registered for subject '{subject}'. "
                "Call initialize() first."
            )
        schema = self._schema_cache.get(schema_id)
        if schema is None:
            raise SchemaError(f"Schema id={schema_id} not in local cache.")

        buf = io.BytesIO()
        buf.write(struct.pack(">bI", _MAGIC_BYTE, schema_id))
        writer = avro.io.DatumWriter(schema)
        encoder = avro.io.BinaryEncoder(buf)
        writer.write(payload, encoder)
        return buf.getvalue()

    def deserialise(self, data: bytes) -> dict:
        """
        Decode Confluent Avro wire format bytes to Python dict.
        Fetches schema from registry if not cached (first time only).
        """
        if len(data) < _SCHEMA_HEADER_SIZE:
            raise SchemaError("Message too short to contain Confluent wire format header")

        magic, schema_id = struct.unpack_from(">bI", data, 0)
        if magic != _MAGIC_BYTE:
            raise SchemaError(f"Invalid magic byte: {magic:#x} (expected {_MAGIC_BYTE:#x})")

        schema = self._schema_cache.get(schema_id)
        if schema is None:
            schema = self._fetch_schema_by_id(schema_id)
            self._schema_cache[schema_id] = schema

        reader = avro.io.DatumReader(schema)
        decoder = avro.io.BinaryDecoder(io.BytesIO(data[_SCHEMA_HEADER_SIZE:]))
        return reader.read(decoder)  # type: ignore[no-any-return]

    # ── internal helpers ──────────────────────────────────────────────────────

    def _register_or_get(self, subject: str, avsc_path: str) -> int:
        """Register schema and return its ID. Re-uses existing if identical."""
        import json
        schema_str = open(avsc_path, encoding="utf-8").read()

        # Check if already registered (idempotency)
        check_url = f"{self._url}/subjects/{subject}"
        r = self._session.post(check_url, json={"schema": schema_str, "schemaType": "AVRO"}, timeout=10)
        if r.status_code == 200:
            schema_id = r.json()["id"]
            self._id_cache[subject] = schema_id
            return schema_id

        # Register new
        reg_url = f"{self._url}/subjects/{subject}/versions"
        r = self._session.post(reg_url, json={"schema": schema_str, "schemaType": "AVRO"}, timeout=10)
        if r.status_code not in (200, 201):
            raise SchemaError(f"Registration failed for {subject}: {r.status_code} {r.text[:200]}")

        schema_id = r.json()["id"]
        self._id_cache[subject] = schema_id

        # Set BACKWARD compatibility
        compat_url = f"{self._url}/config/{subject}"
        self._session.put(compat_url, json={"compatibility": "BACKWARD"}, timeout=5)

        return schema_id

    def _preload_schema(self, schema_id: int, avsc_path: str) -> None:
        """Parse and cache the avro Schema object for encoding/decoding."""
        if avro is None:
            raise SchemaError("avro-python3 not installed")
        schema_str = open(avsc_path, encoding="utf-8").read()
        parsed = avro.schema.parse(schema_str)
        self._schema_cache[schema_id] = parsed

    def _fetch_schema_by_id(self, schema_id: int) -> Any:
        """Fetch schema from registry by ID (for deserialization of unknown schemas)."""
        r = self._session.get(f"{self._url}/schemas/ids/{schema_id}", timeout=10)
        if r.status_code != 200:
            raise SchemaError(f"Cannot fetch schema id={schema_id}: {r.status_code}")
        schema_str = r.json()["schema"]
        parsed = avro.schema.parse(schema_str)
        self._schema_cache[schema_id] = parsed
        return parsed


# ── Module-level singleton ────────────────────────────────────────────────────

_registry: SchemaRegistryClient | None = None


def get_schema_registry() -> SchemaRegistryClient:
    global _registry
    if _registry is None:
        raise RuntimeError("Schema registry not initialized. Call init_schema_registry() first.")
    return _registry


def init_schema_registry(url: str, schema_paths: dict[str, str]) -> SchemaRegistryClient:
    global _registry
    _registry = SchemaRegistryClient(url)
    _registry.initialize(schema_paths)
    return _registry
