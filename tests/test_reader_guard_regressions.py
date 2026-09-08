from gemini_worker import (
    StoryDraft,
)

from process_city import (
    normalize_validated_action_language,
    reconcile_entity_verification_notes,
)


def unclear_action(
    topic,
    item,
    section,
    agenda_title="",
):
    return {
        "topic":
            topic,
        "agenda_title":
            agenda_title,
        "item_number":
            item,
        "agenda_section":
            section,
        "action_status":
            "unclear",
        "validated":
            False,
        "agenda_linkage_conflict":
            False,
    }


def test_mapped_unvalidated_unclear_actions_fail_closed():
    story = StoryDraft(
        headline=(
            "Council Reviews August Agenda"
        ),
        dek=(
            "The City Council approved geotechnical engineering "
            "agreements, approved a 20 mph school zone speed limit, "
            "and approved procurement for traffic signal materials "
            "and agenda management software."
        ),
        body=[
            (
                "The Council adopted a resolution establishing a "
                "20 mph prima facie speed limit in all designated "
                "school zones. Councilmember Bradley J. McGirr "
                "noted that the Council is evaluating local schools "
                "to determine whether zones should be expanded."
            ),
            (
                "The Council appointed Mayor Pro Tempore Anne D. "
                "Figueroa to serve as the voting delegate for the "
                "League of California Cities Annual Conference. "
                "During public comment, a speaker separately "
                "questioned Figueroa's role as a delegate."
            ),
            (
                "The Council approved a $32,073.04 purchase order "
                "with Granicus, LLC for annual agenda management "
                "subscription services. The Council waived formal "
                "bidding requirements for this contract."
            ),
        ],
        key_facts=[
            (
                "A 20 mph prima facie speed limit was adopted "
                "for all designated school zones."
            ),
            (
                "Mayor Pro Tempore Anne D. Figueroa was appointed "
                "as the voting delegate for the League of "
                "California Cities Annual Conference."
            ),
            (
                "The Council approved a $32,073.04 purchase order "
                "for annual agenda management software."
            ),
        ],
        verification_notes=[],
    )

    intelligence = {
        "action_ledger": [
            unclear_action(
                (
                    "School Zone Speed Limits "
                    "and Traffic Policy"
                ),
                "5.9",
                "CONSENT CALENDAR",
                (
                    "School Zone Speed Limits "
                    "and Traffic Policy"
                ),
            ),
            unclear_action(
                (
                    "Appointment of League of "
                    "California Cities Delegate"
                ),
                "5.11",
                "CONSENT CALENDAR",
                (
                    "Appointment of League of "
                    "California Cities Delegate"
                ),
            ),
            unclear_action(
                (
                    "Approval of Agenda "
                    "Management Software"
                ),
                "5.10",
                "CONSENT CALENDAR",
                (
                    "Approval of Agenda "
                    "Management Software"
                ),
            ),
        ]
    }

    changed = (
        normalize_validated_action_language(
            story,
            intelligence,
        )
    )

    assert changed

    combined = "\n".join(
        [
            story.headline,
            story.dek,
            *story.body,
            *story.key_facts,
        ]
    )

    assert (
        "adopted a resolution"
        not in combined
    )

    assert (
        "was adopted for all designated school zones"
        not in combined
    )

    assert (
        "appointed Mayor Pro Tempore"
        not in combined
    )

    assert (
        "was appointed as the voting delegate"
        not in combined
    )

    assert (
        "approved a $32,073.04 purchase order"
        not in combined
    )

    assert (
        "waived formal bidding requirements for this contract"
        not in combined
    )

    assert (
        "evaluating local schools"
        in combined
    )

    assert (
        "speaker separately questioned"
        in combined
    )

    assert (
        "geotechnical engineering agreements"
        in story.dek
    )


def test_unmapped_unvalidated_unclear_row_does_not_delete_copy():
    original = (
        "The Council approved the mystery software contract."
    )

    story = StoryDraft(
        headline="Mystery Software Contract",
        dek=original,
        body=[original],
        key_facts=[original],
        verification_notes=[],
    )

    intelligence = {
        "action_ledger": [
            {
                "topic":
                    "Mystery Software Contract",
                "agenda_title":
                    "Mystery Software Contract",
                "item_number":
                    "",
                "agenda_section":
                    "",
                "action_status":
                    "unclear",
                "validated":
                    False,
                "agenda_linkage_conflict":
                    False,
            }
        ]
    }

    normalize_validated_action_language(
        story,
        intelligence,
    )

    assert story.dek == original
    assert story.body == [original]
    assert story.key_facts == [original]


