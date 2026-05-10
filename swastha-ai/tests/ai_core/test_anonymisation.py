from app.ai_core.anonymisation import Anonymiser


def test_anonymiser_replaces_indian_pii_with_stable_pseudonyms():
    anonymiser = Anonymiser()
    text = (
        "Patient ID PT-12345, Aadhaar 1234 5678 9012, phone +91 9876543210 "
        "and email reviewer@example.org were submitted."
    )

    result = anonymiser.anonymise("DOC-123", text)

    assert result.entity_count >= 4
    assert "1234 5678 9012" not in result.anonymised_text
    assert "9876543210" not in result.anonymised_text
    assert "reviewer@example.org" not in result.anonymised_text
    assert "<AADHAAR_" in result.anonymised_text
    assert result.confidence > 0.85


def test_anonymiser_uses_same_pseudonym_for_repeated_values():
    anonymiser = Anonymiser()
    result = anonymiser.anonymise("DOC-123", "Email a@b.co appears twice: a@b.co.")
    email_entities = [entity for entity in result.entities if entity.entity_type == "EMAIL"]

    assert len(email_entities) == 2
    assert email_entities[0].pseudonym == email_entities[1].pseudonym
