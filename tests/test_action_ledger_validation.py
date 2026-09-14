import meeting_intelligence as mi


class _FakeResponse:
    def __init__(self, records):
        self.parsed = mi.ActionLedger(
            items=[mi.ActionRecord(**record) for record in records]
        )
        self.text = self.parsed.model_dump_json()


class _FakeModels:
    def __init__(self, response):
        self._response = response

    def generate_content(self, **_kwargs):
        return self._response


class _FakeClient:
    def __init__(self, response):
        self.models = _FakeModels(response)


def _install_model_records(monkeypatch, records):
    response = _FakeResponse(records)
    monkeypatch.setattr(
        mi.genai,
        "Client",
        lambda: _FakeClient(response),
    )


def _meeting():
    return {
        "city_name": "Testville",
        "meeting_date": "2026-09-01",
    }


def test_action_ledger_validates_topic_linked_formal_action(monkeypatch):
    agenda = """
DISCUSSION/ACTION ITEMS
15. CAPITAL IMPROVEMENT PLAN UPDATE
"""
    notes = "Council approved the Capital Improvement Plan Update."

    _install_model_records(
        monkeypatch,
        [
            {
                "topic": "Capital Improvement Plan Update",
                "item_number": "15",
                "action_status": "approved",
                "evidence_source": "notes",
                "evidence_quote": notes,
            }
        ],
    )

    result = mi.build_action_ledger(_meeting(), notes, agenda)

    assert len(result) == 1
    item = result[0]
    assert item["item_number"] == "15"
    assert item["agenda_section"] == "DISCUSSION"
    assert item["action_status"] == "approved"
    assert item["validated"] is True
    assert item["validation_note"] == ""


def test_action_ledger_downgrades_unproved_agenda_formal_claim_to_discussion(monkeypatch):
    agenda = """
DISCUSSION/ACTION ITEMS
15. CAPITAL IMPROVEMENT PLAN UPDATE
"""
    notes = (
        "Staff provided an update on the Capital Improvement Plan Update.\n"
        "Council discussed project timing and funding."
    )

    _install_model_records(
        monkeypatch,
        [
            {
                "topic": "Capital Improvement Plan Update",
                "item_number": "15",
                "action_status": "approved",
                "evidence_source": "agenda",
                "evidence_quote": "15. CAPITAL IMPROVEMENT PLAN UPDATE",
            }
        ],
    )

    result = mi.build_action_ledger(_meeting(), notes, agenda)

    item = result[0]
    assert item["action_status"] == "discussed"
    assert item["evidence_source"] == "notes"
    assert item["validated"] is True
    assert "Formal action was not validated" in item["validation_note"]


def test_action_ledger_repairs_paraphrased_public_comment_to_exact_notes(monkeypatch):
    agenda = ""
    notes = (
        "Resident Jane Doe spoke about electric bicycle safety advocacy near schools.\n"
        "She advocated for safer crossings and rider education."
    )

    _install_model_records(
        monkeypatch,
        [
            {
                "topic": "Electric Bicycle Safety Advocacy",
                "item_number": "",
                "action_status": "resident comment",
                "evidence_source": "notes",
                "evidence_quote": "Jane asked for safer e-bikes near schools.",
            }
        ],
    )

    result = mi.build_action_ledger(_meeting(), notes, agenda)

    item = result[0]
    assert item["action_status"] == "resident comment"
    assert item["evidence_source"] == "notes"
    assert "Resident Jane Doe" in item["evidence_quote"]
    assert item["validated"] is True


def test_action_ledger_promotes_explicit_staff_followup_over_generic_discussion(monkeypatch):
    agenda = ""
    notes = (
        "Council discussed electric bicycle safety policy near schools.\n"
        "Council requested staff follow-up on electric bicycle safety policy enforcement."
    )

    _install_model_records(
        monkeypatch,
        [
            {
                "topic": "Electric Bicycle Safety Policy",
                "item_number": "",
                "action_status": "discussed",
                "evidence_source": "notes",
                "evidence_quote": "Council discussed electric bicycle safety policy near schools.",
            }
        ],
    )

    result = mi.build_action_ledger(_meeting(), notes, agenda)

    item = result[0]
    assert item["action_status"] == "requested staff follow-up"
    assert "requested staff follow-up" in item["evidence_quote"].lower()
    assert item["validated"] is True


