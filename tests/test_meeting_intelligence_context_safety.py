import json
from types import SimpleNamespace

import pytest

import entity_registry
import meeting_intelligence as mi


def test_conduit_anchor_tokens_drop_generic_financing_words():
    tokens = mi._conduit_anchor_tokens(
        "City public debt financing for Acme Medical Center tax-exempt loan"
    )

    assert "acme" in tokens
    assert "medical" in tokens
    assert "center" in tokens
    assert "city" not in tokens
    assert "debt" not in tokens
    assert "financing" not in tokens


def test_agenda_item_source_block_stops_at_next_numbered_item():
    agenda = """
15. TAX-EXEMPT LOAN FOR ACME MEDICAL CENTER
The authority may issue financing for the benefit of Acme Medical Center.
16. TRAFFIC SIGNAL MAINTENANCE
Routine maintenance agreement.
"""

    block = mi._agenda_item_source_block(agenda, "15.")

    assert "ACME MEDICAL CENTER" in block
    assert "benefit of Acme Medical Center" in block
    assert "TRAFFIC SIGNAL MAINTENANCE" not in block
    assert mi._agenda_item_source_block(agenda, "99.") == ""
    assert mi._agenda_item_source_block(agenda, "") == ""


def test_conduit_contexts_preserve_per_item_city_obligation(monkeypatch):
    agenda = """
15. TAX-EXEMPT LOAN BY DEVELOPMENT AUTHORITY FOR BENEFIT OF ACME MEDICAL CENTER
The Development Authority may issue the loan for the benefit of Acme Medical Center.
16. TAX-EXEMPT LOAN BY DEVELOPMENT AUTHORITY FOR BENEFIT OF BETA HOUSING
The City is the borrower for the Beta Housing financing.
"""

    monkeypatch.setattr(
        mi,
        "parse_agenda_structure",
        lambda _agenda: [
            {
                "item_number": "15.",
                "title": (
                    "Tax-Exempt Loan by Development Authority for Benefit of "
                    "Acme Medical Center"
                ),
            },
            {
                "item_number": "16.",
                "title": (
                    "Tax-Exempt Loan by Development Authority for Benefit of "
                    "Beta Housing"
                ),
            },
            {
                "item_number": "17.",
                "title": "Routine Traffic Signal Maintenance",
            },
        ],
    )

    contexts = mi._conduit_financing_agenda_contexts(agenda)

    assert len(contexts) == 2
    by_number = {item["item_number"]: item for item in contexts}
    assert by_number["15."]["city_obligation"] is False
    assert by_number["16."]["city_obligation"] is True
    assert {"acme", "medical", "center"} <= by_number["15."]["anchors"]


def test_conduit_coverage_guard_rewrites_only_matching_third_party_item(monkeypatch):
    plan = mi.CoveragePlan(
        items=[
            mi.CoverageItem(
                rank=1,
                topic="City debt issuance for Acme Medical Center",
                score=8,
                category="finance",
                action_status="approved",
                summary=(
                    "The council approved public debt issuance within the city "
                    "for Acme Medical Center."
                ),
                why_it_matters="The city borrowing would finance the Acme Medical Center.",
                must_include=True,
            ),
            mi.CoverageItem(
                rank=2,
                topic="City debt for Beta Housing",
                score=7,
                category="finance",
                action_status="approved",
                summary="The city borrowing would finance Beta Housing.",
                why_it_matters="Beta Housing financing.",
            ),
            mi.CoverageItem(
                rank=3,
                topic="City debt policy update",
                score=5,
                category="finance",
                action_status="discussed",
                summary="Council discussed general city debt policy.",
                why_it_matters="General borrowing policy.",
            ),
        ]
    )

    monkeypatch.setattr(
        mi,
        "_conduit_financing_agenda_contexts",
        lambda _agenda: [
            {
                "item_number": "15.",
                "title": "Acme Medical Center financing",
                "anchors": {"acme", "medical", "center"},
                "source_block": "",
                "city_obligation": False,
            },
            {
                "item_number": "16.",
                "title": "Beta Housing financing",
                "anchors": {"beta", "housing"},
                "source_block": "",
                "city_obligation": True,
            },
        ],
    )

    assert mi._guard_conduit_financing_coverage_plan(plan, "agenda") is True

    acme = plan.items[0]
    assert "City debt" not in acme.topic
    assert "third-party tax-exempt financing" in acme.summary
    assert "city borrowing" not in acme.why_it_matters.lower()

    beta = plan.items[1]
    assert beta.topic == "City debt for Beta Housing"
    assert "city borrowing" in beta.summary.lower()

    unrelated = plan.items[2]
    assert unrelated.topic == "City debt policy update"


