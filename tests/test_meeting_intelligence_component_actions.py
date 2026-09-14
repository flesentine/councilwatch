from meeting_intelligence import _supplemental_component_formal_action


def _agenda_items():
    return [
        {
            "item_number": "15",
            "section": "DISCUSSION",
            "title": "DIGITAL SIGNAGE POLICY",
        },
        {
            "item_number": "16",
            "section": "DISCUSSION",
            "title": "AUTOMATED LICENSE PLATE RECOGNITION POLICY",
        },
    ]


def test_component_formal_action_recovers_one_supported_compound_component():
    result = _supplemental_component_formal_action(
        "Automated License Plate Recognition and Digital Signage Policy",
        "directed",
        (
            "Agenda Item 15: Council directed staff to return with a proposal "
            "regarding Digital Signage Policy."
        ),
        "notes",
        _agenda_items(),
    )

    assert result is not None
    assert result["topic"] == "Digital Signage Policy"
    assert result["item_number"] == "15"
    assert result["agenda_section"] == "DISCUSSION"
    assert result["action_status"] == "directed"
    assert result["evidence_item_numbers"] == ["15"]
    assert result["agenda_linkage_conflict"] is False
    assert result["validated"] is True


def test_component_formal_action_fails_closed_for_wrong_source_or_nonformal_status():
    topic = "Automated License Plate Recognition and Digital Signage Policy"
    quote = "Agenda Item 15: Council directed staff regarding Digital Signage Policy."

    assert _supplemental_component_formal_action(
        topic,
        "directed",
        quote,
        "agenda",
        _agenda_items(),
    ) is None

    assert _supplemental_component_formal_action(
        topic,
        "discussed",
        quote,
        "notes",
        _agenda_items(),
    ) is None

    assert _supplemental_component_formal_action(
        topic,
        "directed",
        "",
        "notes",
        _agenda_items(),
    ) is None


def test_component_formal_action_is_not_needed_when_quote_supports_full_parent():
    assert _supplemental_component_formal_action(
        "Automated License Plate Recognition and Digital Signage Policy",
        "directed",
        (
            "Council directed staff regarding Automated License Plate Recognition "
            "and Digital Signage Policy."
        ),
        "notes",
        _agenda_items(),
    ) is None


def test_component_formal_action_rejects_quote_without_claimed_action_language():
    assert _supplemental_component_formal_action(
        "Automated License Plate Recognition and Digital Signage Policy",
        "directed",
        "Agenda Item 15: Council discussed Digital Signage Policy.",
        "notes",
        _agenda_items(),
    ) is None


def test_component_formal_action_marks_conflicting_source_item_number():
    result = _supplemental_component_formal_action(
        "Automated License Plate Recognition and Digital Signage Policy",
        "directed",
        "Agenda Item 99: Council directed staff regarding Digital Signage Policy.",
        "notes",
        _agenda_items(),
    )

    assert result is not None
    assert result["item_number"] == "15"
    assert result["evidence_item_numbers"] == ["99"]
    assert result["agenda_linkage_conflict"] is True
    assert "conflicts with the official agenda mapping" in result["validation_note"]


def test_component_formal_action_accepts_unique_consent_leaf_shorthand():
    agenda_items = [
        {
            "item_number": "5.6",
            "section": "CONSENT CALENDAR",
            "title": "DIGITAL SIGNAGE POLICY",
        },
        {
            "item_number": "5.7",
            "section": "CONSENT CALENDAR",
            "title": "AUTOMATED LICENSE PLATE RECOGNITION POLICY",
        },
    ]

    result = _supplemental_component_formal_action(
        "Automated License Plate Recognition and Digital Signage Policy",
        "directed",
        "Agenda Item 6: Council directed staff regarding Digital Signage Policy.",
        "notes",
        agenda_items,
    )

    assert result is not None
    assert result["item_number"] == "5.6"
    assert result["evidence_item_numbers"] == ["6"]
    assert result["agenda_linkage_conflict"] is False
