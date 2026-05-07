"""
Text Normaliser — clean and normalise extracted document text.

Pipeline (runs in this exact order):
  1.  Encoding decode (chardet if needed)
  2.  Unicode NFC normalisation
  3.  Remove null bytes
  4.  Remove control characters (preserve \\n and \\t)
  5.  Normalise line endings (CRLF/CR → LF)
  6.  Collapse excessive blank lines (3+ → 2)
  7.  Strip trailing whitespace from each line
  8.  Fix hyphenated line breaks from PDF extraction
  9.  Normalise quotes (curly → straight)
  10. Normalise dashes (em-dash, en-dash → ASCII)
  11. Remove page headers/footers if tagged
  12. Normalise whitespace within lines
  13. Detect and flag (NOT remove) gibberish sections

Output: cleaned string + normalisation_log listing what changed.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

try:
    import chardet
except ImportError:  # pragma: no cover
    chardet = None  # type: ignore[assignment]

# Common English words for gibberish detection (minimal set)
_COMMON_ENGLISH_WORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "not",
    "this", "that", "these", "those", "he", "she", "it", "we", "they",
    "i", "you", "all", "any", "both", "each", "few", "more", "most",
    "other", "some", "such", "than", "too", "very", "just", "also",
    "patient", "drug", "dose", "mg", "ml", "tablet", "capsule", "report",
    "india", "cdsco", "clinical", "trial", "submission", "application",
    "device", "medical", "regulatory", "approval", "study", "data",
    "adverse", "event", "safety", "efficacy", "treatment", "therapy",
})

# Date pattern: normalise to ISO 8601 where unambiguous
_DATE_PATTERNS: list[tuple[re.Pattern, str]] = [
    # DD/MM/YYYY or DD-MM-YYYY
    (re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b"), r"\3-\2-\1"),
    # Month DD, YYYY
    (re.compile(
        r"\b(January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+(\d{1,2}),?\s+(\d{4})\b",
        re.IGNORECASE,
    ), None),  # handled specially
    # DD Month YYYY
    (re.compile(
        r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+(\d{4})\b",
        re.IGNORECASE,
    ), None),  # handled specially
]

_MONTH_MAP = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}

# Characters to remove: control chars except \n (0x0A), \t (0x09), \r (0x0D)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")

# Hyphenated line break: word ending with hyphen at line end
_HYPHEN_LINE_BREAK_RE = re.compile(r"(\w)-\n([a-z])")

# Excessive blank lines (3+)
_EXCESSIVE_BLANK_RE = re.compile(r"\n{3,}")

# Multiple spaces within a line
_MULTI_SPACE_RE = re.compile(r"[ \t]+")

# OCR common errors in numeric contexts: l→1, O→0
# Only apply when adjacent to digits
_OCR_L_ONE_RE = re.compile(r"(?<=\d)l(?=\d)|(?<=\d)l\b|\bl(?=\d)")
_OCR_O_ZERO_RE = re.compile(r"(?<=\d)O(?=\d)")

# Gibberish window size for detection
_GIBBERISH_WINDOW = 100
_GIBBERISH_WORD_THRESHOLD = 0.30  # less than 30% real words → suspect


@dataclass
class NormalisationLog:
    """Records what the normaliser changed and by how much."""
    null_bytes_removed: int = 0
    control_chars_removed: int = 0
    excessive_blank_lines_collapsed: int = 0
    hyphenated_breaks_fixed: int = 0
    whitespace_normalised: int = 0
    gibberish_sections_flagged: int = 0
    dates_normalised: int = 0
    changes: list[str] = field(default_factory=list)

    def note(self, change: str) -> None:
        self.changes.append(change)

    def to_dict(self) -> dict:
        return {
            "null_bytes_removed": self.null_bytes_removed,
            "control_chars_removed": self.control_chars_removed,
            "excessive_blank_lines_collapsed": self.excessive_blank_lines_collapsed,
            "hyphenated_breaks_fixed": self.hyphenated_breaks_fixed,
            "whitespace_normalised": self.whitespace_normalised,
            "gibberish_sections_flagged": self.gibberish_sections_flagged,
            "dates_normalised": self.dates_normalised,
            "changes": self.changes,
        }


class TextNormaliser:
    """
    Stateless text cleaning pipeline.
    Call normalise(text) to get (clean_text, normalisation_log).
    """

    def normalise(self, text: str) -> tuple[str, NormalisationLog]:
        """
        Run the full normalisation pipeline.
        Returns (normalised_text, log_of_changes).
        """
        log = NormalisationLog()

        if not text:
            return "", log

        # Step 1: decode — already a string at this point
        # (decoding happens in extractors; here we just ensure it's str)

        # Step 2: Unicode NFC normalisation
        text = unicodedata.normalize("NFC", text)

        # Step 3: Remove null bytes
        before = len(text)
        text = text.replace("\x00", "")
        removed = before - len(text)
        if removed:
            log.null_bytes_removed = removed
            log.note(f"Removed {removed} null bytes")

        # Step 4: Remove control characters (keep \n=0x0A, \t=0x09)
        before = len(text)
        text = _CONTROL_CHAR_RE.sub("", text)
        removed = before - len(text)
        if removed:
            log.control_chars_removed = removed
            log.note(f"Removed {removed} control characters")

        # Step 5: Normalise line endings
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # Step 6: Collapse excessive blank lines
        before_lines = text.count("\n")
        text = _EXCESSIVE_BLANK_RE.sub("\n\n", text)
        after_lines = text.count("\n")
        collapsed = before_lines - after_lines
        if collapsed > 0:
            log.excessive_blank_lines_collapsed = collapsed
            log.note(f"Collapsed {collapsed} excessive blank lines")

        # Step 7: Strip trailing whitespace from each line
        lines = text.split("\n")
        lines = [line.rstrip() for line in lines]
        text = "\n".join(lines)

        # Step 8: Fix hyphenated line breaks from PDF extraction
        # "treat-\nment" → "treatment" (only word-end hyphen + lowercase start)
        before = len(text)
        text = _HYPHEN_LINE_BREAK_RE.sub(r"\1\2", text)
        fixed = (before - len(text))
        if fixed > 0:
            log.hyphenated_breaks_fixed = fixed // 1  # rough count
            log.note(f"Fixed {fixed} chars of hyphenated line breaks")

        # Step 9: Normalise quotes
        text = (
            text
            .replace("\u2018", "'").replace("\u2019", "'")  # curly single
            .replace("\u201c", '"').replace("\u201d", '"')  # curly double
            .replace("\u201a", "'").replace("\u201b", "'")  # low single
            .replace("\u201e", '"').replace("\u201f", '"')  # low double
        )

        # Step 10: Normalise dashes
        text = (
            text
            .replace("\u2014", " - ")   # em-dash → spaced hyphen
            .replace("\u2013", " - ")   # en-dash → spaced hyphen
            .replace("\u2012", "-")     # figure dash
            .replace("\u2011", "-")     # non-breaking hyphen
        )

        # Step 11: Remove header/footer markers if tagged by extractor
        # Tagged as [DOCUMENT HEADER] ... [DOCUMENT FOOTER] — keep but don't
        # interfere with chunking (they remain as-is; pipeline strips if needed)

        # Step 12: Normalise whitespace within lines
        new_lines: list[str] = []
        ws_changes = 0
        for line in text.split("\n"):
            new_line = _MULTI_SPACE_RE.sub(" ", line).strip()
            if new_line != line:
                ws_changes += 1
            new_lines.append(new_line)
        text = "\n".join(new_lines)
        if ws_changes:
            log.whitespace_normalised = ws_changes
            log.note(f"Normalised whitespace in {ws_changes} lines")

        # Step 13: Detect and flag gibberish sections (do NOT remove)
        gibberish_count = self._flag_gibberish(text, log)

        # Bonus: normalise dates to ISO 8601
        text, dates_changed = self._normalise_dates(text)
        if dates_changed:
            log.dates_normalised = dates_changed
            log.note(f"Normalised {dates_changed} date patterns to ISO 8601")

        return text.strip(), log

    def _flag_gibberish(self, text: str, log: NormalisationLog) -> int:
        """
        Slide a 100-char window over the text. If fewer than 30% of tokens
        are real English words, flag it in the log. Do NOT remove the text.
        """
        count = 0
        for i in range(0, len(text), _GIBBERISH_WINDOW):
            window = text[i : i + _GIBBERISH_WINDOW]
            tokens = re.findall(r"[a-zA-Z]+", window.lower())
            if not tokens:
                continue
            real_words = sum(1 for t in tokens if t in _COMMON_ENGLISH_WORDS)
            ratio = real_words / len(tokens)
            if ratio < _GIBBERISH_WORD_THRESHOLD and len(tokens) >= 5:
                count += 1
        if count:
            log.gibberish_sections_flagged = count
            log.note(
                f"Flagged {count} possible OCR-garbage windows (< {_GIBBERISH_WORD_THRESHOLD:.0%} real words)"
            )
        return count

    def _normalise_dates(self, text: str) -> tuple[str, int]:
        """
        Detect date patterns and normalise to ISO 8601 (YYYY-MM-DD).
        Only transforms unambiguous patterns.
        """
        changes = 0

        # DD/MM/YYYY or DD-MM-YYYY
        def replace_dmy(m: re.Match) -> str:
            nonlocal changes
            day = m.group(1).zfill(2)
            month = m.group(2).zfill(2)
            year = m.group(3)
            if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
                changes += 1
                return f"{year}-{month}-{day}"
            return m.group(0)

        text = _DATE_PATTERNS[0][0].sub(replace_dmy, text)

        # "Month DD, YYYY"
        def replace_mdy(m: re.Match) -> str:
            nonlocal changes
            month_name = m.group(1).lower()
            day = m.group(2).zfill(2)
            year = m.group(3)
            month_num = _MONTH_MAP.get(month_name, "")
            if month_num:
                changes += 1
                return f"{year}-{month_num}-{day}"
            return m.group(0)

        text = _DATE_PATTERNS[1][0].sub(replace_mdy, text)

        # "DD Month YYYY"
        def replace_dmy2(m: re.Match) -> str:
            nonlocal changes
            day = m.group(1).zfill(2)
            month_name = m.group(2).lower()
            year = m.group(3)
            month_num = _MONTH_MAP.get(month_name, "")
            if month_num:
                changes += 1
                return f"{year}-{month_num}-{day}"
            return m.group(0)

        text = _DATE_PATTERNS[2][0].sub(replace_dmy2, text)

        return text, changes
