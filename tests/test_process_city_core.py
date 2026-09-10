import json
from datetime import datetime

import pytest

import process_city as pc
from gemini_worker import AuditIssue, AuditResult, StoryDraft


def make_story(
    headline="Council update",
    dek="A concise summary.",
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


def test_status_helpers_round_trip_and_update(monkeypatch, tmp_path):
    status_file = tmp_path / "status.json"
    monkeypatch.setattr(pc, "STATUS_FILE", status_file)

    assert pc.load_status() == {"cities": {}}

    status_file.write_text("not json", encoding="utf-8")
    assert pc.load_status() == {"cities": {}}

    status = {"cities": {}}
    pc.write_status(status)

    written = json.loads(status_file.read_text(encoding="utf-8"))
    assert written["cities"] == {}
    parsed = datetime.fromisoformat(written["updated_at"])
    assert parsed.tzinfo is not None

    meeting = {
        "city_slug": "lake-forest",
        "city_name": "Lake Forest",
        "meeting_date": "2026-08-18",
        "external_id": 123,
    }

    pc.update_status(
        status,
        meeting,
        "auditing",
        "Evidence audit pass 1.",
        draft="lake-forest--123.json",
    )

    updated = json.loads(status_file.read_text(encoding="utf-8"))
    city = updated["cities"]["lake-forest"]
    assert city["city"] == "Lake Forest"
    assert city["phase"] == "auditing"
    assert city["external_id"] == "123"
    assert city["draft"] == "lake-forest--123.json"


def test_strip_public_agenda_item_numbers_across_story_fields():
    story = make_story(
        headline="Council acts on Agenda Item 5.6",
        dek="The council discussed zoning (Agenda Item 21).",
        body=[
            "Agenda Items 17 and 18 were discussed; unrelated text remains.",
            "The Council reviewed Items 5.7 and 5.8 before adjournment.",
        ],
        key_facts=[
            "Item 14 covered sports park renovations.",
        ],
    )

    assert pc.strip_public_agenda_item_numbers(story)

    combined = "\n".join(
        [story.headline, story.dek, *story.body, *story.key_facts]
    ).lower()

    assert "agenda item 5.6" not in combined
    assert "agenda item 21" not in combined
    assert "agenda items 17 and 18" not in combined
    assert "items 5.7 and 5.8" not in combined
    assert "item 14" not in combined
    assert "unrelated text remains" in combined
    assert "sports park renovations" in combined

    assert not pc.strip_public_agenda_item_numbers(story)


def test_non_consent_topic_cannot_be_attached_to_consent_calendar():
    story = make_story(
        dek=(
            "The Consent Calendar, which included zoning and development "
            "code amendments, was approved."
        ),
        body=[
            (
                "The Consent Calendar, including zoning and development code "
                "amendments, was approved unanimously."
            ),
            "Residents separately discussed neighborhood traffic.",
        ],
        key_facts=[
            "The Consent Calendar included zoning and development code amendments.",
        ],
    )

    intelligence = {
        "action_ledger": [
            action(
                "Zoning and Development Code Amendments",
                "discussed",
                section="PUBLIC HEARINGS",
                item_number="21",
            )
        ]
    }

    assert pc.enforce_public_action_constraints(story, intelligence)

    combined = "\n".join([story.dek, *story.body, *story.key_facts]).lower()
    assert "consent calendar" in combined
    assert not (
        "consent calendar" in story.dek.lower()
        and "zoning" in story.dek.lower()
    )
    assert "neighborhood traffic" in combined


def test_true_consent_topic_is_not_scrubbed():
    original = "The Consent Calendar included the traffic signal contract."
    story = make_story(dek=original, body=[original], key_facts=[original])
    intelligence = {
        "action_ledger": [
            action(
                "Traffic Signal Contract",
                "approved",
                section="CONSENT CALENDAR",
                item_number="5.7",
            )
        ]
    }

    assert not pc.enforce_public_action_constraints(story, intelligence)
    assert story.dek == original
    assert story.body == [original]


@pytest.mark.parametrize(
    ("draft_verb", "expected_verb"),
    [
        ("awarded", "approved"),
        ("awards", "approves"),
        ("award", "approve"),
        ("awarding", "approving"),
    ],
)
def test_formal_status_normalization_preserves_tense(draft_verb, expected_verb):
    sentence = (
        f"The Council {draft_verb} the geotechnical services agreements "
        "to GeoCorp."
    )
    story = make_story(body=[sentence])
    intelligence = {
        "action_ledger": [
            action(
                "Geotechnical Services Agreements",
                "approved",
                agenda_title="Geotechnical Services Agreements for Capital Projects",
                section="CONSENT CALENDAR",
                item_number="5.6",
            )
        ]
    }

    assert pc.normalize_validated_formal_status_language(story, intelligence)
    result = story.body[0].lower()
    assert expected_verb in result
    assert draft_verb not in result or draft_verb == expected_verb


def test_formal_status_normalization_handles_two_independent_clauses():
    story = make_story(
        dek=(
            "Council awarded the geotechnical services agreements to GeoCorp, "
            "and approved the sports park design contract with DesignCo."
        )
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

    assert pc.normalize_validated_formal_status_language(story, intelligence)
    low = story.dek.lower()
    assert "approved the geotechnical services agreements" in low
    assert "awarded the sports park design contract" in low


def test_adopted_resolution_can_preserve_embedded_appointment_language():
    original = (
        "The Council appointed the planning commissioners under the resolution."
    )
    story = make_story(body=[original])
    intelligence = {
        "action_ledger": [
            action(
                "Resolution Appointing Planning Commissioners",
                "adopted",
                agenda_title=(
                    "Adoption of Resolution Providing for Appointment of "
                    "Planning Commissioners"
                ),
            )
        ]
    }

    pc.normalize_validated_formal_status_language(story, intelligence)
    assert story.body == [original]


def test_requested_follow_up_cannot_be_strengthened_to_direction():
    original = (
        "The Council directed staff to investigate traffic nuisance concerns."
    )
    story = make_story(body=[original])
    intelligence = {
        "action_ledger": [
            action(
                "Traffic and Nuisance Concerns",
                "requested staff follow-up",
                evidence_quote=(
                    "Council members requested staff follow-up on traffic and "
                    "nuisance concerns."
                ),
            )
        ]
    }

    assert pc.normalize_validated_action_language(story, intelligence)
    low = story.body[0].lower()
    assert "directed staff" not in low
    assert "requested staff follow-up on" in low
    assert "traffic nuisance concerns" in low


def test_independently_validated_stronger_action_preserves_stronger_wording():
    original = (
        "The Council directed staff to investigate roadway traffic safety enforcement."
    )
    story = make_story(body=[original])
    intelligence = {
        "action_ledger": [
            action(
                "Traffic and Nuisance Concerns",
                "requested staff follow-up",
                evidence_quote="Residents raised traffic nuisance concerns.",
            ),
            action(
                "Roadway Traffic Safety Enforcement",
                "directed",
                evidence_quote=(
                    "Council directed staff regarding roadway traffic safety enforcement."
                ),
            ),
        ]
    }

    pc.normalize_validated_action_language(story, intelligence)
    assert story.body == [original]


def test_restore_required_topic_from_existing_key_fact():
    fact = "The Council discussed electric bicycle municipal code amendments."
    story = make_story(
        body=["The meeting opened with a transportation presentation."],
        key_facts=[fact],
    )
    intelligence = {
        "coverage_items": [
            {
                "rank": 1,
                "topic": "Electric Bicycle Municipal Code Amendments",
                "must_include": True,
            }
        ],
        "action_ledger": [],
    }

    assert pc.restore_required_topics_from_key_facts(story, intelligence)
    assert story.body[1] == fact

    assert not pc.restore_required_topics_from_key_facts(story, intelligence)


def test_restore_required_topic_from_validated_action_ledger():
    story = make_story(
        body=["The lead covers another issue."],
        key_facts=[],
    )
    intelligence = {
        "coverage_items": [
            {
                "rank": 2,
                "topic": "Zoning and Development Code Amendments",
                "must_include": True,
            }
        ],
        "action_ledger": [
            action(
                "Zoning and Development Code Amendments",
                "discussed",
                section="PUBLIC HEARINGS",
                item_number="21",
            )
        ],
    }

    assert pc.restore_required_topics_from_key_facts(story, intelligence)
    restored = "\n".join(story.body).lower()
    assert "during a public hearing" in restored
    assert "discussed zoning and development code amendments" in restored


def test_restore_required_topic_drops_section_timing_when_linkage_conflicts():
    story = make_story(body=["Lead paragraph."], key_facts=[])
    intelligence = {
        "coverage_items": [
            {
                "rank": 2,
                "topic": "Capital Improvement Plan",
                "must_include": True,
            }
        ],
        "action_ledger": [
            action(
                "Capital Improvement Plan",
                "considered",
                section="NEW BUSINESS",
                item_number="15",
                conflict=True,
            )
        ],
    }

    assert pc.restore_required_topics_from_key_facts(story, intelligence)
    restored = "\n".join(story.body).lower()
    assert "council considered capital improvement plan" in restored
    assert "during new business" not in restored


def test_reconcile_verified_person_note_requires_official_source_and_exact_subject():
    sample_note = (
        "The identity associated with the source reference 'Mr. Sample' "
        "remains unverified; CouncilWatch did not rely on that name as an "
        "identified person."
    )
    no_source_note = (
        "The identity associated with the source reference 'Ms. NoSource' "
        "remains unverified; CouncilWatch did not rely on that name as an "
        "identified person."
    )

    story = make_story(
        verification_notes=[sample_note, no_source_note, "Other verification note."]
    )
    intelligence = {
        "entities": [
            {
                "entity_type": "person",
                "status": "VERIFIED",
                "observed_text": "Mr. Sample",
                "canonical_text": "John Sample",
                "official_source_url": "https://example.gov/roster",
            },
            {
                "entity_type": "person",
                "status": "CORRECTED",
                "observed_text": "Ms. NoSource",
                "canonical_text": "Jane NoSource",
                "official_source_url": "",
            },
            {
                "entity_type": "organization",
                "status": "CORRECTED",
                "observed_text": "Other verification note",
                "canonical_text": "Other Organization",
                "official_source_url": "https://example.gov/org",
            },
        ]
    }

    assert pc.reconcile_entity_verification_notes(story, intelligence)
    combined = "\n".join(story.verification_notes)
    assert "was verified as John Sample" in combined
    assert no_source_note in story.verification_notes
    assert "Other verification note." in story.verification_notes


def test_apply_audit_corrections_updates_fields_then_scrubs_item_numbers():
    story = make_story(
        headline="Old headline",
        dek="Old dek",
        body=["Old body."],
        key_facts=["Old fact."],
        verification_notes=["Old note."],
    )

    audit = AuditResult(
        ok=False,
        issues=[],
        corrected_headline="New headline",
        corrected_dek="Council approved the contract (Agenda Item 5.6).",
        corrected_body=["Corrected body for Agenda Item 21."],
        corrected_key_facts=["Corrected fact for Item 14."],
        corrected_verification_notes=["Corrected note."],
    )

    assert pc.apply_audit_corrections(story, audit)
    assert story.headline == "New headline"
    assert "agenda item" not in story.dek.lower()
    assert "agenda item" not in "\n".join(story.body).lower()
    assert "item 14" not in "\n".join(story.key_facts).lower()
    assert story.verification_notes == ["Corrected note."]


def test_apply_audit_corrections_returns_false_for_identical_empty_corrections():
    story = make_story(
        headline="Headline",
        dek="Dek",
        body=["Body."],
        key_facts=["Fact."],
        verification_notes=["Note."],
    )
    audit = AuditResult(ok=True, issues=[])

    assert not pc.apply_audit_corrections(story, audit)


def test_story_fields_flattens_list_fields_for_exact_audit_matching():
    story = make_story(
        headline="Headline",
        dek="Dek",
        body=["Body one.", "Body two."],
        key_facts=["Fact one.", "Fact two."],
        verification_notes=["Note one.", "Note two."],
    )

    fields = pc.story_fields(story)
    assert fields["headline"] == "Headline"
    assert fields["body"] == "Body one.\nBody two."
    assert fields["key_facts"] == "Fact one.\nFact two."
    assert fields["verification_notes"] == "Note one.\nNote two."


def test_valid_audit_issues_drops_stale_and_already_corrected_findings():
    story = make_story(
        headline="Current headline",
        dek="Current dek",
        body=["Current body sentence."],
        key_facts=["Current fact."],
    )

    valid_issue = AuditIssue(
        severity="material",
        field="body",
        draft_text="Current body sentence.",
        source_evidence="Source contradicts the sentence.",
        correction="Delete the sentence.",
    )
    stale_issue = AuditIssue(
        severity="minor",
        field="dek",
        draft_text="Old dek text that is gone.",
        source_evidence="Old evidence.",
        correction="Fix it.",
    )
    resolved_issue = AuditIssue(
        severity="minor",
        field="headline",
        draft_text="Current headline",
        source_evidence="Source supports the current headline.",
        correction="No change.",
    )

    audit = AuditResult(
        ok=False,
        issues=[valid_issue, stale_issue, resolved_issue],
        corrected_headline="Current headline",
        corrected_body=["Corrected body sentence."],
    )

    assert pc.valid_audit_issues(story, audit) == [valid_issue]


def test_valid_audit_issues_requires_nonempty_exact_draft_text():
    story = make_story(body=["Exact current sentence."])
    empty = AuditIssue(
        severity="minor",
        field="body",
        draft_text="",
        source_evidence="Evidence.",
        correction="Correction.",
    )
    partial = AuditIssue(
        severity="minor",
        field="body",
        draft_text="Exact current sentence.",
        source_evidence="Evidence.",
        correction="Correction.",
    )
    wrong_field = AuditIssue(
        severity="minor",
        field="dek",
        draft_text="Exact current sentence.",
        source_evidence="Evidence.",
        correction="Correction.",
    )

    audit = AuditResult(ok=False, issues=[empty, partial, wrong_field])
    assert pc.valid_audit_issues(story, audit) == [partial]


def test_find_meeting_returns_requested_city(monkeypatch):
    meetings = [
        {"city_slug": "aliso-viejo", "city_name": "Aliso Viejo"},
        {"city_slug": "lake-forest", "city_name": "Lake Forest"},
    ]
    monkeypatch.setattr(pc, "latest_ready_meetings", lambda: meetings)

    assert pc.find_meeting("lake-forest") == meetings[1]


def test_find_meeting_error_lists_available_cities(monkeypatch):
    meetings = [
        {"city_slug": "rsm", "city_name": "Rancho Santa Margarita"},
        {"city_slug": "lake-forest", "city_name": "Lake Forest"},
    ]
    monkeypatch.setattr(pc, "latest_ready_meetings", lambda: meetings)

    with pytest.raises(SystemExit) as exc:
        pc.find_meeting("missing-city")

    message = str(exc.value)
    assert "missing-city" in message
    assert "lake-forest" in message
    assert "rsm" in message