def test_corrected_entity_reconciles_only_matching_stale_note():
    ordona = (
        "The identity associated with the source reference "
        "'Mr. Ordona' remains unverified; CouncilWatch did not "
        "rely on that name as an identified person."
    )

    okconor = (
        "The identity associated with the source reference "
        "'Mr. Okconor' remains unverified; CouncilWatch did not "
        "rely on that name as an identified person."
    )

    story = StoryDraft(
        headline="Laguna Niguel Council Meeting",
        dek="Council actions and public comments.",
        body=[],
        key_facts=[],
        verification_notes=[
            ordona,
            okconor,
        ],
    )

    intelligence = {
        "entities": [
            {
                "observed_text":
                    "Mr. Okconor",
                "canonical_text":
                    "Kevin O'Connor",
                "entity_type":
                    "person",
                "status":
                    "CORRECTED",
                "confidence":
                    "high",
                "official_source_url":
                    (
                        "https://www.cityoflagunaniguel.org/"
                        "Directory.aspx?did=70"
                    ),
            },
            {
                "observed_text":
                    "Mr. Ordona",
                "canonical_text":
                    "Mr. Ordona",
                "entity_type":
                    "person",
                "status":
                    "UNVERIFIED",
                "confidence":
                    "low",
                "official_source_url":
                    "",
            },
        ]
    }

    changed = (
        reconcile_entity_verification_notes(
            story,
            intelligence,
        )
    )

    assert changed

    assert ordona in story.verification_notes

    assert okconor not in story.verification_notes

    corrected = "\n".join(
        story.verification_notes
    )

    assert (
        "Mr. Okconor"
        in corrected
    )

    assert (
        "Kevin O'Connor"
        in corrected
    )

    assert (
        "Mr. Okconor' remains unverified"
        not in corrected
    )


def test_note_reconciliation_requires_official_source():
    stale = (
        "The identity associated with the source reference "
        "'Example Name' remains unverified; CouncilWatch did not "
        "rely on that name as an identified person."
    )

    story = StoryDraft(
        headline="Test",
        dek="Test",
        body=[],
        key_facts=[],
        verification_notes=[
            stale
        ],
    )

    intelligence = {
        "entities": [
            {
                "observed_text":
                    "Example Name",
                "canonical_text":
                    "Correct Name",
                "entity_type":
                    "person",
                "status":
                    "CORRECTED",
                "official_source_url":
                    "",
            }
        ]
    }

    changed = (
        reconcile_entity_verification_notes(
            story,
            intelligence,
        )
    )

    assert not changed
    assert story.verification_notes == [
        stale
    ]


def test_unclear_phrase_anchor_does_not_bridge_removed_stopword():
    original = (
        "The Council approved agenda annual management services."
    )

    story = StoryDraft(
        headline="Test",
        dek="Test",
        body=[original],
        key_facts=[],
        verification_notes=[],
    )

    intelligence = {
        "action_ledger": [
            {
                "topic":
                    "Approval of Agenda Management Software",
                "agenda_title":
                    "Approval of Agenda Management Software",
                "item_number":
                    "5.10",
                "agenda_section":
                    "CONSENT CALENDAR",
                "action_status":
                    "unclear",
                "validated":
                    False,
                "agenda_linkage_conflict":
                    False,
            }
        ]
    }

    changed = normalize_validated_action_language(
        story,
        intelligence,
    )

    # "agenda annual management" must NOT manufacture the
    # nonexistent adjacent phrase "agenda management".
    assert not changed
    assert story.body == [original]


from gemini_worker import (
    AuditIssue,
    AuditResult,
    _audit_issue_correction_is_noop,
    _guard_audit_result,
    _protected_public_comment_entity,
)


def test_audit_guard_rejects_exact_noop_correction():
    issue = AuditIssue(
        severity="material",
        field="body",
        draft_text=(
            "During public comment, a resident questioned "
            "the delegate's status."
        ),
        source_evidence="The notes support the statement.",
        correction=(
            "During public comment, a resident questioned "
            "the delegate's status."
        ),
    )

    assert (
        _audit_issue_correction_is_noop(
            issue
        )
    )


