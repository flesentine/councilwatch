from __future__ import annotations

import pytest

import process_city as pc
from gemini_worker import StoryDraft


def story(
    headline="",
    dek="",
    body=None,
    key_facts=None,
    verification_notes=None,
):
    return StoryDraft(
        headline=headline,
        dek=dek,
        body=list(body or []),
        key_facts=list(key_facts or []),
        verification_notes=list(verification_notes or []),
    )


def action(
    topic,
    status,
    *,
    validated=True,
    agenda_title="",
    section="",
    item_number="",
    evidence_quote="",
    conflict=False,
):
    return {
        "topic": topic,
        "action_status": status,
        "validated": validated,
        "agenda_title": agenda_title or topic,
        "agenda_section": section,
        "item_number": item_number,
        "evidence_quote": evidence_quote,
        "agenda_linkage_conflict": conflict,
    }


def must_item(topic, rank=1):
    return {
        "rank": rank,
        "score": 9,
        "must_include": True,
        "topic": topic,
    }


def test_formal_status_normalization_preserves_capitalization():
    draft = story(
        headline="Council Awarded the geotechnical services contract to GeoCorp."
    )
    intelligence = {
        "action_ledger": [
            action(
                "Geotechnical Services Contract",
                "approved",
                agenda_title="Geotechnical Services Contract for Capital Projects",
            )
        ]
    }

    assert pc.normalize_validated_formal_status_language(draft, intelligence)
    assert draft.headline == (
        "Council Approved the geotechnical services contract to GeoCorp."
    )


def test_approved_contract_grammar_changes_to_to_with_vendor():
    draft = story(
        headline="Council awarded contract to GeoCorp for geotechnical services."
    )
    intelligence = {
        "action_ledger": [
            action(
                "Geotechnical Services Contract",
                "approved",
                agenda_title="Geotechnical Services Contract for Capital Projects",
            )
        ]
    }

    assert pc.normalize_validated_formal_status_language(draft, intelligence)
    assert "approved contract with geocorp" in draft.headline.lower()


def test_awarded_contract_grammar_changes_with_to_to_vendor():
    draft = story(
        dek="Council approved contract with DesignCo for sports park design."
    )
    intelligence = {
        "action_ledger": [
            action(
                "Sports Park Design Contract",
                "awarded",
                agenda_title="Award of Sports Park Design Contract",
            )
        ]
    }

    assert pc.normalize_validated_formal_status_language(draft, intelligence)
    assert "awarded contract to designco" in draft.dek.lower()


def test_formal_status_normalization_handles_semicolon_clause_boundary():
    draft = story(
        body=[
            "Council awarded geotechnical services agreements to GeoCorp; "
            "approved the sports park design contract with DesignCo."
        ]
    )
    intelligence = {
        "action_ledger": [
            action(
                "Geotechnical Services Agreements",
                "approved",
                agenda_title="Geotechnical Services Agreements for Capital Projects",
            ),
            action(
                "Sports Park Design Contract",
                "awarded",
                agenda_title="Sports Park Design Contract Award",
            ),
        ]
    }

    assert pc.normalize_validated_formal_status_language(draft, intelligence)
    text = draft.body[0].lower()
    assert "approved geotechnical services agreements" in text
    assert "awarded the sports park design contract" in text


def test_adopted_instrument_does_not_preserve_unrelated_formal_verb():
    draft = story(
        body=["Council awarded the planning commission selection resolution."]
    )
    intelligence = {
        "action_ledger": [
            action(
                "Planning Commission Selection Resolution",
                "adopted",
                agenda_title="Adoption of Planning Commission Selection Resolution",
            )
        ]
    }

    assert pc.normalize_validated_formal_status_language(draft, intelligence)
    assert "adopted the planning commission selection resolution" in draft.body[0].lower()
    assert "awarded" not in draft.body[0].lower()


def test_unclear_validated_action_needs_no_item_mapping_to_fail_closed():
    draft = story(
        body=[
            "Council approved the agenda management software contract.",
            "Residents discussed a separate park issue.",
        ]
    )
    intelligence = {
        "action_ledger": [
            action(
                "Agenda Management Software Contract",
                "unclear",
                validated=True,
            )
        ]
    }

    assert pc.normalize_validated_action_language(draft, intelligence)
    assert draft.body == ["Residents discussed a separate park issue."]


