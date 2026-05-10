from app.ai_core.summarisation import Summariser


def test_summariser_returns_schema_valid_regulatory_summary():
    summariser = Summariser()
    text = (
        "Clinical Trial Phase 3 protocol evaluates efficacy endpoint. "
        "The protocol includes adverse event monitoring and eligibility criteria. "
        "Sponsor proposes standard reviewer follow-up."
    )

    result = summariser.summarise("DOC-9", "clinical_trial", text, {"protocol_number": "CT-001"})

    assert result.doc_id == "DOC-9"
    assert result.submission_type == "clinical_trial"
    assert result.word_count > 0
    assert result.key_findings
    assert result.confidence >= 0.7
