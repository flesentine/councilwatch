import pytest

from meeting_intelligence import (
    _action_evidence_quote_is_bounded,
    _action_topic_component_labels,
    _action_topic_components,
    _action_word_root,
    _action_words,
    _agenda_identity_supported_for_source,
    _agenda_item_number_value,
    _agenda_item_sort_key,
    _agenda_item_transition_numbers,
    _agenda_match_score,
    _agenda_section_from_heading,
    _best_supported_consent_action_quote,
    _best_supported_formal_action_quote,
    _best_supported_raw_council_commentary_quote,
    _candidate_has_foreign_agenda_transition,
    _canonical_agenda_section,
    _canonical_formal_status_from_quote,
    _conflicted_generic_collective_formal_action,
    _consent_item_source_labels,
    _evidence_agenda_item_numbers,
    _evidence_text_norm,
    _formal_action_has_topic_support,
    _formal_status_supported,
    _local_topic_anchor_supported,
    _quote_is_in_source,
    _raw_council_commentary_supported,
    _resolve_agenda_item,
    _single_line_transcript_turn_windows,
    _topic_scope_supported,
    _turn_window_agenda_identity_supported,
    _turn_window_formal_finality_supported,
    _turn_window_topic_identity_span,
    parse_agenda_structure,
)


def test_agenda_section_normalization_aliases_and_curly_apostrophe():
    assert _canonical_agenda_section("Public Hearing Items") == "PUBLIC HEARINGS"
    assert _canonical_agenda_section("  consent   calendar items  ") == "CONSENT CALENDAR"
    assert _canonical_agenda_section("not a section") == ""

    assert _agenda_section_from_heading("Discussion / Action Items:") == "DISCUSSION"
    assert _agenda_section_from_heading("CITY MANAGER’S REPORT:") == "CITY MANAGER REPORTS"
    assert _agenda_section_from_heading("Community Input") == "PUBLIC COMMENTS"
    assert _agenda_section_from_heading("random body text") == ""


def test_parse_agenda_structure_handles_realistic_mixed_layouts():
    agenda = """
    CONSENT CALENDAR ITEMS

    5.6
    GEOTECHNICAL SERVICES AGREEMENTS
    FOR CAPITAL PROJECTS
    STAFF RECOMMENDS APPROVAL

    5.7 Traffic Signal Maintenance Contract

    PUBLIC HEARING ITEMS:
    21. ZONING AND DEVELOPMENT CODE AMENDMENTS

    DISCUSSION/ACTION ITEMS
    14. AWARD OF PROFESSIONAL CONSULTANT SERVICES AGREEMENT
    FOR SPORTS PARK RENOVATIONS
    15. CAPITAL IMPROVEMENT PLAN UPDATE

    30111 Crown Valley Parkway
    2026
    """

    items = parse_agenda_structure(agenda)

    assert items == [
        {
            "item_number": "5.6",
            "section": "CONSENT CALENDAR",
            "title": "GEOTECHNICAL SERVICES AGREEMENTS FOR CAPITAL PROJECTS",
        },
        {
            "item_number": "5.7",
            "section": "CONSENT CALENDAR",
            "title": "Traffic Signal Maintenance Contract",
        },
        {
            "item_number": "21",
            "section": "PUBLIC HEARINGS",
            "title": "ZONING AND DEVELOPMENT CODE AMENDMENTS",
        },
        {
            "item_number": "14",
            "section": "DISCUSSION",
            "title": (
                "AWARD OF PROFESSIONAL CONSULTANT SERVICES AGREEMENT "
                "FOR SPORTS PARK RENOVATIONS"
            ),
        },
        {
            "item_number": "15",
            "section": "DISCUSSION",
            "title": "CAPITAL IMPROVEMENT PLAN UPDATE",
        },
    ]


def test_parse_agenda_numbered_section_tracks_major_and_clears_on_mismatch():
    agenda = """
    5. CONSENT CALENDAR ITEMS
    5.6 Security and Access Control Systems Agreement
    6.1 Unrelated Major Section Item
    """

    items = parse_agenda_structure(agenda)

    assert items[0]["section"] == "CONSENT CALENDAR"
    assert items[0]["item_number"] == "5.6"
    assert items[1]["section"] == ""
    assert items[1]["item_number"] == "6.1"


def test_action_word_normalization_and_components():
    assert _action_word_root("policies") == "policy"
    assert _action_word_root("cameras") == "camera"
    assert _action_word_root("glass") == "glass"

    assert _action_words("City Council cameras and policies update") == {
        "camera",
        "policy",
    }

    topic = "Automated License Plate Recognition (ALPR) and Digital Signage"
    labels = _action_topic_component_labels(topic)
    components = _action_topic_components(topic)

    assert labels == [
        "Automated License Plate Recognition (ALPR)",
        "Digital Signage",
    ]
    assert len(components) == 2
    assert "alpr" in components[0]
    assert {"digital", "signage"}.issubset(components[1])


