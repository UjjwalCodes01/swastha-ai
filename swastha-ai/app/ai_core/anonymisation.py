"""PII/PHI anonymisation for Layer 3.

The module runs fully offline with deterministic recognisers, and upgrades to
Presidio/spaCy automatically when those packages are installed on-prem.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.ai_core.schemas import AnonymisationResult, PIIEntity


@dataclass(frozen=True)
class _Pattern:
    entity_type: str
    regex: re.Pattern[str]
    confidence: float


class Anonymiser:
    """Detects Indian regulatory PII and replaces it with stable pseudonyms."""

    model_version = "swastha-anonymiser-1.0.0"

    def __init__(self) -> None:
        self._patterns = [
            _Pattern("AADHAAR", re.compile(r"\b(?:\d{4}[\s-]?){2}\d{4}\b"), 0.98),
            _Pattern("PAN", re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"), 0.96),
            _Pattern("CIN", re.compile(r"\b[UL]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b"), 0.96),
            _Pattern("INDIAN_PHONE", re.compile(r"\b(?:\+91[\s-]?)?[6-9]\d{9}\b"), 0.94),
            _Pattern("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), 0.98),
            _Pattern("TRIAL_SUBJECT", re.compile(r"\b(?:patient|subject)\s*(?:id|no\.?|number)?\s*[:#-]?\s*[A-Z0-9-]{3,}\b", re.I), 0.88),
            _Pattern("DATE_OF_BIRTH", re.compile(r"\b(?:dob|date of birth)\s*[:#-]?\s*\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b", re.I), 0.9),
        ]
        self._presidio = self._load_presidio()
        self._spacy = self._load_spacy()

    def anonymise(self, doc_id: str, text: str) -> AnonymisationResult:
        """Return anonymised text plus an auditable entity map."""
        entities = self._detect_entities(text)
        deduped = self._dedupe_overlaps(entities)
        pseudonym_map: dict[tuple[str, str], str] = {}

        replacements: list[PIIEntity] = []
        for entity in deduped:
            key = (entity.entity_type, entity.original.lower())
            pseudonym = pseudonym_map.setdefault(
                key,
                self._stable_pseudonym(doc_id, entity.entity_type, entity.original, len(pseudonym_map) + 1),
            )
            replacements.append(entity.model_copy(update={"pseudonym": pseudonym}))

        anonymised = text
        for entity in sorted(replacements, key=lambda item: item.start, reverse=True):
            anonymised = anonymised[:entity.start] + entity.pseudonym + anonymised[entity.end:]

        confidence = 0.99 if not replacements else round(
            sum(entity.confidence for entity in replacements) / len(replacements),
            3,
        )
        method = "hybrid" if self._presidio or self._spacy else "custom-regex"
        return AnonymisationResult(
            doc_id=doc_id,
            anonymised_text=anonymised,
            entities=replacements,
            entity_count=len(replacements),
            confidence=confidence,
            method=method,
            model_version=self.model_version,
        )

    def _detect_entities(self, text: str) -> list[PIIEntity]:
        entities: list[PIIEntity] = []
        for pattern in self._patterns:
            for match in pattern.regex.finditer(text):
                entities.append(
                    PIIEntity(
                        entity_type=pattern.entity_type,
                        original=match.group(0),
                        pseudonym="",
                        start=match.start(),
                        end=match.end(),
                        confidence=pattern.confidence,
                    )
                )

        if self._presidio:
            analyzer, _ = self._presidio
            for result in analyzer.analyze(text=text, language="en"):
                entities.append(
                    PIIEntity(
                        entity_type=result.entity_type,
                        original=text[result.start:result.end],
                        pseudonym="",
                        start=result.start,
                        end=result.end,
                        confidence=float(result.score),
                    )
                )

        if self._spacy:
            doc = self._spacy(text[:1_000_000])
            for ent in doc.ents:
                if ent.label_ in {"PERSON", "GPE", "LOC", "ORG"}:
                    entities.append(
                        PIIEntity(
                            entity_type=ent.label_,
                            original=ent.text,
                            pseudonym="",
                            start=ent.start_char,
                            end=ent.end_char,
                            confidence=0.82,
                        )
                    )
        return entities

    def _dedupe_overlaps(self, entities: list[PIIEntity]) -> list[PIIEntity]:
        ordered = sorted(entities, key=lambda item: (item.start, -(item.end - item.start), -item.confidence))
        selected: list[PIIEntity] = []
        occupied: list[range] = []
        for entity in ordered:
            span = range(entity.start, entity.end)
            if any(entity.start < used.stop and entity.end > used.start for used in occupied):
                continue
            selected.append(entity)
            occupied.append(span)
        return sorted(selected, key=lambda item: item.start)

    def _stable_pseudonym(self, doc_id: str, entity_type: str, original: str, index: int) -> str:
        digest = hashlib.sha256(f"{doc_id}:{entity_type}:{original.lower()}".encode()).hexdigest()[:6].upper()
        return f"<{entity_type}_{index}_{digest}>"

    def _load_presidio(self):
        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_anonymizer import AnonymizerEngine
            return AnalyzerEngine(), AnonymizerEngine()
        except Exception:
            return None

    def _load_spacy(self):
        try:
            import spacy
            return spacy.load("en_core_web_lg")
        except Exception:
            return None