def test_unclear_action_with_linkage_conflict_does_not_delete_copy():
    original = "Council approved the agenda management software contract."
    draft = story(body=[original])
    intelligence = {
        "action_ledger": [
            action(
                "Agenda Management Software Contract",
                "unclear",
                validated=True,
                section="CONSENT CALENDAR",
                item_number="5.10",
                conflict=True,
            )
        ]
    }

    assert not pc.normalize_validated_action_language(draft, intelligence)
    assert draft.body == [original]


def test_unclear_action_without_meaningful_signature_is_ignored():
    original = "Council approved the road contract."
    draft = story(body=[original])
    intelligence = {
        "action_ledger": [
            action(
                "City Council",
                "unclear",
                validated=True,
                agenda_title="City Council Agenda",
            )
        ]
    }

    assert not pc.normalize_validated_action_language(draft, intelligence)
    assert draft.body == [original]


def test_unclear_action_removes_dependent_followup_and_preserves_later_sentence():
    draft = story(
        body=[
            "The Council approved the agenda management software contract with Acme Inc. "
            "The Council waived formal bidding requirements for this contract. "
            "A resident later discussed park lighting."
        ]
    )
    intelligence = {
        "action_ledger": [
            action(
                "Agenda Management Software Contract",
                "unclear",
                validated=False,
                agenda_title="Approval of Agenda Management Software Contract",
                section="CONSENT CALENDAR",
                item_number="5.10",
            )
        ]
    }

    assert pc.normalize_validated_action_language(draft, intelligence)
    combined = " ".join(draft.body)
    assert "approved the agenda management" not in combined.lower()
    assert "waived formal bidding" not in combined.lower()
    assert "park lighting" in combined.lower()


def test_unclear_summary_clause_removes_only_unsupported_action():
    draft = story(
        headline=(
            "Council approved agenda management software contract, and "
            "approved sports park lighting contract."
        )
    )
    intelligence = {
        "action_ledger": [
            action(
                "Agenda Management Software Contract",
                "unclear",
                validated=False,
                section="CONSENT CALENDAR",
                item_number="5.10",
            )
        ]
    }

    assert pc.normalize_validated_action_language(draft, intelligence)
    low = draft.headline.lower()
    assert "agenda management" not in low
    assert "sports park lighting contract" in low


def test_unclear_summary_with_only_unsupported_clause_becomes_empty():
    draft = story(headline="Council approved agenda management software contract.")
    intelligence = {
        "action_ledger": [
            action(
                "Agenda Management Software Contract",
                "unclear",
                validated=False,
                section="CONSENT CALENDAR",
                item_number="5.10",
            )
        ]
    }

    assert pc.normalize_validated_action_language(draft, intelligence)
    assert draft.headline == ""


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "Council directed city staff to investigate traffic noise complaints.",
            "requested that city staff follow up on traffic noise complaints",
        ),
        (
            "Council directed staff to provide a follow-up report regarding traffic noise complaints.",
            "requested staff follow-up on traffic noise complaints",
        ),
        (
            "Council directing city staff to address traffic noise complaints.",
            "requesting that city staff follow up on traffic noise complaints",
        ),
        (
            "Council ordered staff to investigate traffic noise complaints.",
            "requested staff follow-up on traffic noise complaints",
        ),
        (
            "Council instructed city staff to address traffic noise complaints.",
            "requested that city staff follow up on traffic noise complaints",
        ),
    ],
)
def test_requested_followup_replacement_variants(source, expected):
    draft = story(body=[source])
    intelligence = {
        "action_ledger": [
            action(
                "Traffic Noise Complaints",
                "requested staff follow-up",
                evidence_quote="Council requested staff follow-up on traffic noise complaints.",
            )
        ]
    }

    assert pc.normalize_validated_action_language(draft, intelligence)
    assert expected in draft.body[0].lower()


def test_requested_followup_does_not_rewrite_unrelated_strong_action():
    original = "Council directed staff to investigate park irrigation costs."
    draft = story(body=[original])
    intelligence = {
        "action_ledger": [
            action(
                "Traffic Noise Complaints",
                "requested staff follow-up",
                evidence_quote="Council requested staff follow-up on traffic noise complaints.",
            )
        ]
    }

    assert not pc.normalize_validated_action_language(draft, intelligence)
    assert draft.body == [original]