def test_topic_scope_requires_full_compound_support():
    topic = "Automated License Plate Recognition (ALPR) and Digital Signage"

    assert _topic_scope_supported(
        topic,
        "Council discussed ALPR cameras and digital signage locations.",
    )

    assert not _topic_scope_supported(
        topic,
        "Council discussed ALPR cameras and retention policy.",
    )

    assert _topic_scope_supported(
        "Capital Improvement Plan Update",
        "Council discussed the capital improvement plan.",
    )


def test_agenda_match_score_supports_narrow_status_qualifier_fallback():
    assert _agenda_match_score(
        "Historical Preservation Committee and landmark designation",
        "Historical Preservation Committee",
    ) >= 2

    assert _agenda_match_score(
        "ALPR and Digital Signage",
        "Automated License Plate Recognition camera system",
    ) == 0


def test_resolve_agenda_item_prefers_supported_official_mapping():
    agenda_items = [
        {
            "item_number": "5.9",
            "section": "CONSENT CALENDAR",
            "title": "SCHOOL ZONE SPEED LIMITS",
        },
        {
            "item_number": "5.10",
            "section": "CONSENT CALENDAR",
            "title": "AGENDA MANAGEMENT SOFTWARE",
        },
    ]

    assert _resolve_agenda_item(
        "School Zone Speed Limits",
        "5.9",
        agenda_items,
    ) == agenda_items[0]

    assert _resolve_agenda_item(
        "School Zone Speed Limits",
        "5.10",
        agenda_items,
    ) == agenda_items[0]

    assert _resolve_agenda_item(
        "Completely unrelated topic",
        "99",
        agenda_items,
    ) is None


def test_resolve_agenda_item_allows_exact_number_three_word_fallback():
    agenda_items = [
        {
            "item_number": "4.6",
            "section": "CONSENT CALENDAR",
            "title": "HISTORICAL PRESERVATION COMMITTEE",
        }
    ]

    resolved = _resolve_agenda_item(
        "Historical Preservation Committee and Security Access Control",
        "4.6",
        agenda_items,
    )

    assert resolved == agenda_items[0]


def test_evidence_item_number_extraction_and_numeric_sorting():
    evidence = (
        "Agenda Items 5.6 and 5.9 were discussed. "
        "Item 21 was heard later. Item 6.2 was renumbered to 6.3."
    )

    assert _evidence_agenda_item_numbers(evidence) == {
        "5.6",
        "5.9",
        "21",
        "6.2",
        "6.3",
    }

    assert sorted(
        ["21", "5.10", "5.6", "17"],
        key=_agenda_item_sort_key,
    ) == ["5.6", "5.10", "17", "21"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("021", "21"),
        ("seven", "7"),
        ("twenty-one", "21"),
        ("thirty two", "32"),
        ("ninety", "90"),
        ("one hundred", ""),
        ("5.6", ""),
    ],
)
def test_agenda_item_number_value(raw, expected):
    assert _agenda_item_number_value(raw) == expected


def test_transition_numbers_only_capture_actual_agenda_boundaries():
    text = (
        "We are moving on to agenda item twenty-one. "
        "Later the clerk read the title for item number 22. "
        "Someone merely referenced item 3 in discussion."
    )

    assert _agenda_item_transition_numbers(text) == {"21", "22"}
    assert not _candidate_has_foreign_agenda_transition(text, "21") is False
    assert _candidate_has_foreign_agenda_transition(text, "20")


def test_evidence_markdown_normalization_and_membership():
    source = """
    * **Motion** to approve: Councilmember O\\'Connor
    The motion passed unanimously.
    """

    assert _evidence_text_norm(
        "* **Motion** to approve: Councilmember O\\'Connor"
    ) == "motion to approve: councilmember o'connor"

    assert _quote_is_in_source(
        "Motion to approve: Councilmember O'Connor",
        source,
    )

    assert not _quote_is_in_source(
        "Motion to deny the item",
        source,
    )


def test_consent_item_source_labels_allow_only_unique_leaf_shorthand():
    agenda_items = [
        {"item_number": "5.6", "section": "CONSENT CALENDAR", "title": "A"},
        {"item_number": "5.7", "section": "CONSENT CALENDAR", "title": "B"},
        {"item_number": "6.6", "section": "DISCUSSION", "title": "C"},
    ]

    assert _consent_item_source_labels("5.6", agenda_items) == {"5.6", "6"}
    assert _consent_item_source_labels("", agenda_items) == set()

    agenda_items.append(
        {"item_number": "4.6", "section": "CONSENT CALENDAR", "title": "D"}
    )

    assert _consent_item_source_labels("5.6", agenda_items) == {"5.6"}


