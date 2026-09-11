#!/usr/bin/env python3

import argparse
import json
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path

from agenda import agenda_text
from gemini_worker import (
    AuditIssue,
    make_source_notes,
    make_story,
    audit_story,
)
from source_acquisition import acquire_source
from meetings import latest_ready_meetings
from meeting_intelligence import (
    ACTION_FORMAL_STATUSES,
    build_meeting_intelligence,
    writer_context,
    audit_verification_context,
    make_rich_story,
    make_comprehensive_source_notes,
    retry_api_call,
)
from notifications import notify_ready_for_review
from settings import (
    DRAFTS,
    WORK,
    STATUS_FILE,
    TRANSCRIPT_MODEL,
    STORY_MODEL,
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load_status():
    try:
        return json.loads(STATUS_FILE.read_text())
    except Exception:
        return {"cities": {}}


def write_status(status):
    status["updated_at"] = utc_now()
    STATUS_FILE.write_text(
        json.dumps(
            status,
            indent=2,
        )
    )


def update_status(
    status,
    meeting,
    phase,
    message,
    draft=None,
):
    slug = meeting["city_slug"]

    payload = {
        "city": meeting["city_name"],
        "phase": phase,
        "meeting_date": meeting.get("meeting_date"),
        "external_id": str(meeting.get("external_id", "")),
        "message": message,
    }

    if draft:
        payload["draft"] = draft

    status.setdefault("cities", {})
    status["cities"][slug] = payload

    write_status(status)


def unresolved_high_priority_formal_action_issues(
    story,
    intelligence,
):
    """
    Fail closed when a high-priority formal-action agenda item has
    no verified final disposition.

    An agenda recommendation to approve/adopt/authorize is not
    evidence that the council actually did so. But a lead or other
    high-priority MUST INCLUDE item may not pass final audit while
    its validated ledger remains only discussed/considered/unclear.
    """
    coverage_items = [
        item
        for item in intelligence.get(
            "coverage_items",
            [],
        )
        if (
            item.get("must_include")
            and (
                int(item.get("rank") or 999) == 1
                or int(item.get("score") or 0) >= 8
            )
        )
    ]

    if not coverage_items:
        return []

    formal_intent = re.compile(
        r"\b(?:"
        r"approval|approve|approved|"
        r"adopt|adoption|adopted|"
        r"authorize|authorization|authorized|"
        r"award|awarded|"
        r"appoint|appointment|appointed|"
        r"deny|denial|denied|"
        r"reject|rejection|rejected"
        r")\b",
        re.I,
    )

    unresolved_statuses = {
        "unclear",
        "unknown",
        "discussed",
        "considered",
        "reviewed",
        "no council action",
        "no action",
    }

    stopwords = {
        "agenda",
        "approval",
        "approve",
        "approved",
        "city",
        "council",
        "item",
        "public",
        "resolution",
        "the",
        "with",
        "from",
        "for",
    }

    def tokens(value):
        return {
            token
            for token in re.findall(
                r"[a-z0-9]+",
                str(value or "").casefold(),
            )
            if len(token) >= 4
            and token not in stopwords
        }

    ledger = intelligence.get(
        "action_ledger",
        [],
    )

    issues = []

    for coverage in coverage_items:
        coverage_topic = str(
            coverage.get(
                "topic",
                "",
            )
        ).strip()
        coverage_tokens = tokens(
            coverage_topic
        )

        if len(coverage_tokens) < 2:
            continue

        candidates = []

        for action in ledger:
            action_status = str(
                action.get(
                    "action_status",
                    "",
                )
            ).strip().lower()

            if action_status in ACTION_FORMAL_STATUSES:
                continue

            if action_status not in unresolved_statuses:
                continue

            section = str(
                action.get(
                    "agenda_section",
                    "",
                )
            ).strip().upper()

            if section == "CONSENT CALENDAR":
                continue

            agenda_title = str(
                action.get(
                    "agenda_title",
                    "",
                )
            ).strip()

            if not formal_intent.search(
                agenda_title
            ):
                continue

            action_tokens = tokens(
                str(
                    action.get(
                        "topic",
                        "",
                    )
                )
                + " "
                + agenda_title
            )
            overlap = len(
                coverage_tokens
                & action_tokens
            )

            if overlap < 2:
                continue

            candidates.append(
                (overlap, action)
            )

        if not candidates:
            continue

        candidates.sort(
            key=lambda entry: entry[0],
            reverse=True,
        )
        action = candidates[0][1]

        item_number = str(
            action.get(
                "item_number",
                "",
            )
        ).strip()
        agenda_title = str(
            action.get(
                "agenda_title",
                "",
            )
        ).strip()
        action_status = str(
            action.get(
                "action_status",
                "unclear",
            )
        ).strip().lower()

        field = "headline"
        draft_text = str(
            story.headline or ""
        ).strip()

        if not draft_text:
            field = "dek"
            draft_text = str(
                story.dek or ""
            ).strip()

        if not draft_text and story.body:
            field = "body"
            draft_text = str(
                story.body[0] or ""
            ).strip()

        if not draft_text:
            continue

        item_label = (
            f"Agenda item {item_number}"
            if item_number
            else "The official agenda item"
        )

        issues.append(
            AuditIssue(
                severity="material",
                field=field,
                draft_text=draft_text,
                source_evidence=(
                    f"{item_label} calls for formal action "
                    f"({agenda_title}), but CouncilWatch's "
                    "source-validated action ledger only "
                    f"establishes '{action_status}'. The final "
                    "council disposition is unresolved."
                ),
                correction=(
                    "Verify the same item's motion/vote from the "
                    "recording or another official result source. "
                    "Do not infer approval or adoption from the "
                    "agenda recommendation alone."
                ),
            )
        )

    return issues


def strip_public_agenda_item_numbers(story):
    """
    Agenda item numbers are useful internally but are not needed
    in reader-facing CouncilWatch copy.

    Recording-derived notes can contain mistaken item-number
    associations. Rather than asking language models to reconcile
    those numbers repeatedly, remove agenda-number references
    deterministically from publishable article fields.

    The source-validated action ledger retains official item
    numbers for internal verification and review.
    """

    changed = False

    parenthetical = re.compile(
        r"""
        \s*
        \(
        \s*
        agenda\s+items?
        \s+
        \d+(?:\.\d+)*
        (?:
            \s*
            (?:,|and|&|/|-)
            \s*
            \d+(?:\.\d+)*
        )*
        \s*
        \)
        """,
        re.I | re.X,
    )

    standalone = re.compile(
        r"""
        \bagenda\s+items?
        \s+
        \d+(?:\.\d+)*
        (?:
            \s*
            (?:,|and|&|/|-)
            \s*
            \d+(?:\.\d+)*
        )*
        \b
        """,
        re.I | re.X,
    )

    bare_parenthetical = re.compile(
        r"""
        \s*
        \(
        \s*
        items?
        \s+
        \d+(?:\.\d+)*
        (?:
            \s*
            (?:,|and|&|/|-)
            \s*
            \d+(?:\.\d+)*
        )*
        \s*
        \)
        """,
        re.I | re.X,
    )

    bare_item = re.compile(
        r"""
        \bitems?
        \s+
        \d+(?:\.\d+)*
        (?:
            \s*
            (?:,|and|&|/|-)
            \s*
            \d+(?:\.\d+)*
        )*
        \b
        """,
        re.I | re.X,
    )

    def scrub(value):
        value = str(value or "")

        cleaned = parenthetical.sub(
            "",
            value,
        )

        cleaned = standalone.sub(
            "",
            cleaned,
        )

        cleaned = bare_parenthetical.sub(
            "",
            cleaned,
        )

        cleaned = bare_item.sub(
            "",
            cleaned,
        )

        cleaned = re.sub(
            r"\s+([,.;:])",
            r"\1",
            cleaned,
        )

        cleaned = re.sub(
            r"[ \t]{2,}",
            " ",
            cleaned,
        )

        return cleaned.strip()

    new_headline = scrub(
        story.headline
    )

    if new_headline != story.headline:
        story.headline = new_headline
        changed = True

    new_dek = scrub(
        story.dek
    )

    if new_dek != story.dek:
        story.dek = new_dek
        changed = True

    new_body = [
        scrub(paragraph)
        for paragraph in story.body
    ]

    if new_body != story.body:
        story.body = new_body
        changed = True

    new_key_facts = [
        scrub(fact)
        for fact in story.key_facts
    ]

    if new_key_facts != story.key_facts:
        story.key_facts = new_key_facts
        changed = True

    return changed


def enforce_public_action_constraints(
    story,
    intelligence,
):
    """
    Prevent public copy from attaching an officially
    non-consent topic to the Consent Calendar.

    Official agenda section placement comes from the
    source-validated action ledger.

    This is deterministic and intentionally narrow.
    """

    changed = False

    non_consent_topics = []

    for action in intelligence.get(
        "action_ledger",
        [],
    ):
        section = str(
            action.get(
                "agenda_section",
                "",
            )
        ).strip().upper()

        topic = str(
            action.get(
                "topic",
                "",
            )
        ).strip()

        if (
            section
            and section != "CONSENT CALENDAR"
            and topic
        ):
            non_consent_topics.append(
                topic
            )

    def words(value):
        return {
            word
            for word in re.findall(
                r"[a-z0-9]+",
                str(value or "").lower(),
            )
            if len(word) >= 4
            and word not in {
                "with",
                "from",
                "that",
                "this",
                "city",
                "council",
                "calendar",
                "consent",
                "item",
                "items",
            }
        }

    topic_word_sets = [
        words(topic)
        for topic in non_consent_topics
    ]

    def links_non_consent_to_consent(
        value,
    ):
        low = str(
            value or ""
        ).lower()

        if "consent calendar" not in low:
            return False

        value_words = words(value)

        for topic_words in topic_word_sets:
            if (
                len(
                    value_words
                    & topic_words
                )
                >= 2
            ):
                return True

        return False

    def scrub(value):
        value = str(value or "")

        if not links_non_consent_to_consent(
            value
        ):
            return value

        cleaned = value

        # Example:
        # "Consent Calendar, which included zoning...,"
        cleaned = re.sub(
            r",\s*which\s+included\b[^.]*",
            "",
            cleaned,
            flags=re.I,
        )

        # Example:
        # "Consent Calendar, including zoning..., was approved"
        cleaned = re.sub(
            r",\s*including\b[^,.;]*,\s*",
            " ",
            cleaned,
            flags=re.I,
        )

        cleaned = re.sub(
            r"\s+([,.;:])",
            r"\1",
            cleaned,
        )

        cleaned = re.sub(
            r"[ \t]{2,}",
            " ",
            cleaned,
        )

        cleaned = cleaned.strip()

        # If the semantic contamination still survives,
        # fail closed instead of publishing the relationship.
        if links_non_consent_to_consent(
            cleaned
        ):
            return ""

        return cleaned

    new_headline = scrub(
        story.headline
    )

    if new_headline != story.headline:
        # An empty result is intentional fail-closed behavior.
        # The subsequent audit must rebuild a supported headline.
        story.headline = new_headline
        changed = True

    new_dek = scrub(
        story.dek
    )

    if new_dek != story.dek:
        # As with the headline, blank is safer than retaining a
        # relationship the deterministic guard rejected.
        story.dek = new_dek
        changed = True

    new_body = []

    for paragraph in story.body:
        cleaned = scrub(
            paragraph
        )

        if cleaned:
            new_body.append(
                cleaned
            )

    if new_body != story.body:
        story.body = new_body
        changed = True

    new_key_facts = []

    for fact in story.key_facts:
        cleaned = scrub(
            fact
        )

        if cleaned:
            new_key_facts.append(
                cleaned
            )

    if new_key_facts != story.key_facts:
        story.key_facts = new_key_facts
        changed = True

    return changed


def normalize_validated_formal_status_language(
    story,
    intelligence,
):
    """
    Keep reader-facing formal council-action verbs aligned with
    the source-validated action ledger.

    Example:

      ledger: APPROVED geotechnical contracts

    Public copy must not silently strengthen or mutate that into:

      authorized geotechnical services
      awarded geotechnical agreements

    A rewrite occurs only when an attributable sentence or clause
    has at least two meaningful topical cues for validated formal
    action records and all matching records agree on the permitted
    status.

    If different independently validated formal actions occur in
    separate comma/semicolon clauses, normalize each attributable
    clause independently.

    If different validated formal actions remain mixed inside the
    same clause, leave that clause alone rather than guessing which
    verb belongs to which action.
    """

    actions = [
        action
        for action in intelligence.get(
            "action_ledger",
            [],
        )
        if (
            action.get(
                "validated"
            )
            is True
            and str(
                action.get(
                    "action_status",
                    "",
                )
            ).strip().lower()
            in ACTION_FORMAL_STATUSES
        )
    ]

    if not actions:
        return False

    # --------------------------------------------------------
    # Formal verb forms.
    #
    # The keys are the canonical ledger statuses.
    # --------------------------------------------------------

    forms = {
        "approved": {
            "approve",
            "approves",
            "approved",
            "approving",
        },
        "adopted": {
            "adopt",
            "adopts",
            "adopted",
            "adopting",
        },
        "authorized": {
            "authorize",
            "authorizes",
            "authorized",
            "authorizing",
        },
        "awarded": {
            "award",
            "awards",
            "awarded",
            "awarding",
        },
        "directed": {
            "direct",
            "directs",
            "directed",
            "directing",
        },
        "rejected": {
            "reject",
            "rejects",
            "rejected",
            "rejecting",
        },
        "denied": {
            "deny",
            "denies",
            "denied",
            "denying",
        },
        "appointed": {
            "appoint",
            "appoints",
            "appointed",
            "appointing",
        },
        "accepted": {
            "accept",
            "accepts",
            "accepted",
            "accepting",
        },
        "passed": {
            "pass",
            "passes",
            "passed",
            "passing",
        },
    }

    word_to_status = {
        word: status
        for status, words in forms.items()
        for word in words
    }

    # Replacement surface form should preserve the broad tense
    # of the writer's original verb.

    present = {
        "approved": "approves",
        "adopted": "adopts",
        "authorized": "authorizes",
        "awarded": "awards",
        "directed": "directs",
        "rejected": "rejects",
        "denied": "denies",
        "appointed": "appoints",
        "accepted": "accepts",
        "passed": "passes",
    }

    infinitive = {
        "approved": "approve",
        "adopted": "adopt",
        "authorized": "authorize",
        "awarded": "award",
        "directed": "direct",
        "rejected": "reject",
        "denied": "deny",
        "appointed": "appoint",
        "accepted": "accept",
        "passed": "pass",
    }

    progressive = {
        "approved": "approving",
        "adopted": "adopting",
        "authorized": "authorizing",
        "awarded": "awarding",
        "directed": "directing",
        "rejected": "rejecting",
        "denied": "denying",
        "appointed": "appointing",
        "accepted": "accepting",
        "passed": "passing",
    }

    past = {
        status: status
        for status in ACTION_FORMAL_STATUSES
    }

    all_action_words = set(
        word_to_status
    )

    stopwords = {
        "agenda",
        "annual",
        "city",
        "council",
        "councilmember",
        "item",
        "items",
        "meeting",
        "motion",
        "public",
        "staff",
        "vote",
        "voted",

        # Formal verbs themselves are action strength, not topic
        # evidence.
        *all_action_words,
    }

    def tokens(value):
        return {
            word
            for word in re.findall(
                r"[a-z0-9]+",
                str(
                    value or ""
                ).lower(),
            )
            if (
                len(word) >= 4
                and word not in stopwords
            )
        }

    indexed = []

    for action in actions:
        cues = tokens(
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

        if len(cues) < 2:
            continue

        indexed.append(
            (
                action,
                cues,
            )
        )

    if not indexed:
        return False

    # Count how many validated formal actions use each topical
    # cue. A word shared by several actions is useful context but
    # cannot, by itself, identify which action owns a sentence.
    #
    # This is especially important for boilerplate official-title
    # words such as a municipality name. For example, two Lake
    # Forest agenda titles may both contain "Lake Forest"; those
    # two words must not cause an unrelated action to claim copy
    # merely because the city name appears in the sentence.
    cue_frequency = {}

    for _, cues in indexed:
        for cue in cues:
            cue_frequency[cue] = (
                cue_frequency.get(
                    cue,
                    0,
                )
                + 1
            )

    verb_pattern = re.compile(
        r"\b("
        + "|".join(
            sorted(
                (
                    re.escape(word)
                    for word
                    in all_action_words
                ),
                key=len,
                reverse=True,
            )
        )
        + r")\b",
        re.I,
    )

    def replacement_word(
        original,
        canonical_status,
    ):
        low = original.lower()

        if low.endswith(
            "ing"
        ):
            replacement = progressive[
                canonical_status
            ]

        elif low in {
            "approves",
            "adopts",
            "authorizes",
            "awards",
            "directs",
            "rejects",
            "denies",
            "appoints",
            "accepts",
            "passes",
        }:
            replacement = present[
                canonical_status
            ]

        elif low in {
            "approve",
            "adopt",
            "authorize",
            "award",
            "direct",
            "reject",
            "deny",
            "appoint",
            "accept",
            "pass",
        }:
            replacement = infinitive[
                canonical_status
            ]

        else:
            replacement = past[
                canonical_status
            ]

        if (
            original
            and original[0].isupper()
        ):
            replacement = (
                replacement[0].upper()
                + replacement[1:]
            )

        return replacement

    def matching_actions(
        value,
    ):
        """
        Return validated formal actions with at least two topical
        cues in this exact piece of copy.
        """

        value_tokens = tokens(
            value
        )

        matched = []

        for action, cues in indexed:
            overlap_cues = (
                value_tokens
                & cues
            )

            overlap = len(
                overlap_cues
            )

            if overlap < 2:
                continue

            # When several validated formal actions exist, at
            # least one matching topical cue must discriminate
            # this action from the others.
            #
            # Shared context such as a city name, "agreement",
            # or another word appearing in multiple action
            # identities may contribute to the >=2 overlap but
            # cannot be the sole basis for attribution.
            if (
                len(indexed) > 1
                and not any(
                    cue_frequency.get(
                        cue,
                        0,
                    )
                    == 1
                    for cue in overlap_cues
                )
            ):
                continue

            matched.append(
                (
                    action,
                    overlap,
                )
            )

        return matched


    def normalize_segment(
        value,
    ):
        """
        Normalize one independently attributable clause.

        If multiple validated formal statuses still match this
        exact clause, fail closed and leave it unchanged.
        """

        value = str(
            value or ""
        )

        matched = matching_actions(
            value
        )

        if not matched:
            return value

        statuses = {
            str(
                action.get(
                    "action_status",
                    "",
                )
            ).strip().lower()
            for action, _ in matched
        }

        # Multiple validated action types remain inside this
        # exact clause. Do not guess which verb belongs to which
        # action.
        if len(
            statuses
        ) != 1:
            return value

        canonical_status = next(
            iter(
                statuses
            )
        )

        def adopted_instrument_supports_embedded_status(
            observed_status,
        ):
            """
            An adopted ordinance/resolution can itself carry a
            separately named formal effect.

            Example:

              ADOPTION OF RESOLUTION ... PROVIDING FOR THE
              APPOINTMENT ...

            If that validated instrument was adopted, reader copy
            may correctly say both:

              Council adopted the resolution.
              Council appointed the named officeholders.

            Do not rewrite the embedded APPOINTED effect into the
            syntactically wrong "adopted the candidates."

            This exception is intentionally narrow:
              - the canonical ledger status must be ADOPTED
              - the observed embedded status must be APPOINTED
              - the same matched action's official agenda identity
                must explicitly contain appointment language

            Rejected, denied, proposed or unrelated appointment
            items receive no exception.
            """
            if (
                canonical_status
                != "adopted"
            ):
                return False

            if (
                observed_status
                != "appointed"
            ):
                return False

            for action, _ in matched:
                action_status = str(
                    action.get(
                        "action_status",
                        "",
                    )
                ).strip().lower()

                if (
                    action_status
                    != "adopted"
                ):
                    continue

                identity = (
                    str(
                        action.get(
                            "agenda_title",
                            "",
                        )
                    )
                    + " "
                    + str(
                        action.get(
                            "topic",
                            "",
                        )
                    )
                )

                if re.search(
                    r"\bappoint(?:ed|ing|ment|ments)?\b",
                    identity,
                    re.I,
                ):
                    return True

            return False


        def replace(
            match
        ):
            original = match.group(
                0
            )

            observed_status = (
                word_to_status.get(
                    original.lower()
                )
            )

            if (
                not observed_status
                or observed_status
                == canonical_status
            ):
                return original

            if adopted_instrument_supports_embedded_status(
                observed_status
            ):
                return original

            return replacement_word(
                original,
                canonical_status,
            )

        cleaned = verb_pattern.sub(
            replace,
            value,
        )

        # ----------------------------------------------------
        # Grammar cleanup for common contract language.
        # ----------------------------------------------------

        if canonical_status == "approved":
            cleaned = re.sub(
                r"\b("
                r"approve|approves|approved|approving"
                r")"
                r"(\s+(?:(?:an?|the)\s+)?"
                r"(?:agreements?|contracts?))"
                r"\s+to\b",
                r"\1\2 with",
                cleaned,
                flags=re.I,
            )

        if canonical_status == "awarded":
            cleaned = re.sub(
                r"\b("
                r"award|awards|awarded|awarding"
                r")"
                r"(\s+(?:(?:an?|the)\s+)?"
                r"(?:agreements?|contracts?))"
                r"\s+with\b",
                r"\1\2 to",
                cleaned,
                flags=re.I,
            )

        return cleaned


    # --------------------------------------------------------
    # SAFE CLAUSE BOUNDARIES
    #
    # Split only before another explicit FORMAL action verb
    # following a comma or semicolon.
    #
    # This handles:
    #
    #   approved staff ... digital signage,
    #   approved a conditional use permit ...
    #
    # without splitting ordinary commas in company names,
    # monetary values, addresses, or descriptive lists.
    # --------------------------------------------------------

    clause_action_words = (
        "|".join(
            sorted(
                (
                    re.escape(
                        word
                    )
                    for word
                    in all_action_words
                ),
                key=len,
                reverse=True,
            )
        )
    )


    clause_split_pattern = re.compile(
        r"("
        r"(?:,\s+|;\s+)"
        r"(?="
        r"(?:and\s+|but\s+)?"
        r"(?:(?:the\s+)?(?:city\s+)?council\s+)?"
        r"(?:"
        + clause_action_words
        + r")\b"
        r")"
        r"(?:and\s+|but\s+)?"
        r")",
        re.I,
    )


    def normalize_sentence(
        value,
    ):
        value = str(
            value or ""
        )

        matched = matching_actions(
            value
        )

        if not matched:
            return value

        statuses = {
            str(
                action.get(
                    "action_status",
                    "",
                )
            ).strip().lower()
            for action, _ in matched
        }

        # Fast path: one validated formal status owns the entire
        # sentence.
        if len(
            statuses
        ) == 1:
            return normalize_segment(
                value
            )

        # More than one validated formal status appears in the
        # sentence. Try only conservative clause boundaries.
        parts = clause_split_pattern.split(
            value
        )

        if len(
            parts
        ) == 1:
            # Still genuinely ambiguous.
            return value

        for i in range(
            0,
            len(
                parts
            ),
            2,
        ):
            parts[i] = (
                normalize_segment(
                    parts[i]
                )
            )

        return "".join(
            parts
        )


    def normalize_value(value):
        value = str(
            value or ""
        )

        # Work sentence by sentence so one action in a paragraph
        # cannot rewrite an unrelated formal verb elsewhere.
        parts = re.split(
            r"(?<=[.!?])(\s+)",
            value,
        )

        for i in range(
            0,
            len(parts),
            2,
        ):
            parts[i] = (
                normalize_sentence(
                    parts[i]
                )
            )

        return "".join(
            parts
        )

    changed = False

    new_headline = normalize_value(
        story.headline
    )

    if new_headline != story.headline:
        story.headline = new_headline
        changed = True

    new_dek = normalize_value(
        story.dek
    )

    if new_dek != story.dek:
        story.dek = new_dek
        changed = True

    new_body = [
        normalize_value(
            paragraph
        )
        for paragraph in story.body
    ]

    if new_body != story.body:
        story.body = new_body
        changed = True

    new_key_facts = [
        normalize_value(
            fact
        )
        for fact in story.key_facts
    ]

    if new_key_facts != story.key_facts:
        story.key_facts = new_key_facts
        changed = True

    return changed




def _remove_validated_unclear_formal_claims(
    story,
    intelligence,
):
    """
    Fail closed when a ledger record maps to a concrete agenda
    item but its formal disposition is UNCLEAR and reader-facing
    copy nevertheless claims a formal Council action.

    A ledger row may itself be unvalidated precisely because the
    claimed formal-action evidence failed source validation. Such
    a row must never authorize strong reader-facing action language.

    We therefore trust an UNCLEAR row for fail-closed removal when:

      * it is already validated; OR
      * it has a concrete item number + agenda section and no
        agenda-linkage conflict.

    Unmapped, unvalidated UNCLEAR rows remain too ambiguous to use
    for deterministic topic deletion.

    There is no evidence-safe replacement verb for UNCLEAR.

    Remove only the unsupported formal sentence/fact. Preserve
    unrelated supported sentences. For summary copy containing
    several formal clauses, remove only clauses linked to UNCLEAR
    actions when a safe clause boundary exists.

    A directly dependent follow-up sentence such as:

      "The Council waived formal bidding requirements for this
       contract."

    is also removed when the immediately preceding unsupported
    contract/action sentence is removed.
    """
    unclear_actions = [
        action
        for action in intelligence.get(
            "action_ledger",
            [],
        )
        if (
            str(
                action.get(
                    "action_status",
                    "",
                )
            ).strip().lower()
            == "unclear"
            and not bool(
                action.get(
                    "agenda_linkage_conflict"
                )
            )
            and (
                action.get(
                    "validated"
                )
                is True
                or (
                    bool(
                        str(
                            action.get(
                                "item_number",
                                "",
                            )
                        ).strip()
                    )
                    and bool(
                        str(
                            action.get(
                                "agenda_section",
                                "",
                            )
                        ).strip()
                    )
                )
            )
        )
    ]

    if not unclear_actions:
        return False

    formal_words = {
        "approve",
        "approves",
        "approved",
        "approving",

        "adopt",
        "adopts",
        "adopted",
        "adopting",

        "authorize",
        "authorizes",
        "authorized",
        "authorizing",

        "award",
        "awards",
        "awarded",
        "awarding",

        "direct",
        "directs",
        "directed",
        "directing",

        "reject",
        "rejects",
        "rejected",
        "rejecting",

        "deny",
        "denies",
        "denied",
        "denying",

        "appoint",
        "appoints",
        "appointed",
        "appointing",

        "accept",
        "accepts",
        "accepted",
        "accepting",

        "pass",
        "passes",
        "passed",
        "passing",

        "waive",
        "waives",
        "waived",
        "waiving",
    }

    formal_word_source = (
        "|".join(
            sorted(
                (
                    re.escape(
                        word
                    )
                    for word
                    in formal_words
                ),
                key=len,
                reverse=True,
            )
        )
    )

    formal_pattern = re.compile(
        r"\b(?:"
        + formal_word_source
        + r")\b",
        re.I,
    )

    stopwords = {
        "agenda",
        "annual",
        "calendar",
        "city",
        "consent",
        "council",
        "councilmember",
        "item",
        "items",
        "meeting",
        "motion",
        "municipal",
        "public",
        "staff",
        "vote",
        "voted",
        *formal_words,
    }

    def tokens(
        value,
    ):
        return {
            word
            for word in re.findall(
                r"[a-z0-9]+",
                str(
                    value or ""
                ).lower(),
            )
            if (
                len(
                    word
                ) >= 4
                and word
                not in stopwords
            )
        }

    # Normal token matching intentionally treats "agenda" as a
    # generic Council word. That is usually correct, but it loses
    # an important lexical relationship in phrases such as:
    #
    #   ledger: "Agenda Management Software"
    #   copy:   "agenda management subscription services"
    #
    # Keep the >=2 meaningful-token rule, and supplement it only
    # with exact adjacent two-word phrase anchors.
    phrase_stopwords = {
        *stopwords,
        "approval",
        "appointment",
    }

    # "agenda" is generic by itself, but meaningful as part of
    # an exact adjacent phrase such as "agenda management".
    phrase_stopwords.discard(
        "agenda"
    )

    def phrase_anchors(
        value,
    ):
        # Preserve ORIGINAL adjacency. Do not remove stopwords
        # before pairing, because that can manufacture a phrase
        # that never appeared in the source text.
        words = re.findall(
            r"[a-z0-9]+",
            str(
                value or ""
            ).lower(),
        )

        anchors = set()

        for index in range(
            len(words) - 1
        ):
            first = words[
                index
            ]

            second = words[
                index + 1
            ]

            if (
                len(first) < 4
                or len(second) < 4
                or first in phrase_stopwords
                or second in phrase_stopwords
            ):
                continue

            anchors.add(
                first
                + " "
                + second
            )

        return anchors

    unclear_signatures = []

    for action in unclear_actions:
        identity = (
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

        cues = tokens(
            identity
        )

        anchors = phrase_anchors(
            identity
        )

        if (
            len(cues) >= 2
            or anchors
        ):
            unclear_signatures.append(
                (
                    cues,
                    anchors,
                )
            )

    if not unclear_signatures:
        return False

    def unsupported_formal_claim(
        value,
    ):
        value = str(
            value or ""
        )

        if not formal_pattern.search(
            value
        ):
            return False

        value_tokens = tokens(
            value
        )

        value_phrase_anchors = (
            phrase_anchors(
                value
            )
        )

        return any(
            (
                len(
                    value_tokens
                    & cues
                )
                >= 2
            )
            or bool(
                value_phrase_anchors
                & anchors
            )
            for cues, anchors
            in unclear_signatures
        )

    abbreviation_pattern = re.compile(
        r"\b(?:"
        r"Inc|Corp|Co|Ltd|"
        r"No|"
        r"Mr|Mrs|Ms|Dr|"
        r"Jr|Sr"
        r")\.",
        re.I,
    )

    initialism_pattern = re.compile(
        r"\b(?:[A-Za-z]\.){2,}"
    )

    # A single middle initial must not become a sentence boundary.
    #
    # Example:
    #
    #   "Anne D. Figueroa"
    #
    # Without this protection the fail-closed action guard sees:
    #
    #   "The Council appointed ... Anne D."
    #   "Figueroa to serve as the voting delegate ..."
    #
    # which separates the formal verb from the topical evidence.
    single_middle_initial_pattern = re.compile(
        r"\b[A-Z]\.(?=\s+[A-Z][a-z])"
    )

    placeholder = "\uE000"

    def protect_abbreviation_periods(
        value,
    ):
        value = abbreviation_pattern.sub(
            lambda match:
                match.group(
                    0
                )[:-1]
                + placeholder,
            value,
        )

        value = initialism_pattern.sub(
            lambda match:
                match.group(
                    0
                ).replace(
                    ".",
                    placeholder,
                ),
            value,
        )

        value = single_middle_initial_pattern.sub(
            lambda match:
                match.group(
                    0
                )[:-1]
                + placeholder,
            value,
        )

        return value

    def restore_abbreviation_periods(
        value,
    ):
        return value.replace(
            placeholder,
            ".",
        )

    def sentence_parts(
        value,
    ):
        protected = (
            protect_abbreviation_periods(
                str(
                    value or ""
                )
            )
        )

        parts = re.split(
            r"(?<=[.!?])(\s+)",
            protected,
        )

        return [
            restore_abbreviation_periods(
                part
            )
            for part
            in parts
        ]

    dependent_reference_pattern = re.compile(
        r"\b(?:this|that|these|those)\s+"
        r"(?:"
        r"contract|contracts|"
        r"agreement|agreements|"
        r"item|items|"
        r"resolution|resolutions|"
        r"ordinance|ordinances|"
        r"purchase\s+order|purchase\s+orders"
        r")\b",
        re.I,
    )

    def scrub(
        value,
    ):
        value = str(
            value or ""
        )

        if not value:
            return value

        parts = sentence_parts(
            value
        )

        for index in range(
            0,
            len(parts),
            2,
        ):
            sentence = parts[
                index
            ]

            if not unsupported_formal_claim(
                sentence
            ):
                continue

            parts[
                index
            ] = ""

            if (
                index + 1
                < len(parts)
            ):
                parts[
                    index + 1
                ] = ""

            next_index = (
                index + 2
            )

            if (
                next_index
                < len(parts)
            ):
                next_sentence = (
                    parts[
                        next_index
                    ]
                )

                if (
                    formal_pattern.search(
                        next_sentence
                    )
                    and dependent_reference_pattern.search(
                        next_sentence
                    )
                ):
                    parts[
                        next_index
                    ] = ""

                    if (
                        next_index + 1
                        < len(parts)
                    ):
                        parts[
                            next_index + 1
                        ] = ""

        cleaned = "".join(
            parts
        )

        cleaned = re.sub(
            r"[ \t]{2,}",
            " ",
            cleaned,
        ).strip()

        return cleaned

    summary_clause_split_pattern = re.compile(
        r"[,;]\s+"
        r"(?="
        r"(?:and\s+|but\s+)?"
        r"(?:(?:the\s+)?(?:city\s+)?council\s+)?"
        r"(?:"
        + formal_word_source
        + r")\b"
        r")",
        re.I,
    )

    def scrub_summary(
        value,
    ):
        value = str(
            value or ""
        )

        if (
            not value
            or not unsupported_formal_claim(
                value
            )
        ):
            return value

        clauses = (
            summary_clause_split_pattern.split(
                value
            )
        )

        if len(
            clauses
        ) <= 1:
            return scrub(
                value
            )

        kept = []

        for clause in clauses:
            clause = clause.strip()

            if not clause:
                continue

            if unsupported_formal_claim(
                clause
            ):
                continue

            clause = re.sub(
                r"^(?:and|but)\s+",
                "",
                clause,
                flags=re.I,
            ).strip()

            clause = clause.rstrip(
                " .!?"
            )

            if clause:
                kept.append(
                    clause
                )

        if not kept:
            return ""

        cleaned = ", ".join(
            kept
        )

        ending = value.rstrip()[-1:]

        if ending in {
            ".",
            "!",
            "?",
        }:
            cleaned += ending

        return cleaned

    changed = False

    new_headline = scrub_summary(
        story.headline
    )

    if new_headline != story.headline:
        story.headline = new_headline
        changed = True

    new_dek = scrub_summary(
        story.dek
    )

    if new_dek != story.dek:
        story.dek = new_dek
        changed = True

    new_body = []

    for paragraph in story.body:
        cleaned = scrub(
            paragraph
        )

        if cleaned:
            new_body.append(
                cleaned
            )

    if new_body != story.body:
        story.body = new_body
        changed = True

    new_key_facts = []

    for fact in story.key_facts:
        cleaned = scrub(
            fact
        )

        if cleaned:
            new_key_facts.append(
                cleaned
            )

    if new_key_facts != story.key_facts:
        story.key_facts = new_key_facts
        changed = True

    return changed



def reconcile_entity_verification_notes(
    story,
    intelligence,
):
    """
    Reconcile the standard CouncilWatch "remains unverified"
    verification note when fresh entity verification now establishes
    that the exact observed source form is VERIFIED or CORRECTED.

    This is intentionally exact and conservative:

      * person entities only;
      * VERIFIED/CORRECTED only;
      * official source URL required;
      * the verification note's source reference must match the
        entity's observed_text;
      * unrelated unverified names remain untouched.
    """
    entities = intelligence.get(
        "entities",
        [],
    )

    def identity_key(
        value,
    ):
        return " ".join(
            re.findall(
                r"[a-z0-9]+",
                str(
                    value or ""
                ).casefold(),
            )
        )

    resolved = {}

    for entity in entities:
        if str(
            entity.get(
                "entity_type",
                "",
            )
        ).casefold() != "person":
            continue

        status = str(
            entity.get(
                "status",
                "",
            )
        ).upper()

        if status not in {
            "VERIFIED",
            "CORRECTED",
        }:
            continue

        observed = str(
            entity.get(
                "observed_text",
                "",
            )
            or ""
        ).strip()

        canonical = str(
            entity.get(
                "canonical_text",
                "",
            )
            or ""
        ).strip()

        official_url = str(
            entity.get(
                "official_source_url",
                "",
            )
            or ""
        ).strip()

        if (
            not observed
            or not canonical
            or not official_url
        ):
            continue

        resolved[
            identity_key(
                observed
            )
        ] = {
            "observed":
                observed,
            "canonical":
                canonical,
            "status":
                status,
        }

    if not resolved:
        return False

    stale_pattern = re.compile(
        r"^The identity associated with the source reference "
        r"(?P<quote>['\"])(?P<observed>.+?)(?P=quote) "
        r"remains unverified; CouncilWatch did not rely on that "
        r"name as an identified person\.$",
        re.I,
    )

    new_notes = []

    for note in (
        story.verification_notes
        or []
    ):
        note = str(
            note
            or ""
        )

        match = stale_pattern.fullmatch(
            note.strip()
        )

        if not match:
            new_notes.append(
                note
            )
            continue

        observed = match.group(
            "observed"
        )

        entity = resolved.get(
            identity_key(
                observed
            )
        )

        if not entity:
            new_notes.append(
                note
            )
            continue

        canonical = entity[
            "canonical"
        ]

        status = entity[
            "status"
        ]

        if (
            status == "CORRECTED"
            and identity_key(
                observed
            )
            != identity_key(
                canonical
            )
        ):
            replacement = (
                "The source reference "
                f"'{observed}' was corrected to "
                f"{canonical} using official source "
                "verification."
            )
        else:
            replacement = (
                "The identity associated with the source "
                f"reference '{observed}' was verified as "
                f"{canonical} using official source "
                "verification."
            )

        if replacement not in new_notes:
            new_notes.append(
                replacement
            )

    if (
        new_notes
        == list(
            story.verification_notes
            or []
        )
    ):
        return False

    story.verification_notes = (
        new_notes
    )

    return True


def normalize_validated_action_language(
    story,
    intelligence,
):
    """
    Prevent reader-facing copy from strengthening a validated
    REQUESTED STAFF FOLLOW-UP into a direction, order,
    instruction or investigation mandate.

    The action ledger controls the permitted action strength.
    """

    changed = (
        normalize_validated_formal_status_language(
            story,
            intelligence,
        )
    )

    if _remove_validated_unclear_formal_claims(
        story,
        intelligence,
    ):
        changed = True


    actions = intelligence.get(
        "action_ledger",
        [],
    )

    requested_actions = [
        action
        for action in actions
        if (
            str(
                action.get(
                    "action_status",
                    "",
                )
            ).strip().lower()
            == "requested staff follow-up"
            and action.get("validated") is True
        )
    ]

    if not requested_actions:
        return changed

    stronger_actions = [
        action
        for action in actions
        if (
            str(
                action.get(
                    "action_status",
                    "",
                )
            ).strip().lower()
            in {
                "directed",
                "ordered",
                "instructed",
            }
            and action.get("validated") is True
        )
    ]

    stopwords = {
        "councilmember",
        "council",
        "staff",
        "requested",
        "request",
        "follow",
        "regarding",
        "specific",
        "concerns",
        "concern",
        "raised",
        "during",
        "public",
        "comments",
        "comment",
        "city",

        # Action-strength words are not topical evidence.
        # Without excluding them, two unrelated actions can
        # appear related merely because both say "directed",
        # "ordered" or "instructed".
        "directed",
        "directing",
        "ordered",
        "ordering",
        "instructed",
        "instructing",
    }

    def tokens(value):
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

    requested_cues = set()

    for action in requested_actions:
        requested_cues |= tokens(
            str(
                action.get(
                    "topic",
                    "",
                )
            )
            + " "
            + str(
                action.get(
                    "evidence_quote",
                    "",
                )
            )
        )

    stronger_cue_sets = []

    for action in stronger_actions:
        stronger_cue_sets.append(
            tokens(
                str(
                    action.get(
                        "topic",
                        "",
                    )
                )
                + " "
                + str(
                    action.get(
                        "evidence_quote",
                        "",
                    )
                )
            )
        )

    strong_pattern = re.compile(
        r"\b(?:"
        r"directed|directing|"
        r"ordered|ordering|"
        r"instructed|instructing"
        r")\s+(?:city\s+)?staff\b",
        re.I,
    )

    replacements = [
        (
            re.compile(
                r"\bdirected city staff to "
                r"(?:investigate|address)\b",
                re.I,
            ),
            "requested that city staff follow up on",
        ),
        (
            re.compile(
                r"\bdirected staff to "
                r"(?:investigate|address)\b",
                re.I,
            ),
            "requested staff follow-up on",
        ),
        (
            re.compile(
                r"\bdirected city staff to follow up on\b",
                re.I,
            ),
            "requested that city staff follow up on",
        ),
        (
            re.compile(
                r"\bdirected staff to follow up on\b",
                re.I,
            ),
            "requested staff follow-up on",
        ),
        (
            re.compile(
                r"\bdirected city staff to provide "
                r"(?:a\s+)?follow-up (?:report\s+)?"
                r"(?:on|regarding)\b",
                re.I,
            ),
            "requested that city staff follow up on",
        ),
        (
            re.compile(
                r"\bdirected staff to provide "
                r"(?:a\s+)?follow-up (?:report\s+)?"
                r"(?:on|regarding)\b",
                re.I,
            ),
            "requested staff follow-up on",
        ),
        (
            re.compile(
                r"\bdirecting city staff to "
                r"(?:investigate|address)\b",
                re.I,
            ),
            "requesting that city staff follow up on",
        ),
        (
            re.compile(
                r"\bdirecting staff to "
                r"(?:investigate|address)\b",
                re.I,
            ),
            "requesting staff follow-up on",
        ),
        (
            re.compile(
                r"\bdirecting city staff to follow up on\b",
                re.I,
            ),
            "requesting that city staff follow up on",
        ),
        (
            re.compile(
                r"\bdirecting staff to follow up on\b",
                re.I,
            ),
            "requesting staff follow-up on",
        ),
        (
            re.compile(
                r"\bdirecting city staff to provide "
                r"(?:a\s+)?follow-up (?:report\s+)?"
                r"(?:on|regarding)\b",
                re.I,
            ),
            "requesting that city staff follow up on",
        ),
        (
            re.compile(
                r"\bdirecting staff to provide "
                r"(?:a\s+)?follow-up (?:report\s+)?"
                r"(?:on|regarding)\b",
                re.I,
            ),
            "requesting staff follow-up on",
        ),
        (
            re.compile(
                r"\bordered city staff to "
                r"(?:investigate|address)\b",
                re.I,
            ),
            "requested that city staff follow up on",
        ),
        (
            re.compile(
                r"\bordered staff to "
                r"(?:investigate|address)\b",
                re.I,
            ),
            "requested staff follow-up on",
        ),
        (
            re.compile(
                r"\binstructed city staff to "
                r"(?:investigate|address)\b",
                re.I,
            ),
            "requested that city staff follow up on",
        ),
        (
            re.compile(
                r"\binstructed staff to "
                r"(?:investigate|address)\b",
                re.I,
            ),
            "requested staff follow-up on",
        ),
    ]

    def normalize(value):
        value = str(value or "")

        if not strong_pattern.search(
            value
        ):
            return value

        value_tokens = tokens(
            value
        )

        # Require at least one meaningful connection to a
        # validated follow-up topic. The previous >=2 rule was
        # too strict for concise deks and key facts.
        if (
            requested_cues
            and not (
                value_tokens
                & requested_cues
            )
        ):
            return value

        # Preserve stronger wording only when this exact piece
        # of copy also matches an independently validated
        # stronger action.
        for cue_set in stronger_cue_sets:
            if (
                cue_set
                and len(
                    value_tokens
                    & cue_set
                ) >= 2
            ):
                return value

        cleaned = value

        for pattern, replacement in replacements:
            cleaned = pattern.sub(
                replacement,
                cleaned,
            )

        return cleaned

    new_headline = normalize(
        story.headline
    )

    if new_headline != story.headline:
        story.headline = new_headline
        changed = True

    new_dek = normalize(
        story.dek
    )

    if new_dek != story.dek:
        story.dek = new_dek
        changed = True

    new_body = [
        normalize(paragraph)
        for paragraph in story.body
    ]

    if new_body != story.body:
        story.body = new_body
        changed = True

    new_key_facts = [
        normalize(fact)
        for fact in story.key_facts
    ]

    if new_key_facts != story.key_facts:
        story.key_facts = new_key_facts
        changed = True

    return changed

def restore_required_topics_from_key_facts(
    story,
    intelligence,
):
    """
    If an audit correction removes a MUST INCLUDE topic from the
    body, restore an already-generated matching key fact as a
    concise paragraph.

    The resulting body is audited again normally.
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

    must_items = sorted(
        [
            item
            for item in intelligence.get(
                "coverage_items",
                [],
            )
            if item.get("must_include")
        ],
        key=lambda item: int(
            item.get("rank") or 999
        ),
    )

    changed = False

    for item in must_items:
        topic = str(
            item.get(
                "topic",
                "",
            )
        ).strip()

        topic_words = words(
            topic
        )

        if len(topic_words) < 2:
            continue

        body_words = words(
            "\n".join(
                story.body
            )
        )

        if (
            len(
                topic_words
                & body_words
            )
            >= 2
        ):
            continue

        best_fact = ""
        best_score = 0

        for fact in story.key_facts:
            score = len(
                topic_words
                & words(fact)
            )

            if score > best_score:
                best_score = score
                best_fact = fact

        restore_text = ""

        if (
            best_fact
            and best_score >= 2
            and best_fact not in story.body
        ):
            restore_text = best_fact

        # If audit cleanup also removed the matching key fact,
        # use only a validated lower-level action from the
        # source-backed action ledger.
        if not restore_text:
            for action in intelligence.get(
                "action_ledger",
                [],
            ):
                if action.get(
                    "validated"
                ) is not True:
                    continue

                action_topic = str(
                    action.get(
                        "topic",
                        "",
                    )
                ).strip()

                if (
                    len(
                        topic_words
                        & words(action_topic)
                    )
                    < 2
                ):
                    continue

                action_status = str(
                    action.get(
                        "action_status",
                        "",
                    )
                ).strip().lower()

                agenda_section = str(
                    action.get(
                        "agenda_section",
                        "",
                    )
                ).strip().upper()

                if action.get(
                    "agenda_linkage_conflict"
                ):
                    agenda_section = ""

                readable_topic = (
                    action_topic[:1].lower()
                    + action_topic[1:]
                )

                if action_status == "discussed":
                    if agenda_section == "PUBLIC HEARINGS":
                        restore_text = (
                            "During a public hearing, "
                            "the Council discussed "
                            + readable_topic
                            + "."
                        )

                    elif agenda_section == "NEW BUSINESS":
                        restore_text = (
                            "During new business, "
                            "the Council discussed "
                            + readable_topic
                            + "."
                        )

                    else:
                        restore_text = (
                            "The Council discussed "
                            + readable_topic
                            + "."
                        )

                elif action_status == "considered":
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

        if not restore_text:
            continue

        rank = int(
            item.get("rank")
            or 999
        )

        if rank == 1:
            insert_at = min(
                1,
                len(story.body),
            )
        else:
            insert_at = min(
                max(rank - 1, 1),
                len(story.body),
            )

        story.body.insert(
            insert_at,
            restore_text,
        )

        changed = True

    return changed


def apply_audit_corrections(story, audit):
    changed = False

    if audit.corrected_headline:
        if audit.corrected_headline != story.headline:
            story.headline = audit.corrected_headline
            changed = True

    if audit.corrected_dek:
        if audit.corrected_dek != story.dek:
            story.dek = audit.corrected_dek
            changed = True

    if audit.corrected_body:
        if audit.corrected_body != story.body:
            story.body = audit.corrected_body
            changed = True

    if audit.corrected_key_facts:
        if audit.corrected_key_facts != story.key_facts:
            story.key_facts = audit.corrected_key_facts
            changed = True

    if audit.corrected_verification_notes:
        if (
            audit.corrected_verification_notes
            != story.verification_notes
        ):
            story.verification_notes = (
                audit.corrected_verification_notes
            )
            changed = True

    if strip_public_agenda_item_numbers(
        story
    ):
        changed = True

    return changed


def story_fields(story):
    return {
        "headline": story.headline,
        "dek": story.dek,
        "body": "\n".join(story.body),
        "key_facts": "\n".join(story.key_facts),
        "verification_notes":
            "\n".join(story.verification_notes),
    }


def valid_audit_issues(story, audit):
    fields = story_fields(story)

    corrected_fields = {
        "headline": audit.corrected_headline or "",
        "dek": audit.corrected_dek or "",
        "body": "\n".join(audit.corrected_body or []),
        "key_facts": "\n".join(
            audit.corrected_key_facts or []
        ),
        "verification_notes": "\n".join(
            audit.corrected_verification_notes or []
        ),
    }

    valid = []

    for issue in audit.issues:
        quoted = (issue.draft_text or "").strip()
        field_text = fields.get(issue.field, "")

        # The complained-about text must actually exist
        # in the CURRENT story.
        if not quoted or quoted not in field_text:
            continue

        # If the auditor's own corrected version is already
        # identical to the current field, the complaint is
        # stale/resolved and must not survive the final audit.
        corrected = corrected_fields.get(
            issue.field,
            "",
        )

        if corrected and corrected == field_text:
            continue

        valid.append(issue)

    return valid


def find_meeting(city_slug):
    meetings = latest_ready_meetings()

    matches = [
        m
        for m in meetings
        if m["city_slug"] == city_slug
    ]

    if not matches:
        available = ", ".join(
            sorted(
                {
                    m["city_slug"]
                    for m in meetings
                }
            )
        )

        raise SystemExit(
            f"No ready meeting found for '{city_slug}'. "
            f"Available: {available}"
        )

    return matches[0]


def process_city(
    city_slug,
    force_story=False,
    force_notes=False,
    meeting_override=None,
):
    meeting = (
        meeting_override
        if meeting_override is not None
        else find_meeting(city_slug)
    )

    slug = meeting["city_slug"]
    city = meeting["city_name"]
    external_id = str(meeting["external_id"])

    notes_file = (
        DRAFTS
        / f"{slug}--{external_id}.notes.txt"
    )

    story_file = (
        DRAFTS
        / f"{slug}--{external_id}.json"
    )

    # Keep media/captions isolated per meeting.
    # Never reuse one meeting's source material for another.
    workdir = WORK / slug / external_id
    workdir.mkdir(
        parents=True,
        exist_ok=True,
    )

    audio = workdir / "meeting.mp3"

    status = load_status()

    print()
    print("======================================================")
    print(f" {city.upper()}")
    print("======================================================")
    print()
    print("Meeting :", meeting.get("meeting_date"))
    print("Title   :", meeting.get("title"))
    print("ID      :", external_id)
    print("Media   :", meeting.get("recording_url"))
    print()

    # --------------------------------------------------
    # Existing completed draft
    # --------------------------------------------------

    if story_file.exists() and not force_story:
        existing = json.loads(
            story_file.read_text(encoding="utf-8")
        )

        if existing.get("audit_ok") is True:
            print("Existing audited draft already passes.")
            print()
            print(existing.get("headline", ""))
            print()
            print(
                "Use --force-story if you intentionally "
                "want to regenerate it."
            )
            return

    try:
        # --------------------------------------------------
        # SOURCE NOTES
        # --------------------------------------------------

        if (
            notes_file.exists()
            and not force_notes
        ):
            print("Using cached source notes.")
            print("No media download or retranscription.")
            notes = notes_file.read_text(encoding="utf-8")

        else:
            update_status(
                status,
                meeting,
                "source_acquisition",
                "Acquiring complete meeting source material.",
            )

            source = acquire_source(
                meeting["recording_url"],
                workdir,
                audio,
                refresh=force_notes,
            )

            if source["kind"] == "captions":
                notes = source["text"]

                print()
                print(
                    "Using full cleaned YouTube captions "
                    "as source evidence."
                )
                print(
                    "Caption transcript:",
                    f"{len(notes):,} characters"
                )

                update_status(
                    status,
                    meeting,
                    "source_notes",
                    (
                        "Using complete meeting captions "
                        "as source evidence."
                    ),
                )

            else:
                source_audio = Path(
                    source["path"]
                )

                print(
                    "Audio source ready:",
                    f"{source_audio.stat().st_size / 1024 / 1024:.1f} MB",
                )

                update_status(
                    status,
                    meeting,
                    "source_notes",
                    "Analyzing complete meeting recording.",
                )

                print(
                    "Sending full meeting to Gemini..."
                )

                notes = make_comprehensive_source_notes(
                    source_audio,
                    meeting,
                )

            if not notes or len(notes.strip()) < 100:
                raise RuntimeError(
                    "Source material was unexpectedly short."
                )

            notes_file.write_text(
                notes,
                encoding="utf-8",
            )

            print(
                "Source evidence saved:",
                notes_file.name,
            )

            if source["kind"] == "audio":
                try:
                    Path(source["path"]).unlink()
                except Exception:
                    pass

        # --------------------------------------------------
        # OFFICIAL AGENDA MATERIAL
        # --------------------------------------------------

        print()
        print("Reading official agenda/source material...")

        agenda_url = meeting.get("agenda_url") or ""

        try:
            agenda = (
                agenda_text(agenda_url)
                if agenda_url
                else ""
            )
        except Exception as exc:
            print(
                "WARNING: agenda retrieval failed:",
                type(exc).__name__,
                exc,
            )
            agenda = ""

        if agenda:
            print(
                "Agenda/source text:",
                f"{len(agenda):,} characters"
            )
        else:
            print(
                "WARNING: no agenda text available; "
                "writer will rely on recording-derived notes."
            )

        # --------------------------------------------------
        # REAL-WORLD VERIFICATION + COVERAGE PLANNING
        # --------------------------------------------------

        intelligence_file = (
            DRAFTS
            / f"{slug}--{external_id}.intelligence.json"
        )

        if (
            intelligence_file.exists()
            and not force_notes
        ):
            print()
            print(
                "Using cached real-world verification "
                "and coverage plan."
            )

            intelligence = json.loads(
                intelligence_file.read_text(
                    encoding="utf-8"
                )
            )

        else:
            update_status(
                status,
                meeting,
                "verifying",
                (
                    "Verifying real-world names/places "
                    "and ranking meeting coverage."
                ),
            )

            print()
            print(
                "Verifying names, places, programs "
                "and organizations against reality..."
            )

            intelligence = build_meeting_intelligence(
                meeting,
                notes,
                agenda,
            )

            intelligence_file.write_text(
                json.dumps(
                    intelligence,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        print()
        print("Coverage plan:")

        for item in intelligence.get(
            "coverage_items",
            [],
        ):
            must = (
                " MUST INCLUDE"
                if item.get(
                    "must_include"
                )
                else ""
            )

            print(
                f"  #{item.get('rank')} "
                f"{item.get('topic')} "
                f"[{item.get('score')}/10]"
                f"{must}"
            )

        # IMPORTANT:
        # The fact-checker receives source notes plus deterministic
        # entity identity/spelling context ONLY.
        #
        # Never feed the AI-generated coverage plan or editorial
        # "why it matters" reasoning back as source evidence.
        audit_notes = (
            notes
            + "\n\n"
            + audit_verification_context(
                intelligence
            )
        )

        # --------------------------------------------------
        # WRITE
        # --------------------------------------------------

        update_status(
            status,
            meeting,
            "writing",
            "Writing private draft.",
        )

        print()
        print("Writing story...")

        story = make_rich_story(
            meeting,
            notes,
            agenda,
            intelligence,
        )

        public_guard_changed = False

        if strip_public_agenda_item_numbers(
            story
        ):
            public_guard_changed = True

        if enforce_public_action_constraints(
            story,
            intelligence,
        ):
            public_guard_changed = True

        if normalize_validated_action_language(
            story,
            intelligence,
        ):
            public_guard_changed = True

        if reconcile_entity_verification_notes(
            story,
            intelligence,
        ):
            public_guard_changed = True

        if restore_required_topics_from_key_facts(
            story,
            intelligence,
        ):
            public_guard_changed = True

        if public_guard_changed:
            print(
                "Applied deterministic "
                "reader-facing safety guards."
            )

        # --------------------------------------------------
        # AUDIT / CORRECT LOOP
        # --------------------------------------------------

        final_audit = None
        valid_issues = []

        for pass_num in range(1, 4):
            update_status(
                status,
                meeting,
                "auditing",
                f"Evidence audit pass {pass_num}.",
            )

            print(
                f"Running evidence audit pass {pass_num}..."
            )

            audit = retry_api_call(
                f"Audit pass {pass_num}",
                lambda: audit_story(
                    meeting,
                    audit_notes,
                    agenda,
                    story,
                ),
            )

            valid_issues = valid_audit_issues(
                story,
                audit,
            )

            material = [
                issue
                for issue in valid_issues
                if issue.severity.lower() == "material"
            ]

            print(
                f"  valid issues: {len(valid_issues)}"
            )
            print(
                f"  material:     {len(material)}"
            )

            final_audit = audit

            # Apply usable corrections for ALL valid issues,
            # including minor spelling/name errors. A known
            # error should never survive merely because it is
            # non-material.
            changed = False

            if valid_issues:
                changed = apply_audit_corrections(
                    story,
                    audit,
                )

                if enforce_public_action_constraints(
                    story,
                    intelligence,
                ):
                    changed = True

                if normalize_validated_action_language(
                    story,
                    intelligence,
                ):
                    changed = True

                if reconcile_entity_verification_notes(
                    story,
                    intelligence,
                ):
                    changed = True

                if restore_required_topics_from_key_facts(
                    story,
                    intelligence,
                ):
                    changed = True

            if changed:
                print(
                    "  Corrections applied; "
                    "auditing corrected copy again."
                )
                continue

            if material:
                print(
                    "  Auditor found material issue but "
                    "did not supply a usable correction."
                )

            break

        # Always audit exactly the text that will be saved.
        # If the final audit finds a usable correction, including
        # a minor one, apply it and audit the corrected copy again.
        final_story_was_changed = False

        for final_pass in range(1, 5):
            print()
            print(
                "Running FINAL audit on saved copy "
                f"(pass {final_pass})..."
            )

            final_audit = retry_api_call(
                f"Final audit pass {final_pass}",
                lambda: audit_story(
                    meeting,
                    audit_notes,
                    agenda,
                    story,
                ),
            )

            valid_issues = valid_audit_issues(
                story,
                final_audit,
            )

            material = [
                issue
                for issue in valid_issues
                if issue.severity.lower() == "material"
            ]

            final_story_was_changed = False

            if valid_issues:
                changed = apply_audit_corrections(
                    story,
                    final_audit,
                )

                if enforce_public_action_constraints(
                    story,
                    intelligence,
                ):
                    changed = True

                if normalize_validated_action_language(
                    story,
                    intelligence,
                ):
                    changed = True

                if reconcile_entity_verification_notes(
                    story,
                    intelligence,
                ):
                    changed = True

                if restore_required_topics_from_key_facts(
                    story,
                    intelligence,
                ):
                    changed = True

                if changed:
                    final_story_was_changed = True

                    print(
                        "  Final-audit correction applied; "
                        "auditing corrected copy again."
                    )
                    continue

            break

        # If the LAST permitted correction pass changed the
        # article, the audit result above describes the older
        # copy rather than the text now held in `story`.
        #
        # Fail closed unless the exact final text receives one
        # more read-only audit.
        if final_story_was_changed:
            print()
            print(
                "Last final-audit pass changed the story."
            )
            print(
                "Auditing exact saved copy one more time..."
            )

            final_audit = retry_api_call(
                "Exact saved-copy audit",
                lambda: audit_story(
                    meeting,
                    audit_notes,
                    agenda,
                    story,
                ),
            )

            valid_issues = valid_audit_issues(
                story,
                final_audit,
            )

            material = [
                issue
                for issue in valid_issues
                if issue.severity.lower()
                == "material"
            ]

            print(
                "  exact-copy valid issues:",
                len(valid_issues),
            )

            print(
                "  exact-copy material:",
                len(material),
            )

        deterministic_action_issues = (
            unresolved_high_priority_formal_action_issues(
                story,
                intelligence,
            )
        )

        if deterministic_action_issues:
            existing_issue_keys = {
                (
                    issue.field,
                    issue.draft_text,
                    issue.source_evidence,
                )
                for issue in valid_issues
            }

            for issue in deterministic_action_issues:
                key = (
                    issue.field,
                    issue.draft_text,
                    issue.source_evidence,
                )

                if key not in existing_issue_keys:
                    valid_issues.append(issue)
                    existing_issue_keys.add(key)

            material = [
                issue
                for issue in valid_issues
                if issue.severity.lower() == "material"
            ]

        final_ok = len(material) == 0

        # --------------------------------------------------
        # SAVE
        # --------------------------------------------------

        payload = {
            "generated_at_utc": utc_now(),
            "status": "READY FOR REVIEW",
            "city_slug": slug,
            "city_name": city,
            "meeting_date": meeting.get("meeting_date"),
            "meeting_title": meeting.get("title"),
            "external_id": external_id,
            "headline": story.headline,
            "dek": story.dek,
            "body": story.body,
            "key_facts": story.key_facts,
            "verification_notes":
                story.verification_notes,
            "entity_verification":
                intelligence.get(
                    "entities",
                    [],
                ),
            "action_ledger":
                intelligence.get(
                    "action_ledger",
                    [],
                ),
            "coverage_plan":
                intelligence.get(
                    "coverage_items",
                    [],
                ),
            "editorial_summary":
                intelligence.get(
                    "editorial_summary",
                    "",
                ),
            "audit_ok": final_ok,
            "audit_issues": [
                issue.model_dump()
                for issue in valid_issues
            ],
            "final_audit": True,
            "source_url": meeting.get("source_url"),
            "agenda_url": meeting.get("agenda_url"),
            "recording_url":
                meeting.get("recording_url"),
            "transcript_model": TRANSCRIPT_MODEL,
            "story_model": STORY_MODEL,
            "published": False,
        }

        story_file.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        if final_ok:
            # Notification is intentionally non-blocking.
            # A notification problem must never make an
            # otherwise successful meeting-processing job fail.
            try:
                notify_ready_for_review(
                    meeting,
                    payload,
                )
            except Exception as exc:
                print(
                    "WARNING: ready-for-review "
                    "notification failed:",
                    type(exc).__name__,
                    exc,
                )

        update_status(
            status,
            meeting,
            "complete",
            (
                "READY FOR REVIEW"
                if final_ok
                else "READY FOR REVIEW - MATERIAL ISSUE"
            ),
            draft=story_file.name,
        )

        print()
        print("======================================================")
        print(f" {city.upper()} READY FOR REVIEW")
        print("======================================================")
        print()
        print(story.headline)
        print()
        print(story.dek)
        print()
        print("Final audit passed:", final_ok)
        print("Remaining issues:", len(valid_issues))
        print("Material issues:", len(material))

        for issue in valid_issues:
            print()
            print(
                f"[{issue.severity.upper()}] "
                f"{issue.field}"
            )
            print(
                "Draft   :",
                issue.draft_text,
            )
            print(
                "Evidence:",
                issue.source_evidence,
            )
            print(
                "Fix     :",
                issue.correction,
            )

        print()
        print("Saved:", story_file)

    except Exception as exc:
        update_status(
            status,
            meeting,
            "failed",
            (
                f"{city} processing failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        )

        traceback.print_exc()
        raise


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate and audit one CouncilWatch "
            "private city draft."
        )
    )

    parser.add_argument(
        "--city",
        required=True,
        help=(
            "City slug, e.g. lake-forest "
            "or laguna-niguel"
        ),
    )

    parser.add_argument(
        "--force-story",
        action="store_true",
        help=(
            "Rewrite the article even if an audited "
            "draft already exists."
        ),
    )

    parser.add_argument(
        "--force-notes",
        action="store_true",
        help=(
            "Reacquire source material and regenerate "
            "source evidence."
        ),
    )

    args = parser.parse_args()

    process_city(
        args.city,
        force_story=args.force_story,
        force_notes=args.force_notes,
    )


if __name__ == "__main__":
    main()
