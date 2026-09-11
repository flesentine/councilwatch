from pathlib import Path


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, found {count}")
    return text.replace(old, new, 1)


# ------------------------------------------------------------
# meeting_intelligence.py: scope conduit-financing cleanup to
# the matched agenda item and include CoverageItem.topic.
# ------------------------------------------------------------
mi_path = Path("meeting_intelligence.py")
mi = mi_path.read_text(encoding="utf-8")

start = mi.index("def _guard_conduit_financing_coverage_plan(")
end = mi.index("\n\ndef build_coverage_plan(", start)

new_conduit_helpers = r'''_CONDUIT_GENERIC_TOKENS = {
    "agenda",
    "approval",
    "approve",
    "approved",
    "authority",
    "benefit",
    "bond",
    "bonds",
    "borrowing",
    "city",
    "council",
    "debt",
    "development",
    "exempt",
    "finance",
    "financing",
    "issuance",
    "issued",
    "item",
    "loan",
    "municipal",
    "public",
    "resolution",
    "tax",
    "the",
    "with",
    "from",
    "for",
    "and",
    "its",
}


_CITY_FINANCIAL_OBLIGATION_RE = re.compile(
    r"\bcity(?:\s+of\s+[a-z]+(?:\s+[a-z]+){0,5})?\s+"
    r"(?:is|will\s+be|shall\s+be|acts\s+as)\s+"
    r"(?:the\s+)?(?:borrower|obligor|debtor|guarantor)\b"
    r"|\bcity(?:'s)?\s+"
    r"(?:debt|borrowing|financial\s+obligation|liability)\b",
    re.I,
)


def _conduit_anchor_tokens(value):
    return {
        token
        for token in re.findall(
            r"[a-z0-9]+",
            str(value or "").casefold(),
        )
        if len(token) >= 4
        and token not in _CONDUIT_GENERIC_TOKENS
    }


def _agenda_item_source_block(
    agenda,
    item_number,
):
    """
    Return the raw source block for one numbered agenda item.

    This is used only to keep City-obligation evidence scoped to
    the same conduit-financing item rather than the whole meeting.
    """
    agenda_text = str(
        agenda or ""
    )

    item_number = str(
        item_number or ""
    ).strip()

    if not item_number:
        return ""

    start_match = re.search(
        rf"(?mi)^\s*{re.escape(item_number)}(?:\s+|$)",
        agenda_text,
    )

    if not start_match:
        return ""

    next_match = re.search(
        r"(?mi)^\s*(?:\d+(?:\.\d+)+|\d+\.)(?:\s+|$)",
        agenda_text[start_match.end():],
    )

    if next_match:
        end_index = (
            start_match.end()
            + next_match.start()
        )
    else:
        end_index = len(
            agenda_text
        )

    return agenda_text[
        start_match.start():end_index
    ]


def _conduit_financing_agenda_contexts(
    agenda,
):
    """
    Identify individual official agenda items that are clearly
    third-party conduit financing, preserving per-item scope.
    """
    contexts = []

    for agenda_item in parse_agenda_structure(
        agenda
    ):
        title = str(
            agenda_item.get(
                "title",
                "",
            )
        ).strip()

        title_lower = title.casefold()

        if not (
            "tax-exempt loan" in title_lower
            and "development authority" in title_lower
            and "benefit of" in title_lower
        ):
            continue

        anchors = _conduit_anchor_tokens(
            title
        )

        if len(anchors) < 2:
            continue

        source_block = _agenda_item_source_block(
            agenda,
            agenda_item.get(
                "item_number",
                "",
            ),
        )

        contexts.append(
            {
                "item_number": str(
                    agenda_item.get(
                        "item_number",
                        "",
                    )
                    or ""
                ),
                "title": title,
                "anchors": anchors,
                "source_block": source_block,
                "city_obligation": bool(
                    _CITY_FINANCIAL_OBLIGATION_RE.search(
                        source_block
                    )
                ),
            }
        )

    return contexts


def _conduit_text_matches_context(
    value,
    context,
):
    return len(
        _conduit_anchor_tokens(
            value
        )
        & set(
            context.get(
                "anchors",
                set(),
            )
        )
    ) >= 2


def _guard_conduit_financing_coverage_plan(
    plan,
    agenda,
):
    """
    Prevent one specific third-party conduit-financing item from
    being characterized as City debt without changing unrelated
    City borrowing/debt items elsewhere in the meeting.
    """
    contexts = _conduit_financing_agenda_contexts(
        agenda
    )

    if not contexts:
        return False

    replacements = (
        (
            re.compile(
                r"\bpublic\s+debt\s+issuance"
                r"(?:\s+within\s+the\s+city)?\b",
                re.I,
            ),
            "third-party tax-exempt financing",
        ),
        (
            re.compile(
                r"\bmunicipal\s+debt(?:\s+issuance)?\b",
                re.I,
            ),
            "third-party tax-exempt financing",
        ),
        (
            re.compile(
                r"\bcity\s+debt(?:\s+issuance)?\b",
                re.I,
            ),
            "the financing",
        ),
        (
            re.compile(
                r"\bcity\s+borrowing\b",
                re.I,
            ),
            "the financing",
        ),
        (
            re.compile(
                r"\bdebt\s+issued\s+by\s+the\s+city\b",
                re.I,
            ),
            "the financing approved by the council",
        ),
    )

    changed = False

    for item in plan.items:
        item_context = " ".join(
            [
                str(item.topic or ""),
                str(item.summary or ""),
                str(item.why_it_matters or ""),
            ]
        )

        matching_context = next(
            (
                context
                for context in contexts
                if _conduit_text_matches_context(
                    item_context,
                    context,
                )
            ),
            None,
        )

        if (
            not matching_context
            or matching_context.get(
                "city_obligation"
            )
        ):
            continue

        for field in (
            "topic",
            "summary",
            "why_it_matters",
        ):
            value = str(
                getattr(
                    item,
                    field,
                    "",
                )
                or ""
            )
            cleaned = value

            for pattern, replacement in replacements:
                cleaned = pattern.sub(
                    replacement,
                    cleaned,
                )

            if (
                field == "topic"
                and cleaned
            ):
                cleaned = (
                    cleaned[0].upper()
                    + cleaned[1:]
                )

            if cleaned != value:
                setattr(
                    item,
                    field,
                    cleaned,
                )
                changed = True

    return changed
'''

