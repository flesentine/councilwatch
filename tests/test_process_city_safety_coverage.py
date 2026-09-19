from __future__ import annotations

import pytest

import process_city as pc
from gemini_worker import StoryDraft


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


def coverage_item(topic="Sports Park Contract", **overrides):
    item = {
        "rank": 1,
        "score": 9,
        "must_include": True,
        "topic": topic,
    }
    item.update(overrides)
    return item


def ledger_action(**overrides):
    item = {
        "topic": "Sports Park Contract",
        "action_status": "discussed",
        "validated": True,
        "agenda_title": "Approval of Sports Park Contract",
        "agenda_section": "NEW BUSINESS",
        "item_number": "12",
    }
    item.update(overrides)
    return item


def test_update_status_without_draft_omits_draft_key(monkeypatch):
    writes = []
    monkeypatch.setattr(pc, "write_status", lambda status: writes.append(status.copy()))

    status = {}
    meeting = {
        "city_slug": "lake-forest",
        "city_name": "Lake Forest",
        "meeting_date": "2026-08-18",
        "external_id": None,
    }

    pc.update_status(status, meeting, "starting", "Starting generation.")

    payload = status["cities"]["lake-forest"]
    assert payload["external_id"] == "None"
    assert "draft" not in payload
    assert writes


def test_unresolved_high_priority_formal_action_reports_supported_candidate():
    story = make_story(headline="Sports park contract remains unresolved")
    intelligence = {
        "coverage_items": [coverage_item()],
        "action_ledger": [ledger_action()],
    }

    issues = pc.unresolved_high_priority_formal_action_issues(story, intelligence)

    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == "material"
    assert issue.field == "headline"
    assert "Agenda item 12" in issue.source_evidence
    assert "'discussed'" in issue.source_evidence
    assert "Do not infer approval" in issue.correction


@pytest.mark.parametrize(
    "coverage,action",
    [
        ([], ledger_action()),
        ([coverage_item(must_include=False)], ledger_action()),
        ([coverage_item(topic="Park")], ledger_action()),
        ([coverage_item()], ledger_action(validated=False)),
        ([coverage_item()], ledger_action(action_status="approved")),
        ([coverage_item()], ledger_action(action_status="recommended")),
        ([coverage_item()], ledger_action(agenda_section="CONSENT CALENDAR")),
        ([coverage_item()], ledger_action(agenda_title="Sports Park Contract")),
        (
            [coverage_item()],
            ledger_action(
                topic="Traffic Signal Maintenance",
                agenda_title="Approval of Traffic Signal Maintenance",
            ),
        ),
    ],
)
def test_unresolved_high_priority_formal_action_rejects_unsafe_candidates(
    coverage,
    action,
):
    story = make_story(headline="Sports park contract update")
    intelligence = {
        "coverage_items": coverage,
        "action_ledger": [action],
    }

    assert pc.unresolved_high_priority_formal_action_issues(story, intelligence) == []


def test_unresolved_high_priority_formal_action_uses_dek_body_and_fail_closed_empty():
    intelligence = {
        "coverage_items": [coverage_item()],
        "action_ledger": [ledger_action(item_number="")],
    }

    dek_story = make_story(headline="", dek="Sports park contract status")
    dek_issues = pc.unresolved_high_priority_formal_action_issues(
        dek_story,
        intelligence,
    )
    assert len(dek_issues) == 1
    assert dek_issues[0].field == "dek"
    assert "The official agenda item" in dek_issues[0].source_evidence

    body_story = make_story(
        headline="",
        dek="",
        body=["Sports park contract status"],
    )
    body_issues = pc.unresolved_high_priority_formal_action_issues(
        body_story,
        intelligence,
    )
    assert len(body_issues) == 1
    assert body_issues[0].field == "body"

    empty_story = make_story(headline="", dek="", body=[])
    assert (
        pc.unresolved_high_priority_formal_action_issues(empty_story, intelligence)
        == []
    )


def test_unsupported_conduit_financing_story_issues_scope_and_deduplicate(monkeypatch):
    context = {
        "item_number": "15",
        "city_obligation": False,
        "anchors": {"acme", "medical", "center"},
    }
    monkeypatch.setattr(
        pc,
        "_conduit_financing_agenda_contexts",
        lambda agenda: [context],
    )
    monkeypatch.setattr(
        pc,
        "_conduit_text_matches_context",
        lambda value, _context: "acme medical" in str(value).lower(),
    )

    duplicate = "Acme Medical Center city debt issuance moved forward."
    story = make_story(
        headline="Acme Medical Center city borrowing approved",
        dek="City debt issuance proceeded.",
        body=[
            duplicate,
            duplicate,
            "Unrelated city borrowing for the capital plan.",
        ],
        key_facts=["Debt issued by the city for Acme Medical Center."],
    )

    issues = pc.unsupported_conduit_financing_story_issues(story, "agenda")

    assert [issue.field for issue in issues] == [
        "headline",
        "dek",
        "body",
        "key_facts",
    ]
    assert sum(issue.draft_text == duplicate for issue in issues) == 1
    assert all("Official agenda item 15" in issue.source_evidence for issue in issues)
    assert not any("capital plan" in issue.draft_text.lower() for issue in issues)