def test_conduit_coverage_guard_no_context_is_noop(monkeypatch):
    plan = mi.CoveragePlan(
        items=[
            mi.CoverageItem(
                rank=1,
                topic="City debt policy",
                score=5,
                category="finance",
                action_status="discussed",
                summary="Discussion only.",
                why_it_matters="General policy.",
            )
        ]
    )
    monkeypatch.setattr(mi, "_conduit_financing_agenda_contexts", lambda _agenda: [])

    assert mi._guard_conduit_financing_coverage_plan(plan, "agenda") is False
    assert plan.items[0].topic == "City debt policy"


class _FakeCoverageModels:
    def __init__(self, response):
        self.response = response

    def generate_content(self, **_kwargs):
        return self.response


class _FakeCoverageClient:
    def __init__(self, response):
        self.models = _FakeCoverageModels(response)


def _coverage_payload():
    return {
        "items": [
            {
                "rank": 1,
                "topic": "Traffic Safety",
                "score": 8,
                "category": "public safety",
                "action_status": "discussed",
                "summary": "Council discussed traffic safety.",
                "why_it_matters": "Residents raised safety concerns.",
                "must_include": True,
            }
        ],
        "editorial_summary": "Traffic safety led the meeting.",
    }


def test_build_coverage_plan_accepts_parsed_schema_and_runs_guard(monkeypatch):
    plan = mi.CoveragePlan.model_validate(_coverage_payload())
    response = SimpleNamespace(parsed=plan, text=plan.model_dump_json())
    monkeypatch.setattr(mi.genai, "Client", lambda: _FakeCoverageClient(response))

    guard_calls = []
    monkeypatch.setattr(
        mi,
        "_guard_conduit_financing_coverage_plan",
        lambda plan_arg, agenda_arg: guard_calls.append((plan_arg, agenda_arg)) or False,
    )

    result = mi.build_coverage_plan(
        {"city_name": "Testville", "meeting_date": "2026-09-01"},
        "notes",
        "agenda",
        [],
    )

    assert result["items"][0]["topic"] == "Traffic Safety"
    assert result["editorial_summary"] == "Traffic safety led the meeting."
    assert len(guard_calls) == 1
    assert guard_calls[0][1] == "agenda"


def test_build_coverage_plan_falls_back_to_json_text(monkeypatch):
    payload = _coverage_payload()
    response = SimpleNamespace(parsed=None, text=json.dumps(payload))
    monkeypatch.setattr(mi.genai, "Client", lambda: _FakeCoverageClient(response))
    monkeypatch.setattr(
        mi,
        "_guard_conduit_financing_coverage_plan",
        lambda _plan, _agenda: False,
    )

    result = mi.build_coverage_plan(
        {"city_name": "Testville", "meeting_date": "2026-09-01"},
        "notes",
        "agenda",
        [],
    )

    assert result == payload


