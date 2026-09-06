from meeting_intelligence import (
    _agenda_item_transition_numbers,
    _best_supported_nonformal_quote,
    _candidate_has_foreign_agenda_transition,
)


def test_spoken_foreign_item_title_is_boundary():
    candidate = (
        "I-5 El Toro Road interchange project discussion. "
        "We're going to move on. Madam City Clerk, please read "
        "the title to uh to item number three."
    )

    assert _agenda_item_transition_numbers(candidate) == {"3"}
    assert _candidate_has_foreign_agenda_transition(
        candidate,
        "2",
    )


def test_same_item_transition_is_not_foreign():
    candidate = (
        "Madam City Clerk, please read the title to item number two. "
        "The I-5 El Toro Road interchange project was discussed."
    )

    assert not _candidate_has_foreign_agenda_transition(
        candidate,
        "2",
    )


def test_clean_window_without_item_number_is_allowed():
    candidate = (
        "OCTA provided an update on the I-5 El Toro Road "
        "interchange project and council discussed congestion."
    )

    assert _agenda_item_transition_numbers(candidate) == set()

    assert not _candidate_has_foreign_agenda_transition(
        candidate,
        "2",
    )


def test_incidental_item_reference_is_not_boundary():
    candidate = (
        "During discussion of the I-5 project, a council member "
        "mentioned item 3 from an earlier packet."
    )

    assert _agenda_item_transition_numbers(candidate) == set()

    assert not _candidate_has_foreign_agenda_transition(
        candidate,
        "2",
    )


def test_i5_foreign_transition_candidate_is_rejected():
    topic = "I-5 El Toro Road Interchange Project Update"

    contaminated = (
        "The I-5 El Toro Road interchange project was discussed "
        "as an update on traffic congestion and emergency response. "
        "Madam City Clerk, please read the title to uh to item "
        "number three."
    )

    assert (
        _best_supported_nonformal_quote(
            topic,
            contaminated,
            agenda_title=(
                "I-5/EL TORO ROAD INTERCHANGE PROJECT UPDATE"
            ),
            agenda_item_number="2",
        )
        is None
    )


def test_clean_i5_candidate_remains_eligible():
    topic = "I-5 El Toro Road Interchange Project Update"

    clean = (
        "The I-5 El Toro Road interchange project was discussed "
        "as an update on traffic congestion and emergency response."
    )

    result = _best_supported_nonformal_quote(
        topic,
        clean,
        agenda_title=(
            "I-5/EL TORO ROAD INTERCHANGE PROJECT UPDATE"
        ),
        agenda_item_number="2",
    )

    assert result is not None
    assert result["action_status"] == "discussed"
    assert result["evidence_quote"] == clean
