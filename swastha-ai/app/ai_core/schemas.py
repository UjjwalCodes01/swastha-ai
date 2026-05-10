"""Pydantic contracts for Layer 3 outputs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SubmissionType = Literal["drug", "medical_device", "clinical_trial", "sae"]
Priority = Literal["critical", "high", "medium", "low", "informational"]


class AICoreModel(BaseModel):
    model_config = ConfigDict(protected_namespaces=())


class PIIEntity(AICoreModel):
    entity_type: str
    original: str
    pseudonym: str
    start: int
    end: int
    confidence: float = Field(ge=0.0, le=1.0)


class AnonymisationResult(AICoreModel):
    doc_id: str
    anonymised_text: str
    entities: list[PIIEntity]
    entity_count: int
    confidence: float = Field(ge=0.0, le=1.0)
    method: str
    model_version: str


class RegulatorySummary(AICoreModel):
    doc_id: str
    submission_type: SubmissionType
    executive_summary: str
    key_findings: list[str]
    risks: list[str]
    missing_information: list[str]
    recommended_next_steps: list[str]
    word_count: int
    confidence: float = Field(ge=0.0, le=1.0)
    model_id: str
    model_version: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ClassificationResult(AICoreModel):
    category: str
    subcategory: str | None = None
    priority: Priority
    risk_score: float = Field(ge=0.0, le=1.0)
    sae_severity: str | None = None
    requires_review: bool = False
    labels: list[str] = []
    confidence: float = Field(ge=0.0, le=1.0)


class Scorecard(AICoreModel):
    doc_id: str
    completeness_score: float = Field(ge=0.0, le=1.0)
    missing_required_fields: list[str]
    duplicate_candidates: list[dict]
    classification: ClassificationResult
    model_id: str
    model_version: str


class ComparisonResult(AICoreModel):
    comparison_id: str
    doc_id_a: str
    doc_id_b: str
    report_id: str
    report_markdown: str
    changed_sections: list[str]
    additions: int
    deletions: int
    regulatory_significance: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