def test_build_meeting_intelligence_merges_registry_and_secondary_entities(monkeypatch):
    registry = [
        {
            "observed_text": "OCTA",
            "canonical_text": "Orange County Transportation Authority",
            "entity_type": "organization",
            "status": "VERIFIED",
        }
    ]

    monkeypatch.setattr(entity_registry, "find_entities_in_text", lambda _text: registry)
    monkeypatch.setattr(
        mi,
        "verify_entities",
        lambda *_args, **_kwargs: [
            {
                "observed_text": "OCTA",
                "canonical_text": "Orange County Transportation Authority",
                "entity_type": "organization",
                "status": "VERIFIED",
            },
            {
                "observed_text": "Jane Doe",
                "canonical_text": "Jane Doe",
                "entity_type": "person",
                "status": "VERIFIED",
            },
        ],
    )
    monkeypatch.setattr(
        mi,
        "build_coverage_plan",
        lambda *_args, **_kwargs: {
            "items": [{"topic": "Traffic Safety", "rank": 1}],
            "editorial_summary": "Editorial summary.",
        },
    )
    monkeypatch.setattr(
        mi,
        "build_action_ledger",
        lambda *_args, **_kwargs: [{"topic": "Traffic Safety", "validated": True}],
    )

    result = mi.build_meeting_intelligence(
        {"city_name": "Testville", "meeting_date": "2026-09-01"},
        "notes",
        "agenda",
    )

    assert len(result["entities"]) == 2
    assert {item["canonical_text"] for item in result["entities"]} == {
        "Orange County Transportation Authority",
        "Jane Doe",
    }
    assert result["verification_warning"] == ""
    assert result["editorial_summary"] == "Editorial summary."
    assert result["action_ledger"][0]["validated"] is True


def test_build_meeting_intelligence_keeps_registry_when_secondary_verifier_fails(monkeypatch):
    registry = [
        {
            "observed_text": "OCTA",
            "canonical_text": "Orange County Transportation Authority",
            "entity_type": "organization",
            "status": "VERIFIED",
        }
    ]
    monkeypatch.setattr(entity_registry, "find_entities_in_text", lambda _text: registry)

    def fail_verifier(*_args, **_kwargs):
        raise RuntimeError("verifier unavailable")

    monkeypatch.setattr(mi, "verify_entities", fail_verifier)
    monkeypatch.setattr(
        mi,
        "build_coverage_plan",
        lambda *_args, **_kwargs: {
            "items": [],
            "editorial_summary": "Base editorial summary.",
        },
    )
    monkeypatch.setattr(mi, "build_action_ledger", lambda *_args, **_kwargs: [])

    result = mi.build_meeting_intelligence(
        {"city_name": "Testville", "meeting_date": "2026-09-01"},
        "notes",
        "agenda",
    )

    assert result["entities"] == registry
    assert "Secondary proper-noun verification was unavailable" in result[
        "verification_warning"
    ]
    assert "RuntimeError" in result["verification_warning"]
    assert result["editorial_summary"].endswith("Base editorial summary.")


def test_build_meeting_intelligence_fails_closed_when_action_ledger_fails(monkeypatch):
    monkeypatch.setattr(entity_registry, "find_entities_in_text", lambda _text: [])
    monkeypatch.setattr(mi, "verify_entities", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        mi,
        "build_coverage_plan",
        lambda *_args, **_kwargs: {"items": [], "editorial_summary": ""},
    )

    def fail_ledger(*_args, **_kwargs):
        raise ValueError("ledger unsafe")

    monkeypatch.setattr(mi, "build_action_ledger", fail_ledger)

    with pytest.raises(ValueError, match="ledger unsafe"):
        mi.build_meeting_intelligence(
            {"city_name": "Testville", "meeting_date": "2026-09-01"},
            "notes",
            "agenda",
        )


