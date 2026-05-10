from app.ai_core.comparison import Comparator


def test_comparator_generates_diff_report_and_significance():
    comparator = Comparator()

    result = comparator.compare(
        "CMP-1",
        "DOC-A",
        "DOC-B",
        "Indication: fever\nDosage: 5mg\nSafety: no contraindication",
        "Indication: fever\nDosage: 10mg\nSafety: renal contraindication added",
        ["dosage", "safety"],
    )

    assert result.report_id.startswith("RPT-")
    assert result.additions >= 1
    assert result.deletions >= 1
    assert "dosage" in result.changed_sections
    assert any("contraindication" in note for note in result.regulatory_significance)
