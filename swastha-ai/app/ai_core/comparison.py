"""Document comparison and LLM-powered change narration for Layer 3.

Two-pass approach (as specified in layers spec):
  Pass 1: Deterministic unified diff (difflib) — exact line changes
  Pass 2: LLM narrates the regulatory significance of those changes

Falls back to keyword pattern matching when LLM is unavailable.
"""

from __future__ import annotations

import difflib
import logging
import time
from typing import Any

from app.ai_core.llm_client import get_llm_client
from app.ai_core.schemas import ComparisonResult

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a senior CDSCO regulatory affairs expert.
You explain the regulatory significance of changes between document versions clearly and concisely.
Focus on what matters to a CDSCO reviewer: safety signals, labelling changes, contraindication updates, 
dosage modifications, manufacturing deviations, and clinical data updates.
Always respond in JSON only."""

_NARRATOR_PROMPT = """The following is a unified diff between two regulatory document versions submitted to CDSCO.
Identify and explain the regulatory significance of the changes.

Respond with a JSON object:
{{
  "significance_notes": [
    "<specific explanation of change 1 and its regulatory implication>",
    "<specific explanation of change 2 and its regulatory implication>",
    ...
  ],
  "overall_assessment": "<1-2 sentence overall regulatory impact assessment>"
}}

If no safety-significant changes are found, say so explicitly.

UNIFIED DIFF (first 300 lines):
{diff_text}

COMPARISON ASPECTS REQUESTED: {aspects}"""


class Comparator:
    """Produces deterministic diffs with LLM-powered regulatory change narration."""

    def compare(
        self,
        comparison_id: str,
        doc_id_a: str,
        doc_id_b: str,
        text_a: str,
        text_b: str,
        comparison_aspects: list[str] | None = None,
    ) -> ComparisonResult:
        """Synchronous entry point — delegates to async implementation."""
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self._async_compare(comparison_id, doc_id_a, doc_id_b, text_a, text_b, comparison_aspects)
        )

    async def async_compare(
        self,
        comparison_id: str,
        doc_id_a: str,
        doc_id_b: str,
        text_a: str,
        text_b: str,
        comparison_aspects: list[str] | None = None,
    ) -> ComparisonResult:
        return await self._async_compare(
            comparison_id, doc_id_a, doc_id_b, text_a, text_b, comparison_aspects
        )

    async def _async_compare(
        self,
        comparison_id: str,
        doc_id_a: str,
        doc_id_b: str,
        text_a: str,
        text_b: str,
        comparison_aspects: list[str] | None = None,
    ) -> ComparisonResult:
        comparison_aspects = comparison_aspects or []

        # ── Pass 1: Deterministic diff ─────────────────────────────────────────
        lines_a = [line.strip() for line in text_a.splitlines() if line.strip()]
        lines_b = [line.strip() for line in text_b.splitlines() if line.strip()]
        diff = list(
            difflib.unified_diff(lines_a, lines_b, fromfile=doc_id_a, tofile=doc_id_b, lineterm="")
        )
        additions = len([l for l in diff if l.startswith("+") and not l.startswith("+++")])
        deletions = len([l for l in diff if l.startswith("-") and not l.startswith("---")])
        changed_sections = self._changed_sections(diff, comparison_aspects)

        # ── Pass 2: LLM change narration ───────────────────────────────────────
        significance = await self._narrate_changes(diff, comparison_aspects)

        report_id = f"RPT-{int(time.time() * 1000)}"
        report = self._render_report(
            report_id, doc_id_a, doc_id_b, diff, additions, deletions, significance
        )

        return ComparisonResult(
            comparison_id=comparison_id,
            doc_id_a=doc_id_a,
            doc_id_b=doc_id_b,
            report_id=report_id,
            report_markdown=report,
            changed_sections=changed_sections,
            additions=additions,
            deletions=deletions,
            regulatory_significance=significance,
            confidence=0.90 if not self._is_trivial(diff) else 0.75,
        )

    async def _narrate_changes(
        self, diff: list[str], aspects: list[str]
    ) -> list[str]:
        """Use LLM to generate regulatory significance notes from the diff."""
        if not diff:
            return ["No differences found between the two document versions."]

        diff_text = "\n".join(diff[:300])
        aspects_str = ", ".join(aspects) if aspects else "general regulatory content"

        try:
            async with get_llm_client() as llm:
                data = await llm.complete_json(
                    _SYSTEM_PROMPT,
                    _NARRATOR_PROMPT.format(diff_text=diff_text, aspects=aspects_str),
                    max_tokens=1024,
                )
            notes = [str(n) for n in data.get("significance_notes", []) if n]
            assessment = str(data.get("overall_assessment", "")).strip()
            if assessment:
                notes.append(f"Overall: {assessment}")

            if not notes:
                raise ValueError("LLM returned empty significance notes")

            logger.debug("LLM change narration complete", extra={"notes_count": len(notes)})
            return notes

        except Exception as exc:
            logger.warning(
                "LLM change narration failed — using keyword fallback",
                extra={"error": str(exc)},
            )
            return self._keyword_significance(diff)

    def _keyword_significance(self, diff: list[str]) -> list[str]:
        """Offline keyword-based significance detection."""
        lower = "\n".join(diff).lower()
        notes = []
        regulatory_terms = [
            ("contraindication", "Contraindication section modified — reviewer must assess patient safety impact."),
            ("adverse", "Adverse event data changed — requires pharmacovigilance review."),
            ("dosage", "Dosage information changed — verify against approved Schedule Y data."),
            ("indication", "Indication modified — may require new clinical evidence assessment."),
            ("death", "Death-related content changed — immediate reviewer escalation required."),
            ("stability", "Stability data changed — manufacturing quality review required."),
            ("manufacturing", "Manufacturing information updated — GMP compliance check needed."),
            ("contraindication", "Safety labelling change detected."),
        ]
        seen = set()
        for term, note in regulatory_terms:
            if term in lower and note not in seen:
                notes.append(note)
                seen.add(note)
        return notes or ["No specific high-impact regulatory term was detected in the diff."]

    def _changed_sections(self, diff: list[str], aspects: list[str]) -> list[str]:
        changed = []
        lower_diff = "\n".join(diff).lower()
        for aspect in aspects:
            if aspect.lower() in lower_diff:
                changed.append(aspect)
        for aspect in ["safety", "efficacy", "dosage", "indication", "manufacturing"]:
            if aspect in lower_diff and aspect not in changed:
                changed.append(aspect)
        return changed or ["general_content"]

    def _is_trivial(self, diff: list[str]) -> bool:
        """Return True if the diff is very small (whitespace/formatting only)."""
        meaningful = [l for l in diff if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
        return len(meaningful) <= 3

    def _render_report(
        self,
        report_id: str,
        doc_id_a: str,
        doc_id_b: str,
        diff: list[str],
        additions: int,
        deletions: int,
        significance: list[str],
    ) -> str:
        body = "\n".join(diff[:300])
        notes = "\n".join(f"- {note}" for note in significance)
        return (
            f"# CDSCO Comparison Report {report_id}\n\n"
            f"Documents compared: `{doc_id_a}` vs `{doc_id_b}`\n\n"
            f"**Additions:** {additions} | **Deletions:** {deletions}\n\n"
            f"## Regulatory Significance (AI-Narrated)\n{notes}\n\n"
            f"## Unified Diff\n```diff\n{body}\n```\n"
        )
