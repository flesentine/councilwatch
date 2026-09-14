from meeting_intelligence import (
    _best_supported_local_public_comment_quote,
    _best_supported_local_staff_followup_quote,
    _staff_followup_language_supported,
)


def _raw_transcript(*turns):
    padded = list(turns)
    while len(padded) < 10:
        padded.append(f"filler turn {len(padded)}")
    return " >> ".join(padded)


def test_staff_followup_language_requires_explicit_followup_action():
    assert _staff_followup_language_supported(
        "City staff will follow up with the applicant and report back."
    )
    assert _staff_followup_language_supported(
        "The council asked for a response and report back from staff."
    )
    assert not _staff_followup_language_supported(
        "City staff presented the report and answered questions."
    )


def test_local_public_comment_recovers_exact_commenter_turn_only_while_open():
    notes = _raw_transcript(
        "Mayor: We will now move on to public comments.",
        (
            "First public commenter is Jane Doe. Electric bicycle safety advocacy "
            "is needed near schools and neighborhood crossings."
        ),
        "Another resident discussed park maintenance.",
        "Mayor: Public comments are now closed.",
        (
            "Council Member Smith later discussed electric bicycle safety advocacy "
            "during council reports."
        ),
    )

    result = _best_supported_local_public_comment_quote(
        "Electric Bicycle Safety Advocacy",
        notes,
    )

    assert result is not None
    assert "First public commenter is Jane Doe" in result
    assert "electric bicycle safety advocacy" in result.lower()
    assert "Council Member Smith" not in result


def test_local_public_comment_rejects_topic_mention_after_comment_period_closes():
    notes = _raw_transcript(
        "Mayor: We will now open public comments.",
        "First public commenter is Jane Doe. She discussed park maintenance.",
        "Mayor: Public comments are closed.",
        (
            "Council Member Smith discussed electric bicycle safety advocacy "
            "near schools and neighborhood crossings."
        ),
    )

    assert _best_supported_local_public_comment_quote(
        "Electric Bicycle Safety Advocacy",
        notes,
    ) is None


def test_local_staff_followup_recovers_topic_adjacent_followup_window():
    notes = _raw_transcript(
        "Mayor: We are moving to the Capital Improvement Plan Update.",
        (
            "Staff presented the Capital Improvement Plan Update and the project "
            "funding schedule."
        ),
        "Council Member Smith asked about project timing and delivery milestones.",
        (
            "City staff will follow up on the Capital Improvement Plan Update "
            "and report back with the revised schedule."
        ),
    )

    result = _best_supported_local_staff_followup_quote(
        "Capital Improvement Plan Update",
        notes,
        agenda_title="CAPITAL IMPROVEMENT PLAN UPDATE",
    )

    assert result is not None
    assert "City staff will follow up" in result
    assert "Capital Improvement Plan Update" in result


def test_local_staff_followup_rejects_followup_far_from_topic_identity():
    notes = _raw_transcript(
        "The Capital Improvement Plan Update was presented.",
        "Council discussed the project funding schedule.",
        "unrelated turn one",
        "unrelated turn two",
        "unrelated turn three",
        "unrelated turn four",
        "City staff will follow up and report back on playground repairs.",
        "unrelated turn five",
        "unrelated turn six",
        "unrelated turn seven",
    )

    assert _best_supported_local_staff_followup_quote(
        "Capital Improvement Plan Update",
        notes,
        agenda_title="CAPITAL IMPROVEMENT PLAN UPDATE",
    ) is None
