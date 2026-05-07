import pytest
from app.preprocessing.extractors.xml_extractor import XMLExtractor


@pytest.mark.asyncio
async def test_xml_extractor_fhir_detection():
    extractor = XMLExtractor()
    
    fhir_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <Patient xmlns="http://hl7.org/fhir">
      <id value="example"/>
      <active value="true"/>
      <name>
        <family value="Doe"/>
        <given value="John"/>
      </name>
      <gender value="male"/>
    </Patient>
    """
    
    result = await extractor.extract(fhir_xml)
    
    assert "FORMAT_DETECTED_HL7_FHIR" in result.extraction_warnings
    assert len(result.pages) == 1
    text = result.pages[0].text
    
    assert "[HL7 FHIR DOCUMENT]" in text
    assert "Resource Type: Patient" in text
    assert "id.value: example" in text
    assert "name.family.value: Doe" in text
    assert "gender.value: male" in text


@pytest.mark.asyncio
async def test_xml_extractor_generic_flattening():
    extractor = XMLExtractor()
    
    generic_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <submission type="drug">
      <applicant name="PharmaCo">
        <address>123 Science Way</address>
      </applicant>
      <product>
        <name>CureAll</name>
        <dosage>10mg</dosage>
      </product>
    </submission>
    """
    
    result = await extractor.extract(generic_xml)
    
    assert "FORMAT_DETECTED_HL7_FHIR" not in result.extraction_warnings
    text = result.pages[0].text
    
    assert "submission[@type]: drug" in text
    assert "submission.applicant[@name]: PharmaCo" in text
    assert "submission.applicant.address: 123 Science Way" in text
    assert "submission.product.name: CureAll" in text
    assert "submission.product.dosage: 10mg"


@pytest.mark.asyncio
async def test_xml_extractor_malformed_recovery():
    extractor = XMLExtractor()
    
    # Missing closing tag for applicant
    malformed_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <submission>
      <applicant>PharmaCo
      <product>CureAll</product>
    </submission>
    """
    
    result = await extractor.extract(malformed_xml)
    
    assert any("XML_PARSE_ERROR_RECOVERY_ATTEMPTED" in w for w in result.extraction_warnings)
    text = result.pages[0].text
    
    # html.parser recovery should still pull out the text content
    assert "PharmaCo" in text
    assert "CureAll" in text