def test_unsupported_conduit_financing_story_issues_ignore_safe_contexts(monkeypatch):
    story = make_story(headline="Acme Medical Center city borrowing approved")

    monkeypatch.setattr(
        pc,
        "_conduit_financing_agenda_contexts",
        lambda agenda: [],
    )
    assert pc.unsupported_conduit_financing_story_issues(story, "agenda") == []

    monkeypatch.setattr(
        pc,
        "_conduit_financing_agenda_contexts",
        lambda agenda: [
            {
                "item_number": "15",
                "city_obligation": True,
                "anchors": {"acme", "medical"},
            }
        ],
    )
    assert pc.unsupported_conduit_financing_story_issues(story, "agenda") == []


def test_non_consent_contamination_fails_closed_when_phrase_cannot_be_surgically_removed():
    contaminated = "Consent Calendar approved zoning development code amendments"
    story = make_story(
        headline=contaminated,
        dek=contaminated,
        body=[contaminated, "Residents discussed neighborhood traffic."],
        key_facts=[contaminated],
    )
    intelligence = {
        "action_ledger": [
            {
                "topic": "Zoning Development Code Amendments",
                "action_status": "discussed",
                "agenda_section": "PUBLIC HEARINGS",
            }
        ]
    }

    assert pc.enforce_public_action_constraints(story, intelligence)
    assert story.headline == ""
    assert story.dek == ""
    assert story.body == ["Residents discussed neighborhood traffic."]
    assert story.key_facts == []


def test_formal_status_normalizer_rejects_unattributable_or_ambiguous_copy():
    story = make_story(body=["Council awarded the road contract."])
    assert not pc.normalize_validated_formal_status_language(
        story,
        {"action_ledger": []},
    )

    one_cue = {
        "action_ledger": [
            {
                "topic": "Road",
                "agenda_title": "Road",
                "action_status": "approved",
                "validated": True,
            }
        ]
    }
    assert not pc.normalize_validated_formal_status_language(story, one_cue)

    ambiguous_story = make_story(
        body=[
            "Council approved geotechnical services and sports park design contract."
        ]
    )
    ambiguous = {
        "action_ledger": [
            {
                "topic": "Geotechnical Services",
                "agenda_title": "Geotechnical Services Agreement",
                "action_status": "approved",
                "validated": True,
            },
            {
                "topic": "Sports Park Design",
                "agenda_title": "Sports Park Design Contract",
                "action_status": "awarded",
                "validated": True,
            },
        ]
    }

    original = list(ambiguous_story.body)
    assert not pc.normalize_validated_formal_status_language(
        ambiguous_story,
        ambiguous,
    )
    assert ambiguous_story.body == original


def test_formal_status_normalizer_preserves_shared_only_cues():
    story = make_story(body=["Council awarded the Lake Forest contract."])
    intelligence = {
        "action_ledger": [
            {
                "topic": "Lake Forest Contract Alpha",
                "agenda_title": "Lake Forest Contract Alpha",
                "action_status": "approved",
                "validated": True,
            },
            {
                "topic": "Lake Forest Contract Beta",
                "agenda_title": "Lake Forest Contract Beta",
                "action_status": "awarded",
                "validated": True,
            },
        ]
    }

    original = list(story.body)
    assert not pc.normalize_validated_formal_status_language(story, intelligence)
    assert story.body == original


def test_missing_substantive_formal_action_flags_validated_budget_omission():
    story = make_story(
        body=[
            "The council recognized a local business and announced community events."
        ]
    )

    intelligence = {
        "action_ledger": [
            {
                "topic": "FY 2026-27 Budget Reappropriation",
                "agenda_title": (
                    "RESOLUTION REAPPROPRIATING CERTAIN FISCAL YEAR "
                    "2025-26 FUND BALANCES AND AMENDING THE FISCAL "
                    "YEAR 2026-27 BUDGET"
                ),
                "item_number": "4.5",
                "agenda_section": "CONSENT CALENDAR",
                "action_status": "adopted",
                "validated": True,
                "evidence_quote": (
                    "Motion to approve the consent calendar passed 4-0."
                ),
            }
        ]
    }

    issues = pc.missing_substantive_formal_action_issues(
        story,
        intelligence,
    )

    assert len(issues) == 1
    assert issues[0].severity == "material"
    assert issues[0].field == "body"
    assert "agenda item 4.5" in issues[0].source_evidence


