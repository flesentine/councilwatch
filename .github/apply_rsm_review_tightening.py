from pathlib import Path


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, found {count}")
    return text.replace(old, new, 1)


pc_path = Path("process_city.py")
pc = pc_path.read_text(encoding="utf-8")

pc = replace_once(
    pc,
    '''        for action in ledger:
            action_status = str(
''',
    '''        for action in ledger:
            if action.get("validated") is not True:
                continue

            action_status = str(
''',
    "validated unresolved-action filter",
)

story_guard = '''

def unsupported_conduit_financing_story_issues(
    story,
    agenda,
):
    """
    Fail closed when publishable copy assigns third-party conduit
    financing debt/borrowing to the City without official support.

    This does not rewrite prose. It creates a material audit issue
    so the exact final copy must be corrected and re-audited.
    """
    agenda_lower = str(
        agenda or ""
    ).casefold()

    is_conduit_financing = (
        "tax-exempt loan" in agenda_lower
        and "development authority" in agenda_lower
        and "benefit of" in agenda_lower
    )

    if not is_conduit_financing:
        return []

    explicit_city_obligation = re.search(
        r"\\bcity(?:\\s+of\\s+[a-z\\s]+)?\\s+"
        r"(?:is|will\\s+be|shall\\s+be|acts\\s+as)\\s+"
        r"(?:the\\s+)?(?:borrower|obligor|debtor|guarantor)\\b"
        r"|\\bcity(?:'s)?\\s+"
        r"(?:debt|borrowing|financial\\s+obligation|liability)\\b",
        agenda_lower,
    )

    if explicit_city_obligation:
        return []

    unsupported = re.compile(
        r"\\b(?:"
        r"city\\s+debt(?:\\s+issuance)?|"
        r"city\\s+borrowing|"
        r"municipal\\s+debt(?:\\s+issuance)?|"
        r"public\\s+debt\\s+issuance\\s+within\\s+the\\s+city|"
        r"public\\s+debt\\s+(?:issued|issuance)\\s+by\\s+the\\s+city|"
        r"debt\\s+issued\\s+by\\s+the\\s+city"
        r")\\b",
        re.I,
    )

    fields = [
        (
            "headline",
            [story.headline],
        ),
        (
            "dek",
            [story.dek],
        ),
        (
            "body",
            story.body,
        ),
        (
            "key_facts",
            story.key_facts,
        ),
    ]

    issues = []
    seen = set()

    for field, values in fields:
        for raw_value in values:
            value = str(
                raw_value or ""
            ).strip()

            if (
                not value
                or not unsupported.search(
                    value
                )
            ):
                continue

            key = (
                field,
                value,
            )

            if key in seen:
                continue

            seen.add(key)

            issues.append(
                AuditIssue(
                    severity="material",
                    field=field,
                    draft_text=value,
                    source_evidence=(
                        "The official material describes a "
                        "tax-exempt loan issued by a separate "
                        "development/financing authority for "
                        "another beneficiary and does not "
                        "establish the City as borrower, obligor, "
                        "debtor, guarantor, or financially liable."
                    ),
                    correction=(
                        "Describe the financing and the council's "
                        "approval role precisely without calling "
                        "it City debt, City borrowing, municipal "
                        "debt, or debt issued by the City unless "
                        "official evidence establishes that "
                        "obligation."
                    ),
                )
            )

    return issues
'''

pc = replace_once(
    pc,
    "\n\ndef strip_public_agenda_item_numbers(story):\n",
    story_guard + "\n\ndef strip_public_agenda_item_numbers(story):\n",
    "conduit story guard insertion",
)

pc = replace_once(
    pc,
    '''        deterministic_action_issues = (
            unresolved_high_priority_formal_action_issues(
                story,
                intelligence,
            )
        )

        if deterministic_action_issues:
''',
    '''        deterministic_action_issues = (
            unresolved_high_priority_formal_action_issues(
                story,
                intelligence,
            )
        )

        deterministic_action_issues.extend(
            unsupported_conduit_financing_story_issues(
                story,
                agenda,
            )
        )

        if deterministic_action_issues:
''',
    "conduit story guard wiring",
)

pc_path.write_text(pc, encoding="utf-8")


test_path = Path("tests/test_rsm_sept9_regressions.py")
tests = test_path.read_text(encoding="utf-8")

tests += r'''


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
        headline="Council Considers School Financing",
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
'''

test_path.write_text(tests, encoding="utf-8")

print("RSM review tightening applied.")
