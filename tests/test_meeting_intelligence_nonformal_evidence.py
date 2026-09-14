from meeting_intelligence import (
    _best_supported_nonformal_quote,
    _best_supported_public_comment_quote,
    _best_supported_staff_followup_quote,
    _compound_topic_has_substantive_line_support,
    _nonformal_status_from_quote,
)


def test_compound_topic_requires_substantive_support_for_each_component():
    topic = "ALPR and Digital Signage"

    assert _compound_topic_has_substantive_line_support(
        topic,
        (
            "Council discussed ALPR camera policy and retention.\n"
            "Staff presented digital signage locations and operating rules."
        ),
    )

    assert not _compound_topic_has_substantive_line_support(
        topic,
        (
            "ALPR\n"
            "Staff presented digital signage locations and operating rules."
        ),
    )

    assert _compound_topic_has_substantive_line_support(
        "Capital Improvement Plan Update",
        "No special compound handling is required.",
    )


def test_nonformal_status_classifies_discussion_presentation_and_consideration():
    topic = "Capital Improvement Plan Update"

    assert _nonformal_status_from_quote(
        topic,
        "Council discussed the Capital Improvement Plan Update.",
    ) == "discussed"

    assert _nonformal_status_from_quote(
        topic,
        "Staff provided an update on the Capital Improvement Plan Update.",
    ) == "discussed"

    assert _nonformal_status_from_quote(
        topic,
        "The City Council considered the Capital Improvement Plan Update.",
    ) == "considered"

    assert _nonformal_status_from_quote(
        topic,
        "The project received priority consideration for grant funding.",
    ) is None


def test_nonformal_local_identity_does_not_inherit_prior_item_discussion():
    topic = "Capital Improvement Plan Update"

    assert _nonformal_status_from_quote(
        topic,
        (
            "The prior agenda item was discussed at length. "
            "Capital Improvement Plan Update was then introduced."
        ),
        require_local_identity=True,
    ) is None

    assert _nonformal_status_from_quote(
        topic,
        "Council gave an update on the Capital Improvement Plan Update.",
        require_local_identity=True,
    ) == "discussed"


def test_best_nonformal_quote_recovers_structured_discussion_evidence():
    result = _best_supported_nonformal_quote(
        "Capital Improvement Plan Update",
        (
            "Unrelated housekeeping was completed.\n"
            "Staff provided an update on the Capital Improvement Plan Update.\n"
            "Council discussed project timing and funding."
        ),
        agenda_title="CAPITAL IMPROVEMENT PLAN UPDATE",
        agenda_item_number="14",
    )

    assert result is not None
    assert result["action_status"] == "discussed"
    assert "Capital Improvement Plan Update" in result["evidence_quote"]


def test_best_nonformal_quote_rejects_public_comment_without_agenda_mapping():
    assert _best_supported_nonformal_quote(
        "Public Comment Electric Bicycle Safety",
        "A resident discussed electric bicycle safety.",
    ) is None

    assert _best_supported_nonformal_quote(
        "Budget",
        "Council discussed the budget.",
    ) is None


def test_best_nonformal_quote_supports_compound_topic_only_with_both_components():
    topic = "ALPR and Digital Signage"

    result = _best_supported_nonformal_quote(
        topic,
        (
            "Council discussed ALPR camera policy and retention.\n"
            "Staff provided an update on digital signage locations and operating rules."
        ),
        agenda_title="ALPR AND DIGITAL SIGNAGE",
    )

    assert result is not None
    assert result["action_status"] == "discussed"
    assert "ALPR" in result["evidence_quote"]
    assert "digital signage" in result["evidence_quote"].lower()

    assert _best_supported_nonformal_quote(
        topic,
        "Council discussed ALPR camera policy and retention only.",
        agenda_title="ALPR AND DIGITAL SIGNAGE",
    ) is None


def test_structured_public_comment_quote_requires_topic_and_speaker_language():
    notes = (
        "Resident Jane Doe spoke about electric bicycle safety near schools.\n"
        "She advocated for additional rider education and safer crossings."
    )

    result = _best_supported_public_comment_quote(
        "Electric Bicycle Safety Advocacy",
        notes,
    )

    assert result is not None
    assert "Resident Jane Doe" in result
    assert "electric bicycle safety" in result.lower()

    assert _best_supported_public_comment_quote(
        "Electric Bicycle Safety Advocacy",
        "Electric Bicycle Safety Advocacy\nAgenda presentation materials",
    ) is None


def test_structured_staff_followup_quote_requires_request_staff_and_followup():
    notes = (
        "Council discussed electric bicycle safety policy near schools.\n"
        "Council requested staff follow-up on electric bicycle safety policy enforcement."
    )

    result = _best_supported_staff_followup_quote(
        "Electric Bicycle Safety Policy",
        notes,
    )

    assert result is not None
    assert "requested staff follow-up" in result.lower()

    assert _best_supported_staff_followup_quote(
        "Electric Bicycle Safety Policy",
        "Staff presented the electric bicycle safety policy and answered questions.",
    ) is None
