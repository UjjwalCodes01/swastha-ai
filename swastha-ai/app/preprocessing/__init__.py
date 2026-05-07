"""
RxFlow Layer 1 — Document Pre-processing Engine.

This package consumes raw.documents.ingested Kafka events, runs every document
through a 12-step pipeline (extraction → OCR → normalisation → chunking →
embedding → quality assessment → storage → publish), and emits
documents.preprocessed events for the AI modules in Layer 3.
"""