mi = mi[:start] + new_conduit_helpers + mi[end:]
mi_path.write_text(mi, encoding="utf-8")


# ------------------------------------------------------------
# process_city.py: cover the rest of formal-action vocabulary,
# and scope story-copy conduit checks to the matched agenda item.
# ------------------------------------------------------------
pc_path = Path("process_city.py")
pc = pc_path.read_text(encoding="utf-8")

pc = replace_once(
    pc,
    '''    make_comprehensive_source_notes,
    retry_api_call,
)
''',
    '''    make_comprehensive_source_notes,
    retry_api_call,
    _conduit_financing_agenda_contexts,
    _conduit_text_matches_context,
)
''',
    "conduit scope helper imports",
)

pc = replace_once(
    pc,
    '''        r"award|awarded|"
        r"appoint|appointment|appointed|"
        r"deny|denial|denied|"
        r"reject|rejection|rejected"
''',
    '''        r"award|awarded|"
        r"appoint|appointment|appointed|"
        r"direct|direction|directed|"
        r"accept|acceptance|accepted|"
        r"pass|passed|"
        r"deny|denial|denied|"
        r"reject|rejection|rejected"
''',
    "formal intent vocabulary",
)

story_start = pc.index("def unsupported_conduit_financing_story_issues(")
story_end = pc.index("\n\ndef strip_public_agenda_item_numbers(", story_start)

new_story_guard = r'''def unsupported_conduit_financing_story_issues(
    story,
    agenda,
):
    """
    Fail closed when publishable copy assigns a matched third-party
    conduit-financing item to City debt/borrowing without official
    support, while leaving unrelated City debt items alone.
    """
    contexts = [
        context
        for context in _conduit_financing_agenda_contexts(
            agenda
        )
        if not context.get(
            "city_obligation"
        )
    ]

    if not contexts:
        return []

    unsupported = re.compile(
        r"\b(?:"
        r"city\s+debt(?:\s+issuance)?|"
        r"city\s+borrowing|"
        r"municipal\s+debt(?:\s+issuance)?|"
        r"public\s+debt\s+issuance\s+within\s+the\s+city|"
        r"public\s+debt\s+(?:issued|issuance)\s+by\s+the\s+city|"
        r"debt\s+issued\s+by\s+the\s+city"
        r")\b",
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

            scoped_text = " ".join(
                [
                    str(story.headline or ""),
                    str(story.dek or ""),
                    value,
                ]
            )

            matching_context = next(
                (
                    context
                    for context in contexts
                    if _conduit_text_matches_context(
                        scoped_text,
                        context,
                    )
                ),
                None,
            )

            if not matching_context:
                continue

            key = (
                field,
                value,
            )

            if key in seen:
                continue

            seen.add(key)

            item_number = matching_context.get(
                "item_number",
                "",
            )

            issue_label = (
                f"Official agenda item {item_number}"
                if item_number
                else "The matched official agenda item"
            )

            issues.append(
                AuditIssue(
                    severity="material",
                    field=field,
                    draft_text=value,
                    source_evidence=(
                        f"{issue_label} describes a tax-exempt "
                        "loan issued by a separate development/"
                        "financing authority for another "
                        "beneficiary and does not establish the "
                        "City as borrower, obligor, debtor, "
                        "guarantor, or financially liable."
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

pc = pc[:story_start] + new_story_guard + pc[story_end:]
pc_path.write_text(pc, encoding="utf-8")


# ------------------------------------------------------------
# Regression tests for all three review findings.
# ------------------------------------------------------------
test_path = Path("tests/test_rsm_sept9_regressions.py")
tests = test_path.read_text(encoding="utf-8")

# Make the existing model-wiring and explicit-obligation cases
# identifiable as the RSM conduit item under the new scoped guard.
tests = tests.replace(
    'topic="Tax-Exempt Loan",\n                score=9,',
    'topic="Tax-Exempt Loan for St. Junipero Serra Catholic School",\n                score=9,',
)

tests = tests.replace(
    'headline="Council Considers School Financing",\n        dek="The public hearing concerned a tax-exempt loan.",',
    'headline="Council Considers St. Junipero Serra Catholic School Financing",\n        dek="The public hearing concerned a tax-exempt loan.",',
)

# Append focused review regressions.
tests += r'''


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
'''

test_path.write_text(tests, encoding="utf-8")

print("PR 17 review findings applied.")
