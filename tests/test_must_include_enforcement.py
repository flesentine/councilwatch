from gemini_worker import StoryDraft
import process_city as pc


def veterans_intelligence(status="accepted", validated=True):
    return {
        "coverage_items": [
            {
                "rank": 1,
                "topic": "St. Junipero Serra Catholic School Conduit Financing",
                "score": 8,
                "must_include": True,
            },
            {
                "rank": 2,
                "topic": "Veterans Monument Project Notice of Completion",
                "score": 6,
                "must_include": True,
            },
        ],
        "action_ledger": [
            {
                "topic": "Veterans Monument Project Notice of Completion",
                "item_number": "5.6",
                "agenda_section": "CONSENT CALENDAR",
                "agenda_title": (
                    "VETERANS MONUMENT PROJECT NOTICE OF COMPLETION"
                ),
                "action_status": status,
                "validated": validated,
                "evidence_quote": (
                    "The Council accepted the improvements and "
                    "authorized filing the Notice of Completion."
                ),
            }
        ],
    }


def base_story():
    return StoryDraft(
        headline="Council Adopts School Conduit Financing Resolution",
        dek="The financing carries no City debt obligation.",
        body=[
            (
                "The Council adopted conduit financing for "
                "St. Junipero Serra Catholic School."
            )
        ],
        key_facts=[],
        verification_notes=[],
    )


def test_missing_must_include_topic_is_material_issue():
    story = base_story()

    issues = pc.missing_required_topic_issues(
        story,
        veterans_intelligence(),
    )

    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == "material"
    assert issue.field == "body"
    assert issue.draft_text == (
        "Veterans Monument Project Notice of Completion"
    )
    assert "agenda item 5.6" in issue.source_evidence
    assert "accepted" in issue.source_evidence
    assert "MUST INCLUDE" in issue.correction


def test_formal_validated_action_can_restore_required_topic():
    story = base_story()
    intelligence = veterans_intelligence()

    assert pc.restore_required_topics_from_key_facts(
        story,
        intelligence,
    )

    restored = "\n".join(story.body).casefold()
    assert "veterans monument" in restored
    assert "accepted" in restored

    assert pc.missing_required_topic_issues(
        story,
        intelligence,
    ) == []


def test_unvalidated_formal_action_is_not_used_for_restore():
    story = base_story()
    intelligence = veterans_intelligence(validated=False)

    assert not pc.restore_required_topics_from_key_facts(
        story,
        intelligence,
    )

    issues = pc.missing_required_topic_issues(
        story,
        intelligence,
    )

    assert len(issues) == 1
    assert "coverage topic MUST INCLUDE" in (
        issues[0].source_evidence
    )


def test_required_topic_present_in_body_passes_completeness_gate():
    story = base_story()
    story.body.append(
        "The Council accepted the Veterans Monument Notice of Completion."
    )

    assert pc.missing_required_topic_issues(
        story,
        veterans_intelligence(),
    ) == []


def test_required_topic_matching_is_paragraph_local():
    story = base_story()
    story.body.extend(
        [
            "Veterans attended the ceremony.",
            "The monument was discussed separately.",
        ]
    )

    issues = pc.missing_required_topic_issues(
        story,
        veterans_intelligence(),
    )

    assert len(issues) == 1
    assert issues[0].draft_text.startswith("Veterans Monument")
