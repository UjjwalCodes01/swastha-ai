"""Pydantic contracts for Layer 4 compliance decisions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["info", "warning", "critical"]
Decision = Literal["pass", "review_required", "blocked"]


class ComplianceFinding(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    rule_id: str
    framework: str
    severity: Severity
    message: str
    evidence: dict[str, str] = {}


class ComplianceDecision(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    doc_id: str
    source_event: str
    decision: Decision
    frameworks_checked: list[str]
    findings: list[ComplianceFinding]
    human_review_required: bool
    blocked: bool
    confidence: float = Field(ge=0.0, le=1.0)


class XAIDecision(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    doc_id: str
    module_name: str
    model_id: str
    model_version: str
    confidence: float
    decision_summary: str
    input_refs: dict[str, str] = {}
    output_refs: dict[str, str] = {}
    rationale: list[str]