def test_consent_action_quote_accepts_unambiguous_leaf_block():
    agenda_items = [
        {
            "item_number": "5.6",
            "section": "CONSENT CALENDAR",
            "title": "GEOTECHNICAL SERVICES AGREEMENTS",
        },
        {
            "item_number": "5.7",
            "section": "CONSENT CALENDAR",
            "title": "TRAFFIC SIGNAL MAINTENANCE",
        },
    ]

    notes = """
Consent Calendar (Items 6, 7, 8)
Motion to approve the Consent Calendar passed unanimously.

Agenda Item 9 was discussed separately.
"""

    quote = _best_supported_consent_action_quote(
        "5.6",
        agenda_items,
        notes,
    )

    assert quote is not None
    assert "Items 6, 7, 8" in quote
    assert "passed unanimously" in quote


def test_consent_action_quote_rejects_conflicting_dotted_item():
    agenda_items = [
        {
            "item_number": "4.6",
            "section": "CONSENT CALENDAR",
            "title": "SECURITY AND ACCESS CONTROL SYSTEMS AGREEMENT",
        }
    ]

    notes = """
Consent Calendar (Agenda Item 6)
Item 6.1: Historical Preservation Committee
Motion to approve the Consent Calendar passed unanimously.
"""

    assert _best_supported_consent_action_quote(
        "4.6",
        agenda_items,
        notes,
    ) is None


@pytest.mark.parametrize(
    ("status", "quote", "expected"),
    [
        ("approved", "Council approved the agreement.", True),
        ("adopted", "Council adopted the ordinance.", True),
        ("appointed", "Council appointed the delegate.", True),
        (
            "approved",
            "A motion was made to form the committee. The motion passed unanimously.",
            True,
        ),
        ("denied", "A motion to approve the request passed.", False),
        ("unknown", "The motion passed.", False),
    ],
)
def test_formal_status_supported(status, quote, expected):
    assert _formal_status_supported(status, quote) is expected


@pytest.mark.parametrize(
    ("quote", "expected"),
    [
        ("Motion to adopt the ordinance passed 5-0.", "adopted"),
        ("Motion to appoint the delegate passed unanimously.", "appointed"),
        ("Motion to form the committee passed 4-1.", "approved"),
        ("The motion passed.", "passed"),
    ],
)
def test_canonical_formal_status_prefers_specific_action(quote, expected):
    assert _canonical_formal_status_from_quote("passed", quote) == expected

    assert _canonical_formal_status_from_quote(
        "denied",
        quote,
    ) == "denied"


def test_conflicted_collective_consent_action_fails_closed():
    agenda_items = [
        {
            "item_number": "21",
            "section": "PUBLIC HEARINGS",
            "title": "ZONING AND DEVELOPMENT CODE AMENDMENTS",
        }
    ]

    generic = (
        "Discussion occurred regarding agenda items 17 and 18. "
        "The Consent Calendar was approved unanimously."
    )

    assert _conflicted_generic_collective_formal_action(
        "21",
        "PUBLIC HEARINGS",
        "approved",
        generic,
        agenda_items,
    )

    specific = (
        "A motion to approve the zoning amendments passed 5-0. "
        "Discussion also referenced agenda items 17 and 18. "
        "The Consent Calendar was approved unanimously."
    )

    assert not _conflicted_generic_collective_formal_action(
        "21",
        "PUBLIC HEARINGS",
        "approved",
        specific,
        agenda_items,
    )

    assert not _conflicted_generic_collective_formal_action(
        "21",
        "PUBLIC HEARINGS",
        "approved",
        "Agenda Item 21 was approved.",
        agenda_items,
    )


def test_formal_action_topic_support_requires_action_and_claimed_status():
    quote = "Agenda Item 21: Motion to approve zoning amendments passed 5-0."

    assert _formal_action_has_topic_support(
        "Zoning and Development Code Amendments",
        "21",
        quote,
        "approved",
    )

    assert not _formal_action_has_topic_support(
        "Zoning and Development Code Amendments",
        "21",
        quote,
        "adopted",
    )

    assert not _formal_action_has_topic_support(
        "Zoning and Development Code Amendments",
        "21",
        "Agenda Item 21 was discussed at length.",
        "approved",
    )


def test_single_line_turn_windows_require_raw_transcript_shape_and_are_bounded():
    turns = [f"speaker {index} text" for index in range(10)]
    source = " >> ".join(turns)

    windows = _single_line_transcript_turn_windows(
        source,
        max_turns=2,
        max_chars=100,
    )

    assert windows
    assert "speaker 0 text" in windows
    assert "speaker 9 text" in windows
    assert all(len(window) <= 100 for window in windows)

    assert _single_line_transcript_turn_windows("one\ntwo\nthree") == []
    assert _single_line_transcript_turn_windows("a >> b >> c") == []
    assert _single_line_transcript_turn_windows("") == []


