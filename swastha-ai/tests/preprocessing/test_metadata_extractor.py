import pytest
from app.preprocessing.metadata.extractor import MetadataExtractor


@pytest.fixture
def extractor():
    return MetadataExtractor()


def test_universal_metadata_extraction(extractor):
    text = """
    # Clinical Trial Protocol
    Version 2.3
    Application No: CT-123456
    Date: 2024-05-12
    Some random text here.
    """
    result = extractor.extract(
        text=text,
        submission_type="clinical_trial",
        original_filename="protocol_final.pdf",
        page_count=10,
        word_count=500,
        doc_properties={"author": "Dr. Smith"}
    )
    
    assert result["page_count"] == 10
    assert result["document_title"] == "Clinical Trial Protocol"
    assert result["document_version"] == "2.3"
    assert result["application_number"] == "CT-123456"
    assert result["submission_date_mentioned"] == "2024-05-12"
    assert result["author"] == "Dr. Smith"


def test_drug_metadata_extraction(extractor):
    text = """
    Applicant: Pfizer India Ltd
    Active Ingredient: Paracetamol
    Trade Name: Crocin Advance
    Proposed Indication: Relief from mild to moderate pain and fever.
    """
    result = extractor.extract(
        text=text,
        submission_type="drug",
        original_filename="drug_app.pdf",
        page_count=2,
        word_count=100
    )
    
    assert result["applicant"] == "Pfizer India Ltd"
    assert result["inn"] == "Paracetamol"
    assert result["brand"] == "Crocin Advance"
    assert result["indication"] == "Relief from mild to moderate pain and fever."


def test_sae_metadata_extraction(extractor):
    text = """
    Report Type: Initial
    Event: Hospitalisation
    Suspect Drug: Aspirin 50mg
    """
    result = extractor.extract(
        text=text,
        submission_type="sae",
        original_filename="sae_report.pdf",
        page_count=1,
        word_count=50
    )
    
    assert result["report_type"].lower() == "initial"
    assert result["event_type"].lower() == "hospitalisation"
    assert result["suspect_drug"] == "Aspirin 50mg"


def test_device_metadata_extraction(extractor):
    text = """
    Device Name: Heart pacemaker X1
    Class of Device: Class C
    Manufacturer: Medtronic Inc.
    """
    result = extractor.extract(
        text=text,
        submission_type="medical_device",
        original_filename="device_doc.pdf",
        page_count=1,
        word_count=50
    )
    
    assert result["device_name"] == "Heart pacemaker X1"
    assert result["device_class"].upper() == "C"
    assert result["manufacturer"] == "Medtronic Inc."


def test_title_fallback_to_filename(extractor):
    text = "Just some text without a markdown heading."
    result = extractor.extract(
        text=text,
        submission_type="drug",
        original_filename="my_document_v1.pdf",
        page_count=1,
        word_count=10
    )
    
    assert result["document_title"] == "my document v1"
