from types import SimpleNamespace

import meeting_intelligence as mi
import process_city as pc
from gemini_worker import StoryDraft


RSM_AGENDA = """
ROLL CALL OF CITY COUNCIL MEMBERS:
Baert, Keri Lynn, Council Member

6. PUBLIC HEARING ITEMS
6.1 PUBLIC HEARING AND APPROVAL OF A RESOLUTION APPROVING THE ISSUANCE BY THE CALIFORNIA ENTERPRISE DEVELOPMENT AUTHORITY (CEDA) OF A TAX-EXEMPT LOAN FOR THE BENEFIT OF ST. JUNIPERO SERRA CATHOLIC SCHOOL AND THE ROMAN CATHOLIC BISHOP OF ORANGE
RECOMMENDATION:
Adopt a Resolution approving the issuance by the California Enterprise Development Authority of its tax-exempt loan in an aggregate principal amount not to exceed $10,000,000 for the benefit of St. Junipero Serra Catholic School and the Roman Catholic Bishop of Orange.
"""


def test_role_labeled_barrett_can_correct_to_official_baert():
    role_candidates = ["Councilmember Barrett"]

    assert mi._role_labeled_person_correction_plausible(
        "Councilmember Barrett",
        "Keri Lynn Baert",
        role_candidates,
    )

    assert not mi._role_labeled_person_correction_plausible(
        "Councilmember Ordona",
        "Kevin O'Connor",
        ["Councilmember Ordona"],
    )

    assert not mi._role_labeled_person_correction_plausible(
        "Barrett",
        "Keri Lynn Baert",
        [],
    )


def test_conduit_financing_coverage_language_is_not_city_debt():
    plan = mi.CoveragePlan(
        items=[
            mi.CoverageItem(
                rank=1,
                topic="Tax-Exempt Loan for St. Junipero Serra Catholic School",
                score=9,
                category="finance",
                action_status="Discussed",
                summary="Council considered the CEDA tax-exempt loan.",
                why_it_matters=(
                    "This involves a significant financial transaction "
                    "and public debt issuance within the city, requiring "
                    "formal council oversight and approval."
                ),
                must_include=True,
            )
        ]
    )

    assert mi._guard_conduit_financing_coverage_plan(
        plan,
        RSM_AGENDA,
    )

    why = plan.items[0].why_it_matters.casefold()
    assert "public debt" not in why
    assert "city debt" not in why
    assert "third-party tax-exempt financing" in why


def test_conduit_guard_respects_explicit_city_obligation():
    plan = mi.CoveragePlan(
        items=[
            mi.CoverageItem(
                rank=1,
                topic="Tax-Exempt Loan",
                score=9,
                category="finance",
                action_status="Approved",
                summary="The City debt issuance was approved.",
                why_it_matters="The City debt affects municipal finances.",
                must_include=True,
            )
        ]
    )

    agenda = RSM_AGENDA + "\nThe City is the borrower and obligor for this financing."

    assert not mi._guard_conduit_financing_coverage_plan(
        plan,
        agenda,
    )
    assert "City debt" in plan.items[0].why_it_matters


def test_build_coverage_plan_applies_conduit_guard(monkeypatch):
    parsed = mi.CoveragePlan(
        items=[
            mi.CoverageItem(
                rank=1,
                topic="Tax-Exempt Loan",
                score=9,
                category="finance",
                action_status="Discussed",
                summary="Public debt issuance within the city.",
                why_it_matters="City borrowing requires oversight.",
                must_include=True,
            )
        ]
    )

    class FakeModels:
        def generate_content(self, **_kwargs):
            return SimpleNamespace(parsed=parsed, text="")

    monkeypatch.setattr(
        mi.genai,
        "Client",
        lambda: SimpleNamespace(models=FakeModels()),
    )
    monkeypatch.setattr(
        mi,
        "retry_api_call",
        lambda _label, callback: callback(),
    )

    result = mi.build_coverage_plan(
        {
            "city_name": "Rancho Santa Margarita",
            "meeting_date": "2026-09-09",
        },
        "Council held the public hearing.",
        RSM_AGENDA,
        [],
    )

    item = result["items"][0]
    assert "public debt" not in item["summary"].casefold()
    assert "city borrowing" not in item["why_it_matters"].casefold()


def _rsm_intelligence(status):
    return {
        "coverage_items": [
            {
                "rank": 1,
                "topic": "Tax-Exempt Loan for St. Junipero Serra Catholic School",
                "score": 9,
                "must_include": True,
            }
        ],
        "action_ledger": [
            {
                "topic": "Tax-Exempt Loan for St. Junipero Serra Catholic School",
                "item_number": "6.1",
                "agenda_section": "PUBLIC HEARINGS",
                "agenda_title": (
                    "PUBLIC HEARING AND APPROVAL OF A RESOLUTION APPROVING "
                    "THE ISSUANCE BY THE CALIFORNIA ENTERPRISE DEVELOPMENT "
                    "AUTHORITY OF A TAX-EXEMPT LOAN FOR THE BENEFIT OF "
                    "ST. JUNIPERO SERRA CATHOLIC SCHOOL"
                ),
                "action_status": status,
                "validated": True,
                "evidence_quote": "Council held the public hearing.",
            }
        ],
    }


def test_unresolved_lead_formal_action_blocks_green_audit():
    story = StoryDraft(
        headline=(
            "City Council Considers Tax-Exempt Loan for "
            "St. Junipero Serra Catholic School"
        ),
        dek="Council held a public hearing on the financing.",
        body=["The final vote was not established by the evidence."],
        key_facts=[],
        verification_notes=[],
    )

    issues = pc.unresolved_high_priority_formal_action_issues(
        story,
        _rsm_intelligence("discussed"),
    )

    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == "material"
    assert issue.field == "headline"
    assert "Agenda item 6.1" in issue.source_evidence
    assert "final council disposition is unresolved" in issue.source_evidence
    assert "Do not infer approval or adoption" in issue.correction


def test_verified_formal_outcome_does_not_trigger_unresolved_gate():
    story = StoryDraft(
        headline="Council Adopts Tax-Exempt Loan Resolution",
        dek="The council adopted the resolution.",
        body=["The motion passed."],
        key_facts=[],
        verification_notes=[],
    )

    assert pc.unresolved_high_priority_formal_action_issues(
        story,
        _rsm_intelligence("adopted"),
    ) == []


def test_informational_discussion_without_formal_intent_is_allowed():
    story = StoryDraft(
        headline="Council Reviews Vector Control Report",
        dek="The council discussed the report.",
        body=["No formal action was scheduled."],
        key_facts=[],
        verification_notes=[],
    )

    intelligence = {
        "coverage_items": [
            {
                "rank": 1,
                "topic": "Mosquito and Vector Control District Report",
                "score": 8,
                "must_include": True,
            }
        ],
        "action_ledger": [
            {
                "topic": "Mosquito and Vector Control District Report",
                "item_number": "5.7",
                "agenda_section": "ITEMS REMOVED FROM THE CONSENT CALENDAR",
                "agenda_title": (
                    "CITY REPRESENTATIVE REPORT FOR THE ORANGE COUNTY "
                    "MOSQUITO AND VECTOR CONTROL DISTRICT"
                ),
                "action_status": "discussed",
                "validated": True,
            }
        ],
    }

    assert pc.unresolved_high_priority_formal_action_issues(
        story,
        intelligence,
    ) == []