def test_public_comment_org_is_protected_from_agenda_canonicalization():
    issue = AuditIssue(
        severity="material",
        field="body",
        draft_text=(
            "During public comment, a speaker questioned "
            "Figueroa's role as a delegate to the Orange "
            "County League of Cities."
        ),
        source_evidence=(
            "The agenda refers to the League of "
            "California Cities."
        ),
        correction=(
            "During public comment, a speaker questioned "
            "Figueroa's role as a delegate to the League "
            "of California Cities."
        ),
    )

    notes = (
        "A speaker suggested the Council reconsider "
        "Figueroa's status as a delegate to the "
        "Orange County League of Cities."
    )

    assert (
        _protected_public_comment_entity(
            issue,
            notes,
        )
        == "Orange County League of Cities"
    )


def test_public_comment_person_is_not_broadly_overprotected():
    issue = AuditIssue(
        severity="material",
        field="body",
        draft_text=(
            "During public comment, speaker Jon Smyth spoke."
        ),
        source_evidence=(
            "Official records identify the person differently."
        ),
        correction=(
            "During public comment, speaker John Smith spoke."
        ),
    )

    notes = (
        "Speaker Jon Smyth spoke during public comment."
    )

    assert (
        _protected_public_comment_entity(
            issue,
            notes,
        )
        == ""
    )


def test_rejected_body_issue_cannot_leak_through_corrected_body():
    original_public_comment = (
        "During public comment, a speaker questioned "
        "Figueroa's role as a delegate to the Orange "
        "County League of Cities."
    )

    original_traffic = (
        "The Council waived formal competitive bidding "
        "requirements for traffic signal materials."
    )

    story = StoryDraft(
        headline="Test",
        dek="Test",
        body=[
            original_public_comment,
            original_traffic,
        ],
        key_facts=[],
        verification_notes=[],
    )

    protected_issue = AuditIssue(
        severity="material",
        field="body",
        draft_text=original_public_comment,
        source_evidence=(
            "The agenda refers to the League of "
            "California Cities."
        ),
        correction=(
            "During public comment, a speaker questioned "
            "Figueroa's role as a delegate to the League "
            "of California Cities."
        ),
    )

    valid_issue = AuditIssue(
        severity="material",
        field="body",
        draft_text=original_traffic,
        source_evidence=(
            "The notes say this concerned traffic signal "
            "enhancement materials."
        ),
        correction=(
            "The Council waived formal competitive bidding "
            "requirements for traffic signal enhancement "
            "materials."
        ),
    )

    result = AuditResult(
        ok=False,
        issues=[
            protected_issue,
            valid_issue,
        ],
        corrected_headline="Test",
        corrected_dek="Test",
        corrected_body=[
            (
                "During public comment, a speaker questioned "
                "Figueroa's role as a delegate to the League "
                "of California Cities."
            ),
            (
                "The Council waived formal competitive bidding "
                "requirements for traffic signal enhancement "
                "materials."
            ),
        ],
        corrected_key_facts=[],
        corrected_verification_notes=[],
    )

    guarded = _guard_audit_result(
        result,
        story,
        (
            "A speaker questioned Figueroa's status as a "
            "delegate to the Orange County League of Cities."
        ),
    )

    assert len(
        guarded.issues
    ) == 1

    assert (
        guarded.issues[
            0
        ].draft_text
        == original_traffic
    )

    # Because BODY contained a rejected unsafe edit, the entire
    # corrected body must be reset to current source-faithful copy.
    assert (
        guarded.corrected_body
        == story.body
    )

    assert (
        "Orange County League of Cities"
        in guarded.corrected_body[
            0
        ]
    )


def test_public_comment_org_does_not_protect_unrelated_false_claim():
    issue = AuditIssue(
        severity="material",
        field="body",
        draft_text=(
            "During public comment, a resident said the "
            "Orange County League of Cities formally endorsed "
            "Measure X."
        ),
        source_evidence=(
            "The notes mention the organization, but do not "
            "say it endorsed Measure X."
        ),
        correction=(
            "During public comment, a resident discussed "
            "Measure X."
        ),
    )

    notes = (
        "A resident mentioned the Orange County League of "
        "Cities during comments. The notes do not establish "
        "any organizational endorsement of Measure X."
    )

    assert (
        _protected_public_comment_entity(
            issue,
            notes,
        )
        == ""
    )


from gemini_worker import (
    AuditIssue,
    AuditResult,
    StoryDraft,
    _guard_audit_result,
    _protected_publishable_person_rewrite,
)


