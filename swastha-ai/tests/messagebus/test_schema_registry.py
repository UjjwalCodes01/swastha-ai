"""Tests for SchemaRegistryClient (messagebus/schema_registry.py)."""
from __future__ import annotations

import json
import struct
from unittest.mock import MagicMock, patch

import pytest

from app.messagebus.schema_registry import SchemaRegistryClient, SchemaError


@pytest.fixture
def client():
    return SchemaRegistryClient("http://localhost:8081")


@pytest.fixture
def sample_schema_str():
    return json.dumps({
        "type": "record",
        "name": "TestEvent",
        "namespace": "ai.swasta.test",
        "fields": [
            {"name": "event_id", "type": "string"},
            {"name": "doc_id", "type": "string"},
            {"name": "value", "type": "int", "default": 0}
        ]
    })


def test_schema_id_cached_after_registration(client, sample_schema_str, tmp_path):
    avsc_path = tmp_path / "test.avsc"
    avsc_path.write_text(sample_schema_str)

    with patch.object(client._session, "post") as mock_post, \
         patch.object(client._session, "put") as mock_put:
        # Simulate registry returning schema_id=42
        mock_post.return_value = MagicMock(status_code=200, json=MagicMock(return_value={"id": 42}))
        mock_put.return_value = MagicMock(status_code=200)

        schema_id = client._register_or_get("test.topic-value", str(avsc_path))

    assert schema_id == 42
    assert client._id_cache["test.topic-value"] == 42


def test_serialise_produces_valid_wire_format(client, sample_schema_str, tmp_path):
    avsc_path = tmp_path / "test.avsc"
    avsc_path.write_text(sample_schema_str)

    # Pre-load schema and set cache
    client._id_cache["test.topic-value"] = 99

    # We can test the wire format structure without actual avro library
    # by mocking the DatumWriter
    with patch("app.messagebus.schema_registry.avro") as mock_avro:
        mock_schema = MagicMock()
        client._schema_cache[99] = mock_schema
        mock_writer = MagicMock()
        mock_avro.io.DatumWriter.return_value = mock_writer

        # Override write to actually write something predictable
        def fake_write(payload, encoder):
            encoder._writer.write(b"PAYLOAD")
        mock_writer.write = MagicMock(side_effect=fake_write)
        mock_avro.io.BinaryEncoder = MagicMock(side_effect=lambda buf: MagicMock(_writer=buf))

        payload = {"event_id": "ev-001", "doc_id": "DOC-123"}
        result = client.serialise("test.topic", payload)

    # Check wire format header
    magic, schema_id = struct.unpack_from(">bI", result, 0)
    assert magic == 0x00
    assert schema_id == 99


def test_serialise_raises_if_topic_not_registered(client):
    with pytest.raises(SchemaError, match="No schema registered"):
        client.serialise("unregistered.topic", {"doc_id": "DOC-X"})


def test_schema_registry_unreachable_at_startup_raises():
    client = SchemaRegistryClient("http://does-not-exist:8081")
    with pytest.raises(SchemaError, match="unreachable"):
        client.initialize({"some.topic": "/nonexistent.avsc"})


def test_deserialise_wire_format(client):
    # Build a wire format payload: magic + schema_id + json bytes (mock avro)
    schema_id = 7
    fake_payload = {"event_id": "ev-abc", "doc_id": "DOC-456"}
    fake_avro_bytes = json.dumps(fake_payload).encode()
    wire_data = struct.pack(">bI", 0x00, schema_id) + fake_avro_bytes

    mock_schema = MagicMock()
    client._schema_cache[schema_id] = mock_schema

    with patch("app.messagebus.schema_registry.avro") as mock_avro:
        mock_reader = MagicMock()
        mock_reader.read.return_value = fake_payload
        mock_avro.io.DatumReader.return_value = mock_reader

        result = client.deserialise(wire_data)

    assert result == fake_payload


def test_deserialise_raises_on_invalid_magic_byte(client):
    bad_data = struct.pack(">bI", 0xFF, 1) + b"garbage"
    with pytest.raises(SchemaError, match="magic byte"):
        client.deserialise(bad_data)


def test_deserialise_raises_on_too_short_message(client):
    with pytest.raises(SchemaError, match="too short"):
        client.deserialise(b"\x00\x01")
