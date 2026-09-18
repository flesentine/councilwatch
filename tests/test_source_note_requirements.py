from meeting_intelligence import source_note_audit_requirements


def test_source_note_requirements_preserve_pilot_failure_details():
    text = source_note_audit_requirements()

    assert "exact dollar amounts" in text
    assert "addresses" in text
    assert "square footage" in text
    assert "suite numbers" in text
    assert "contract length" in text
    assert "data acquisition" in text
    assert "retention" in text
    assert "City-owned systems" in text
    assert "privately owned systems" in text
    assert "Do NOT round" in text
    assert "Do NOT merge facts from adjacent topics" in text
    assert "agenda recommendation" in text