def test_missing_required_topic_skips_nonrequired_and_generic_topics():
    draft = story(body=["Another subject was covered."])
    intelligence = {
        "coverage_items": [
            {"topic": "Park", "must_include": True},
            {"topic": "Specific Traffic Study", "must_include": False},
        ],
        "action_ledger": [],
    }

    assert pc.missing_required_topic_issues(draft, intelligence) == []


def test_missing_required_topic_uses_validated_evidence_without_item_or_status():
    draft = story(body=["Another subject was covered."])
    intelligence = {
        "coverage_items": [must_item("Veterans Monument Completion")],
        "action_ledger": [
            action(
                "Veterans Monument Completion",
                "",
                item_number="",
                evidence_quote="The monument work was completed and accepted for filing.",
            )
        ],
    }

    issues = pc.missing_required_topic_issues(draft, intelligence)
    assert len(issues) == 1
    assert "validated action ledger." in issues[0].source_evidence
    assert "Validated evidence:" in issues[0].source_evidence
    assert "agenda item" not in issues[0].source_evidence


@pytest.mark.parametrize(
    ("status", "section", "expected"),
    [
        ("discussed", "NEW BUSINESS", "During new business, the Council discussed"),
        ("discussed", "", "The Council discussed"),
        ("considered", "PUBLIC HEARINGS", "During a public hearing, the Council considered"),
        ("considered", "NEW BUSINESS", "During new business, the Council considered"),
        ("considered", "", "The Council considered"),
        ("approved", "CONSENT CALENDAR", "The Council approved"),
        ("adopted", "CONSENT CALENDAR", "The Council adopted"),
        ("authorized", "CONSENT CALENDAR", "The Council authorized"),
        ("awarded", "CONSENT CALENDAR", "The Council awarded"),
        ("directed", "CONSENT CALENDAR", "The Council directed"),
        ("rejected", "CONSENT CALENDAR", "The Council rejected"),
        ("denied", "CONSENT CALENDAR", "The Council denied"),
        ("appointed", "CONSENT CALENDAR", "The Council appointed"),
        ("accepted", "CONSENT CALENDAR", "The Council accepted"),
        ("passed", "CONSENT CALENDAR", "The Council passed"),
    ],
)
def test_restore_required_topic_from_ledger_status_and_section(status, section, expected):
    topic = "Veterans Monument Completion"
    draft = story(body=["Lead paragraph."], key_facts=[])
    intelligence = {
        "coverage_items": [must_item(topic, rank=2)],
        "action_ledger": [
            action(
                topic,
                status,
                section=section,
            )
        ],
    }

    assert pc.restore_required_topics_from_key_facts(draft, intelligence)
    assert expected in draft.body[1]
    assert "veterans Monument Completion" in draft.body[1]


def test_restore_required_topic_rank_one_inserts_after_lead_and_rank_three_after_second():
    first_topic = "Transit Center Expansion"
    third_topic = "Veterans Monument Completion"
    draft = story(
        body=["Lead paragraph.", "Existing second paragraph."],
        key_facts=[
            "Transit Center Expansion was discussed.",
            "Veterans Monument Completion was accepted.",
        ],
    )
    intelligence = {
        "coverage_items": [
            must_item(first_topic, rank=1),
            must_item(third_topic, rank=3),
        ],
        "action_ledger": [],
    }

    assert pc.restore_required_topics_from_key_facts(draft, intelligence)
    assert draft.body[1] == "Transit Center Expansion was discussed."
    assert "Veterans Monument Completion was accepted." in draft.body


def test_restore_required_topic_ignores_unvalidated_or_unsupported_ledger_status():
    topic = "Veterans Monument Completion"
    for ledger in (
        [action(topic, "approved", validated=False)],
        [action(topic, "recommended", validated=True)],
    ):
        draft = story(body=["Lead paragraph."], key_facts=[])
        intelligence = {
            "coverage_items": [must_item(topic)],
            "action_ledger": ledger,
        }
        assert not pc.restore_required_topics_from_key_facts(draft, intelligence)
        assert draft.body == ["Lead paragraph."]
