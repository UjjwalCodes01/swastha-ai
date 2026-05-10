"""Document comparison and report generation for Layer 3."""

from __future__ import annotations

import difflib
import time

from app.ai_core.schemas import ComparisonResult


class Comparator:
    """Produces deterministic diffs plus regulatory significance notes."""

    def compare(
        self,
        comparison_id: str,
        doc_id_a: str,
        doc_id_b: str,
        text_a: str,
        text_b: str,
        comparison_aspects: list[str] | None = None,
    ) -> ComparisonResult:
        comparison_aspects = comparison_aspects or []
        lines_a = [line.strip() for line in text_a.splitlines() if line.strip()]
        lines_b = [line.strip() for line in text_b.splitlines() if line.strip()]
        diff = list(difflib.unified_diff(lines_a, lines_b, fromfile=doc_id_a, tofile=doc_id_b, lineterm=""))
        additions = len([line for line in diff if line.startswith("+") and not line.startswith("+++")])
        deletions = len([line for line in diff if line.startswith("-") and not line.startswith("---")])
        changed_sections = self._changed_sections(diff, comparison_aspects)
        significance = self._significance(diff)
        report_id = f"RPT-{int(time.time() * 1000)}"
        report = self._render_report(report_id, doc_id_a, doc_id_b, diff, additions, deletions, significance)
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
            confidence=0.88,
        )

    def _changed_sections(self, diff: list[str], aspects: list[str]) -> list[str]:
        changed = []
        lower_diff = "\n".join(diff).lower()
        for aspect in aspects:
            if aspect.lower() in lower_diff:
                changed.append(aspect)
        defaults = ["safety", "efficacy", "dosage", "indication", "manufacturing"]
        for aspect in defaults:
            if aspect in lower_diff and aspect not in changed:
                changed.append(aspect)
        return changed or ["general_content"]

    def _significance(self, diff: list[str]) -> list[str]:
        lower = "\n".join(diff).lower()
        notes = []
        for term in ("contraindication", "adverse", "dosage", "indication", "death", "stability"):
            if term in lower:
                notes.append(f"Change touches {term}; reviewer should assess regulatory impact.")
        return notes or ["No specific high-impact regulatory term was detected in the diff."]

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
        body = "\n".join(diff[:200])
        notes = "\n".join(f"- {note}" for note in significance)
        return (
            f"# CDSCO Comparison Report {report_id}\n\n"
            f"Documents compared: `{doc_id_a}` vs `{doc_id_b}`\n\n"
            f"Additions: {additions}\n\n"
            f"Deletions: {deletions}\n\n"
            f"## Regulatory Significance\n{notes}\n\n"
            f"## Unified Diff\n```diff\n{body}\n```\n"
        )