def test_action_evidence_quote_bounding_only_applies_to_long_raw_transcripts():
    source = " >> ".join(["x" * 2500 for _ in range(9)])

    assert _action_evidence_quote_is_bounded("q" * 16000, source)
    assert not _action_evidence_quote_is_bounded("q" * 16001, source)
    assert not _action_evidence_quote_is_bounded("", source)

    assert _action_evidence_quote_is_bounded(
        "normal quote",
        "normal multi-line\nstructured notes\nwith evidence",
    )


def test_topic_identity_span_requires_strong_local_cluster():
    topic = "Electric Bicycle Municipal Code Amendments"

    identity = _turn_window_topic_identity_span(
        topic,
        "Council discussed electric bicycle municipal code amendments and safety.",
    )

    assert identity is not None
    assert len(identity[2]) >= 3

    assert _turn_window_topic_identity_span(
        topic,
        "A different municipal code matter came up.",
    ) is None


def test_local_topic_anchor_handles_ebike_spelling_variants():
    assert _local_topic_anchor_supported(
        "E-bike Safety Advocacy",
        "A resident spoke about ebike safety and helmet education.",
    )

    assert not _local_topic_anchor_supported(
        "E-bike Safety Advocacy",
        "A resident discussed an unrelated city budget matter.",
    )


def test_raw_council_commentary_requires_council_speaker_context():
    substantive = (
        "I believe electric bicycle safety policy needs additional work, and I support "
        "continuing the current safety rules while staff studies enforcement options "
        "for riders, schools, and families throughout the community."
    )

    assert _raw_council_commentary_supported(
        "Electric Bicycle Safety Policy",
        f"Council Member Smith >> {substantive}",
    )

    assert not _raw_council_commentary_supported(
        "Electric Bicycle Safety Policy",
        f"Resident Jane Doe >> {substantive}",
    )


def test_best_raw_council_commentary_recovers_exact_local_turn_window():
    substantive = (
        "I believe electric bicycle safety policy needs additional work, and I support "
        "continuing the current safety rules while staff studies enforcement options "
        "for riders, schools, and families throughout the community."
    )

    turns = [
        "Opening remarks",
        "Council Member Smith",
        substantive,
        "filler one",
        "filler two",
        "filler three",
        "filler four",
        "filler five",
        "filler six",
    ]

    result = _best_supported_raw_council_commentary_quote(
        "Electric Bicycle Safety Policy",
        " >> ".join(turns),
    )

    assert result is not None
    assert "Council Member Smith" in result
    assert "electric bicycle safety policy" in result


def test_turn_window_agenda_identity_rejects_partial_compound_topic():
    assert _turn_window_agenda_identity_supported(
        "Capital Improvement Plan Update",
        "CAPITAL IMPROVEMENT PLAN UPDATE",
        "Council discussed the capital improvement plan update.",
    )

    assert not _turn_window_agenda_identity_supported(
        "ALPR and Digital Signage",
        "ALPR AND DIGITAL SIGNAGE",
        "Council discussed automated license plate recognition cameras only.",
    )


def test_agenda_identity_rule_applies_only_to_raw_turn_transcripts():
    structured = "Structured notes\nwith multiple\nlines"

    assert _agenda_identity_supported_for_source(
        "Anything",
        "Anything",
        "unrelated quote",
        structured,
    )

    raw_source = " >> ".join([f"turn {i}" for i in range(9)])

    assert not _agenda_identity_supported_for_source(
        "Capital Improvement Plan Update",
        "CAPITAL IMPROVEMENT PLAN UPDATE",
        "unrelated quote",
        raw_source,
    )


def test_turn_window_formal_finality_requires_action_after_topic_identity():
    topic = "Property Tax Allocation Correction"

    good = (
        "Property tax allocation correction was presented. "
        "Council adopted the resolution and the motion passed unanimously."
    )

    assert _turn_window_formal_finality_supported(
        topic,
        "adopted",
        good,
    )

    bad = (
        "The prior motion passed unanimously. "
        "Property tax allocation correction was then presented for discussion."
    )

    assert not _turn_window_formal_finality_supported(
        topic,
        "passed",
        bad,
    )


def test_best_supported_formal_action_quote_prefers_topic_specific_block():
    notes = """
The council discussed unrelated housekeeping and approved minutes.

Property Tax Allocation Correction was presented. Council adopted the resolution.
"""

    result = _best_supported_formal_action_quote(
        "Property Tax Allocation Correction",
        "adopted",
        notes,
        agenda_title="PROPERTY TAX ALLOCATION CORRECTION",
    )

    assert result is not None
    assert "Property Tax Allocation Correction" in result
    assert "adopted the resolution" in result