PERSON_CONTEXT = """
VERIFIED ENTITY CONTEXT:
IDENTITY/SPELLING ONLY — NOT EVIDENCE OF MEETING ACTIONS,
POLICY EFFECTS, TECHNICAL RELATIONSHIPS OR CONSEQUENCES.

- CORRECTED: 'Council member Otto' -> 'Stephanie Oddo'
- CORRECTED: 'Council member Winstead' -> 'Stephanie Winstead'

PUBLISHABLE PERSON NAMES:
- Stephanie Oddo
- Stephanie Winstead

PERSON-NAME RULE: Any human name not listed above must not
appear in headline, dek, body or key facts.
""".strip()


def test_publishable_person_guard_blocks_near_name_downgrade():
    issue = AuditIssue(
        severity="material",
        field="body",
        draft_text="Stephanie Oddo",
        source_evidence=(
            "The raw transcript uses Stephanie Otto."
        ),
        correction="Stephanie Otto",
    )

    assert (
        _protected_publishable_person_rewrite(
            issue,
            PERSON_CONTEXT,
        )
        == "Stephanie Oddo"
    )


def test_publishable_person_guard_blocks_role_form_downgrade():
    issue = AuditIssue(
        severity="material",
        field="body",
        draft_text="Stephanie Oddo",
        source_evidence=(
            "The recording says Council Member Otto."
        ),
        correction=(
            "Replace 'Stephanie Oddo' with "
            "'Council Member Otto'."
        ),
    )

    assert (
        _protected_publishable_person_rewrite(
            issue,
            PERSON_CONTEXT,
        )
        == "Stephanie Oddo"
    )


def test_publishable_person_guard_allows_other_whitelisted_person():
    issue = AuditIssue(
        severity="material",
        field="body",
        draft_text="Stephanie Oddo",
        source_evidence=(
            "The verified context establishes "
            "Stephanie Winstead instead."
        ),
        correction="Stephanie Winstead",
    )

    assert (
        _protected_publishable_person_rewrite(
            issue,
            PERSON_CONTEXT,
        )
        == ""
    )


def test_publishable_person_guard_allows_generic_deidentification():
    issue = AuditIssue(
        severity="material",
        field="body",
        draft_text="Stephanie Oddo",
        source_evidence=(
            "The attribution cannot be established."
        ),
        correction=(
            "the District 4 representative"
        ),
    )

    assert (
        _protected_publishable_person_rewrite(
            issue,
            PERSON_CONTEXT,
        )
        == ""
    )


def test_guarded_audit_cannot_apply_otto_over_oddo():
    story = StoryDraft(
        headline="Test",
        dek="Test",
        body=[
            (
                "The Council appointed Ray Gennawey "
                "for District 2 and Stephanie Oddo "
                "for District 4."
            ),
        ],
        key_facts=[
            (
                "Ray Gennawey and Stephanie Oddo "
                "were appointed."
            ),
        ],
        verification_notes=[],
    )

    body_issue = AuditIssue(
        severity="material",
        field="body",
        draft_text="Stephanie Oddo",
        source_evidence=(
            "The agenda and notes identify "
            "Stephanie Otto."
        ),
        correction="Stephanie Otto",
    )

    fact_issue = AuditIssue(
        severity="material",
        field="key_facts",
        draft_text="Stephanie Oddo",
        source_evidence=(
            "The agenda and notes identify "
            "Stephanie Otto."
        ),
        correction="Stephanie Otto",
    )

    result = AuditResult(
        ok=False,
        issues=[
            body_issue,
            fact_issue,
        ],
        corrected_headline="Test",
        corrected_dek="Test",
        corrected_body=[
            (
                "The Council appointed Ray Gennawey "
                "for District 2 and Stephanie Otto "
                "for District 4."
            ),
        ],
        corrected_key_facts=[
            (
                "Ray Gennawey and Stephanie Otto "
                "were appointed."
            ),
        ],
        corrected_verification_notes=[],
    )

    guarded = _guard_audit_result(
        result,
        story,
        PERSON_CONTEXT,
    )

    assert guarded.issues == []

    # No contaminated corrected field may survive.
    assert guarded.corrected_body == []
    assert guarded.corrected_key_facts == []

    assert "Stephanie Oddo" in story.body[0]
    assert "Stephanie Otto" not in story.body[0]