def test_action_ledger_recovers_consent_approval_from_unique_leaf_block(monkeypatch):
    agenda = """
CONSENT CALENDAR ITEMS
5.6 GEOTECHNICAL SERVICES AGREEMENTS
5.7 TRAFFIC SIGNAL MAINTENANCE
"""
    notes = """
Consent Calendar (Items 6, 7, 8)
Motion to approve the Consent Calendar passed unanimously.

Agenda Item 9 was discussed separately.
"""

    _install_model_records(
        monkeypatch,
        [
            {
                "topic": "Geotechnical Services Agreements",
                "item_number": "5.6",
                "action_status": "unclear",
                "evidence_source": "notes",
                "evidence_quote": "Consent Calendar (Items 6, 7, 8)",
            }
        ],
    )

    result = mi.build_action_ledger(_meeting(), notes, agenda)

    item = result[0]
    assert item["item_number"] == "5.6"
    assert item["agenda_section"] == "CONSENT CALENDAR"
    assert item["action_status"] == "approved"
    assert item["validated"] is True
    assert item["agenda_linkage_conflict"] is False
    assert "passed unanimously" in item["evidence_quote"]


def test_action_ledger_canonicalizes_passed_motion_to_specific_action(monkeypatch):
    agenda = """
PUBLIC HEARING ITEMS
21. NOISE ORDINANCE
"""
    quote = "Agenda Item 21: Motion to adopt the Noise Ordinance passed 5-0."
    notes = quote

    _install_model_records(
        monkeypatch,
        [
            {
                "topic": "Noise Ordinance",
                "item_number": "21",
                "action_status": "passed",
                "evidence_source": "notes",
                "evidence_quote": quote,
            }
        ],
    )

    result = mi.build_action_ledger(_meeting(), notes, agenda)

    item = result[0]
    assert item["action_status"] == "adopted"
    assert item["validated"] is True
    assert item["item_number"] == "21"


def test_action_ledger_preserves_topic_action_but_marks_item_number_conflict(monkeypatch):
    agenda = """
DISCUSSION/ACTION ITEMS
15. CAPITAL IMPROVEMENT PLAN UPDATE
"""
    quote = "Agenda Item 99: Council approved the Capital Improvement Plan Update."
    notes = quote

    _install_model_records(
        monkeypatch,
        [
            {
                "topic": "Capital Improvement Plan Update",
                "item_number": "15",
                "action_status": "approved",
                "evidence_source": "notes",
                "evidence_quote": quote,
            }
        ],
    )

    result = mi.build_action_ledger(_meeting(), notes, agenda)

    item = result[0]
    assert item["validated"] is True
    assert item["action_status"] == "approved"
    assert item["item_number"] == "15"
    assert item["evidence_item_numbers"] == ["99"]
    assert item["agenda_linkage_conflict"] is True
    assert "official agenda maps this topic to item 15" in item["validation_note"]


def test_action_ledger_appends_narrow_formal_action_when_parent_scope_fails(monkeypatch):
    agenda = """
DISCUSSION/ACTION ITEMS
15. DIGITAL SIGNAGE POLICY
16. AUTOMATED LICENSE PLATE RECOGNITION POLICY
"""
    quote = "Agenda Item 15: Council directed staff regarding Digital Signage Policy."
    notes = quote

    _install_model_records(
        monkeypatch,
        [
            {
                "topic": "Automated License Plate Recognition and Digital Signage Policy",
                "item_number": "",
                "action_status": "directed",
                "evidence_source": "notes",
                "evidence_quote": quote,
            }
        ],
    )

    result = mi.build_action_ledger(_meeting(), notes, agenda)

    assert len(result) == 2
    parent = next(
        item
        for item in result
        if item["topic"].startswith("Automated License Plate Recognition")
    )
    supplemental = next(
        item
        for item in result
        if item["topic"] == "Digital Signage Policy"
    )

    assert parent["action_status"] == "unclear"
    assert parent["validated"] is False
    assert supplemental["action_status"] == "directed"
    assert supplemental["validated"] is True
    assert supplemental["item_number"] == "15"


def test_action_ledger_deduplicates_supplemental_against_validated_direct_record(monkeypatch):
    agenda = """
DISCUSSION/ACTION ITEMS
15. DIGITAL SIGNAGE POLICY
16. AUTOMATED LICENSE PLATE RECOGNITION POLICY
"""
    quote = "Agenda Item 15: Council directed staff regarding Digital Signage Policy."
    notes = quote

    _install_model_records(
        monkeypatch,
        [
            {
                "topic": "Automated License Plate Recognition and Digital Signage Policy",
                "item_number": "",
                "action_status": "directed",
                "evidence_source": "notes",
                "evidence_quote": quote,
            },
            {
                "topic": "Digital Signage Policy",
                "item_number": "15",
                "action_status": "directed",
                "evidence_source": "notes",
                "evidence_quote": quote,
            },
        ],
    )

    result = mi.build_action_ledger(_meeting(), notes, agenda)

    digital_records = [
        item for item in result if item["topic"] == "Digital Signage Policy"
    ]
    assert len(digital_records) == 1
    assert digital_records[0]["validated"] is True
    assert digital_records[0]["action_status"] == "directed"
