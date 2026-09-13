from pathlib import Path

PROCESS = Path("process_city.py")
text = PROCESS.read_text(encoding="utf-8")

insert_anchor = "\ndef restore_required_topics_from_key_facts(\n    story,\n    intelligence,\n):\n"
assert text.count(insert_anchor) == 1, "restore helper anchor changed"

helper = r'''

def missing_required_topic_issues(
    story,
    intelligence,
):
    """
    Fail closed when a coverage-plan MUST INCLUDE topic is still
    absent from the finished article body.

    Topic matching is paragraph-local so words scattered across
    unrelated paragraphs cannot accidentally satisfy the gate.
    This is a deterministic editorial-completeness check; it does
    not turn coverage-plan prose into factual source evidence.
    """

    stopwords = {
        "and",
        "the",
        "for",
        "with",
        "from",
        "into",
        "city",
        "council",
        "annual",
        "program",
        "update",
        "concerns",
        "concern",
        "amendment",
        "amendments",
        "project",
    }

    def words(value):
        return {
            word
            for word in re.findall(
                r"[a-z0-9]+",
                str(value or "").lower(),
            )
            if (
                len(word) >= 4
                and word not in stopwords
            )
        }

    paragraphs = [
        str(paragraph or "")
        for paragraph in story.body
        if str(paragraph or "").strip()
    ]

    issues = []

    for item in intelligence.get(
        "coverage_items",
        [],
    ):
        if not item.get("must_include"):
            continue

        topic = str(
            item.get(
                "topic",
                "",
            )
        ).strip()

        topic_words = words(topic)

        # Very short/generic topics are too ambiguous for a
        # deterministic lexical completeness gate.
        if len(topic_words) < 2:
            continue

        if any(
            len(
                topic_words
                & words(paragraph)
            ) >= 2
            for paragraph in paragraphs
        ):
            continue

        best_action = None
        best_overlap = 0

        for action in intelligence.get(
            "action_ledger",
            [],
        ):
            if action.get("validated") is not True:
                continue

            action_words = words(
                str(
                    action.get(
                        "topic",
                        "",
                    )
                )
                + " "
                + str(
                    action.get(
                        "agenda_title",
                        "",
                    )
                )
            )

            overlap = len(
                topic_words
                & action_words
            )

            if overlap > best_overlap:
                best_overlap = overlap
                best_action = action

        if best_action and best_overlap >= 2:
            item_number = str(
                best_action.get(
                    "item_number",
                    "",
                )
            ).strip()
            status = str(
                best_action.get(
                    "action_status",
                    "",
                )
            ).strip()
            evidence_quote = str(
                best_action.get(
                    "evidence_quote",
                    "",
                )
            ).strip()

            source_evidence = (
                "CouncilWatch marked this topic MUST INCLUDE and "
                "the validated action ledger"
                + (
                    f" for agenda item {item_number}"
                    if item_number
                    else ""
                )
                + (
                    f" records the action as {status}."
                    if status
                    else "."
                )
            )

            if evidence_quote:
                source_evidence += (
                    " Validated evidence: "
                    + evidence_quote
                )
        else:
            source_evidence = (
                "CouncilWatch marked this coverage topic MUST INCLUDE, "
                "but no finished body paragraph contains enough "
                "topic-specific anchors to show that it was covered."
            )

        issues.append(
            AuditIssue(
                severity="material",
                field="body",
                draft_text=topic,
                source_evidence=source_evidence,
                correction=(
                    "Add a source-supported paragraph covering this "
                    "MUST INCLUDE topic, then audit the exact saved copy "
                    "again. Do not mark the draft audit-clean while the "
                    "required topic is absent."
                ),
            )
        )

    return issues
'''

text = text.replace(insert_anchor, helper + insert_anchor, 1)

formal_anchor = '''                elif action_status == "considered":
                    if agenda_section == "PUBLIC HEARINGS":
                        restore_text = (
                            "During a public hearing, "
                            "the Council considered "
                            + readable_topic
                            + "."
                        )

                    elif agenda_section == "NEW BUSINESS":
                        restore_text = (
                            "During new business, "
                            "the Council considered "
                            + readable_topic
                            + "."
                        )

                    else:
                        restore_text = (
                            "The Council considered "
                            + readable_topic
                            + "."
                        )

                if restore_text:
                    break
'''

formal_replacement = '''                elif action_status == "considered":
                    if agenda_section == "PUBLIC HEARINGS":
                        restore_text = (
                            "During a public hearing, "
                            "the Council considered "
                            + readable_topic
                            + "."
                        )

                    elif agenda_section == "NEW BUSINESS":
                        restore_text = (
                            "During new business, "
                            "the Council considered "
                            + readable_topic
                            + "."
                        )

                    else:
                        restore_text = (
                            "The Council considered "
                            + readable_topic
                            + "."
                        )

                elif action_status in ACTION_FORMAL_STATUSES:
                    formal_verbs = {
                        "approved": "approved",
                        "adopted": "adopted",
                        "authorized": "authorized",
                        "awarded": "awarded",
                        "directed": "directed",
                        "rejected": "rejected",
                        "denied": "denied",
                        "appointed": "appointed",
                        "accepted": "accepted",
                        "passed": "passed",
                    }

                    verb = formal_verbs.get(
                        action_status,
                        action_status,
                    )

                    restore_text = (
                        "The Council "
                        + verb
                        + " "
                        + readable_topic
                        + "."
                    )

                if restore_text:
                    break
'''

assert text.count(formal_anchor) == 1, "formal restore anchor changed"
text = text.replace(formal_anchor, formal_replacement, 1)

final_anchor = '''        deterministic_action_issues.extend(
            unsupported_conduit_financing_story_issues(
                story,
                agenda,
            )
        )

        if deterministic_action_issues:
'''

final_replacement = '''        deterministic_action_issues.extend(
            unsupported_conduit_financing_story_issues(
                story,
                agenda,
            )
        )

        deterministic_action_issues.extend(
            missing_required_topic_issues(
                story,
                intelligence,
            )
        )

        if deterministic_action_issues:
'''

assert text.count(final_anchor) == 1, "final deterministic gate anchor changed"
text = text.replace(final_anchor, final_replacement, 1)

PROCESS.write_text(text, encoding="utf-8")

TEST = Path("tests/test_must_include_enforcement.py")
TEST.write_text(r'''from gemini_worker import StoryDraft
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
''', encoding="utf-8")

# Remove the temporary patch machinery from the branch's net diff.
Path(".github/scripts/apply_must_include_fix.py").unlink(missing_ok=True)
Path(".github/workflows/apply-must-include-fix.yml").unlink(missing_ok=True)

print("Applied MUST INCLUDE final-enforcement patch.")