def _intelligence_fixture():
    return {
        "entities": [
            {
                "observed_text": "J. Doe",
                "canonical_text": "Jane Doe",
                "entity_type": "person",
                "status": "CORRECTED",
                "official_source_url": "https://city.example.gov/roster",
            },
            {
                "observed_text": "Unknown Resident",
                "canonical_text": "Unknown Resident",
                "entity_type": "person",
                "status": "UNVERIFIED",
                "official_source_url": "",
            },
            {
                "observed_text": "OCTA",
                "canonical_text": "Orange County Transportation Authority",
                "entity_type": "organization",
                "status": "VERIFIED",
                "official_source_url": "https://octa.net",
            },
        ],
        "action_ledger": [
            {
                "topic": "Traffic Safety",
                "item_number": "15",
                "action_status": "directed",
                "validated": True,
                "agenda_linkage_conflict": True,
                "evidence_quote": "Council directed staff to return with options.",
            },
            {
                "topic": "Park Maintenance",
                "item_number": "",
                "action_status": "unclear",
                "validated": False,
                "agenda_linkage_conflict": False,
                "evidence_quote": "",
            },
        ],
        "coverage_items": [
            {
                "rank": 1,
                "score": 8,
                "must_include": True,
                "topic": "Traffic Safety",
            },
            {
                "rank": 2,
                "score": 5,
                "must_include": False,
                "topic": "Park Maintenance",
            },
        ],
    }


def test_audit_verification_context_whitelists_people_and_marks_action_conflict():
    context = mi.audit_verification_context(_intelligence_fixture())

    assert "PUBLISHABLE PERSON NAMES:" in context
    assert "- Jane Doe" in context
    publishable_section = context.split("PUBLISHABLE PERSON NAMES:", 1)[1].split(
        "PERSON-NAME RULE", 1
    )[0]
    assert "Unknown Resident" not in publishable_section
    assert "DIRECTED: Traffic Safety [VALIDATED]" in context
    assert "Agenda linkage conflict: YES" in context
    assert "Exact source excerpt: Council directed staff to return with options." in context
    assert "UNCLEAR: Park Maintenance [NOT VALIDATED]" in context


def test_writer_context_enforces_person_whitelist_and_action_evidence():
    context = mi.writer_context(_intelligence_fixture())

    assert "CORRECTED: 'J. Doe' -> 'Jane Doe'" in context
    assert "Official source: https://city.example.gov/roster" in context
    assert "PUBLISHABLE PERSON NAMES:" in context
    assert "- Jane Doe" in context
    assert "Item 15: Traffic Safety" in context
    assert "Status: DIRECTED" in context
    assert "Evidence validated: YES" in context
    assert "Agenda linkage conflict: YES" in context
    assert "[8/10] [MUST INCLUDE] Traffic Safety" in context
    assert "[5/10] [OPTIONAL] Park Maintenance" in context


def test_deterministic_verification_notes_only_report_unique_unverified_people():
    intelligence = _intelligence_fixture()
    intelligence["entities"].append(
        {
            "observed_text": "unknown resident",
            "canonical_text": "unknown resident",
            "entity_type": "person",
            "status": "UNVERIFIED",
        }
    )
    intelligence["entities"].append(
        {
            "observed_text": "Unverified Agency",
            "canonical_text": "Unverified Agency",
            "entity_type": "organization",
            "status": "UNVERIFIED",
        }
    )

    notes = mi.deterministic_verification_notes(intelligence)

    assert len(notes) == 1
    assert "Unknown Resident" in notes[0]
    assert "Unverified Agency" not in notes[0]


def test_audit_and_writer_context_handle_empty_people_and_actions():
    intelligence = {"entities": [], "action_ledger": [], "coverage_items": []}

    audit = mi.audit_verification_context(intelligence)
    writer = mi.writer_context(intelligence)

    assert "PUBLISHABLE PERSON NAMES:\n- NONE" in audit
    assert "SOURCE-VALIDATED ACTION LEDGER:" in audit
    assert "- NONE AVAILABLE" in audit
    assert "PUBLISHABLE PERSON NAMES:\n- NONE" in writer
    assert "- NONE AVAILABLE" in writer
