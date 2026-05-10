from app.ai_core.classification import Classifier


def test_classifier_marks_death_sae_as_critical_review_required():
    classifier = Classifier()
    text = "Patient ID PT-1 had event date 2026-01-01. Outcome: death. Causality possible."

    scorecard = classifier.classify("DOC-SAE", "sae", text)

    assert scorecard.classification.priority == "critical"
    assert scorecard.classification.sae_severity == "death"
    assert scorecard.classification.requires_review is True
    assert scorecard.classification.risk_score >= 0.9


def test_classifier_reports_missing_required_fields():
    classifier = Classifier()

    scorecard = classifier.classify("DOC-DRUG", "drug", "Drug name is present only.")

    assert "applicant_name" in scorecard.missing_required_fields
    assert scorecard.completeness_score < 1
