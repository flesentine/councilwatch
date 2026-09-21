import pytest

from meeting_intelligence import (
    CoverageItem,
    CoveragePlan,
    _action_evidence_quote_is_bounded,
    _agenda_item_source_block,
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
    _best_supported_consent_remainder_quote,
    _best_supported_formal_action_quote,
    _best_supported_raw_council_commentary_quote,
    _candidate_has_foreign_agenda_transition,
    _canonical_action_status,
    _dedupe_action_ledger_rows,
    _ledger_agenda_linkage_conflict,
    _canonical_agenda_section,
    _canonical_formal_status_from_quote,
    _conflicted_generic_collective_formal_action,
    _consent_item_source_labels,
    _evidence_agenda_item_numbers,
    _evidence_text_norm,
    _formal_action_has_topic_support,
    _formal_action_record_supported,
    _formal_status_supported,
    _guard_coverage_plan_money_values,
    _local_topic_anchor_supported,
    _nonformal_source_supported,
    _quote_is_in_source,
    _reconcile_coverage_items_with_action_ledger,
    _raw_council_commentary_supported,
    _resolve_agenda_item,
    _resolve_agenda_item_from_source_block,
    _single_line_transcript_turn_windows,
    _topic_scope_supported,
    _turn_window_agenda_identity_supported,
    _turn_window_formal_finality_supported,
    _turn_window_topic_identity_span,
    parse_agenda_structure,
    substantive_formal_agenda_items,
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


def test_agenda_item_source_block_accepts_number_dot_title():
    agenda = """
DISCUSSION/ACTION ITEMS

12. ENCROACHMENT PERMIT DEPOSITS FOR PUBLIC UTILITIES
Fee Study Follow-Up: Encroachment Permit Deposits for Public Utilities
City Council Discretion

13. OTHER ITEM
Background text.
"""

    block = _agenda_item_source_block(
        agenda,
        "12",
    )

    assert "ENCROACHMENT PERMIT DEPOSITS" in block
    assert "Fee Study Follow-Up" in block
    assert "OTHER ITEM" not in block


def test_resolve_agenda_item_from_source_block_links_fee_study_alias():
    agenda = """
DISCUSSION/ACTION ITEMS

12. ENCROACHMENT PERMIT DEPOSITS FOR PUBLIC UTILITIES
Fee Study Follow-Up: Encroachment Permit Deposits for Public Utilities
The comprehensive fee study included public works fee deposits.
City Council Discretion

13. CAPITAL IMPROVEMENT PLAN UPDATE
Staff will review capital projects.
"""

    items = parse_agenda_structure(
        agenda
    )

    resolved = _resolve_agenda_item_from_source_block(
        "Comprehensive Fee Study Receive and File",
        "",
        items,
        agenda,
    )

    assert resolved is not None
    assert resolved["item_number"] == "12"
    assert resolved["title"] == (
        "ENCROACHMENT PERMIT DEPOSITS FOR PUBLIC UTILITIES"
    )


def test_resolve_agenda_item_from_source_block_prefers_supported_exact_number():
    agenda = """
DISCUSSION/ACTION ITEMS

12. FIRST FEE ITEM
The comprehensive fee study covers permit deposits here.

13. SECOND FEE ITEM
The comprehensive fee study covers another permit issue here.
"""

    items = parse_agenda_structure(
        agenda
    )

    resolved = _resolve_agenda_item_from_source_block(
        "Comprehensive Fee Study Receive and File",
        "12",
        items,
        agenda,
    )

    assert resolved is not None
    assert resolved["item_number"] == "12"


def test_resolve_agenda_item_from_source_block_fails_closed_when_ambiguous():
    agenda = """
DISCUSSION/ACTION ITEMS

12. FIRST FEE ITEM
The comprehensive fee study covers this item.

13. SECOND FEE ITEM
The comprehensive fee study covers this item too.
"""

    items = parse_agenda_structure(
        agenda
    )

    assert _resolve_agenda_item_from_source_block(
        "Comprehensive Fee Study Receive and File",
        "",
        items,
        agenda,
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


def test_formal_action_record_gate_rejects_historical_raw_action():
    topic = (
        "FY 2025-26 Community Development Block Grant "
        "Consolidated Annual Performance Evaluation Report"
    )

    quote = (
        "Tonight's presentation is the fiscal year 2025-26 community "
        "development block grant consolidated annual performance "
        "evaluation report. The annual action plan the city council "
        "approved in May of 2025 remains in effect. Staff recommends "
        "the city council adopt the associated resolution."
    )

    notes = " >> ".join(
        [
            quote,
            "filler 1",
            "filler 2",
            "filler 3",
            "filler 4",
            "filler 5",
            "filler 6",
            "filler 7",
            "filler 8",
            "filler 9",
        ]
    )

    assert not _formal_action_record_supported(
        topic=topic,
        status="approved",
        source_name="notes",
        quote=quote,
        quote_valid=True,
        notes=notes,
        agenda_title="",
        item_number="",
        agenda_section="",
        agenda_items=[],
        consent_action_quote=None,
    )


def test_formal_action_record_gate_accepts_current_raw_motion():
    topic = "CDBG Consolidated Annual Performance Evaluation Report"

    quote = (
        "The CDBG consolidated annual performance evaluation report "
        "was presented. The council moved to adopt the report. "
        "The motion passed 4-0."
    )

    notes = " >> ".join(
        [
            quote,
            "filler 1",
            "filler 2",
            "filler 3",
            "filler 4",
            "filler 5",
            "filler 6",
            "filler 7",
            "filler 8",
            "filler 9",
        ]
    )

    assert _formal_action_record_supported(
        topic=topic,
        status="adopted",
        source_name="notes",
        quote=quote,
        quote_valid=True,
        notes=notes,
        agenda_title="",
        item_number="",
        agenda_section="",
        agenda_items=[],
        consent_action_quote=None,
    )


def test_turn_window_formal_finality_rejects_historical_action_after_identity():
    topic = (
        "Fiscal Year 2025-26 CDBG Consolidated Annual "
        "Performance Evaluation Report"
    )

    candidate = (
        "Tonight's presentation is the fiscal year 2025-26 consolidated "
        "annual performance evaluation report. The city council adopted "
        "the 2025-2030 consolidated plan last year on May 6th. The "
        "activities are consistent with the annual action plan the city "
        "council approved in May of 2025. Staff recommends the city "
        "council adopt the associated resolution."
    )

    assert not _turn_window_formal_finality_supported(
        topic,
        "approved",
        candidate,
    )


def test_turn_window_formal_finality_keeps_current_direct_action():
    topic = "CDBG Consolidated Annual Performance Evaluation Report"

    candidate = (
        "The CDBG consolidated annual performance evaluation report "
        "was presented. The council adopted the resolution tonight."
    )

    assert _turn_window_formal_finality_supported(
        topic,
        "adopted",
        candidate,
    )


def test_coverage_reconciliation_uses_official_nonformal_agenda_identity():
    items = [
        {
            "rank": 4,
            "topic": "Complete City Fee Study Receive and File",
            "score": 6,
            "category": "Fees",
            "action_status": "Received and Filed",
            "summary": (
                "The City Council received and filed the complete city "
                "fee study following staff clarification."
            ),
            "why_it_matters": "Municipal fees affect local services.",
            "must_include": False,
        }
    ]

    ledger = [
        {
            "topic": "Complete City Fee Study Receive and File",
            "action_status": "no council action",
            "validated": True,
            "agenda_linkage_conflict": False,
            "agenda_title": (
                "ENCROACHMENT PERMIT DEPOSITS FOR PUBLIC UTILITIES"
            ),
        }
    ]

    assert _reconcile_coverage_items_with_action_ledger(
        items,
        ledger,
    )

    assert items[0]["action_status"] == "No Council Action"
    assert items[0]["topic"] == (
        "Complete City Fee Study Receive and File"
    )
    assert items[0]["display_topic"] == (
        "ENCROACHMENT PERMIT DEPOSITS FOR PUBLIC UTILITIES"
    )
    assert "received and filed" not in items[0]["summary"].lower()
    assert "no council action" in items[0]["summary"].lower()
    assert (
        "encroachment permit deposits for public utilities"
        in items[0]["summary"].lower()
    )


def test_coverage_reconciliation_does_not_call_off_agenda_comment_an_agenda_item():
    items = [
        {
            "rank": 3,
            "topic": "Resident Comment About Parking",
            "score": 6,
            "category": "Public Comment",
            "action_status": "Discussed",
            "summary": "A resident raised parking concerns.",
            "why_it_matters": "Public input.",
            "must_include": False,
        }
    ]

    ledger = [
        {
            "topic": "Resident Comment About Parking",
            "action_status": "no council action",
            "validated": True,
            "agenda_linkage_conflict": False,
            "agenda_title": "",
        }
    ]

    assert _reconcile_coverage_items_with_action_ledger(
        items,
        ledger,
    )

    assert items[0]["action_status"] == "No Council Action"
    assert items[0]["summary"].startswith(
        "Coverage topic: Resident Comment About Parking."
    )
    assert "Agenda item:" not in items[0]["summary"]


def test_coverage_reconciliation_fails_closed_on_multiple_council_actions():
    items = [
        {
            "rank": 1,
            "topic": "Park Contract",
            "score": 9,
            "category": "Contracts",
            "action_status": "Discussed",
            "summary": (
                "The council discussed the park contract, but later "
                "the council rejected it."
            ),
            "why_it_matters": "Public spending.",
            "must_include": True,
        }
    ]

    ledger = [
        {
            "topic": "Park Contract",
            "action_status": "approved",
            "validated": True,
            "agenda_linkage_conflict": False,
            "agenda_title": "PARK CONTRACT",
        }
    ]

    assert _reconcile_coverage_items_with_action_ledger(
        items,
        ledger,
    )

    assert items[0]["action_status"] == "Approved"
    assert items[0]["summary"] == (
        "Validated council disposition for PARK CONTRACT: Approved."
    )
    assert "discussed" not in items[0]["summary"].lower()
    assert "rejected" not in items[0]["summary"].lower()


def test_coverage_reconciliation_normalizes_warrant_summary_to_passed():
    items = [
        {
            "rank": 2,
            "topic": "Warrant Register Approval & Flock Safety Camera Expenditures",
            "score": 8,
            "category": "Budget & Public Safety",
            "action_status": "Approved",
            "summary": (
                "The City Council certified the warrant register, "
                "which included Flock Safety camera expenditures."
            ),
            "why_it_matters": "Tracks municipal spending.",
            "must_include": True,
        }
    ]

    ledger = [
        {
            "topic": "Warrant Register Approval & Flock Safety Camera Expenditures",
            "action_status": "passed",
            "validated": True,
            "agenda_linkage_conflict": False,
            "agenda_title": "CERTIFICATION OF WARRANT REGISTER",
        }
    ]

    assert _reconcile_coverage_items_with_action_ledger(
        items,
        ledger,
    )

    assert items[0]["action_status"] == "Passed"
    assert "Council passed the warrant register" in items[0]["summary"]
    # Formal compound coverage keeps its richer editorial topic label.
    assert items[0]["topic"].startswith("Warrant Register Approval")


def test_coverage_reconciliation_leaves_unmatched_topic_unchanged():
    items = [
        {
            "rank": 1,
            "topic": "Unmatched Topic",
            "score": 9,
            "category": "Other",
            "action_status": "Discussed",
            "summary": "The council discussed the unmatched topic.",
            "why_it_matters": "Context.",
            "must_include": True,
        }
    ]

    original = dict(items[0])

    assert not _reconcile_coverage_items_with_action_ledger(
        items,
        [],
    )

    assert items[0] == original


def test_coverage_money_guard_repairs_spaced_cents_model_error():
    plan = CoveragePlan(
        items=[
            CoverageItem(
                rank=1,
                topic="Warrant Register and Legal Expenditures",
                score=8,
                category="Budget & Finance",
                action_status="Approved",
                summary=(
                    "The council approved a $270,024 payment "
                    "to legal counsel."
                ),
                why_it_matters="Tracks municipal spending.",
                must_include=True,
            )
        ]
    )

    notes = (
        "A speaker protested a check for $270,000 24 "
        "written to legal counsel."
    )

    assert _guard_coverage_plan_money_values(
        plan,
        notes,
        "",
    )

    assert "$270,000.24" in plan.items[0].summary
    assert "$270,024" not in plan.items[0].summary


def test_coverage_money_guard_does_not_snap_unrelated_amount():
    plan = CoveragePlan(
        items=[
            CoverageItem(
                rank=1,
                topic="Budget",
                score=8,
                category="Budget & Finance",
                action_status="Approved",
                summary="The council approved $250,000.",
                why_it_matters="Tracks municipal spending.",
                must_include=True,
            )
        ]
    )

    assert not _guard_coverage_plan_money_values(
        plan,
        "The source discussed $270,000 24.",
        "",
    )

    assert "$250,000" in plan.items[0].summary


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



def test_substantive_formal_agenda_items_catches_aliso_style_actions():
    agenda = """
    1. SPECIAL PRESENTATIONS
    1.1 BUSINESS SPOTLIGHT - FS8 ALISO VIEJO
    1.2 PROCLAMATION - HUNGER ACTION MONTH

    4. CONSENT CALENDAR
    4.1 WAIVE THE READING OF ALL ORDINANCES AND RESOLUTIONS
    4.2 APPROVAL OF MINUTES
    4.5 RESOLUTION REAPPROPRIATING CERTAIN FISCAL YEAR 2025-26 FUND BALANCES
    AND AMENDING THE FISCAL YEAR 2026-27 BUDGET
    4.7 ADOPTION OF THE 2025-2026 CONSOLIDATED ANNUAL PERFORMANCE AND
    EVALUATION REPORT (CAPER) FOR EXPENDITURES OF COMMUNITY DEVELOPMENT
    BLOCK GRANT (CDBG) FUNDS
    4.8 PROPOSED LEASE RENEWAL WITH FAMILY ASSISTANCE MINISTRIES
    """

    candidates = substantive_formal_agenda_items(
        agenda
    )

    assert [
        item["item_number"]
        for item in candidates
    ] == [
        "4.5",
        "4.7",
        "4.8",
    ]

    assert not any(
        "BUSINESS SPOTLIGHT"
        in item["title"]
        for item in candidates
    )

    assert not any(
        "APPROVAL OF MINUTES"
        in item["title"]
        for item in candidates
    )


def test_action_words_ignore_calendar_year_tokens():
    assert _action_words(
        "Fiscal Year 2025 2026 Budget Reappropriation"
    ) == {
        "fiscal",
        "year",
        "budget",
        "reappropriation",
    }


def test_topic_scope_rejects_unrelated_generic_fiscal_overlap():
    topic = (
        "Fiscal Year 2025-26 Fund Balance "
        "Reappropriation and Budget Amendment"
    )

    unrelated = (
        "Attended a budget and finance subcommittee meeting "
        "to review fiscal responsibility and capital improvements."
    )

    assert not _topic_scope_supported(
        topic,
        unrelated,
    )

    assert _topic_scope_supported(
        topic,
        (
            "The Council discussed the fund balance "
            "reappropriation and budget amendment."
        ),
    )


def test_topic_scope_rejects_generic_report_overlap_for_caper():
    topic = (
        "2025-2026 Consolidated Annual Performance "
        "and Evaluation Report (CAPER)"
    )

    assert not _topic_scope_supported(
        topic,
        "Staff presented the annual financial report.",
    )

    assert _topic_scope_supported(
        topic,
        (
            "The Council discussed the annual performance "
            "report and CAPER."
        ),
    )


def test_consent_action_quote_accepts_child_item_after_parent_heading():
    agenda_items = [
        {
            "item_number": "4.5",
            "section": "CONSENT CALENDAR",
            "title": "BUDGET REAPPROPRIATION",
        },
        {
            "item_number": "4.7",
            "section": "CONSENT CALENDAR",
            "title": "CAPER",
        },
        {
            "item_number": "4.8",
            "section": "CONSENT CALENDAR",
            "title": "LEASE RENEWAL",
        },
    ]

    notes = """
#### Item 4: Consent Calendar
* **Item 4.8: Lease Renewal.**
* **Motion:** Council Member Tiffany moved to approve Item 4.8.
* **Vote:** Motion carried unanimously (4-0).
"""

    quote = _best_supported_consent_action_quote(
        "4.8",
        agenda_items,
        notes,
    )

    assert quote is not None
    assert "Item 4.8: Lease Renewal" in quote
    assert "Motion carried unanimously" in quote

    # The same block must not be borrowed by neighboring
    # consent items that are not identified in the source.
    assert _best_supported_consent_action_quote(
        "4.5",
        agenda_items,
        notes,
    ) is None

    assert _best_supported_consent_action_quote(
        "4.7",
        agenda_items,
        notes,
    ) is None


def test_nonformal_treatment_requires_recording_derived_notes():
    for status in (
        "discussed",
        "considered",
        "requested staff follow-up",
        "resident comment",
        "public comment",
        "speaker comment",
        "no council action",
    ):
        assert _nonformal_source_supported(
            status,
            "notes",
        )

        assert not _nonformal_source_supported(
            status,
            "agenda",
        )


def test_unclear_may_remain_agenda_backed_without_claiming_treatment():
    assert _nonformal_source_supported(
        "unclear",
        "agenda",
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("approved", "approved"),
        ("Considered", "considered"),
        ("requested staff follow-up", "requested staff follow-up"),
        ("unclear", "unclear"),
        ("agenda", "unclear"),
        ("scheduled", "unclear"),
        ("CONSENT CALENDAR", "unclear"),
        ("", "unclear"),
    ],
)
def test_action_status_is_closed_vocabulary(raw, expected):
    assert _canonical_action_status(raw) == expected


def test_dedupe_action_ledger_collapses_exact_same_item_evidence():
    rows = [
        {
            "topic": "CAPER",
            "item_number": "4.7",
            "agenda_section": "CONSENT CALENDAR",
            "agenda_linkage_conflict": False,
            "evidence_item_numbers": [],
            "agenda_title": "ADOPTION OF CAPER",
            "action_status": "unclear",
            "evidence_source": "agenda",
            "evidence_quote": "ADOPTION OF CAPER",
            "validated": False,
            "validation_note": "First validation reason.",
        },
        {
            "topic": "ADOPTION OF CAPER",
            "item_number": "4.7",
            "agenda_section": "CONSENT CALENDAR",
            "agenda_linkage_conflict": False,
            "evidence_item_numbers": [],
            "agenda_title": "ADOPTION OF CAPER",
            "action_status": "unclear",
            "evidence_source": "agenda",
            "evidence_quote": "ADOPTION OF CAPER",
            "validated": False,
            "validation_note": "Second validation reason.",
        },
    ]

    deduped = _dedupe_action_ledger_rows(
        rows
    )

    assert len(
        deduped
    ) == 1

    assert (
        deduped[0][
            "validation_note"
        ]
        == (
            "First validation reason. "
            "Second validation reason."
        )
    )


def test_dedupe_action_ledger_prefers_validated_exact_duplicate():
    rows = [
        {
            "topic": "Lease",
            "item_number": "4.8",
            "agenda_section": "CONSENT CALENDAR",
            "agenda_linkage_conflict": False,
            "evidence_item_numbers": [],
            "agenda_title": "LEASE RENEWAL",
            "action_status": "approved",
            "evidence_source": "notes",
            "evidence_quote": "Item 4.8 was approved.",
            "validated": False,
            "validation_note": "Earlier path failed validation.",
        },
        {
            "topic": "LEASE RENEWAL",
            "item_number": "4.8",
            "agenda_section": "CONSENT CALENDAR",
            "agenda_linkage_conflict": False,
            "evidence_item_numbers": ["4.8"],
            "agenda_title": "LEASE RENEWAL",
            "action_status": "approved",
            "evidence_source": "notes",
            "evidence_quote": "Item 4.8 was approved.",
            "validated": True,
            "validation_note": "",
        },
    ]

    deduped = _dedupe_action_ledger_rows(
        rows
    )

    assert len(
        deduped
    ) == 1

    assert (
        deduped[0][
            "validated"
        ]
        is True
    )

    assert deduped[0][
        "evidence_item_numbers"
    ] == [
        "4.8",
    ]


def test_dedupe_action_ledger_preserves_distinct_actions_and_itemless_rows():
    common = {
        "item_number": "4.8",
        "agenda_section": "CONSENT CALENDAR",
        "agenda_linkage_conflict": False,
        "evidence_item_numbers": [],
        "agenda_title": "LEASE RENEWAL",
        "evidence_source": "notes",
        "evidence_quote": "Lease evidence.",
        "validated": True,
        "validation_note": "",
    }

    rows = [
        {
            **common,
            "topic": "Lease",
            "action_status": "approved",
        },
        {
            **common,
            "topic": "Privacy directive",
            "action_status": "directed",
        },
        {
            **common,
            "topic": "Itemless privacy topic",
            "item_number": "",
            "action_status": "directed",
        },
    ]

    deduped = _dedupe_action_ledger_rows(
        rows
    )

    assert len(
        deduped
    ) == 3


def test_consent_remainder_vote_applies_only_to_unpulled_official_items():
    agenda_items = [
        {
            "item_number": "4.5",
            "section": "CONSENT CALENDAR",
            "title": "BUDGET REAPPROPRIATION",
        },
        {
            "item_number": "4.7",
            "section": "CONSENT CALENDAR",
            "title": "CAPER",
        },
        {
            "item_number": "4.8",
            "section": "CONSENT CALENDAR",
            "title": "LEASE RENEWAL",
        },
    ]

    notes = """
### Consent Calendar Vote Audit
Items explicitly pulled: Item 4.8
Remainder motion: Motion to approve the remainder of the Consent Calendar.
Remainder vote: Motion carried unanimously.
Separate action: Item 4.8 — motion to approve; motion carried 4-0.
"""

    quote_45 = _best_supported_consent_remainder_quote(
        "4.5",
        agenda_items,
        notes,
    )

    quote_47 = _best_supported_consent_remainder_quote(
        "4.7",
        agenda_items,
        notes,
    )

    quote_48 = _best_supported_consent_remainder_quote(
        "4.8",
        agenda_items,
        notes,
    )

    assert quote_45 is not None
    assert quote_47 is not None
    assert quote_48 is None


def test_consent_remainder_vote_requires_explicit_pulled_items():
    agenda_items = [
        {
            "item_number": "4.5",
            "section": "CONSENT CALENDAR",
            "title": "BUDGET",
        },
    ]

    notes = """
### Consent Calendar Vote Audit
Remainder motion: Motion to approve the remainder of the Consent Calendar.
Remainder vote: Motion carried unanimously.
"""

    assert _best_supported_consent_remainder_quote(
        "4.5",
        agenda_items,
        notes,
    ) is None


def test_generic_consent_approval_does_not_become_remainder_evidence():
    agenda_items = [
        {
            "item_number": "4.5",
            "section": "CONSENT CALENDAR",
            "title": "BUDGET",
        },
    ]

    notes = """
### Consent Calendar
The Consent Calendar was approved unanimously.
"""

    assert _best_supported_consent_remainder_quote(
        "4.5",
        agenda_items,
        notes,
    ) is None


def test_exact_consent_helper_does_not_borrow_remainder_vote_for_pulled_item():
    agenda_items = [
        {
            "item_number": "4.8",
            "section": "CONSENT CALENDAR",
            "title": "LEASE RENEWAL",
        },
    ]

    notes = """
### Consent Calendar Vote Audit
Items explicitly pulled: Item 4.8
Remainder motion: Motion to approve the remainder of the Consent Calendar.
Remainder vote: Motion carried unanimously.
"""

    assert _best_supported_consent_action_quote(
        "4.8",
        agenda_items,
        notes,
    ) is None


def test_exact_consent_helper_preserves_separate_pulled_item_vote():
    agenda_items = [
        {
            "item_number": "4.8",
            "section": "CONSENT CALENDAR",
            "title": "LEASE RENEWAL",
        },
    ]

    notes = """
### Consent Calendar Vote Audit
Items explicitly pulled: Item 4.8
Remainder motion: Motion to approve the remainder of the Consent Calendar.
Remainder vote: Motion carried unanimously.
Separate action: Motion to approve Item 4.8.
Vote: Motion carried unanimously 4-0.
"""

    quote = _best_supported_consent_action_quote(
        "4.8",
        agenda_items,
        notes,
    )

    assert quote is not None
    assert "Motion to approve Item 4.8" in quote


def test_remainder_consent_exclusion_item_numbers_are_not_linkage_conflicts():
    agenda_items = [
        {
            "item_number": "4.5",
            "section": "CONSENT CALENDAR",
            "title": "BUDGET REAPPROPRIATION",
        },
        {
            "item_number": "4.8",
            "section": "CONSENT CALENDAR",
            "title": "LEASE RENEWAL",
        },
    ]

    assert not _ledger_agenda_linkage_conflict(
        "4.5",
        "CONSENT CALENDAR",
        {"4.8"},
        agenda_items,
        consent_remainder=True,
    )


def test_nonremainder_consent_foreign_item_number_remains_conflict():
    agenda_items = [
        {
            "item_number": "4.5",
            "section": "CONSENT CALENDAR",
            "title": "BUDGET REAPPROPRIATION",
        },
        {
            "item_number": "4.8",
            "section": "CONSENT CALENDAR",
            "title": "LEASE RENEWAL",
        },
    ]

    assert _ledger_agenda_linkage_conflict(
        "4.5",
        "CONSENT CALENDAR",
        {"4.8"},
        agenda_items,
        consent_remainder=False,
    )


def test_exact_consent_item_number_is_not_linkage_conflict():
    agenda_items = [
        {
            "item_number": "4.8",
            "section": "CONSENT CALENDAR",
            "title": "LEASE RENEWAL",
        },
    ]

    assert not _ledger_agenda_linkage_conflict(
        "4.8",
        "CONSENT CALENDAR",
        {"4.8"},
        agenda_items,
    )


def test_foreign_item_number_on_nonconsent_item_remains_conflict():
    agenda_items = [
        {
            "item_number": "5.1",
            "section": "PUBLIC HEARINGS",
            "title": "PUBLIC HEARING",
        },
    ]

    assert _ledger_agenda_linkage_conflict(
        "5.1",
        "PUBLIC HEARINGS",
        {"4.8"},
        agenda_items,
        consent_remainder=False,
    )