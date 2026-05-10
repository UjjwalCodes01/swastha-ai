"""Structured regulatory summarisation for Layer 3.

Uses an LLM (Claude or Ollama) with document-type-specific prompt templates.
Validates the output against the RegulatorySummary Pydantic schema and retries
once with a correction prompt if the initial response fails validation.

Falls back to offline extractive heuristics when:
  - No LLM is configured / reachable
  - The LLM fails after retries
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import ValidationError

from app.ai_core.schemas import RegulatorySummary, SubmissionType
from app.ai_core.llm_client import get_llm_client

logger = logging.getLogger(__name__)

# ── Token limit: truncate input to avoid overflowing context windows ───────────
_MAX_INPUT_CHARS = 40_000          # ~10k tokens — safe for all supported models


# ── System prompt ──────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """You are a senior regulatory affairs analyst at CDSCO (India's Central Drugs Standard Control Organisation).
You produce concise, structured summaries of regulatory submissions.
You must ALWAYS respond with valid JSON matching the schema exactly — no prose, no markdown, just JSON.
Be factual. Do not invent information not present in the document."""

# ── Per-type user prompt templates ────────────────────────────────────────────
_PROMPT_TEMPLATES: dict[str, str] = {
    "drug": """Analyse this CDSCO drug regulatory submission and return a JSON object with EXACTLY these keys:
{{
  "executive_summary": "<2-3 sentence overview of what the submission is requesting and its regulatory significance>",
  "key_findings": ["<finding 1>", "<finding 2>", "<finding 3>", "<finding 4>", "<finding 5>"],
  "risks": ["<risk 1>", "<risk 2>"],
  "missing_information": ["<missing field or data 1>", "<missing field 2>"],
  "recommended_next_steps": ["<step 1>", "<step 2>"]
}}

Focus on: drug name, indication, dosage form, manufacturing details, safety/efficacy data, contraindications, missing mandatory Schedule Y fields.

SUBMISSION TEXT:
{text}""",

    "medical_device": """Analyse this CDSCO medical device regulatory submission and return a JSON object with EXACTLY these keys:
{{
  "executive_summary": "<2-3 sentence overview>",
  "key_findings": ["<finding 1>", "<finding 2>", "<finding 3>"],
  "risks": ["<risk 1>", "<risk 2>"],
  "missing_information": ["<missing field 1>", "<missing field 2>"],
  "recommended_next_steps": ["<step 1>", "<step 2>"]
}}

Focus on: device name, risk class (A/B/C/D), manufacturer, intended use, biocompatibility, clinical performance, labelling, sterility.

SUBMISSION TEXT:
{text}""",

    "clinical_trial": """Analyse this CDSCO clinical trial submission and return a JSON object with EXACTLY these keys:
{{
  "executive_summary": "<2-3 sentence overview>",
  "key_findings": ["<finding 1>", "<finding 2>", "<finding 3>"],
  "risks": ["<risk 1>", "<risk 2>"],
  "missing_information": ["<missing field 1>", "<missing field 2>"],
  "recommended_next_steps": ["<step 1>", "<step 2>"]
}}

Focus on: protocol number, phase (I/II/III/IV), sponsor, primary endpoint, subject safety, IEC approval, regulatory compliance with Schedule Y and new drug rules.

SUBMISSION TEXT:
{text}""",

    "sae": """Analyse this Serious Adverse Event (SAE) report submitted to CDSCO and return a JSON object with EXACTLY these keys:
{{
  "executive_summary": "<2-3 sentence overview of the adverse event, patient outcome, and causality assessment>",
  "key_findings": ["<finding 1>", "<finding 2>", "<finding 3>"],
  "risks": ["<risk 1>", "<risk 2>"],
  "missing_information": ["<missing field 1>", "<missing field 2>"],
  "recommended_next_steps": ["<step 1>", "<step 2>"]
}}

Focus on: event seriousness category (death/life-threatening/hospitalisation/disability/congenital anomaly), causality (certain/probable/possible/unlikely), timeline, patient demographics, suspected drug, action taken.

SUBMISSION TEXT:
{text}""",
}

_CORRECTION_PROMPT = """Your previous response was not valid JSON or did not match the required schema.
Required schema:
{{
  "executive_summary": string,
  "key_findings": array of strings (3-5 items),
  "risks": array of strings (1-3 items),
  "missing_information": array of strings,
  "recommended_next_steps": array of strings (1-3 items)
}}

Please respond with ONLY valid JSON matching this schema exactly. No markdown, no explanations.
Previous (invalid) response:
{previous}"""


class Summariser:
    """Creates schema-validated summaries using an LLM with offline fallback."""

    model_version = "1.1.0"

    def summarise(
        self,
        doc_id: str,
        submission_type: SubmissionType,
        text: str,
        metadata: dict | None = None,
    ) -> RegulatorySummary:
        """Synchronous entry point — calls async implementation via asyncio.

        The pipeline is already async so we run inside the event loop directly.
        """
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self._async_summarise(doc_id, submission_type, text, metadata)
        )

    async def async_summarise(
        self,
        doc_id: str,
        submission_type: SubmissionType,
        text: str,
        metadata: dict | None = None,
    ) -> RegulatorySummary:
        """Async entry point used directly from the async pipeline."""
        return await self._async_summarise(doc_id, submission_type, text, metadata)

    async def _async_summarise(
        self,
        doc_id: str,
        submission_type: SubmissionType,
        text: str,
        metadata: dict | None = None,
    ) -> RegulatorySummary:
        metadata = metadata or {}
        truncated = text[:_MAX_INPUT_CHARS]

        try:
            result = await self._llm_summarise(doc_id, submission_type, truncated)
            logger.info(
                "LLM summarisation complete",
                extra={"doc_id": doc_id, "model_id": result.model_id},
            )
            return result
        except Exception as exc:
            logger.warning(
                "LLM summarisation failed — using offline extractive fallback",
                extra={"doc_id": doc_id, "error": str(exc)},
            )
            return self._offline_summarise(doc_id, submission_type, text, metadata)

    async def _llm_summarise(
        self,
        doc_id: str,
        submission_type: SubmissionType,
        text: str,
    ) -> RegulatorySummary:
        template = _PROMPT_TEMPLATES.get(submission_type, _PROMPT_TEMPLATES["drug"])
        user_prompt = template.format(text=text)

        async with get_llm_client() as llm:
            # First attempt
            try:
                data = await llm.complete_json(
                    _SYSTEM_PROMPT, user_prompt, max_tokens=1500
                )
                return self._build_summary(doc_id, submission_type, data, llm._mode, text)
            except (ValueError, ValidationError) as first_err:
                # One retry with correction prompt
                logger.debug(
                    "LLM response schema mismatch — retrying with correction prompt",
                    extra={"doc_id": doc_id, "error": str(first_err)},
                )
                raw = await llm.complete(
                    _SYSTEM_PROMPT,
                    _CORRECTION_PROMPT.format(previous=str(first_err)[:500]),
                    max_tokens=1500,
                    response_format="json",
                )
                from app.ai_core.llm_client import LLMClient
                data = LLMClient._parse_json(raw)
                return self._build_summary(doc_id, submission_type, data, llm._mode, text)

    def _build_summary(
        self,
        doc_id: str,
        submission_type: SubmissionType,
        data: dict[str, Any],
        llm_mode: str,
        original_text: str,
    ) -> RegulatorySummary:
        summary_text = str(data.get("executive_summary", ""))
        key_findings = [str(f) for f in data.get("key_findings", []) if f][:5]
        risks = [str(r) for r in data.get("risks", []) if r][:5]
        missing = [str(m) for m in data.get("missing_information", []) if m]
        next_steps = [str(s) for s in data.get("recommended_next_steps", []) if s][:5]

        if not key_findings:
            key_findings = ["No key findings could be extracted."]
        if not risks:
            risks = ["No explicit risk signals detected."]
        if not next_steps:
            next_steps = ["Proceed to standard reviewer queue."]

        model_id = (
            "claude-haiku-4-5" if llm_mode == "cloud"
            else f"ollama/{self._infer_ollama_model()}" if llm_mode == "offline"
            else "gemini-1.5-flash" if llm_mode == "gemini"
            else "groq/llama3-70b" if llm_mode == "groq"
            else "hybrid-claude-haiku-4-5"
        )

        return RegulatorySummary(
            doc_id=doc_id,
            submission_type=submission_type,
            executive_summary=summary_text,
            key_findings=key_findings,
            risks=risks,
            missing_information=missing,
            recommended_next_steps=next_steps,
            word_count=len(summary_text.split()),
            confidence=0.88,
            model_id=model_id,
            model_version=self.model_version,
            prompt_tokens=max(1, len(original_text.split())),
            completion_tokens=max(1, len(summary_text.split())),
        )

    @staticmethod
    def _infer_ollama_model() -> str:
        try:
            return get_llm_client()._settings.ollama_model
        except Exception:
            return "llama3.1:8b"

    # ── Offline fallback ───────────────────────────────────────────────────────

    def _offline_summarise(
        self,
        doc_id: str,
        submission_type: SubmissionType,
        text: str,
        metadata: dict,
    ) -> RegulatorySummary:
        """Pure extractive heuristic fallback — no LLM required."""
        sentences = self._sentences(text)
        selected = sentences[:5] or ["No readable document content was available."]
        key_findings = self._key_findings(sentences, submission_type)
        risks = self._risks(text, submission_type)
        missing = self._missing_information(text, submission_type, metadata)
        next_steps = self._next_steps(submission_type, missing, risks)
        summary_text = " ".join(selected)

        return RegulatorySummary(
            doc_id=doc_id,
            submission_type=submission_type,
            executive_summary=summary_text,
            key_findings=key_findings[:5],
            risks=risks,
            missing_information=missing,
            recommended_next_steps=next_steps,
            word_count=len(summary_text.split()),
            confidence=self._confidence(text, key_findings, missing),
            model_id="offline-extractive-regulatory-summariser",
            model_version="1.0.0",
            prompt_tokens=max(1, len(text.split())),
            completion_tokens=max(1, len(summary_text.split())),
        )

    def _sentences(self, text: str) -> list[str]:
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            return []
        return [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if len(s.strip()) > 20]

    def _key_findings(self, sentences: list[str], submission_type: str) -> list[str]:
        keywords = {
            "drug": ("indication", "dosage", "contraindication", "stability", "manufacturing", "safety"),
            "medical_device": ("device", "risk", "sterile", "performance", "clinical", "manufacturing"),
            "clinical_trial": ("phase", "endpoint", "protocol", "eligibility", "adverse", "efficacy"),
            "sae": ("death", "life-threatening", "hospital", "serious", "causality", "adverse"),
        }.get(submission_type, ())
        findings = [s for s in sentences if any(k in s.lower() for k in keywords)]
        return findings[:5] or sentences[:3] or ["No key findings could be extracted."]

    def _risks(self, text: str, submission_type: str) -> list[str]:
        lower = text.lower()
        risks: list[str] = []
        for term in ["death", "life-threatening", "contraindication", "serious adverse", "missing", "deviation"]:
            if term in lower:
                risks.append(f"Potential regulatory risk detected: {term}")
        if submission_type == "sae" and not risks:
            risks.append("SAE submission requires reviewer validation even when no critical keyword is present.")
        return risks or ["No explicit high-risk signal detected in the extracted text."]

    def _missing_information(self, text: str, submission_type: str, metadata: dict) -> list[str]:
        required = {
            "drug": ["applicant_name", "drug_name", "indication"],
            "medical_device": ["device_name", "risk_class", "manufacturer"],
            "clinical_trial": ["protocol_number", "phase", "sponsor"],
            "sae": ["patient_id", "event_date", "outcome"],
        }.get(submission_type, [])
        lower = text.lower()
        return [
            field for field in required
            if not metadata.get(field) and field.replace("_", " ") not in lower
        ]

    def _next_steps(self, submission_type: str, missing: list[str], risks: list[str]) -> list[str]:
        steps = []
        if missing:
            steps.append("Request applicant clarification for missing mandatory fields.")
        if risks and "No explicit" not in risks[0]:
            steps.append("Route to CDSCO reviewer for priority assessment.")
        if submission_type == "sae":
            steps.append("Verify SAE seriousness, causality, and mandatory reporting timeline.")
        return steps or ["Proceed to standard reviewer queue."]

    def _confidence(self, text: str, findings: list[str], missing: list[str]) -> float:
        if not text.strip():
            return 0.2
        score = 0.72 + min(len(findings), 5) * 0.04 - len(missing) * 0.03
        return round(max(0.3, min(0.95, score)), 3)