def test_missing_substantive_formal_action_accepts_covered_budget_action():
    story = make_story(
        body=[
            (
                "The council adopted a resolution reappropriating "
                "Fiscal Year 2025-26 fund balances and amending the "
                "Fiscal Year 2026-27 budget."
            )
        ]
    )

    intelligence = {
        "action_ledger": [
            {
                "topic": "FY 2026-27 Budget Reappropriation",
                "agenda_title": (
                    "RESOLUTION REAPPROPRIATING CERTAIN FISCAL YEAR "
                    "2025-26 FUND BALANCES AND AMENDING THE FISCAL "
                    "YEAR 2026-27 BUDGET"
                ),
                "item_number": "4.5",
                "agenda_section": "CONSENT CALENDAR",
                "action_status": "adopted",
                "validated": True,
            }
        ]
    }

    assert pc.missing_substantive_formal_action_issues(
        story,
        intelligence,
    ) == []


@pytest.mark.parametrize(
    "action",
    [
        {
            "topic": "Hunger Action Month Proclamation",
            "agenda_title": "PROCLAMATION - HUNGER ACTION MONTH",
            "action_status": "accepted",
            "validated": True,
        },
        {
            "topic": "FY 2026-27 Budget Reappropriation",
            "agenda_title": "BUDGET REAPPROPRIATION",
            "action_status": "unclear",
            "validated": True,
        },
        {
            "topic": "FY 2026-27 Budget Reappropriation",
            "agenda_title": "BUDGET REAPPROPRIATION",
            "action_status": "adopted",
            "validated": False,
        },
    ],
)
def test_missing_substantive_formal_action_ignores_nontriggering_rows(action):
    story = make_story(
        body=["The council meeting included several updates."]
    )

    assert pc.missing_substantive_formal_action_issues(
        story,
        {
            "action_ledger": [
                action
            ]
        },
    ) == []


def test_missing_required_topic_does_not_count_shared_years_as_coverage():
    story = make_story(
        body=[
            (
                "The Council discussed a resolution reappropriating "
                "Fiscal Year 2025-26 fund balances and amending the "
                "Fiscal Year 2026-27 budget."
            ),
            "Officials also delivered routine reports and community updates.",
        ]
    )

    intelligence = {
        "coverage_items": [
            {
                "rank": 3,
                "score": 6,
                "must_include": True,
                "topic": (
                    "2025-2026 Consolidated Annual Performance and "
                    "Evaluation Report (CAPER)"
                ),
            }
        ],
        "action_ledger": [
            {
                "topic": (
                    "2025-2026 Consolidated Annual Performance and "
                    "Evaluation Report (CAPER)"
                ),
                "agenda_title": (
                    "ADOPTION OF THE 2025-2026 CONSOLIDATED ANNUAL "
                    "PERFORMANCE AND EVALUATION REPORT (CAPER) FOR "
                    "EXPENDITURES OF COMMUNITY DEVELOPMENT BLOCK "
                    "GRANT (CDBG) FUNDS"
                ),
                "item_number": "4.7",
                "action_status": "unclear",
                "validated": True,
            }
        ],
    }

    issues = pc.missing_required_topic_issues(
        story,
        intelligence,
    )

    assert len(issues) == 1
    assert issues[0].severity == "material"
    assert "agenda item 4.7" in issues[0].source_evidence


def test_restore_required_topic_does_not_count_shared_years_as_coverage():
    story = make_story(
        body=[
            (
                "The Council discussed a resolution reappropriating "
                "Fiscal Year 2025-26 fund balances and amending the "
                "Fiscal Year 2026-27 budget."
            )
        ],
        key_facts=[
            (
                "The Council discussed the 2025-2026 Consolidated "
                "Annual Performance and Evaluation Report (CAPER)."
            )
        ],
    )

    intelligence = {
        "coverage_items": [
            {
                "rank": 3,
                "score": 6,
                "must_include": True,
                "topic": (
                    "2025-2026 Consolidated Annual Performance and "
                    "Evaluation Report (CAPER)"
                ),
            }
        ],
        "action_ledger": [],
    }

    assert pc.restore_required_topics_from_key_facts(
        story,
        intelligence,
    )
    assert any(
        "CAPER" in paragraph
        for paragraph in story.body
    )
