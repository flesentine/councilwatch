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
                topic="Tax-Exempt Loan for St. Junipero Serra Catholic School",
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
                topic="Tax-Exempt Loan for St. Junipero Serra Catholic School",
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



def test_unvalidated_unresolved_action_does_not_block_final_audit():
    story = StoryDraft(
        headline="Council Considers Tax-Exempt Loan",
        dek="The council held a public hearing.",
        body=["The disposition remains uncertain."],
        key_facts=[],
        verification_notes=[],
    )

    intelligence = _rsm_intelligence("discussed")
    intelligence["action_ledger"][0]["validated"] = False

    assert pc.unresolved_high_priority_formal_action_issues(
        story,
        intelligence,
    ) == []


def test_publishable_city_debt_wording_is_material_issue():
    story = StoryDraft(
        headline="Council Considers St. Junipero Serra Catholic School Financing",
        dek="The public hearing concerned a tax-exempt loan.",
        body=[
            "The City debt issuance would total up to $10 million."
        ],
        key_facts=[],
        verification_notes=[],
    )

    issues = pc.unsupported_conduit_financing_story_issues(
        story,
        RSM_AGENDA,
    )

    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == "material"
    assert issue.field == "body"
    assert issue.draft_text == story.body[0]
    assert "does not establish the City as borrower" in issue.source_evidence


def test_neutral_conduit_financing_copy_is_allowed():
    story = StoryDraft(
        headline="Council Considers CEDA School Financing",
        dek=(
            "The council considered approval of a tax-exempt loan "
            "issued by the California Enterprise Development Authority."
        ),
        body=[
            "The financing would benefit St. Junipero Serra Catholic School."
        ],
        key_facts=[],
        verification_notes=[],
    )

    assert pc.unsupported_conduit_financing_story_issues(
        story,
        RSM_AGENDA,
    ) == []


def test_explicit_city_obligation_allows_city_debt_wording():
    story = StoryDraft(
        headline="City Debt Financing Considered",
        dek="The City is the borrower for the financing.",
        body=["The City debt would total up to $10 million."],
        key_facts=[],
        verification_notes=[],
    )

    agenda = (
        RSM_AGENDA
        + "\nThe City is the borrower and obligor for this financing."
    )

    assert pc.unsupported_conduit_financing_story_issues(
        story,
        agenda,
    ) == []



def test_conduit_guard_sanitizes_bad_coverage_topic():
    plan = mi.CoveragePlan(
        items=[
            mi.CoverageItem(
                rank=1,
                topic=(
                    "City Debt Issuance for St. Junipero Serra "
                    "Catholic School"
                ),
                score=9,
                category="finance",
                action_status="Discussed",
                summary="Council considered the financing.",
                why_it_matters="The school financing required council review.",
                must_include=True,
            )
        ]
    )

    assert mi._guard_conduit_financing_coverage_plan(
        plan,
        RSM_AGENDA,
    )

    assert "city debt" not in plan.items[0].topic.casefold()
    assert "financing" in plan.items[0].topic.casefold()


def test_conduit_guard_does_not_rewrite_unrelated_city_borrowing():
    mixed_agenda = RSM_AGENDA + """

7.1 APPROVAL OF CITY BORROWING FOR FIRE STATION IMPROVEMENTS
RECOMMENDATION:
Authorize City borrowing for the municipal fire station project.
"""

    conduit_item = mi.CoverageItem(
        rank=1,
        topic="City Debt Issuance for St. Junipero Serra Catholic School",
        score=9,
        category="finance",
        action_status="Discussed",
        summary="Public debt issuance within the city.",
        why_it_matters="The school financing required council review.",
        must_include=True,
    )

    city_item = mi.CoverageItem(
        rank=2,
        topic="Fire Station Improvements",
        score=8,
        category="finance",
        action_status="Approved",
        summary="City borrowing for fire station improvements.",
        why_it_matters="The City borrowing would fund municipal work.",
        must_include=True,
    )

    plan = mi.CoveragePlan(
        items=[
            conduit_item,
            city_item,
        ]
    )

    assert mi._guard_conduit_financing_coverage_plan(
        plan,
        mixed_agenda,
    )

    assert "city debt" not in conduit_item.topic.casefold()
    assert city_item.summary == "City borrowing for fire station improvements."
    assert city_item.why_it_matters == (
        "The City borrowing would fund municipal work."
    )


def test_story_guard_ignores_unrelated_city_debt_item():
    story = StoryDraft(
        headline="Council Reviews School Financing and Fire Station Debt",
        dek=(
            "CEDA financing would benefit St. Junipero Serra Catholic School, "
            "while a separate item concerned the fire station."
        ),
        body=[
            "The City debt issuance would fund fire station improvements."
        ],
        key_facts=[],
        verification_notes=[],
    )

    # The dangerous phrase is in a paragraph about an unrelated
    # City project, so it must not be assigned to the conduit item.
    # Requiring paragraph-level anchors avoids this false positive.
    assert pc.unsupported_conduit_financing_story_issues(
        story,
        RSM_AGENDA,
    ) == []


def test_formal_intent_gate_covers_direct_accept_and_pass():
    story = StoryDraft(
        headline="Council Considers Formal Action",
        dek="The final action remains unresolved.",
        body=["The recording evidence only establishes discussion."],
        key_facts=[],
        verification_notes=[],
    )

    for agenda_title in (
        "DIRECT STAFF TO PREPARE A CONTRACT AMENDMENT",
        "ACCEPT THE ANNUAL FINANCIAL REPORT",
        "PASS A MOTION AUTHORIZING THE PROJECT",
    ):
        intelligence = {
            "coverage_items": [
                {
                    "rank": 1,
                    "topic": "Annual Project Contract Report",
                    "score": 9,
                    "must_include": True,
                }
            ],
            "action_ledger": [
                {
                    "topic": "Annual Project Contract Report",
                    "item_number": "8.1",
                    "agenda_section": "NEW BUSINESS",
                    "agenda_title": agenda_title,
                    "action_status": "discussed",
                    "validated": True,
                }
            ],
        }

        issues = pc.unresolved_high_priority_formal_action_issues(
            story,
            intelligence,
        )

        assert len(issues) == 1, agenda_title
