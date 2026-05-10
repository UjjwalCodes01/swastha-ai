from app.compliance.dpdp import DPDPChecker
from app.compliance.icmr import ICMRChecker
from app.compliance.pii_leak_detector import PIILeakDetector
from app.compliance.xai import XAILogger


def test_pii_leak_detector_flags_remaining_identifiers():
    detector = PIILeakDetector()

    findings = detector.detect("Anonymised text still contains Aadhaar 1234 5678 9012.")

    assert findings
    assert findings[0].rule_id == "DPDP-PII-LEAK"
    assert findings[0].severity == "critical"


def test_dpdp_summary_check_blocks_direct_identifiers():
    checker = DPDPChecker()

    findings = checker.check_summary({"executive_summary": "Contact applicant@example.org for details."})

    assert findings
    assert findings[0].rule_id == "DPDP-SUMMARY-IDENTIFIER"


def test_icmr_requires_review_for_death_sae():
    checker = ICMRChecker()

    findings = checker.check_classification(
        {
            "classification": {
                "sae_severity": "death",
                "priority": "critical",
                "requires_review": False,
            }
        }
    )

    assert any(finding.rule_id == "ICMR-SAE-MANDATORY-REVIEW" for finding in findings)


def test_xai_logger_extracts_classification_rationale():
    xai = XAILogger()

    record = xai.from_payload(
        "documents.classified",
        {
            "doc_id": "DOC-1",
            "model_id": "classifier",
            "model_version": "1",
            "classification": {
                "category": "SAE",
                "priority": "critical",
                "requires_review": True,
                "risk_score": 0.98,
                "labels": ["sae:death"],
                "confidence": 0.91,
            },
        },
    )

    assert record.module_name == "classification"
    assert record.confidence == 0.91
    assert "Risk score" in record.rationale[0]
