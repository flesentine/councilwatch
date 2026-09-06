from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from gemini_worker import StoryDraft
from official_entities import (
    official_entity_material,
    find_official_support,
)

from settings import (
    STORY_MODEL,
    TRANSCRIPT_MODEL,
    TRANSCRIPT_FALLBACK_MODELS,
)


OFFICIAL_DOMAINS = {
    "rsm": {
        "cityofrsm.org",
        "cityofrsm.granicus.com",
    },
    "aliso-viejo": {
        "avcity.org",
        "alisoviejoca.granicus.com",
    },
    "mission-viejo": {
        "cityofmissionviejo.org",
        "missionviejo.granicus.com",
    },
    "lake-forest": {
        "lakeforestca.gov",
    },
    "laguna-niguel": {
        "cityoflagunaniguel.org",
    },
}


class CoverageItem(BaseModel):
    rank: int
    topic: str
    score: int = Field(ge=1, le=10)
    category: str
    action_status: str
    summary: str
    why_it_matters: str
    must_include: bool = False


class CoveragePlan(BaseModel):
    items: list[CoverageItem]
    editorial_summary: str = ""


class ActionRecord(BaseModel):
    topic: str
    item_number: str = ""
    action_status: str
    evidence_source: str
    evidence_quote: str


class ActionLedger(BaseModel):
    items: list[ActionRecord]


ACTION_FORMAL_STATUSES = {
    "approved",
    "adopted",
    "authorized",
    "awarded",
    "directed",
    "rejected",
    "denied",
    "appointed",
    "accepted",
    "passed",
}


ACTION_EVIDENCE_TERMS = {
    "approve",
    "approved",
    "approval",
    "adopt",
    "adopted",
    "authorize",
    "authorized",
    "award",
    "awarded",
    "directed",
    "reject",
    "rejected",
    "deny",
    "denied",
    "appoint",
    "appointed",
    "accepted",
    "passed",
    "motion",
    "moved",
    "vote",
    "voted",
    "carried",
}


ACTION_STOPWORDS = {
    "and",
    "the",
    "for",
    "with",
    "from",
    "into",
    "city",
    "council",
    "program",
    "annual",
    "update",
    "amendment",
    "amendments",
    "item",
}


AGENDA_SECTION_NAMES = {
    "CONSENT CALENDAR",
    "PUBLIC HEARINGS",
    "NEW BUSINESS",
    "PRESENTATIONS",
    "PUBLIC COMMENTS",
    "CLOSED SESSION",
    "OCSD PUBLIC SAFETY UPDATE",
    "OCFA PUBLIC SAFETY UPDATES",
    "CITY MANAGER REPORTS",
    "CITY ATTORNEY REPORTS",
    "COUNCIL MEMBER COMMENTS AND ACTIONS",
    "MAYOR'S, COMMISSION, COMMITTEE REPORTS AND ACTIONS",
}

AGENDA_SECTION_ALIASES = {
    "CONSENT CALENDAR ITEMS":
        "CONSENT CALENDAR",

    "PUBLIC HEARING":
        "PUBLIC HEARINGS",

    "PUBLIC HEARING ITEMS":
        "PUBLIC HEARINGS",

    "PUBLIC HEARINGS ITEMS":
        "PUBLIC HEARINGS",

    "NEW BUSINESS ITEMS":
        "NEW BUSINESS",

    "PRESENTATION":
        "PRESENTATIONS",

    "PRESENTATION ITEMS":
        "PRESENTATIONS",

    "PUBLIC COMMENT":
        "PUBLIC COMMENTS",

    "PUBLIC COMMENT ITEMS":
        "PUBLIC COMMENTS",

    "CLOSED SESSION ITEMS":
        "CLOSED SESSION",

    "ITEMS REMOVED FROM THE CONSENT CALENDAR":
        "ITEMS REMOVED FROM THE CONSENT CALENDAR",
}


def _canonical_agenda_section(value):
    """
    Normalize harmless agenda-heading variations to one
    deterministic section name.
    """

    upper = (
        str(value or "")
        .replace(
            "\u2019",
            "'",
        )
        .replace(
            "\u2018",
            "'",
        )
    )

    upper = re.sub(
        r"\s+",
        " ",
        upper.strip(),
    ).upper()

    if upper in AGENDA_SECTION_NAMES:
        return upper

    return AGENDA_SECTION_ALIASES.get(
        upper,
        "",
    )



def _agenda_section_from_heading(value):
    """
    Recognize common agenda section-heading variations without
    treating their leading outline number as an agenda item.

    The returned value is canonical internal metadata only.
    """

    cleaned = (
        str(value or "")
        .replace(
            "\u2019",
            "'",
        )
        .replace(
            "\u2018",
            "'",
        )
    )

    cleaned = re.sub(
        r"\s+",
        " ",
        cleaned.strip(),
    )

    cleaned = cleaned.rstrip(
        ":"
    ).strip().upper()

    canonical = (
        _canonical_agenda_section(
            cleaned
        )
    )

    if canonical:
        return canonical

    aliases = {
        "SPECIAL PRESENTATIONS":
            "PRESENTATIONS",

        "COMMUNITY INPUT":
            "PUBLIC COMMENTS",

        "DISCUSSION":
            "DISCUSSION",

        "DISCUSSION ITEMS":
            "DISCUSSION",

        "CITY COUNCIL DISCUSSION":
            "DISCUSSION",

        "CITY COUNCIL DISCUSSION ITEMS":
            "DISCUSSION",

        "DISCUSSION/ACTION ITEMS":
            "DISCUSSION",

        "DISCUSSION / ACTION ITEMS":
            "DISCUSSION",

        "DISCUSSION AND ACTION ITEMS":
            "DISCUSSION",

        "DISCUSSION & ACTION ITEMS":
            "DISCUSSION",

        "ACTION ITEMS":
            "DISCUSSION",

        "ADDITIONS, DELETIONS, REORDERING TO THE AGENDA":
            "AGENDA CHANGES",

        "CITY MANAGER'S REPORT":
            "CITY MANAGER REPORTS",

        "CITY MANAGER' S REPORT":
            "CITY MANAGER REPORTS",

        "ANNOUNCEMENTS / COUNCIL COMMENTS / COMMITTEE UPDATES":
            "COUNCIL MEMBER COMMENTS AND ACTIONS",

        "ADJOURNMENT":
            "ADJOURNMENT",
    }

    return aliases.get(
        cleaned,
        "",
    )



def parse_agenda_structure(agenda):
    """
    Deterministically extract numbered agenda items and their
    official sections.

    Supported item forms include:

      21. Public Hearing Title

      5.6
      Consent Calendar Item Title

      5.7 Traffic Signal Item Title

      5.8. Another Item Title

      4.6) Security Agreement

    Wrapped all-caps item titles are merged before matching.

    Obvious document furniture such as a street address or a
    four-digit year is not treated as an agenda item.

    The official agenda, not transcript-derived notes, controls
    item numbering, item titles and section placement.
    """
    items = []
    current_section = ""
    current_section_number = ""
    pending_item_number = None

    lines = [
        re.sub(
            r"\s+",
            " ",
            raw_line.strip(),
        )
        for raw_line in str(
            agenda or ""
        ).splitlines()
    ]

    number_title_pattern = re.compile(
        r"^"
        r"(\d+(?:\.\d+)*)"
        r"(?:[.)])?"
        r"\s+"
        r"(.+)"
        r"$"
    )

    number_only_pattern = re.compile(
        r"^"
        r"(\d+(?:\.\d+)*)"
        r"(?:[.)])?"
        r"$"
    )

    street_suffix_pattern = re.compile(
        r"\b(?:"
        r"parkway|pkwy|"
        r"road|rd|"
        r"street|st|"
        r"avenue|ave|"
        r"drive|dr|"
        r"boulevard|blvd|"
        r"lane|ln|"
        r"court|ct|"
        r"way"
        r")\b",
        re.I,
    )

    def normalized_upper(
        value,
    ):
        return (
            value.replace(
                "\u2019",
                "'",
            )
            .replace(
                "\u2018",
                "'",
            )
            .upper()
        )

    def plausible_item_number(
        value,
    ):
        value = str(
            value or ""
        )

        if "." in value:
            return True

        if not value.isdigit():
            return False

        number = int(
            value
        )

        # Four-digit years appearing inside a wrapped title are
        # content, not agenda item numbers.
        if 1900 <= number <= 2100:
            return False

        return True

    def usable_inline_match(
        value,
    ):
        match = number_title_pattern.match(
            value
        )

        if not match:
            return None

        item_number = match.group(
            1
        )

        title = match.group(
            2
        ).strip()

        if not plausible_item_number(
            item_number
        ):
            return None

        # Reject document-furniture street addresses such as:
        #
        #   30111 Crown Valley Parkway
        #
        # Agenda item numbers in these feeds are not five-digit
        # street numbers, and the street suffix makes the intent
        # explicit.
        if (
            "." not in item_number
            and len(
                item_number
            ) >= 5
            and street_suffix_pattern.search(
                title
            )
        ):
            return None

        return match

    def numbered_section_match(
        value,
    ):
        match = number_title_pattern.match(
            value
        )

        if not match:
            return None

        if not plausible_item_number(
            match.group(
                1
            )
        ):
            return None

        section = (
            _agenda_section_from_heading(
                match.group(
                    2
                )
            )
        )

        if not section:
            return None

        return (
            match,
            section,
        )

    def is_wrapped_title_continuation(
        value,
    ):
        if not value:
            return False

        upper = normalized_upper(
            value
        )

        if _agenda_section_from_heading(
            upper
        ):
            return False

        if numbered_section_match(
            value
        ):
            return False

        if usable_inline_match(
            value
        ):
            return False

        standalone = number_only_pattern.fullmatch(
            value
        )

        if (
            standalone
            and plausible_item_number(
                standalone.group(
                    1
                )
            )
        ):
            return False

        # The agendas that need continuation recovery use
        # uppercase wrapped title lines. Restricting to uppercase
        # prevents staff recommendations/body text from being
        # absorbed into the official title.
        if value != value.upper():
            return False

        if value.endswith(
            ":"
        ):
            return False

        if re.match(
            r"^(?:"
            r"STAFF\s+RECOMMENDS|"
            r"STAFF\s+RECOMMENDATION|"
            r"RECOMMENDATION|"
            r"BACKGROUND|"
            r"FISCAL\s+IMPACT|"
            r"ATTACHMENTS?|"
            r"AGENDA|"
            r"CITY\s+OF"
            r")\b",
            value,
            re.I,
        ):
            return False

        return bool(
            re.search(
                r"[A-Za-z]",
                value,
            )
        )

    def collect_wrapped_title(
        first_title,
        next_index,
    ):
        title_parts = [
            first_title.strip()
        ]

        index = next_index

        while index < len(
            lines
        ):
            candidate = lines[
                index
            ]

            if not is_wrapped_title_continuation(
                candidate
            ):
                break

            title_parts.append(
                candidate
            )

            index += 1

        return (
            " ".join(
                title_parts
            ).strip(),
            index,
        )

    index = 0

    while index < len(
        lines
    ):
        line = lines[
            index
        ]

        if not line:
            index += 1
            continue

        upper = normalized_upper(
            line
        )

        # --------------------------------------------------
        # UNNUMBERED SECTION HEADING
        # --------------------------------------------------

        section = (
            _agenda_section_from_heading(
                upper
            )
        )

        if section:
            current_section = section
            current_section_number = ""
            pending_item_number = None

            index += 1
            continue

        # --------------------------------------------------
        # NUMBERED SECTION HEADING
        # --------------------------------------------------

        numbered_section = (
            numbered_section_match(
                line
            )
        )

        if numbered_section:
            section_match, section = (
                numbered_section
            )

            current_section = section

            current_section_number = (
                section_match.group(
                    1
                ).split(
                    "."
                )[0]
            )

            pending_item_number = None

            index += 1
            continue

        # --------------------------------------------------
        # NUMBER + TITLE ON SAME LINE
        # --------------------------------------------------

        inline_match = usable_inline_match(
            line
        )

        if inline_match:
            item_number = (
                inline_match.group(
                    1
                )
            )

            major = (
                item_number.split(
                    "."
                )[0]
            )

            if (
                current_section_number
                and major
                != current_section_number
            ):
                current_section = ""
                current_section_number = ""

            title, next_index = (
                collect_wrapped_title(
                    inline_match.group(
                        2
                    ),
                    index + 1,
                )
            )

            items.append(
                {
                    "item_number":
                        item_number,

                    "section":
                        current_section,

                    "title":
                        title,
                }
            )

            pending_item_number = None
            index = next_index

            continue

        # --------------------------------------------------
        # NUMBER ON ITS OWN LINE
        # --------------------------------------------------

        standalone_match = (
            number_only_pattern.fullmatch(
                line
            )
        )

        if (
            standalone_match
            and plausible_item_number(
                standalone_match.group(
                    1
                )
            )
        ):
            item_number = (
                standalone_match.group(
                    1
                )
            )

            major = (
                item_number.split(
                    "."
                )[0]
            )

            if (
                current_section_number
                and major
                != current_section_number
            ):
                current_section = ""
                current_section_number = ""

            pending_item_number = (
                item_number
            )

            index += 1
            continue

        # --------------------------------------------------
        # TITLE FOLLOWING A STANDALONE NUMBER
        # --------------------------------------------------

        if pending_item_number:
            title, next_index = (
                collect_wrapped_title(
                    line,
                    index + 1,
                )
            )

            items.append(
                {
                    "item_number":
                        pending_item_number,

                    "section":
                        current_section,

                    "title":
                        title,
                }
            )

            pending_item_number = None
            index = next_index

            continue

        index += 1

    return items


def _action_topic_component_labels(
    topic,
):
    """
    Return explicit textual components of a compound topic.

    This is evidentiary decomposition only. It does not rewrite
    the original coverage topic.

    Example:

      Automated License Plate Recognition (ALPR)
      and Digital Signage

    becomes:

      [
        "Automated License Plate Recognition (ALPR)",
        "Digital Signage",
      ]
    """

    parts = re.split(
        r"\s+(?:and|&)\s+|\s*/\s*",
        str(
            topic or ""
        ),
        flags=re.I,
    )

    labels = []

    for part in parts:
        label = part.strip()

        if not label:
            continue

        if not _action_words(
            label
        ):
            continue

        labels.append(
            label
        )

    return labels


def _action_topic_components(topic):
    """
    Split only explicit compound-topic conjunctions.

    This is used conservatively for evidence scope, not for
    rewriting topic labels.

    Example:

      Automated License Plate Recognition (ALPR)
      and Digital Signage

    becomes two evidentiary components.
    """

    parts = re.split(
        r"\s+(?:and|&)\s+|\s*/\s*",
        str(
            topic or ""
        ),
        flags=re.I,
    )

    components = []

    for part in parts:
        words = _action_words(
            part
        )

        if words:
            components.append(
                words
            )

    return components


def _topic_scope_supported(
    topic,
    evidence,
):
    """
    Require evidence for the full topical scope.

    A compound topic cannot inherit a formal action when the
    evidence describes only one side.

    For a compound topic:
      - total meaningful overlap must be at least two words; and
      - every explicit component must contribute at least one
        meaningful evidence word.
    """

    topic_words = _action_words(
        topic
    )

    evidence_words = _action_words(
        evidence
    )

    total_overlap = len(
        topic_words
        & evidence_words
    )

    if total_overlap < 2:
        return False

    components = (
        _action_topic_components(
            topic
        )
    )

    if len(
        components
    ) <= 1:
        return True

    for component in components:
        if not (
            component
            & evidence_words
        ):
            return False

    return True


def _agenda_match_score(
    topic,
    agenda_title,
):
    if _topic_scope_supported(
        topic,
        agenda_title,
    ):
        return len(
            _action_words(topic)
            & _action_words(agenda_title)
        )

    # Official agenda headings are often broader than editorial
    # coverage labels. A trailing designation/status qualifier can
    # describe the same underlying subject rather than a second
    # independent agenda topic.
    #
    # Keep this intentionally narrow so truly compound topics such
    # as "ALPR and Digital Signage" still require full-scope support.
    simplified_topic = re.sub(
        r"\s+(?:and|&)\s+"
        r"[^,;]+?"
        r"\b(?:designation|status)\b"
        r"\s*$",
        "",
        str(
            topic or ""
        ),
        flags=re.I,
    ).strip()

    if (
        simplified_topic
        and simplified_topic
        != str(
            topic or ""
        ).strip()
        and _topic_scope_supported(
            simplified_topic,
            agenda_title,
        )
    ):
        return len(
            _action_words(
                simplified_topic
            )
            & _action_words(
                agenda_title
            )
        )

    return 0


def _resolve_agenda_item(
    topic,
    proposed_item_number,
    agenda_items,
):
    """
    Resolve a model-proposed item number against the actual
    official agenda.

    A proposed number normally requires the official-title topic
    matcher to succeed.

    One conservative fallback is permitted when:
      - the proposed number exactly exists,
      - that candidate belongs to an official agenda section, and
      - topic and official title share at least three meaningful
        identity words.

    This handles a broader editorial topic label without allowing
    unrelated or unsectioned document furniture to control agenda
    linkage.
    """
    proposed = str(
        proposed_item_number or ""
    ).strip()

    if proposed:
        for item in agenda_items:
            if (
                item[
                    "item_number"
                ]
                != proposed
            ):
                continue

            score = (
                _agenda_match_score(
                    topic,
                    item[
                        "title"
                    ],
                )
            )

            if score >= 2:
                return item

            overlap = (
                _action_words(
                    topic
                )
                & _action_words(
                    item[
                        "title"
                    ]
                )
            )

            if (
                item.get(
                    "section"
                )
                and len(
                    overlap
                ) >= 3
            ):
                return item

    best = None
    best_score = 0

    for item in agenda_items:
        score = _agenda_match_score(
            topic,
            item[
                "title"
            ],
        )

        if score > best_score:
            best = item
            best_score = score

    if best_score >= 2:
        return best

    return None



def _action_norm(value):
    return re.sub(
        r"\s+",
        " ",
        str(value or "").strip().lower(),
    )


def _action_word_root(word):
    """
    Conservative normalization used only for topic/evidence
    matching.

    This is not text rewriting. It lets obvious variants such as
    weed/weeds and camera/cameras compare as the same concept.
    """

    word = str(word or "").lower()

    if (
        len(word) > 4
        and word.endswith("ies")
    ):
        return word[:-3] + "y"

    if (
        len(word) > 4
        and word.endswith("s")
        and not word.endswith("ss")
    ):
        return word[:-1]

    return word


def _agenda_item_sort_key(value):
    """
    Sort hierarchical agenda numbers numerically.

    Examples:
      5.6  < 5.10
      17   < 21
    """

    return tuple(
        int(part)
        for part in str(
            value or ""
        ).split(".")
        if part != ""
    )


def _evidence_agenda_item_numbers(value):
    """
    Extract explicit agenda item numbers from source text.

    Recognizes forms such as:

      Item 21
      Items 17 and 18
      Agenda Item 21
      Item No. 5.6
      Agenda Items 5.6 and 5.9

    Dates, addresses, vote totals and years are intentionally
    ignored.
    """

    numbers = set()

    item_number = (
        r"\d+(?:\.\d+)*"
    )

    pattern = re.compile(
        r"\b"
        r"(?:agenda\s+)?"
        r"items?"
        r"(?:\s+no\.?)?"
        r"\s+"
        r"("
        + item_number
        + r"(?:"
        r"\s*(?:,|and|&|/)\s*"
        + item_number
        + r")*"
        r")",
        re.I,
    )

    for match in pattern.finditer(
        str(value or "")
    ):
        numbers.update(
            re.findall(
                item_number,
                match.group(1),
            )
        )

    # Recording notes sometimes preserve both an original
    # outline number and an explicit reordered number:
    #
    #   Item 6.2 (Post-swap to 6.3)
    #
    # Treat the explicitly identified destination as another
    # source item reference. This is not inference; the source
    # itself states the renumbering/reordering.
    reorder_pattern = re.compile(
        r"\b(?:"
        r"post[- ]?swap|"
        r"reordered|"
        r"renumbered|"
        r"moved"
        r")"
        r"\s+to\s+"
        r"(\d+(?:\.\d+)*)"
        r"\b",
        re.I,
    )

    for match in reorder_pattern.finditer(
        str(
            value or ""
        )
    ):
        numbers.add(
            match.group(
                1
            )
        )

    return numbers



def _agenda_item_number_value(value):
    """
    Normalize a numeric or spoken agenda item number used in an
    explicit transcript agenda transition.
    """

    token = re.sub(
        r"[-\s]+",
        " ",
        str(value or "").strip().casefold(),
    )

    if re.fullmatch(r"\d{1,3}", token):
        return str(int(token))

    units = {
        "zero": 0,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
        "thirteen": 13,
        "fourteen": 14,
        "fifteen": 15,
        "sixteen": 16,
        "seventeen": 17,
        "eighteen": 18,
        "nineteen": 19,
    }

    if token in units:
        return str(units[token])

    tens = {
        "twenty": 20,
        "thirty": 30,
        "forty": 40,
        "fifty": 50,
        "sixty": 60,
        "seventy": 70,
        "eighty": 80,
        "ninety": 90,
    }

    parts = token.split()

    if len(parts) == 1 and parts[0] in tens:
        return str(tens[parts[0]])

    if (
        len(parts) == 2
        and parts[0] in tens
        and parts[1] in units
        and 0 < units[parts[1]] < 10
    ):
        return str(tens[parts[0]] + units[parts[1]])

    return ""


def _agenda_item_transition_numbers(value):
    """
    Return agenda numbers announced as actual transcript boundaries.

    References like "item 3" by themselves are deliberately not
    considered boundaries. We require language that reads or moves
    to the next agenda item.
    """

    number_token = (
        r"(?:"
        r"\d{1,3}|"
        r"zero|one|two|three|four|five|six|seven|eight|nine|"
        r"ten|eleven|twelve|thirteen|fourteen|fifteen|"
        r"sixteen|seventeen|eighteen|nineteen|"
        r"(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
        r"(?:[-\s]+(?:one|two|three|four|five|six|seven|eight|nine))?"
        r")"
    )

    patterns = (
        re.compile(
            r"\bread\s+(?:the\s+)?title"
            r"(?:\s+[a-z]+){0,8}\s+"
            r"(?:agenda\s+)?item(?:\s+number)?\s+"
            + f"(?P<number>{number_token})"
            + r"\b",
            re.I,
        ),
        re.compile(
            r"\bthe\s+title"
            r"(?:\s+[a-z]+){0,8}\s+"
            r"(?:agenda\s+)?item(?:\s+number)?\s+"
            + f"(?P<number>{number_token})"
            + r"\b",
            re.I,
        ),
        re.compile(
            r"\b(?:move|moving|go|going|proceed|proceeding|"
            r"advance|advancing)"
            r"(?:\s+[a-z]+){0,8}\s+"
            r"(?:on\s+)?to\s+"
            r"(?:agenda\s+)?item(?:\s+number)?\s+"
            + f"(?P<number>{number_token})"
            + r"\b",
            re.I,
        ),
    )

    numbers = set()
    source = str(value or "")

    for pattern in patterns:
        for match in pattern.finditer(source):
            number = _agenda_item_number_value(
                match.group("number")
            )
            if number:
                numbers.add(number)

    return numbers


def _candidate_has_foreign_agenda_transition(
    candidate,
    agenda_item_number,
):
    """
    Evidence for one agenda item cannot cross an explicit transition
    to another item.

    Clean evidence does NOT need to repeat the target item number.
    """

    target = _agenda_item_number_value(
        agenda_item_number
    )

    if not target:
        return False

    transitions = _agenda_item_transition_numbers(
        candidate
    )

    return any(
        number != target
        for number in transitions
    )


def _action_words(value):
    words = set()

    for word in re.findall(
        r"[a-z0-9]+",
        _action_norm(value),
    ):
        if (
            len(word) < 4
            or word in ACTION_STOPWORDS
        ):
            continue

        words.add(
            _action_word_root(word)
        )

    return words


def _evidence_text_norm(value):
    """
    Normalize presentation-only Markdown before testing whether
    an evidence excerpt exists in source material.

    This deliberately removes formatting, not factual words.

    For example these are evidentially identical:

      * Motion to approve: Councilmember Smith
      Motion to approve: Councilmember Smith

    The stored evidence excerpt can still retain the exact source
    Markdown when CouncilWatch repairs an invalid model quote.
    """

    text = str(
        value or ""
    )

    text = (
        text.replace(
            "\u2018",
            "'",
        )
        .replace(
            "\u2019",
            "'",
        )
    )

    # Source-note Markdown sometimes escapes punctuation.
    text = text.replace(
        "\\'",
        "'",
    )

    # Remove common emphasis markers.
    text = text.replace(
        "**",
        "",
    )

    text = text.replace(
        "__",
        "",
    )

    # Remove Markdown list markers only at line starts.
    text = re.sub(
        r"(?m)^\s*[-*+]\s+",
        "",
        text,
    )

    # Remove remaining single emphasis markers.
    text = text.replace(
        "*",
        "",
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip().lower()


def _quote_is_in_source(quote, source):
    q = _evidence_text_norm(
        quote
    )

    s = _evidence_text_norm(
        source
    )

    return bool(q) and q in s


def _consent_item_source_labels(
    item_number,
    agenda_items,
):
    """
    Return source-note labels that can safely identify one
    official Consent Calendar item.

    Some agenda systems number consent items hierarchically:

      official agenda: 5.6
      recording notes: Item 6

    The leaf-number shorthand is accepted ONLY when exactly one
    official Consent Calendar item has that leaf number.
    """

    canonical = str(
        item_number or ""
    ).strip()

    if not canonical:
        return set()

    labels = {
        canonical,
    }

    parts = canonical.split(
        "."
    )

    if len(parts) < 2:
        return labels

    leaf = parts[-1]

    matches = [
        item
        for item in agenda_items
        if (
            str(
                item.get(
                    "section",
                    "",
                )
            ).upper()
            == "CONSENT CALENDAR"
            and str(
                item.get(
                    "item_number",
                    "",
                )
            )
            .split(".")[-1]
            == leaf
        )
    ]

    if len(matches) == 1:
        labels.add(
            leaf
        )

    return labels


def _best_supported_consent_action_quote(
    item_number,
    agenda_items,
    notes,
):
    """
    Find an exact recording-note excerpt proving that a specific
    official Consent Calendar item was included in an approval
    block.

    This does NOT treat a generic Consent Calendar vote as proof
    for every consent item.

    The notes must explicitly identify the relevant item number,
    either by its canonical number or by an unambiguous leaf
    shorthand such as:

      official item 5.6
      source heading "Consent Calendar (Item 6, 7, 8)"

    This rule cannot validate Public Hearing or New Business
    items because the official agenda section must be Consent
    Calendar before this helper is used.
    """

    labels = _consent_item_source_labels(
        item_number,
        agenda_items,
    )

    if not labels:
        return None

    raw_lines = str(
        notes or ""
    ).splitlines()

    for i, raw_line in enumerate(
        raw_lines
    ):
        normalized = _evidence_text_norm(
            raw_line
        )

        if (
            "consent calendar"
            not in normalized
        ):
            continue

        cited = (
            _evidence_agenda_item_numbers(
                raw_line
            )
        )

        if not (
            cited
            & labels
        ):
            continue

        block = [
            raw_line,
        ]

        # Consent summaries are normally short. Capture the
        # exact heading plus the immediately following action
        # lines until a blank line or a new Markdown heading.
        for j in range(
            i + 1,
            min(
                len(raw_lines),
                i + 8,
            ),
        ):
            candidate = raw_lines[j]

            if not candidate.strip():
                break

            candidate_norm = (
                _evidence_text_norm(
                    candidate
                )
            )

            if (
                candidate_norm.startswith(
                    "agenda item "
                )
                or candidate_norm.startswith(
                    "consent calendar"
                )
            ):
                break

            block.append(
                candidate
            )

        quote = "\n".join(
            block
        ).strip()

        quote_norm = (
            _evidence_text_norm(
                quote
            )
        )

        # A simple leaf shorthand such as source "Item 6"
        # can identify official Consent item 5.6 only when the
        # same source block does not explicitly identify a
        # different dotted item.
        #
        # This preserves RSM's clean:
        #
        #   Consent Calendar (Item 6, 7, 8)
        #
        # while rejecting Aliso's contradictory block:
        #
        #   Consent Calendar (Agenda Item 6)
        #   Item 6.1: Historical Preservation...
        #
        # as proof of official Consent item 4.6.
        quoted_numbers = (
            _evidence_agenda_item_numbers(
                quote
            )
        )

        dotted_numbers = {
            number
            for number
            in quoted_numbers
            if "." in number
        }

        if (
            dotted_numbers
            and not (
                dotted_numbers
                & labels
            )
        ):
            continue

        has_approval = bool(
            re.search(
                r"\b("
                r"motion\s+to\s+approve|"
                r"approved|"
                r"approval|"
                r"passed|"
                r"carried"
                r")\b",
                quote_norm,
            )
        )

        has_result = bool(
            re.search(
                r"\b("
                r"vote|"
                r"unanimous|"
                r"carried|"
                r"passed"
                r")\b",
                quote_norm,
            )
        )

        if (
            has_approval
            and has_result
            and _quote_is_in_source(
                quote,
                notes,
            )
        ):
            return quote

    return None


def _formal_status_supported(
    status,
    quote,
):
    """
    Require the source excerpt to support the actual claimed
    formal action type, not merely some formal-action word.

    A passed motion whose stated purpose is to form/create/
    establish something also supports APPROVED, because the
    source itself provides both the motion purpose and result.
    """

    status = _action_norm(
        status
    )

    normalized = _action_norm(
        quote
    )

    patterns = {
        "approved":
            r"\b(?:approve|approves|approved|approval)\b",

        "adopted":
            r"\b(?:adopt|adopts|adopted|adoption)\b",

        "authorized":
            r"\b(?:authorize|authorizes|authorized|authorization)\b",

        "awarded":
            r"\b(?:award|awards|awarded)\b",

        "directed":
            r"\b(?:direct|directs|directed|directing)\b",

        "rejected":
            r"\b(?:reject|rejects|rejected|rejection)\b",

        "denied":
            r"\b(?:deny|denies|denied|denial)\b",

        "appointed":
            r"\b(?:appoint|appoints|appointed|appointment)\b",

        "accepted":
            r"\b(?:accept|accepts|accepted|acceptance)\b",

        "passed":
            r"\b(?:pass|passes|passed|carries|carried)\b",
    }

    pattern = patterns.get(
        status
    )

    if (
        pattern
        and re.search(
            pattern,
            normalized,
            re.I,
        )
    ):
        return True

    # Example:
    #
    #   A motion was made to form the committee.
    #   The motion passed unanimously.
    #
    # This directly establishes approval of forming the
    # committee even though the word "approved" is absent.
    if status == "approved":
        if (
            re.search(
                r"\bmotion\b"
                r".*\b(?:form|create|establish)\b"
                r".*\b(?:passed|carries|carried)\b",
                normalized,
                re.I,
            )
        ):
            return True

    return False


def _canonical_formal_status_from_quote(
    status,
    quote,
):
    """
    Prefer the specific action embodied in a passed motion over
    the generic result word "passed".

    Examples:

      motion to approve X ... passed
          -> approved

      motion to adopt X ... passed
          -> adopted

      motion to appoint X ... passed
          -> appointed

      motion to form/create/establish a committee ... passed
          -> approved

    A source that says only "the motion passed" remains PASSED.
    """

    status = _action_norm(
        status
    )

    if status != "passed":
        return status

    normalized = _action_norm(
        quote
    )

    specific_patterns = (
        (
            "adopted",
            r"\b(?:adopt|adopts|adopted|adoption)\b",
        ),
        (
            "authorized",
            r"\b(?:authorize|authorizes|authorized|authorization)\b",
        ),
        (
            "awarded",
            r"\b(?:award|awards|awarded)\b",
        ),
        (
            "directed",
            r"\b(?:direct|directs|directed|directing)\b",
        ),
        (
            "rejected",
            r"\b(?:reject|rejects|rejected|rejection)\b",
        ),
        (
            "denied",
            r"\b(?:deny|denies|denied|denial)\b",
        ),
        (
            "appointed",
            r"\b(?:appoint|appoints|appointed|appointment)\b",
        ),
        (
            "accepted",
            r"\b(?:accept|accepts|accepted|acceptance)\b",
        ),
        (
            "approved",
            r"\b(?:approve|approves|approved|approval)\b",
        ),
    )

    for canonical, pattern in specific_patterns:
        if re.search(
            pattern,
            normalized,
            re.I,
        ):
            return canonical

    # A passed motion whose stated purpose is to create/form/
    # establish something is semantically an approval of that
    # creation.
    if re.search(
        r"\bmotion\b"
        r".*\b(?:form|create|establish)\b"
        r".*\b(?:passed|carries|carried)\b",
        normalized,
        re.I,
    ):
        return "approved"

    return status


def _conflicted_generic_collective_formal_action(
    item_number,
    agenda_section,
    status,
    quote,
    agenda_items,
):
    """
    Return True when a formal action claim depends only on a
    generic collective Consent Calendar disposition while the
    source's explicit item numbers conflict with the official
    agenda mapping.

    This is intentionally narrow.

    Example that MUST fail formal validation:

      official topic -> item 21

      source:
        Discussion occurred regarding agenda items 17 and 18...
        The Consent Calendar was approved unanimously.

    The source supports discussion of the topic, but the generic
    Consent vote cannot prove approval of official item 21.

    A conflicted record may still retain a formal action when the
    source contains its own topic-specific formal action, such as:

      The motion to approve the historical-preservation
      committee ... passed 4-1.
    """

    item_number = str(
        item_number or ""
    ).strip()

    status = _action_norm(
        status
    )

    if (
        not item_number
        or status
        not in ACTION_FORMAL_STATUSES
        or not quote
    ):
        return False

    evidence_numbers = (
        _evidence_agenda_item_numbers(
            quote
        )
    )

    if not evidence_numbers:
        return False

    # Exact official item reference is not conflicted.
    if item_number in evidence_numbers:
        return False

    # Preserve the already-proven RSM-style unique Consent leaf
    # shorthand:
    #
    #   official 5.6
    #   source "Item 6"
    if (
        agenda_section
        == "CONSENT CALENDAR"
    ):
        consent_labels = (
            _consent_item_source_labels(
                item_number,
                agenda_items,
            )
        )

        if (
            consent_labels
            & evidence_numbers
        ):
            return False

    normalized = (
        _evidence_text_norm(
            quote
        )
    )

    # We are guarding only generic COLLECTIVE Consent Calendar
    # dispositions. Do not broaden this to ordinary motions.
    generic_patterns = [
        re.compile(
            r"\bthe consent calendar "
            r"(?:was )?approved\b",
            re.I,
        ),

        re.compile(
            r"\bconsent calendar "
            r"(?:was )?approved\b",
            re.I,
        ),

        re.compile(
            r"\bapproved the consent calendar\b",
            re.I,
        ),

        re.compile(
            r"\bmotion to approve "
            r"(?:the )?consent calendar\b",
            re.I,
        ),

        re.compile(
            r"\bconsent calendar "
            r"(?:motion )?"
            r"(?:passed|carries|carried)\b",
            re.I,
        ),
    ]

    matched_generic = False
    remainder = normalized

    for pattern in generic_patterns:
        if pattern.search(
            remainder
        ):
            matched_generic = True

            remainder = pattern.sub(
                " ",
                remainder,
            )

    if not matched_generic:
        return False

    # If a separate, non-collective action phrase survives after
    # removing the generic Consent vote, do NOT invalidate it.
    #
    # Historical Preservation is the important regression case:
    #
    #   motion to approve the committee ... passed 4-1
    #
    # remains valid even though its source numbering conflicts.
    specific_patterns = {
        "approved":
            r"\b(?:"
            r"motion\s+to\s+approve|"
            r"approve|approves|approved|approval"
            r")\b",

        "adopted":
            r"\b(?:adopt|adopts|adopted|adoption)\b",

        "authorized":
            r"\b(?:authorize|authorizes|authorized|authorization)\b",

        "awarded":
            r"\b(?:award|awards|awarded|awarding)\b",

        "directed":
            r"\b(?:direct|directs|directed|directing)\b",

        "rejected":
            r"\b(?:reject|rejects|rejected|rejection)\b",

        "denied":
            r"\b(?:deny|denies|denied|denial)\b",

        "appointed":
            r"\b(?:appoint|appoints|appointed|appointment)\b",

        "accepted":
            r"\b(?:accept|accepts|accepted|acceptance)\b",

        "passed":
            r"\b(?:pass|passes|passed|passing|carried)\b",
    }

    specific_pattern = (
        specific_patterns.get(
            status
        )
    )

    if (
        specific_pattern
        and re.search(
            specific_pattern,
            remainder,
            re.I,
        )
    ):
        return False

    return True


def _formal_action_has_topic_support(
    topic,
    item_number,
    quote,
    status=None,
):
    quote_words = _action_words(
        quote
    )

    topic_words = _action_words(
        topic
    )

    item_match = False

    item_number = str(
        item_number or ""
    ).strip()

    if item_number:
        item_match = bool(
            re.search(
                rf"\b{re.escape(item_number)}\b",
                str(
                    quote
                ),
            )
        )

    has_action_language = bool(
        quote_words
        & ACTION_EVIDENCE_TERMS
    )

    if not has_action_language:
        return False

    if (
        status
        and not _formal_status_supported(
            status,
            quote,
        )
    ):
        return False

    return (
        item_match
        or _topic_scope_supported(
            topic,
            quote,
        )
    )


def _single_line_transcript_turn_windows(
    notes,
    max_turns=18,
    max_chars=16000,
):
    """
    Return bounded exact contiguous source windows for transcripts
    that arrive as one giant line separated by speaker markers.

    Normal multi-line recording notes continue using the existing
    line/block logic.
    """

    source = str(
        notes or ""
    )

    if not source.strip():
        return []

    if len(
        source.splitlines()
    ) > 2:
        return []

    markers = list(
        re.finditer(
            r">>",
            source,
        )
    )

    if len(
        markers
    ) < 8:
        return []

    starts = [
        0,
        *[
            marker.end()
            for marker in markers
        ],
    ]

    ends = [
        *[
            marker.start()
            for marker in markers
        ],
        len(
            source
        ),
    ]

    windows = []
    seen = set()

    for start_index in range(
        len(
            starts
        )
    ):
        for span in range(
            1,
            max_turns + 1,
        ):
            end_index = (
                start_index
                + span
                - 1
            )

            if end_index >= len(
                ends
            ):
                break

            candidate = source[
                starts[
                    start_index
                ]:
                ends[
                    end_index
                ]
            ].strip()

            if not candidate:
                continue

            if len(
                candidate
            ) > max_chars:
                break

            if candidate in seen:
                continue

            seen.add(
                candidate
            )

            windows.append(
                candidate
            )

    return windows



def _action_evidence_quote_is_bounded(
    quote,
    source,
):
    """
    Reject a whole-meeting transcript as one evidence quotation.

    This guard applies only to long single-line speaker-turn
    transcripts. Normal structured notes are unaffected.
    """

    quote_text = str(
        quote or ""
    ).strip()

    source_text = str(
        source or ""
    )

    if not quote_text:
        return False

    if (
        len(
            source_text.splitlines()
        ) <= 2
        and source_text.count(
            ">>"
        ) >= 8
        and len(
            source_text
        ) >= 20000
    ):
        return (
            len(
                quote_text
            )
            <= 16000
        )

    return True



def _turn_window_topic_identity_span(
    topic,
    candidate,
    max_span_chars=3000,
):
    """
    Locate the strongest local cluster of meaningful topic words.

    Return (start, end, matched_words) only when enough distinct
    topic identity words occur close together.

    This prevents generic overlap such as:

        Electric Bicycle Municipal Code Amendments

    being matched by an unrelated block containing only:

        municipal code

    It also lets final-action validation distinguish a vote from
    the PREVIOUS agenda item from a vote that follows the current
    item's actual identity.
    """

    normalized = _action_norm(
        candidate
    )

    topic_words = sorted(
        _action_words(
            topic
        )
    )

    if not topic_words:
        return None

    occurrences = []

    for word in topic_words:
        for match in re.finditer(
            rf"\b{re.escape(word)}[a-z0-9]*\b",
            normalized,
            re.I,
        ):
            occurrences.append(
                (
                    match.start(),
                    match.end(),
                    word,
                )
            )

    if not occurrences:
        return None

    occurrences.sort(
        key=lambda item: item[0]
    )

    topic_count = len(
        topic_words
    )

    if topic_count <= 2:
        required = topic_count

    elif topic_count <= 4:
        required = 3

    else:
        required = 3

    best = None

    for left in range(
        len(
            occurrences
        )
    ):
        matched = set()

        for right in range(
            left,
            len(
                occurrences
            ),
        ):
            start = occurrences[
                left
            ][
                0
            ]

            end = occurrences[
                right
            ][
                1
            ]

            if (
                end
                - start
                > max_span_chars
            ):
                break

            matched.add(
                occurrences[
                    right
                ][
                    2
                ]
            )

            if len(
                matched
            ) < required:
                continue

            candidate_result = (
                start,
                end,
                frozenset(
                    matched
                ),
            )

            if best is None:
                best = candidate_result
                continue

            best_start, best_end, best_words = (
                best
            )

            # Prefer:
            #   1. more distinct topic words;
            #   2. tighter cluster;
            #   3. later cluster when otherwise tied.
            #
            # The final tie-break is useful when an earlier public
            # comment mentions one or two related words but the
            # actual agenda-item title appears later.
            score = (
                len(
                    matched
                ),
                -(
                    end
                    - start
                ),
                start,
            )

            best_score = (
                len(
                    best_words
                ),
                -(
                    best_end
                    - best_start
                ),
                best_start,
            )

            if score > best_score:
                best = candidate_result

    return best


def _local_topic_anchor_supported(
    topic,
    candidate,
):
    """
    Require strong local topical identity without depending entirely
    on the editorial topic label matching the transcript vocabulary.

    This is intentionally conservative. Generic words such as
    "public", "comment", "resident", and "council" do not count as
    topical anchors.
    """
    if _turn_window_topic_identity_span(
        topic,
        candidate,
    ):
        return True

    topic_words = set(
        _action_words(
            topic
        )
    )

    candidate_words = set(
        _action_words(
            candidate
        )
    )

    generic_words = {
        "action",
        "advocacy",
        "city",
        "comment",
        "comments",
        "council",
        "public",
        "resident",
        "residents",
        "speaker",
    }

    topic_words -= generic_words
    candidate_words -= generic_words

    overlap = len(
        topic_words
        & candidate_words
    )

    topic_norm = _action_norm(
        topic
    )

    candidate_norm = _action_norm(
        candidate
    )

    # Treat common spelling/tokenization variants of e-bike as one
    # strong topical anchor.
    topic_has_ebike = bool(
        re.search(
            r"\be[\s-]*bike\b|\bebike\b",
            topic_norm,
            re.I,
        )
    )

    candidate_has_ebike = bool(
        re.search(
            r"\be[\s-]*bike(?:s)?\b|\bebikes?\b",
            candidate_norm,
            re.I,
        )
    )

    if (
        topic_has_ebike
        and candidate_has_ebike
    ):
        overlap += 2

    if len(
        topic_words
    ) <= 2:
        required = 1
    else:
        required = 2

    return overlap >= required



def _raw_council_commentary_supported(
    topic,
    candidate,
):
    """
    Recognize substantive topic-local commentary by an identified
    Council member in a raw speaker-turn transcript.

    This is deliberately different from a resident public comment:
    a nearby speaker cue must identify a Council member / Mayor Pro
    Tem, or the speaking turn itself must explicitly refer to the
    Council's position.
    """
    turns = [
        turn.strip()
        for turn in re.split(
            r"\s*>>\s*",
            str(
                candidate or ""
            ),
        )
        if turn.strip()
    ]

    if not turns:
        return False

    for index, turn in enumerate(
        turns
    ):
        if not _local_topic_anchor_supported(
            topic,
            turn,
        ):
            continue

        normalized = _action_norm(
            turn
        )

        if len(
            normalized
        ) < 120:
            continue

        prior = " ".join(
            turns[
                max(
                    0,
                    index - 2,
                ):
                index
            ]
        )

        prior_norm = _action_norm(
            prior
        )

        speaker_supported = bool(
            re.search(
                r"\b(?:"
                r"council\s+member|"
                r"mayor\s+pro\s+tem"
                r")\b",
                prior_norm,
                re.I,
            )
            or re.search(
                r"\b(?:"
                r"this|the"
                r")\s+(?:entire\s+)?council\b",
                normalized,
                re.I,
            )
        )

        if not speaker_supported:
            continue

        substantive = re.search(
            r"\b(?:"
            r"agree|"
            r"believe|"
            r"clear|"
            r"concern|"
            r"concerns|"
            r"continue|"
            r"difficult|"
            r"idea|"
            r"ideas|"
            r"implement|"
            r"law|"
            r"laws|"
            r"need|"
            r"needs|"
            r"policy|"
            r"problem|"
            r"problems|"
            r"progress|"
            r"reform|"
            r"rule|"
            r"rules|"
            r"safety|"
            r"support|"
            r"supported|"
            r"think"
            r")\b",
            normalized,
            re.I,
        )

        if substantive:
            return True

    return False



def _best_supported_raw_council_commentary_quote(
    topic,
    notes,
):
    """
    Recover a compact exact speaker-turn window proving substantive
    Council-member commentary on a topic.

    Resident testimony itself cannot satisfy this helper.
    """

    if re.match(
        r"^\s*public\s+comment\b",
        str(
            topic or ""
        ),
        re.I,
    ):
        return None

    if not _single_line_transcript_turn_windows(
        notes
    ):
        return None

    turns = [
        turn.strip()
        for turn in re.split(
            r"\s*>>\s*",
            str(
                notes or ""
            ),
        )
        if turn.strip()
    ]

    candidates = []

    topic_words = set(
        _action_words(
            topic
        )
    )

    for index, turn in enumerate(
        turns
    ):
        if not _local_topic_anchor_supported(
            topic,
            turn,
        ):
            continue

        start = max(
            0,
            index - 2,
        )

        candidate = " >> ".join(
            turns[
                start:
                index + 1
            ]
        )

        if not _raw_council_commentary_supported(
            topic,
            candidate,
        ):
            continue

        candidate_words = set(
            _action_words(
                candidate
            )
        )

        overlap = len(
            topic_words
            & candidate_words
        )

        score = (
            overlap
            * 1000
            - len(
                _action_norm(
                    candidate
                )
            )
        )

        candidates.append(
            (
                score,
                candidate,
            )
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item:
            item[0],
        reverse=True,
    )

    return candidates[0][1]





def _best_supported_local_public_comment_quote(
    topic,
    notes,
):
    """
    Recover one exact public-comment speaker turn from a raw
    single-line transcript.

    Public-comment state is tracked explicitly so later Council
    reports or discussion cannot be mistaken for the resident's
    original comment.

    An explicitly labeled public-commenter turn may satisfy a
    broader editorial public-comment topic when at least two
    meaningful topic words occur in that same speaker turn.
    """
    if not _single_line_transcript_turn_windows(
        notes
    ):
        return None

    turns = [
        turn.strip()
        for turn in re.split(
            r"\s*>>\s*",
            str(
                notes or ""
            ),
        )
        if turn.strip()
    ]

    topic_words = _action_words(
        topic
    )

    if len(
        topic_words
    ) < 2:
        return None

    open_pattern = re.compile(
        r"\b(?:"
        r"move\s+on\s+to|"
        r"open|"
        r"begin|"
        r"start|"
        r"takes\s+us\s+to"
        r")\b"
        r"[^.!?\n]{0,120}"
        r"\bpublic\s+comments?\b"
        r"|"
        r"\bpublic\s+comments?\b"
        r"[^.!?\n]{0,120}"
        r"\b(?:open|begin|start)\b",
        re.I,
    )

    close_pattern = re.compile(
        r"\bpublic\s+comments?\b"
        r"[^.!?\n]{0,100}"
        r"\b(?:close|closed)\b"
        r"|"
        r"\b(?:close|closed)\b"
        r"[^.!?\n]{0,100}"
        r"\bpublic\s+comments?\b",
        re.I,
    )

    explicit_commenter_pattern = re.compile(
        r"\b(?:"
        r"first\s+public\s+commenter|"
        r"next\s+public\s+commenter|"
        r"our\s+next\s+public\s+commenter|"
        r"public\s+commenter\s+is"
        r")\b",
        re.I,
    )

    candidates = []
    in_public_comment = False

    for turn in turns:
        normalized = _action_norm(
            turn
        )

        if open_pattern.search(
            normalized
        ):
            in_public_comment = True

        candidate_words = (
            _action_words(
                turn
            )
        )

        overlap = len(
            topic_words
            & candidate_words
        )

        explicit_commenter = bool(
            explicit_commenter_pattern.search(
                normalized
            )
        )

        locally_supported = (
            _local_topic_anchor_supported(
                topic,
                turn,
            )
            or (
                explicit_commenter
                and overlap >= 2
            )
        )

        if (
            in_public_comment
            and locally_supported
        ):
            score = (
                overlap
                * 1000
                + (
                    500
                    if explicit_commenter
                    else 0
                )
                - len(
                    normalized
                )
            )

            candidates.append(
                (
                    score,
                    turn,
                )
            )

        if close_pattern.search(
            normalized
        ):
            in_public_comment = False

    if not candidates:
        return None

    candidates.sort(
        key=lambda item:
            item[
                0
            ],
        reverse=True,
    )

    return candidates[
        0
    ][
        1
    ]




def _staff_followup_language_supported(
    value,
):
    """
    Require explicit staff-follow-up language.
    """
    normalized = _action_norm(
        value
    )

    patterns = (
        r"\b(?:city\s+)?staff\b"
        r"[^.!?\n]{0,180}"
        r"\b(?:"
        r"follow\s+up|"
        r"followup|"
        r"respond|"
        r"report\s+back|"
        r"return|"
        r"look\s+into"
        r")\b",

        r"\b(?:"
        r"follow\s+up|"
        r"followup|"
        r"respond|"
        r"report\s+back|"
        r"return|"
        r"look\s+into"
        r")\b"
        r"[^.!?\n]{0,180}"
        r"\b(?:city\s+)?staff\b",
    )

    return any(
        re.search(
            pattern,
            normalized,
            re.I,
        )
        for pattern in patterns
    )




def _best_supported_local_staff_followup_quote(
    topic,
    notes,
    agenda_title="",
):
    """
    Recover topic-local staff-follow-up evidence from a raw
    single-line transcript.

    The follow-up language must be within two speaker turns of
    strong current-topic identity.

    For an agenda-mapped topic, that local neighborhood must also
    support the official agenda identity. This prevents a nearby
    agenda item from inheriting "staff will return" merely because
    it happens to mention a generic program name such as CIP.
    """
    turn_windows = (
        _single_line_transcript_turn_windows(
            notes
        )
    )

    if not turn_windows:
        return None

    topic_words = _action_words(
        topic
    )

    if len(
        topic_words
    ) < 2:
        return None

    candidates = []

    for candidate in turn_windows:
        if not _action_evidence_quote_is_bounded(
            candidate,
            notes,
        ):
            continue

        turns = [
            turn.strip()
            for turn in re.split(
                r"\s*>>\s*",
                candidate,
            )
            if turn.strip()
        ]

        followup_indexes = [
            index
            for index, turn
            in enumerate(
                turns
            )
            if _staff_followup_language_supported(
                turn
            )
        ]

        if not followup_indexes:
            continue

        local = False

        for index in followup_indexes:
            # Identity can come only from the same follow-up turn
            # or the two immediately preceding speaker turns.
            start = max(
                0,
                index - 2,
            )

            end = (
                index + 1
            )

            neighborhood = (
                " >> ".join(
                    turns[
                        start:end
                    ]
                )
            )

            if not _local_topic_anchor_supported(
                topic,
                neighborhood,
            ):
                continue

            if not _topic_scope_supported(
                topic,
                neighborhood,
            ):
                continue

            if (
                agenda_title
                and not _turn_window_agenda_identity_supported(
                    topic,
                    agenda_title,
                    neighborhood,
                )
            ):
                continue

            local = True
            break

        if not local:
            continue

        candidate_words = _action_words(
            candidate
        )

        overlap = len(
            topic_words
            & candidate_words
        )

        score = (
            overlap
            * 1000
            - len(
                _action_norm(
                    candidate
                )
            )
        )

        candidates.append(
            (
                score,
                candidate,
            )
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item:
            item[
                0
            ],
        reverse=True,
    )

    return candidates[
        0
    ][
        1
    ]





def _turn_window_agenda_identity_supported(
    topic,
    agenda_title,
    candidate,
):
    """
    Require a strong LOCAL topic identity cluster inside a raw
    transcript window.

    The editorial topic is deliberately the primary identity
    source because it is normally more specific than boilerplate
    official-title language such as city names, "municipal code",
    or "professional services agreement".
    """

    identity = (
        _turn_window_topic_identity_span(
            topic,
            candidate,
        )
    )

    if not identity:
        return False

    # Truly compound parent topics retain the existing full-scope
    # protection. This preserves the ALPR + Digital Signage rule.
    components = (
        _action_topic_components(
            topic
        )
    )

    if (
        len(
            components
        ) > 1
        and not _topic_scope_supported(
            topic,
            candidate,
        )
    ):
        return False

    return True

def _agenda_identity_supported_for_source(
    topic,
    agenda_title,
    quote,
    source,
):
    """
    Apply the strong identity rule only to raw single-line
    speaker-turn transcripts.

    Structured multi-line notes retain their existing behavior.
    """

    source_text = str(
        source or ""
    )

    if (
        len(
            source_text.splitlines()
        ) <= 2
        and source_text.count(
            ">>"
        ) >= 8
    ):
        return (
            _turn_window_agenda_identity_supported(
                topic,
                agenda_title,
                quote,
            )
        )

    return True


def _turn_window_formal_finality_supported(
    topic,
    status,
    candidate,
    agenda_title="",
):
    """
    Require strong local topic identity and final-action evidence
    occurring after that identity.

    This prevents a previous agenda item's vote from validating the
    next item merely because one bounded window contains both.
    """

    identity = (
        _turn_window_topic_identity_span(
            topic,
            candidate,
        )
    )

    if not identity:
        return False

    identity_start, identity_end, _ = (
        identity
    )

    normalized = _action_norm(
        candidate
    )

    status = _action_norm(
        status
    )

    if not _formal_status_supported(
        status,
        candidate,
    ):
        return False

    result_pattern = re.compile(
        r"\b(?:that\s+motion|motion)\b"
        r".{0,140}"
        r"\b(?:passes|passed|carries|carried)\b"
        r"|"
        r"\b(?:passes|passed|carries|carried)\s+unanimously\b",
        re.I,
    )

    result_positions = [
        match.start()
        for match in result_pattern.finditer(
            normalized
        )
    ]

    result_after_identity = any(
        position
        >= identity_end
        for position in result_positions
    )

    if status == "passed":
        return result_after_identity

    # A direct completed council action may stand alone, but it
    # must itself occur after the current topic has been identified.
    direct_pattern = re.compile(
        r"\b(?:city\s+council|council)\b"
        r".{0,180}"
        r"\b(?:"
        r"approved|"
        r"adopted|"
        r"authorized|"
        r"awarded|"
        r"directed|"
        r"rejected|"
        r"denied|"
        r"appointed|"
        r"accepted"
        r")\b",
        re.I,
    )

    direct_after_identity = any(
        match.start()
        >= identity_end
        for match in direct_pattern.finditer(
            normalized
        )
    )

    if direct_after_identity:
        return True

    # Agenda/recommendation wording such as "award of..." or
    # "recommendation is to award..." becomes a completed formal
    # disposition only when a later local motion result establishes
    # that Council acted on it.
    return result_after_identity

def _best_supported_formal_action_quote(
    topic,
    status,
    notes,
    agenda_title="",
):
    """
    Recover an exact local source excerpt supporting a formal
    action.

    Structured summaries use blank-line source blocks.
    Single-line transcripts use bounded speaker-turn windows and
    strong topic identity.
    """

    candidates = []

    turn_windows = (
        _single_line_transcript_turn_windows(
            notes
        )
    )

    if turn_windows:
        source_candidates = (
            turn_windows
        )

    else:
        source_candidates = re.split(
            r"\n\s*\n",
            str(
                notes or ""
            ),
        )

    topic_words = _action_words(
        topic
    )

    identity_words = _action_words(
        agenda_title or topic
    )

    for block in source_candidates:
        candidate = block.strip()

        if not candidate:
            continue

        if not _topic_scope_supported(
            topic,
            candidate,
        ):
            continue

        if (
            turn_windows
            and not _turn_window_agenda_identity_supported(
                topic,
                agenda_title,
                candidate,
            )
        ):
            continue

        if not _formal_status_supported(
            status,
            candidate,
        ):
            continue

        if (
            turn_windows
            and not _turn_window_formal_finality_supported(
                topic,
                status,
                candidate,
                agenda_title=agenda_title,
            )
        ):
            continue

        candidate_words = (
            _action_words(
                candidate
            )
        )

        overlap = len(
            topic_words
            & candidate_words
        )

        identity_overlap = len(
            identity_words
            & candidate_words
        )

        if turn_windows:
            # In raw transcripts, prefer precise agenda identity
            # much more strongly than merely choosing the shortest
            # window.
            score = (
                identity_overlap
                * 1000
                + overlap
                * 100
                - len(
                    _action_norm(
                        candidate
                    )
                )
            )

        else:
            score = (
                overlap
                * 100
                - len(
                    _action_norm(
                        candidate
                    )
                )
            )

        candidates.append(
            (
                score,
                candidate,
            )
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return candidates[0][1]

def _compound_topic_has_substantive_line_support(
    topic,
    evidence,
):
    """
    For an explicit compound topic, require each component to
    appear in at least one substantive source line.

    A Markdown heading by itself is not enough.

    Example:

      Staff was directed regarding digital signage.
      Discussion on ALPR:

    cannot by itself prove substantive discussion of both
    components.

    But:

      Staff was directed regarding digital signage.
      The meeting included a discussion regarding ALPR.

    can support a conservative combined "discussed" status.
    """

    components = (
        _action_topic_components(
            topic
        )
    )

    if len(
        components
    ) <= 1:
        return True

    substantive_context = re.compile(
        r"\b(?:"
        r"council|"
        r"councilmember|"
        r"staff|"
        r"meeting|"
        r"motion|"
        r"vote|"
        r"resident|"
        r"speaker|"
        r"mayor|"
        r"members?"
        r")\b",
        re.I,
    )

    lines = [
        line
        for line in str(
            evidence or ""
        ).splitlines()
        if line.strip()
    ]

    for component in components:
        component_supported = False

        for line in lines:
            if not substantive_context.search(
                _action_norm(
                    line
                )
            ):
                continue

            line_words = (
                _action_words(
                    line
                )
            )

            if (
                component
                & line_words
            ):
                component_supported = True
                break

        if not component_supported:
            return False

    return True


def _supplemental_component_formal_action(
    topic,
    status,
    quote,
    source_name,
    agenda_items,
):
    """
    Recover one component-specific formal action from an exact
    source quote when that formal action does NOT apply to the
    full compound coverage topic.

    This is deliberately conservative.

    Requirements:

      - source must be recording-derived notes;
      - parent topic must have multiple explicit components;
      - claimed formal status must be supported by the quote;
      - quote must NOT support the full parent topic;
      - exactly one meaningful component must be supported;
      - that component must resolve to an official agenda item.

    Example:

      parent:
        ALPR and Digital Signage

      evidence:
        Staff was directed to return with a proposal regarding
        additional digital signage.

      supplemental record:
        Digital Signage -> DIRECTED

    The parent record remains free to fall back to DISCUSSED.
    """

    status = _action_norm(
        status
    )

    if (
        source_name != "notes"
        or status
        not in ACTION_FORMAL_STATUSES
        or not quote
    ):
        return None

    labels = (
        _action_topic_component_labels(
            topic
        )
    )

    if len(
        labels
    ) <= 1:
        return None

    if not _formal_status_supported(
        status,
        quote,
    ):
        return None

    # If the exact formal evidence already supports the entire
    # parent topic, no supplemental component record is needed.
    if _topic_scope_supported(
        topic,
        quote,
    ):
        return None

    candidates = []

    for label in labels:
        words = _action_words(
            label
        )

        # Avoid manufacturing standalone records from weak
        # one-word conjunction fragments such as "Pesticide".
        if len(
            words
        ) < 2:
            continue

        agenda_item = (
            _resolve_agenda_item(
                label,
                "",
                agenda_items,
            )
        )

        if not agenda_item:
            continue

        if not _topic_scope_supported(
            label,
            quote,
        ):
            continue

        canonical_status = (
            _canonical_formal_status_from_quote(
                status,
                quote,
            )
        )

        if not _formal_action_has_topic_support(
            label,
            agenda_item[
                "item_number"
            ],
            quote,
            canonical_status,
        ):
            continue

        evidence_item_numbers = (
            _evidence_agenda_item_numbers(
                quote
            )
        )

        item_number = (
            agenda_item[
                "item_number"
            ]
        )

        agenda_section = (
            agenda_item[
                "section"
            ]
        )

        evidence_matches_item = bool(
            item_number
            and item_number
            in evidence_item_numbers
        )

        if (
            not evidence_matches_item
            and item_number
            and agenda_section
            == "CONSENT CALENDAR"
            and evidence_item_numbers
        ):
            consent_labels = (
                _consent_item_source_labels(
                    item_number,
                    agenda_items,
                )
            )

            evidence_matches_item = bool(
                consent_labels
                & evidence_item_numbers
            )

        agenda_linkage_conflict = bool(
            item_number
            and evidence_item_numbers
            and not evidence_matches_item
        )

        validation_note = (
            "Component-specific formal action recovered "
            "from exact recording-derived evidence because "
            "the formal action did not apply to the full "
            "compound coverage topic."
        )

        if agenda_linkage_conflict:
            validation_note += (
                " Source item numbering conflicts with the "
                "official agenda mapping; agenda-section "
                "timing must not be inferred."
            )

        candidates.append(
            {
                "topic":
                    label,

                "item_number":
                    item_number,

                "agenda_section":
                    agenda_section,

                "agenda_linkage_conflict":
                    agenda_linkage_conflict,

                "evidence_item_numbers":
                    sorted(
                        evidence_item_numbers,
                        key=_agenda_item_sort_key,
                    ),

                "agenda_title":
                    agenda_item[
                        "title"
                    ],

                "action_status":
                    canonical_status,

                "evidence_source":
                    "notes",

                "evidence_quote":
                    quote,

                "validated":
                    True,

                "validation_note":
                    validation_note,
            }
        )

    # Ambiguous subset support fails closed.
    if len(
        candidates
    ) != 1:
        return None

    return candidates[0]


def _nonformal_status_from_quote(
    topic,
    candidate,
    require_local_identity=False,
):
    """
    Classify an exact excerpt as discussion/consideration only when
    the source actually establishes meeting treatment of the topic.

    Treatment language in raw transcripts must be local to the
    current topic identity. It cannot reach across a completed
    sentence from a previous agenda item.
    """

    normalized = _action_norm(
        candidate
    )

    identity_start = 0
    identity_end = 0

    if require_local_identity:
        identity = (
            _turn_window_topic_identity_span(
                topic,
                candidate,
            )
        )

        if not identity:
            return None

        identity_start = identity[0]
        identity_end = identity[1]



    def local_match(pattern):
        for match in re.finditer(
            pattern,
            normalized,
            re.I,
        ):
            if not require_local_identity:
                return True

            # Treatment that begins at/after the current topic
            # identity is plainly local.
            if match.start() >= identity_start:
                return True

            # Treatment may naturally introduce the topic:
            #
            #   "give us an update on the Capital Improvement Plan"
            #
            # Permit that only when the treatment and topic are in
            # the same sentence/clause neighborhood.
            if match.end() >= identity_start:
                return True

            gap = normalized[
                match.end():
                identity_start
            ]

            if (
                len(
                    gap
                ) <= 180
                and not re.search(
                    r"[.!?]",
                    gap,
                )
            ):
                return True

        return False



    # Explicit discussion wording.
    if local_match(
        r"\b(?:"
        r"discussion|"
        r"discussed|"
        r"discussing"
        r")\b"
    ):
        return "discussed"



    # Presentation/update evidence.
    #
    # [^.!?\n] deliberately prevents one agenda item's treatment
    # phrase from reaching across a sentence into the next item's
    # title.
    presentation_patterns = (
        r"\b(?:"
        r"provide|provides|provided|providing|"
        r"give|gives|gave|giving|"
        r"present|presents|presented|presenting|"
        r"receive|receives|received|receiving"
        r")\b"
        r"[^.!?\n]{0,180}?"
        r"\b(?:"
        r"update|presentation|briefing"
        r")\b",

        r"\b(?:"
        r"concludes|concluded|concluding"
        r")\b"
        r"[^.!?\n]{0,100}?"
        r"\b(?:"
        r"presentation|briefing|update"
        r")\b",

        r"\b(?:"
        r"presentation|briefing"
        r")\b"
        r"[^.!?\n]{0,100}?"
        r"\b(?:"
        r"concludes|concluded"
        r")\b",
    )


    if any(
        local_match(
            pattern
        )
        for pattern in presentation_patterns
    ):
        return "discussed"



    # Generic nouns such as:
    #
    #   "priority consideration for grants"
    #
    # are not Council consideration.
    consideration_patterns = (
        r"\b(?:"
        r"city\s+council|"
        r"council"
        r")\b"
        r"[^.!?\n]{0,180}?"
        r"\b(?:"
        r"considered|considering"
        r")\b",

        r"\b(?:"
        r"considered|considering"
        r")\b"
        r"[^.!?\n]{0,180}?"
        r"\b(?:"
        r"by\s+"
        r")?"
        r"(?:the\s+)?"
        r"(?:"
        r"city\s+council|"
        r"council"
        r")\b",
    )


    if any(
        local_match(
            pattern
        )
        for pattern in consideration_patterns
    ):
        return "considered"


    return None

def _best_supported_nonformal_quote(
    topic,
    notes,
    agenda_title="",
    agenda_item_number="",
):
    """
    Search recording-derived notes for an exact excerpt proving
    nonformal treatment of the full topic.

    Single-line transcripts additionally require strong topic /
    agenda-title identity.
    """

    if (
        not agenda_title
        and re.match(
            r"^\s*public\s+comment\b",
            str(
                topic or ""
            ),
            re.I,
        )
    ):
        return None


    topic_words = _action_words(
        topic
    )

    if len(
        topic_words
    ) < 2:
        return None

    turn_windows = (
        _single_line_transcript_turn_windows(
            notes
        )
    )

    candidates = []

    if turn_windows:
        source_candidates = [
            candidate
            for candidate in turn_windows
        ]

    else:
        raw_lines = str(
            notes or ""
        ).splitlines()

        source_candidates = []

        for start in range(
            len(
                raw_lines
            )
        ):
            if not raw_lines[
                start
            ].strip():
                continue

            for span in range(
                1,
                13,
            ):
                end = start + span

                if end > len(
                    raw_lines
                ):
                    break

                candidate = "\n".join(
                    raw_lines[
                        start:end
                    ]
                ).strip()

                if candidate:
                    source_candidates.append(
                        candidate
                    )

    identity_words = _action_words(
        agenda_title or topic
    )

    for candidate in source_candidates:
        if _candidate_has_foreign_agenda_transition(
            candidate,
            agenda_item_number,
        ):
            continue

        if not _topic_scope_supported(
            topic,
            candidate,
        ):
            continue

        if (
            turn_windows
            and not _turn_window_agenda_identity_supported(
                topic,
                agenda_title,
                candidate,
            )
        ):
            continue

        if not _compound_topic_has_substantive_line_support(
            topic,
            candidate,
        ):
            continue

        normalized = _action_norm(
            candidate
        )

        status = _nonformal_status_from_quote(
            topic,
            candidate,
            require_local_identity=bool(
                turn_windows
            ),
        )

        if not status:
            continue

        candidate_words = (
            _action_words(
                candidate
            )
        )

        overlap = len(
            topic_words
            & candidate_words
        )

        identity_overlap = len(
            identity_words
            & candidate_words
        )

        if turn_windows:
            score = (
                identity_overlap
                * 1000
                + overlap
                * 100
                - len(
                    normalized
                )
            )

        else:
            score = (
                overlap
                * 100
                - len(
                    normalized
                )
            )

        candidates.append(
            (
                score,
                status,
                candidate,
            )
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    _, status, quote = (
        candidates[0]
    )

    return {
        "action_status":
            status,

        "evidence_quote":
            quote,
    }

def _best_supported_public_comment_quote(
    topic,
    notes,
):
    """
    Recover an exact recording-note excerpt for a public-comment
    topic when model output paraphrased rather than quoted the
    source.

    This helper validates only the existence/content of the
    comment. It does not infer any Council action.
    """

    topic_words = _action_words(
        topic
    )

    if len(topic_words) < 2:
        return None

    raw_lines = str(
        notes or ""
    ).splitlines()

    candidates = []

    for i in range(
        len(raw_lines)
    ):
        windows = [
            raw_lines[i],
        ]

        if i + 1 < len(
            raw_lines
        ):
            windows.append(
                raw_lines[i]
                + "\n"
                + raw_lines[i + 1]
            )

        if i + 2 < len(
            raw_lines
        ):
            windows.append(
                raw_lines[i]
                + "\n"
                + raw_lines[i + 1]
                + "\n"
                + raw_lines[i + 2]
            )

        for candidate in windows:
            normalized = (
                _evidence_text_norm(
                    candidate
                )
            )

            if not normalized:
                continue

            candidate_words = (
                _action_words(
                    candidate
                )
            )

            overlap = len(
                topic_words
                & candidate_words
            )

            if overlap < 2:
                continue

            # Require language consistent with a speaker's
            # comment rather than an unrelated agenda title.
            if not re.search(
                r"\b("
                r"spoke|"
                r"comment|"
                r"requested|"
                r"advocated|"
                r"expressed|"
                r"questioned|"
                r"identified|"
                r"resident"
                r")\b",
                normalized,
            ):
                continue

            score = (
                overlap * 100
                - len(normalized)
            )

            candidates.append(
                (
                    score,
                    candidate.strip(),
                )
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return candidates[0][1]


def _best_supported_staff_followup_quote(
    topic,
    notes,
):
    """
    Find recording-derived evidence that city staff follow-up
    was requested for a specific topic.

    This lets the action ledger preserve the precise supported
    action instead of collapsing it to generic "discussed".
    """

    topic_words = _action_words(
        topic
    )

    if len(topic_words) < 2:
        return None

    raw_lines = str(
        notes or ""
    ).splitlines()

    candidates = []

    for i in range(
        len(raw_lines)
    ):
        windows = [
            raw_lines[i],
        ]

        if i + 1 < len(raw_lines):
            windows.append(
                raw_lines[i]
                + "\n"
                + raw_lines[i + 1]
            )

        for candidate in windows:
            normalized = _action_norm(
                candidate
            )

            if not normalized:
                continue

            if not re.search(
                r"\brequest(?:ed|ing|s)?\b",
                normalized,
            ):
                continue

            if not re.search(
                r"\bstaff\b",
                normalized,
            ):
                continue

            if not re.search(
                r"\bfollow[- ]?up\b",
                normalized,
            ):
                continue

            candidate_words = _action_words(
                candidate
            )

            overlap = len(
                topic_words
                & candidate_words
            )

            if overlap < 2:
                continue

            score = (
                overlap * 100
                - len(normalized)
            )

            candidates.append(
                (
                    score,
                    candidate.strip(),
                )
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return candidates[0][1]


def build_action_ledger(
    meeting,
    notes,
    agenda,
    coverage_items=None,
):
    """
    Build an evidence-indexed action ledger.

    Model output is not trusted by itself. Every evidence quote
    must exist in the claimed source. Formal actions additionally
    require explicit action language AND topic/item linkage.
    """

    agenda_items = parse_agenda_structure(
        agenda
    )

    coverage_items = coverage_items or []

    required_topics = [
        str(item.get("topic") or "").strip()
        for item in coverage_items
        if str(item.get("topic") or "").strip()
    ]

    prompt = f"""
You are extracting a factual action ledger from a city council
meeting for a local-news fact-checking system.

City: {meeting.get("city_name")}
Meeting date: {meeting.get("meeting_date")}

Identify the important council actions and substantive
discussion topics.

For EACH record return:

- topic
- agenda item number if known
- action_status
- evidence_source: exactly "notes" or "agenda"
- evidence_quote: an EXACT VERBATIM excerpt from that source

CRITICAL RULES:

0. REQUIRED TOPIC COMPLETENESS:
   Return at least one action-ledger record for EVERY topic
   listed under REQUIRED COVERAGE TOPICS below.

   Do not omit a topic merely because its final disposition
   is uncertain.

   If the evidence establishes discussion only, use
   "discussed" or "considered".

   If even that cannot be established safely, use "unclear".

1. APPROVED, ADOPTED, AUTHORIZED, AWARDED, DIRECTED,
   REJECTED, DENIED, APPOINTED, ACCEPTED and PASSED are
   FORMAL ACTION STATUSES.

2. Never use a formal action status unless the evidence quote
   explicitly establishes that action for THAT SAME TOPIC or
   agenda item.

3. A generic statement such as:
   "The Consent Calendar was approved"
   does NOT prove that a separately listed PUBLIC HEARING or
   NEW BUSINESS item was approved.

4. An agenda listing proves that an item was scheduled, not how
   the council ultimately disposed of it.

5. If the source establishes only discussion or consideration,
   use "discussed" or "considered".

6. If final disposition cannot be established, use "unclear".

7. For public comments, describe the action precisely, such as:
   "resident comment", "requested staff follow-up", or
   "no council action".

8. evidence_quote must be copied verbatim. Do not paraphrase it.

================ REQUIRED COVERAGE TOPICS ================

{json.dumps(required_topics, ensure_ascii=False, indent=2)}

================ RECORDING-DERIVED NOTES ================

{notes[:65000]}

================ OFFICIAL AGENDA ================

{agenda[:45000]}
"""

    client = genai.Client()

    response = retry_api_call(
        "Action ledger extraction",
        lambda: client.models.generate_content(
            model=STORY_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ActionLedger,
                temperature=0.0,
            ),
        ),
    )

    parsed = getattr(
        response,
        "parsed",
        None,
    )

    if isinstance(
        parsed,
        ActionLedger,
    ):
        raw_items = parsed.items
    else:
        raw_items = (
            ActionLedger.model_validate_json(
                response.text
            ).items
        )

    # Meeting-level raw transcript detection. Initialize before processing individual ledger records.
    raw_transcript = bool(
        _single_line_transcript_turn_windows(
            notes
        )
    )

    cleaned = []
    supplemental_actions = []

    for record in raw_items:
        item = record.model_dump()

        topic = str(
            item.get("topic") or ""
        ).strip()

        proposed_item_number = str(
            item.get("item_number") or ""
        ).strip()

        agenda_item = _resolve_agenda_item(
            topic,
            proposed_item_number,
            agenda_items,
        )

        if agenda_item:
            item_number = agenda_item[
                "item_number"
            ]
            agenda_section = agenda_item[
                "section"
            ]
            agenda_title = agenda_item[
                "title"
            ]
        else:
            item_number = ""
            agenda_section = ""
            agenda_title = ""

        status = _action_norm(
            item.get("action_status")
        )

        source_name = _action_norm(
            item.get("evidence_source")
        )

        quote = str(
            item.get("evidence_quote") or ""
        ).strip()

        source_text = (
            notes
            if source_name == "notes"
            else agenda
            if source_name == "agenda"
            else ""
        )

        quote_valid = _quote_is_in_source(
            quote,
            source_text,
        )

        if (
            quote_valid
            and source_name == "notes"
            and not _action_evidence_quote_is_bounded(
                quote,
                source_text,
            )
        ):
            quote_valid = False

        # Preserve the model's original exact formal evidence
        # before any full-topic repair/downgrade occurs.
        #
        # A later deterministic step may use this only to recover
        # one narrower component-specific formal action.
        original_status = status
        original_source_name = source_name
        original_quote = quote
        original_quote_valid = quote_valid

        if (
            original_quote_valid
            and original_status
            in ACTION_FORMAL_STATUSES
            and _single_line_transcript_turn_windows(
                notes
            )
            and not _turn_window_formal_finality_supported(
                topic,
                original_status,
                original_quote,
                agenda_title=agenda_title,
            )
        ):
            original_quote_valid = False

        supplemental_component_action = None

        if (
            original_quote_valid
            and original_status
            in ACTION_FORMAL_STATUSES
        ):
            supplemental_component_action = (
                _supplemental_component_formal_action(
                    topic,
                    original_status,
                    original_quote,
                    original_source_name,
                    agenda_items,
                )
            )

        # --------------------------------------------------
        # DETERMINISTIC CONSENT-CALENDAR ACTION RECOVERY
        # --------------------------------------------------
        #
        # A recording summary may describe one motion covering
        # several Consent Calendar items rather than repeating
        # each item's title in the vote language.
        #
        # Use that vote ONLY when:
        #   1. the official agenda maps this topic to Consent;
        #   2. the source Consent block explicitly identifies
        #      this exact item (or a unique leaf shorthand);
        #   3. the block contains approval + result language.
        #
        # This deliberately cannot validate a Public Hearing or
        # New Business item from a generic Consent vote.

        consent_action_quote = None

        if (
            agenda_item
            and agenda_section
            == "CONSENT CALENDAR"
        ):
            consent_action_quote = (
                _best_supported_consent_action_quote(
                    item_number,
                    agenda_items,
                    notes,
                )
            )

            if (
                consent_action_quote
                and not _action_evidence_quote_is_bounded(
                    consent_action_quote,
                    notes,
                )
            ):
                consent_action_quote = None

        if consent_action_quote:
            status = "approved"
            source_name = "notes"
            source_text = notes
            quote = consent_action_quote

            quote_valid = (
                _quote_is_in_source(
                    quote,
                    source_text,
                )
            )

        # --------------------------------------------------
        # PUBLIC-COMMENT QUOTE REPAIR
        # --------------------------------------------------
        #
        # A model may correctly identify a resident comment but
        # paraphrase the source rather than copy it verbatim.
        # Recover an exact topic-matched note excerpt instead of
        # trusting the paraphrase.

        if (
            not quote_valid
            and status
            in {
                "resident comment",
                "public comment",
                "speaker comment",
            }
        ):
            if _single_line_transcript_turn_windows(
                notes
            ):
                repaired_comment = (
                    _best_supported_local_public_comment_quote(
                        topic,
                        notes,
                    )
                )
            else:
                repaired_comment = (
                    _best_supported_public_comment_quote(
                        topic,
                        notes,
                    )
                )

            if repaired_comment:
                source_name = "notes"
                source_text = notes
                quote = repaired_comment

                quote_valid = (
                    _quote_is_in_source(
                        quote,
                        source_text,
                    )
                )

        # --------------------------------------------------
        # FORMAL ACTION SOURCE-BLOCK REPAIR
        # --------------------------------------------------
        #
        # A model may return only a generic motion/vote sentence
        # even when the immediately surrounding exact source
        # block supplies the topic.
        #
        # Recover that exact topic + action block before deciding
        # that a formal action lacks topical support.
        #
        # Consent Calendar collective votes remain governed by
        # the stricter Consent-specific path above.

        if (
            status
            in ACTION_FORMAL_STATUSES
            and not consent_action_quote
        ):
            current_formal_supported = (
                quote_valid
                and _agenda_identity_supported_for_source(
                    topic,
                    agenda_title,
                    quote,
                    notes,
                )
                and _formal_action_has_topic_support(
                    topic,
                    item_number,
                    quote,
                    status,
                )
                and (
                    not _single_line_transcript_turn_windows(
                        notes
                    )
                    or _turn_window_formal_finality_supported(
                        topic,
                        status,
                        quote,
                        agenda_title=agenda_title,
                    )
                )
            )

            if not current_formal_supported:
                repaired_formal_quote = (
                    _best_supported_formal_action_quote(
                        topic,
                        status,
                        notes,
                            agenda_title=agenda_title,
                    )
                )

                if repaired_formal_quote:
                    source_name = "notes"
                    source_text = notes
                    quote = repaired_formal_quote

                    quote_valid = (
                        _quote_is_in_source(
                            quote,
                            source_text,
                        )
                    )

                elif status != "passed":
                    passed_motion_quote = (
                        _best_supported_formal_action_quote(
                            topic,
                            "passed",
                            notes,
                            agenda_title=agenda_title,
                        )
                    )

                    if passed_motion_quote:
                        source_name = "notes"
                        source_text = notes
                        quote = passed_motion_quote

                        quote_valid = (
                            _quote_is_in_source(
                                quote,
                                source_text,
                            )
                        )

                        status = (
                            _canonical_formal_status_from_quote(
                                "passed",
                                quote,
                            )
                        )

        # Prefer a precise action embodied in a passed motion
        # over the generic result word "passed".
        #
        # This runs only after deterministic source repair so the
        # canonical status is derived from the exact evidence
        # excerpt that will be validated.
        if (
            status
            in ACTION_FORMAL_STATUSES
            and quote_valid
        ):
            status = (
                _canonical_formal_status_from_quote(
                    status,
                    quote,
                )
            )

        # For a topic that has been deterministically mapped to
        # an official agenda item, the evidence quote must also
        # actually describe that topic.
        #
        # This prevents a generic Consent Calendar vote or some
        # unrelated nearby action from becoming evidence for a
        # separate Public Hearing or New Business item.
        if (
            agenda_item
            and quote_valid
            and not consent_action_quote
        ):
            topic_supported = (
                _topic_scope_supported(
                    topic,
                    quote,
                )
                and _agenda_identity_supported_for_source(
                    topic,
                    agenda_title,
                    quote,
                    notes,
                )
            )

            if not topic_supported:
                repaired = (
                    _best_supported_nonformal_quote(
                        topic,
                        notes,
                            agenda_title=agenda_title,
                            agenda_item_number=item_number,
                    )
                )

                if repaired:
                    status = repaired[
                        "action_status"
                    ]

                    quote = repaired[
                        "evidence_quote"
                    ]

                    source_name = "notes"
                    source_text = notes

                    quote_valid = (
                        _quote_is_in_source(
                            quote,
                            source_text,
                        )
                    )

                else:
                    # Fail closed. The supplied evidence does
                    # not support this agenda-mapped topic.
                    quote_valid = False

        # --------------------------------------------------
        # GENERIC NONFORMAL EXACT-QUOTE REPAIR
        # --------------------------------------------------
        #
        # If a nonformal model record is unclear or its quote is
        # invalid, recover a bounded exact discussion/update excerpt.
        #
        # This never creates a formal action.

        # A source-verbatim quote is not automatically evidence of
        # meeting treatment. An agenda-title-only excerpt such as:
        #
        #   "The title of item number two is ... project update."
        #
        # does not by itself prove DISCUSSED.
        #
        # Mark it invalid here so the existing bounded nonformal
        # repair below can recover an actual presentation /
        # discussion excerpt.
        if (
            raw_transcript
            and status
            in {
                "discussed",
                "considered",
            }
            and quote_valid
            and (
                _nonformal_status_from_quote(
                    topic,
                    quote,
                    require_local_identity=True,
                )
                != status
            )
            and not (
                status
                == "discussed"
                and _raw_council_commentary_supported(
                    topic,
                    quote,
                )
            )
        ):
            quote_valid = False

        if (
            status not in ACTION_FORMAL_STATUSES
            and (
                not quote_valid
                or status == "unclear"
            )
        ):
            repaired_nonformal = (
                _best_supported_nonformal_quote(
                    topic,
                    notes,
                            agenda_title=agenda_title,
                            agenda_item_number=item_number,
                )
            )

            if repaired_nonformal:
                status = repaired_nonformal[
                    "action_status"
                ]

                quote = repaired_nonformal[
                    "evidence_quote"
                ]

                source_name = "notes"
                source_text = notes

                quote_valid = (
                    _quote_is_in_source(
                        quote,
                        source_text,
                    )
                )

        # Canonical staff-follow-up evidence:
        #
        # If the model returned a generic nonformal status such
        # as "discussed", but the recording-derived notes contain
        # explicit topic-matched evidence that staff follow-up
        # was requested, preserve that more precise action.
        if status not in ACTION_FORMAL_STATUSES:
            if _single_line_transcript_turn_windows(
                notes
            ):
                followup_quote = (
                    _best_supported_local_staff_followup_quote(
                        topic,
                        notes,
                        agenda_title=agenda_title,
                    )
                )
            else:
                followup_quote = (
                    _best_supported_staff_followup_quote(
                        topic,
                        notes,
                    )
                )

            if followup_quote:
                status = (
                    "requested staff follow-up"
                )

                source_name = "notes"
                source_text = notes
                quote = followup_quote

                quote_valid = (
                    _quote_is_in_source(
                        quote,
                        source_text,
                    )
                )

        raw_transcript = bool(
            _single_line_transcript_turn_windows(
                notes
            )
        )

        # --------------------------------------------------
        # RAW NON-AGENDA TOPIC CANONICALIZATION
        # --------------------------------------------------
        #
        # For public-comment / other non-agenda topics, do not
        # preserve a model-selected nonformal status merely because
        # its quotation happens to exist somewhere in the transcript.
        #
        # Derive the strongest supported local treatment instead.
        if (
            raw_transcript
            and not agenda_item
            and status
            not in ACTION_FORMAL_STATUSES
        ):
            local_followup = (
                _best_supported_local_staff_followup_quote(
                    topic,
                    notes,
                    agenda_title=agenda_title,
                )
            )

            council_commentary = (
                _best_supported_raw_council_commentary_quote(
                    topic,
                    notes,
                )
            )

            explicit_nonformal = (
                _best_supported_nonformal_quote(
                    topic,
                    notes,
                    agenda_title=agenda_title,
                    agenda_item_number=item_number,
                )
            )

            local_comment = (
                _best_supported_local_public_comment_quote(
                    topic,
                    notes,
                )
            )

            if local_followup:
                status = (
                    "requested staff follow-up"
                )
                quote = local_followup
                source_name = "notes"
                source_text = notes

            elif council_commentary:
                status = "discussed"
                quote = council_commentary
                source_name = "notes"
                source_text = notes

            elif explicit_nonformal:
                status = explicit_nonformal[
                    "action_status"
                ]
                quote = explicit_nonformal[
                    "evidence_quote"
                ]
                source_name = "notes"
                source_text = notes

            elif local_comment:
                status = "no council action"
                quote = local_comment
                source_name = "notes"
                source_text = notes

            quote_valid = (
                _quote_is_in_source(
                    quote,
                    source_text,
                )
                and (
                    source_name != "notes"
                    or _action_evidence_quote_is_bounded(
                        quote,
                        notes,
                    )
                )
            )

        formal = (
            status
            in ACTION_FORMAL_STATUSES
        )

        formal_valid = True

        if formal:
            # An agenda listing alone never proves a final
            # formal council disposition.
            #
            # A source-identified Consent Calendar approval block
            # is different: the recording notes establish the
            # collective vote, and the official agenda supplies
            # the deterministic item identity/section mapping.
            if consent_action_quote:
                formal_valid = (
                    source_name == "notes"
                    and quote_valid
                )

            else:
                formal_valid = (
                    source_name == "notes"
                    and quote_valid
                    and _agenda_identity_supported_for_source(
                        topic,
                        agenda_title,
                        quote,
                        notes,
                    )
                    and _formal_action_has_topic_support(
                        topic,
                        item_number,
                        quote,
                        status,
                    )
                    and not (
                        _conflicted_generic_collective_formal_action(
                            item_number,
                            agenda_section,
                            status,
                            quote,
                            agenda_items,
                        )
                    )
                )

        nonformal_valid = True

        if (
            not formal
            and raw_transcript
        ):
            if status in {
                "discussed",
                "considered",
            }:
                classified_nonformal = (
                    _nonformal_status_from_quote(
                        topic,
                        quote,
                        require_local_identity=True,
                    )
                )

                nonformal_valid = (
                    classified_nonformal
                    == status
                    or (
                        status == "discussed"
                        and _raw_council_commentary_supported(
                            topic,
                            quote,
                        )
                    )
                )

            elif status == "requested staff follow-up":
                expected_followup = (
                    _best_supported_local_staff_followup_quote(
                        topic,
                        notes,
                        agenda_title=agenda_title,
                    )
                )

                nonformal_valid = bool(
                    expected_followup
                    and _evidence_text_norm(
                        expected_followup
                    )
                    == _evidence_text_norm(
                        quote
                    )
                )

            elif status in {
                "resident comment",
                "public comment",
                "speaker comment",
                "no council action",
            }:
                expected_comment = (
                    _best_supported_local_public_comment_quote(
                        topic,
                        notes,
                    )
                )

                nonformal_valid = bool(
                    expected_comment
                    and _evidence_text_norm(
                        expected_comment
                    )
                    == _evidence_text_norm(
                        quote
                    )
                )

        validated = (
            quote_valid
            and formal_valid
            and nonformal_valid
        )

        validation_note = ""

        if not quote_valid:
            validation_note = (
                "Evidence quote was not found verbatim "
                "in the claimed source."
            )

        elif formal and not formal_valid:
            validation_note = (
                "Formal action lacked sufficiently specific "
                "topic/item-linked action evidence."
            )

        elif not formal and not nonformal_valid:
            validation_note = (
                "Nonformal status lacked sufficiently specific "
                "topic-local treatment evidence."
            )

        if formal and not validated:
            quote_norm = _action_norm(
                quote
            )

            fallback_status = ""
            fallback_quote = ""

            # Preserve a lower-strength action only when the
            # exact excerpt supports the FULL topic scope.
            if (
                quote_valid
                and _topic_scope_supported(
                    topic,
                    quote,
                )
                and re.search(
                    r"\b("
                    r"discussion|"
                    r"discussed|"
                    r"discussing"
                    r")\b",
                    quote_norm,
                )
            ):
                fallback_status = (
                    "discussed"
                )

                fallback_quote = quote

            elif (
                quote_valid
                and _topic_scope_supported(
                    topic,
                    quote,
                )
                and re.search(
                    r"\b("
                    r"considered|"
                    r"consideration|"
                    r"considering"
                    r")\b",
                    quote_norm,
                )
            ):
                fallback_status = (
                    "considered"
                )

                fallback_quote = quote

            else:
                repaired_nonformal = (
                    _best_supported_nonformal_quote(
                        topic,
                        notes,
                            agenda_title=agenda_title,
                            agenda_item_number=item_number,
                    )
                )

                if repaired_nonformal:
                    fallback_status = (
                        repaired_nonformal[
                            "action_status"
                        ]
                    )

                    fallback_quote = (
                        repaired_nonformal[
                            "evidence_quote"
                        ]
                    )

            if (
                fallback_status
                and fallback_quote
            ):
                status = (
                    fallback_status
                )

                quote = (
                    fallback_quote
                )

                source_name = "notes"
                source_text = notes

                quote_valid = (
                    _quote_is_in_source(
                        quote,
                        source_text,
                    )
                )

                validated = (
                    quote_valid
                )

                validation_note = (
                    "Formal action was not validated for the "
                    "full topic scope; exact source evidence "
                    "supports "
                    + status
                    + "."
                )

            else:
                status = "unclear"

        if (
            not validated
            and status
            in {
                "discussed",
                "considered",
            }
        ):
            status = "unclear"

        evidence_item_numbers = (
            _evidence_agenda_item_numbers(
                quote
            )
        )

        # Determine whether explicit source item numbers
        # contradict the official agenda mapping.
        #
        # Some systems use a hierarchical official number such
        # as 5.6 while recording-derived notes use the unique
        # Consent Calendar leaf shorthand "Item 6".
        #
        # That shorthand is NOT a conflict when the official
        # agenda deterministically establishes that 6 uniquely
        # means Consent Calendar item 5.6.
        #
        # This exception applies only to official Consent
        # Calendar items. A Public Hearing item 21 paired with
        # source evidence for items 17/18 remains a conflict.

        evidence_matches_item = bool(
            item_number
            and item_number
            in evidence_item_numbers
        )

        if (
            not evidence_matches_item
            and item_number
            and agenda_section
            == "CONSENT CALENDAR"
            and evidence_item_numbers
        ):
            consent_labels = (
                _consent_item_source_labels(
                    item_number,
                    agenda_items,
                )
            )

            evidence_matches_item = bool(
                consent_labels
                & evidence_item_numbers
            )

        agenda_linkage_conflict = bool(
            item_number
            and evidence_item_numbers
            and not evidence_matches_item
        )

        if agenda_linkage_conflict:
            conflict_note = (
                "Source evidence explicitly references agenda "
                "item(s) "
                + ", ".join(
                    sorted(
                        evidence_item_numbers,
                        key=_agenda_item_sort_key,
                    )
                )
                + " while the official agenda maps this topic "
                "to item "
                + item_number
                + ". Topic/action evidence may still be used, "
                "but agenda-section timing must not be inferred."
            )

            validation_note = (
                (
                    validation_note.rstrip()
                    + " "
                )
                if validation_note
                else ""
            ) + conflict_note

        cleaned.append(
            {
                "topic": topic,
                "item_number": item_number,
                "agenda_section":
                    agenda_section,
                "agenda_linkage_conflict":
                    agenda_linkage_conflict,
                "evidence_item_numbers":
                    sorted(
                        evidence_item_numbers,
                        key=_agenda_item_sort_key,
                    ),
                "agenda_title":
                    agenda_title,
                "action_status": status,
                "evidence_source":
                    source_name,
                "evidence_quote": quote,
                "validated": validated,
                "validation_note":
                    validation_note,
            }
        )

        # Add the narrower formal action only when the final
        # parent record did NOT itself retain a validated formal
        # action for the full topic.
        if (
            supplemental_component_action
            and not (
                validated
                and status
                in ACTION_FORMAL_STATUSES
            )
        ):
            supplemental_actions.append(
                supplemental_component_action
            )

    # ------------------------------------------------------
    # DEDUPLICATE + APPEND SUPPLEMENTAL COMPONENT ACTIONS
    # ------------------------------------------------------

    for supplemental in supplemental_actions:
        duplicate = False

        for existing in cleaned:
            same_topic = (
                _action_norm(
                    existing.get(
                        "topic"
                    )
                )
                == _action_norm(
                    supplemental.get(
                        "topic"
                    )
                )
            )

            same_item_action = (
                supplemental.get(
                    "item_number"
                )
                and existing.get(
                    "item_number"
                )
                == supplemental.get(
                    "item_number"
                )
                and _action_norm(
                    existing.get(
                        "action_status"
                    )
                )
                == _action_norm(
                    supplemental.get(
                        "action_status"
                    )
                )
            )

            if (
                existing.get(
                    "validated"
                )
                is True
                and (
                    same_topic
                    or same_item_action
                )
            ):
                duplicate = True
                break

        if not duplicate:
            cleaned.append(
                supplemental
            )

    return cleaned


def retry_api_call(label, fn, max_attempts=4):
    delay = 20

    for attempt in range(max_attempts):
        try:
            return fn()

        except Exception as exc:
            text = str(exc)

            retryable = (
                "429" in text
                or "RESOURCE_EXHAUSTED" in text
                or "503" in text
                or "UNAVAILABLE" in text
                or "500" in text
                or "ServerError" in text
                or "internal error encountered"
                in text.lower()
            )

            if not retryable or attempt == max_attempts - 1:
                raise

            suggested = re.search(
                r"retry(?:ing)? in ([0-9.]+)s",
                text,
                re.I,
            )

            # If this is a quota 429 without any
            # explicit retry guidance, don't spend
            # several minutes repeatedly hammering it.
            if (
                "429" in text
                and not suggested
                and "retryDelay" not in text
            ):
                print()
                print(
                    f"{label}: Gemini quota exhausted."
                )
                print(
                    "No short retry interval was supplied; "
                    "deferring this meeting."
                )
                raise

            wait = delay

            if suggested:
                try:
                    wait = max(
                        wait,
                        int(float(suggested.group(1))) + 3,
                    )
                except Exception:
                    pass

            print()
            print(f"{label}: temporary Gemini limit.")
            print(f"Waiting {wait} seconds then retrying...")

            time.sleep(wait)
            delay = min(delay * 2, 120)


def _parse_json(text):
    text = (text or "").strip()

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?\s*",
            "",
            text,
            flags=re.I,
        )

        text = re.sub(
            r"\s*```$",
            "",
            text,
        )

    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start >= 0 and end > start:
        return json.loads(
            text[start:end + 1]
        )

    raise ValueError(
        "Gemini did not return parseable JSON."
    )


def _domains_for(meeting):
    domains = set(
        OFFICIAL_DOMAINS.get(
            meeting.get("city_slug", ""),
            set(),
        )
    )

    for key in (
        "source_url",
        "agenda_url",
    ):
        url = meeting.get(key) or ""

        try:
            host = (
                urlparse(url).hostname
                or ""
            ).lower()

            if host:
                domains.add(host)
        except Exception:
            pass

    return domains


def _host_allowed(host, domains):
    host = (host or "").lower()

    if not host:
        return False

    if host.endswith(".gov"):
        return True

    public_agencies = {
        "ocgov.com",
        "www.ocgov.com",
        "octa.net",
        "www.octa.net",
    }

    if host in public_agencies:
        return True

    for domain in domains:
        domain = domain.lower()

        if (
            host == domain
            or host.endswith("." + domain)
        ):
            return True

    return False


def _official_url_is_real(
    url,
    meeting,
    canonical,
    agenda,
    domains,
):
    if not url:
        return False

    # The meeting agenda itself is already an official
    # fetched source. If the canonical spelling appears
    # there, that is sufficient.
    agenda_url = meeting.get("agenda_url") or ""

    if (
        url.rstrip("/") == agenda_url.rstrip("/")
        and canonical
        and canonical.lower() in agenda.lower()
    ):
        return True

    try:
        parsed = urlparse(url)

        if not _host_allowed(
            parsed.hostname,
            domains,
        ):
            return False

        response = requests.get(
            url,
            timeout=12,
            allow_redirects=True,
            headers={
                "User-Agent":
                    "Mozilla/5.0 CouncilWatchVerifier/1.0"
            },
        )

        final_host = (
            urlparse(
                response.url
            ).hostname
            or ""
        )

        if not _host_allowed(
            final_host,
            domains,
        ):
            return False

        return (
            200 <= response.status_code < 400
        )

    except Exception:
        return False


def make_comprehensive_source_notes(
    audio_path,
    meeting,
):
    """
    Produce reporter-grade notes rather than a tiny
    meeting summary.
    """
    client = genai.Client()

    audio_path = Path(audio_path)

    print(
        "    uploading audio for comprehensive notes..."
    )

    uploaded = client.files.upload(
        file=str(audio_path)
    )

    for _ in range(120):
        current = client.files.get(
            name=uploaded.name
        )

        state = getattr(
            getattr(current, "state", None),
            "name",
            str(
                getattr(
                    current,
                    "state",
                    "",
                )
            ),
        )

        state = str(state).upper()

        if "ACTIVE" in state:
            uploaded = current
            break

        if "FAILED" in state:
            raise RuntimeError(
                "Gemini audio-file processing failed."
            )

        time.sleep(2)

    prompt = f"""
You are creating exhaustive reporter source notes from a
local-government meeting recording.

City: {meeting.get("city_name")}
Meeting: {meeting.get("meeting_date")}
Title: {meeting.get("title")}

DO NOT write a short summary.

Create comprehensive source notes that preserve enough
detail for a journalist to decide what is actually
newsworthy.

Cover, when present:

- every substantive agenda item
- motions and vote outcomes
- contracts and dollar amounts
- development and land-use matters
- ordinances and regulations
- policing and public safety
- surveillance technology
- automated license-plate recognition
- ALPR
- Flock Safety cameras
- license-plate-reader cameras
- privacy or data-retention discussion
- resident complaints and concerns
- meaningful public comment
- councilmember reports
- city manager reports
- transportation
- taxes, fees and service changes
- major infrastructure projects
- major consent-calendar expenditures
- discussions where NO vote was taken

TECHNOLOGY SEPARATION RULE:
- Never merge different technologies merely because they were
  discussed near each other.
- Treat speed-feedback signs, portable message boards,
  digital signage, ALPR/license-plate readers, Flock cameras,
  surveillance cameras and similar systems as distinct unless
  the recording explicitly establishes that they are the same.
- Attach staff direction, funding, data collection, retention,
  law-enforcement sharing and enforcement use ONLY to the
  specific technology the recording supports.
- If the recording is ambiguous about whether two technologies
  are the same, preserve that ambiguity instead of resolving it.

For discussion-only subjects, explicitly say:
DISCUSSION ONLY — NO COUNCIL ACTION CONFIRMED.

For proper names:
- preserve the spelling you can actually support from audio
- if uncertain, mark [PHONETIC/UNCERTAIN]
- never invent a surname because it sounds plausible

Do not omit an item merely because it did not result in
a vote.

Use clear headings and detailed bullet points.
Aim for comprehensive reporter notes, not prose journalism.
"""

    try:
        models = [
            TRANSCRIPT_MODEL,
            *[
                model
                for model in TRANSCRIPT_FALLBACK_MODELS
                if model != TRANSCRIPT_MODEL
            ],
        ]

        response = None
        last_exc = None

        for index, model in enumerate(models):
            print()
            print(
                "Comprehensive source notes: "
                f"trying model {model}"
            )

            try:
                response = retry_api_call(
                    f"Comprehensive source notes ({model})",
                    lambda model=model: client.models.generate_content(
                        model=model,
                        contents=[
                            uploaded,
                            prompt,
                        ],
                    ),
                )

                print(
                    "Comprehensive source notes: "
                    f"success with {model}"
                )
                break

            except Exception as exc:
                last_exc = exc
                message = str(exc).lower()

                temporary = (
                    "429" in message
                    or "503" in message
                    or "resource_exhausted" in message
                    or "unavailable" in message
                    or "high demand" in message
                    or "quota" in message
                )

                has_fallback = (
                    index < len(models) - 1
                )

                if not temporary or not has_fallback:
                    raise

                next_model = models[index + 1]

                print()
                print(
                    f"{model} unavailable; "
                    f"falling back to {next_model}."
                )

        if response is None:
            if last_exc:
                raise last_exc

            raise RuntimeError(
                "No transcript model produced a response."
            )

        notes = response.text or ""

        if len(notes.strip()) < 500:
            raise RuntimeError(
                "Comprehensive notes were unexpectedly short."
            )

        return notes

    finally:
        try:
            client.files.delete(
                name=uploaded.name
            )
        except Exception:
            pass


def _person_surname_token(text):
    """
    Return a conservative surname-like token for comparing an
    observed transcript name with an official canonical name.

    This is used only as a safety guard. False negatives are
    preferable to falsely identifying a resident or staff member.
    """
    value = str(text or "").casefold()

    # Split on whitespace first so O'Connor stays one logical token
    # before punctuation is removed.
    raw_tokens = value.split()

    ignored = {
        "mr", "mrs", "ms", "miss", "dr",
        "mayor", "vice", "pro", "tem",
        "councilmember", "council", "member",
        "chair", "chairman", "chairwoman",
        "sergeant", "sgt", "captain", "chief",
    }

    tokens = []

    for token in raw_tokens:
        cleaned = re.sub(
            r"[^a-z0-9]",
            "",
            token,
        )

        if not cleaned:
            continue

        if cleaned in ignored:
            continue

        tokens.append(cleaned)

    if not tokens:
        return ""

    return tokens[-1]


def _person_correction_plausible(observed, canonical):
    """
    A model may propose an official person whose name really exists,
    but that alone does not prove the transcript referred to them.

    Require the surname-like portion to be exact or strongly similar
    before allowing an observed -> canonical person correction.
    """
    from difflib import SequenceMatcher

    observed_name = _person_surname_token(observed)
    canonical_name = _person_surname_token(canonical)

    if not observed_name or not canonical_name:
        return False

    if observed_name == canonical_name:
        return True

    # Very short tokens are unsafe for fuzzy identity matching.
    if min(
        len(observed_name),
        len(canonical_name),
    ) < 4:
        return False

    similarity = SequenceMatcher(
        None,
        observed_name,
        canonical_name,
    ).ratio()

    return similarity >= 0.68


def _role_labeled_person_key(
    value,
):
    """
    Normalize a role-labeled elected-official reference for
    candidate de-duplication.

    Preserve given-name distinctions so two officials who share
    a surname do not collapse into one candidate.

    Examples:
      Council Member John Smith -> john smith
      Council Member Jane Smith -> jane smith
      Council Member Smith      -> smith
    """
    normalized = str(
        value or ""
    ).casefold()

    normalized = re.sub(
        r"[’']",
        "",
        normalized,
    )

    normalized = re.sub(
        r"[^a-z0-9\s-]",
        " ",
        normalized,
    )

    normalized = re.sub(
        r"\s+",
        " ",
        normalized,
    ).strip()

    role_prefix = re.compile(
        r"^(?:"
        r"council\s+member|"
        r"councilmember|"
        r"mayor\s+pro\s+tem|"
        r"vice\s+mayor|"
        r"mayor"
        r")\s+",
        re.I,
    )

    normalized = role_prefix.sub(
        "",
        normalized,
    ).strip()

    return normalized





def _role_labeled_person_candidates(
    notes,
):
    """
    Extract transcript forms that explicitly follow an elected-
    official role label.

    These are safer identity candidates than arbitrary names
    because the transcript itself establishes the person's role.

    This helper does NOT resolve identity. It only makes sure a
    role-labeled observed form cannot silently disappear from the
    verification pass.
    """
    pattern = re.compile(
        r"\b"
        r"(?:(?i:"
        r"council\s+member|"
        r"councilmember|"
        r"mayor\s+pro\s+tem|"
        r"vice\s+mayor|"
        # Do not let bare "Mayor" consume malformed ASR forms
        # such as "Mayor Pro Tim" as though "Pro Tim" were a name.
        r"mayor(?!\s+pro\b)"
        r"))"
        r"\s+"
        r"("
        # Deliberately exclude periods from name tokens.
        # This prevents:
        #
        #   Council Member Otto. That's...
        #
        # from becoming the fake person "Otto. That's".
        r"[A-Z][A-Za-z'’-]*"
        r"(?:\s+[A-Z][A-Za-z'’-]*)?"
        r")"
    )

    generic_surnames = {
        "action",
        "actions",
        "and",
        "comment",
        "comments",
        "meeting",
        "member",
        "members",
        "report",
        "reports",
    }

    found = []
    seen = set()

    for match in pattern.finditer(
        str(
            notes or ""
        )
    ):
        full = re.sub(
            r"\s+",
            " ",
            match.group(0).strip(),
        )

        surname = _person_surname_token(
            full
        )

        if (
            not surname
            or surname
            in generic_surnames
        ):
            continue

        key = _role_labeled_person_key(
            full
        )

        if not key:
            continue

        if key in seen:
            continue

        seen.add(
            key
        )

        found.append(
            full
        )

    return found



def _person_soundex(
    value,
):
    """
    Conservative phonetic key for surname-like tokens.

    Used only as one requirement in the role-labeled elected-
    official correction path. Never sufficient by itself.
    """
    token = _person_surname_token(
        value
    )

    token = re.sub(
        r"[^a-z]",
        "",
        token.casefold(),
    )

    if not token:
        return ""

    codes = {
        **{
            letter: "1"
            for letter in "bfpv"
        },
        **{
            letter: "2"
            for letter in "cgjkqsxz"
        },
        **{
            letter: "3"
            for letter in "dt"
        },
        "l": "4",
        **{
            letter: "5"
            for letter in "mn"
        },
        "r": "6",
    }

    result = [
        token[0].upper()
    ]

    previous = codes.get(
        token[0],
        "",
    )

    for char in token[1:]:
        if char in "aeiouy":
            previous = ""
            continue

        if char in "hw":
            continue

        code = codes.get(
            char,
            "",
        )

        if not code:
            previous = ""
            continue

        if code != previous:
            result.append(
                code
            )

        previous = code

    return (
        "".join(
            result
        )
        + "000"
    )[:4]



def _role_labeled_person_correction_plausible(
    observed,
    canonical,
    role_candidates,
):
    """
    Narrow fallback for elected officials explicitly identified
    by role in the transcript.

    This intentionally does NOT lower the normal person matching
    threshold.

    Requirements:
      - observed surname belongs to a role-labeled transcript form
      - same first letter
      - same surname length
      - at least four characters
      - no more than two character substitutions
      - identical Soundex key

    Exact canonical official-source support is still required later
    by verify_entities before CORRECTED can be accepted.
    """
    observed_name = _person_surname_token(
        observed
    )

    canonical_name = _person_surname_token(
        canonical
    )

    if (
        not observed_name
        or not canonical_name
    ):
        return False

    role_surnames = {
        _person_surname_token(
            candidate
        )
        for candidate in (
            role_candidates or []
        )
    }

    role_surnames.discard(
        ""
    )

    if observed_name not in role_surnames:
        return False

    if min(
        len(
            observed_name
        ),
        len(
            canonical_name
        ),
    ) < 4:
        return False

    if (
        len(
            observed_name
        )
        != len(
            canonical_name
        )
    ):
        return False

    if (
        observed_name[0]
        != canonical_name[0]
    ):
        return False

    differing_positions = sum(
        left != right
        for left, right
        in zip(
            observed_name,
            canonical_name,
        )
    )

    if differing_positions > 2:
        return False

    observed_soundex = _person_soundex(
        observed_name
    )

    canonical_soundex = _person_soundex(
        canonical_name
    )

    return bool(
        observed_soundex
        and observed_soundex
        == canonical_soundex
    )





def verify_entities(
    meeting,
    notes,
    agenda,
    preverified_entities=None,
):
    """
    Verify proper nouns using the official material
    CouncilWatch already retrieved.

    No Google Search is required.

    VERIFIED:
      exact/canonical form is supported by official
      agenda/source material.

    CORRECTED:
      source notes contain a phonetic/mistyped form and
      official agenda material clearly establishes the
      canonical spelling.

    UNVERIFIED:
      official material does not establish the identity
      strongly enough. Never guess or expand it.
    """

    preverified_entities = (
        preverified_entities or []
    )

    mandatory_role_people = (
        _role_labeled_person_candidates(
            notes
        )
    )

    registry_context = json.dumps(
        preverified_entities,
        ensure_ascii=False,
        indent=2,
    )

    extra_official = official_entity_material(
        meeting
    )

    official_context = (
        (agenda or "")
        + "\n\n"
        + extra_official.get("text", "")
    )

    prompt = f"""
You are CouncilWatch's proper-noun fact checker.

CITY:
{meeting.get("city_name")}

MEETING DATE:
{meeting.get("meeting_date")}

Your authoritative verification sources in this task are:
1. the official meeting agenda/source material
2. the official city roster/directory material supplied below
3. CouncilWatch's curated government entity registry supplied below.

The registry establishes canonical government/public-agency names
and aliases only. It does NOT establish what an agency did at the
meeting, a person's identity, or any other meeting-specific claim.

Entities in PREVERIFIED GOVERNMENT ENTITY REGISTRY have already
been resolved. Do not return duplicate rows for them unless
meeting-specific official material clearly contradicts them.

Do not use outside knowledge or infer identities from similarity.

The recording-derived notes may contain phonetic spellings,
automatic-transcription errors or incomplete names.

MANDATORY ROLE-LABELED PERSON COVERAGE:

The following forms were deterministically extracted because the
recording explicitly labels them as elected officials:

{json.dumps(mandatory_role_people, ensure_ascii=False, indent=2)}

Return one PERSON entity row for EVERY distinct person in this
list, even if the safest result is UNVERIFIED.

For these role-labeled forms, compare the observed surname against
the official meeting roll call and official city roster/directory.
Do not omit a role-labeled official merely because the spelling is
phonetic or uncertain.

Identify approximately 10-20 additional significant proper nouns
that could reasonably appear in a news article:

- elected officials
- city staff
- public speakers when identifiable
- streets
- neighborhoods
- parks/facilities
- agencies
- contractors
- organizations
- named programs
- projects
- technologies
- government bodies

ENTITY TYPE RULE:
Named roads, streets, parkways, highways, avenues, drives,
lanes, ways and other roadway names MUST use entity_type
"street", not "place".

STRICT RULES:

1. VERIFIED means the canonical spelling/identity is
   explicitly supported by the official agenda/source text.

2. CORRECTED means the source notes use a different or
   phonetic form and the official agenda/source text clearly
   establishes the canonical form.

3. UNVERIFIED means the official source does NOT clearly
   establish the identity.

4. NEVER invent or expand a person's name.

Example:
notes: "Marie"
agenda does not identify her
=> canonical_text: "Marie"
=> status: UNVERIFIED

5. Do not identify a resident merely because a similar name
   appears elsewhere in the material.

6. A name appearing only in recording-derived notes is NOT
   VERIFIED unless an official source confirms it.

6A. BEFORE returning a person's name as UNVERIFIED, compare
    the observed spelling against names explicitly present in
    the meeting roll call, agenda, staff list and supplied
    official city roster/directory.

    If the observed form is an obvious transcription or
    phonetic variant AND the meeting context strongly supports
    one official identity, return CORRECTED with the exact
    official spelling.

    Example:
    notes: "Camulia"
    agenda roll call: "Camuglia"
    same commissioner/meeting context
    => CORRECTED: Camulia -> Camuglia

    Do NOT use fuzzy similarity alone to identify an otherwise
    unidentified resident or public speaker.

6B. The fact that a real official appears in the city directory
    is NEVER enough by itself to replace an unrelated observed
    name. The observed name must itself be a plausible phonetic,
    transcription, title, or spelling variant of the official
    name. For example, "Mr. Ordona" must NOT become
    "Kevin O'Connor" merely because Kevin O'Connor is a relevant
    city employee.

7. For VERIFIED/CORRECTED entities, explain briefly where
   the official material supports the spelling.

8. official_source_url should identify the official source
   that actually supports the verification.

9. For historical office/title claims such as Mayor,
   Mayor Pro Tem, Councilmember, or staff title, the
   MEETING-DATE agenda/source is authoritative. A current
   city roster may confirm spelling or identity but must
   NEVER override the role/title shown for the meeting date.

Return ONLY JSON:

{{
  "entities": [
    {{
      "observed_text": "form found in notes",
      "canonical_text": "safest supported form",
      "entity_type": "person|street|place|organization|program|project|business|government_body|technology|other",
      "status": "VERIFIED|CORRECTED|UNVERIFIED",
      "confidence": "high|medium|low",
      "evidence": "brief explanation",
      "official_source_url": "official agenda URL or empty"
    }}
  ]
}}

================ PREVERIFIED GOVERNMENT ENTITY REGISTRY ================

{registry_context}

================ RECORDING-DERIVED NOTES ================

{notes[:65000]}

================ OFFICIAL AGENDA / SOURCE ================

{agenda[:60000]}

================ OFFICIAL CITY ROSTER / DIRECTORY ================

{extra_official.get("text", "")[:50000]}
"""

    client = genai.Client()

    response = retry_api_call(
        "Official-source entity verification",
        lambda: client.models.generate_content(
            model=STORY_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=5000,
            ),
        ),
    )

    data = _parse_json(
        response.text
    )

    agenda_lower = (
        agenda or ""
    ).lower()

    agenda_url = (
        meeting.get("agenda_url")
        or meeting.get("source_url")
        or ""
    )

    cleaned = []

    for entity in data.get(
        "entities",
        [],
    ):
        if not isinstance(entity, dict):
            continue

        observed = str(
            entity.get(
                "observed_text",
                "",
            )
        ).strip()

        canonical = str(
            entity.get(
                "canonical_text",
                "",
            )
        ).strip()

        if not observed and not canonical:
            continue

        if not observed:
            observed = canonical

        if not canonical:
            canonical = observed

        status = str(
            entity.get(
                "status",
                "UNVERIFIED",
            )
        ).strip().upper()

        confidence = str(
            entity.get(
                "confidence",
                "low",
            )
        ).strip().lower()

        evidence = str(
            entity.get(
                "evidence",
                "",
            )
        ).strip()

        entity_type = str(
            entity.get(
                "entity_type",
                "other",
            )
        ).strip().lower()

        if status not in {
            "VERIFIED",
            "CORRECTED",
            "UNVERIFIED",
        }:
            status = "UNVERIFIED"

        # PERSON IDENTITY SAFETY GUARD:
        #
        # The existence of a proposed canonical person in an
        # official directory does NOT prove that an unrelated
        # phonetic transcript name refers to that person.
        #
        # Reject implausible person-name substitutions before
        # official-source promotion. This intentionally favors
        # false negatives over false identification.
        if (
            entity_type == "person"
            and canonical.casefold() != observed.casefold()
            and not (
                _person_correction_plausible(
                    observed,
                    canonical,
                )
                or _role_labeled_person_correction_plausible(
                    observed,
                    canonical,
                    mandatory_role_people,
                )
            )
        ):
            proposed = canonical

            canonical = observed
            status = "UNVERIFIED"
            confidence = "low"
            evidence = (
                "CouncilWatch rejected the proposed identity "
                f"{proposed!r} because the observed transcript "
                "name was not sufficiently similar. An official "
                "person's existence alone is not enough to "
                "identify the speaker."
            )

        # Orange County GIS is authoritative for canonical
        # street spelling. Only apply it when Gemini identified
        # the candidate as a place AND the observed form looks
        # structurally like a street. This prevents arbitrary
        # places from fuzzy-matching unrelated road names.
        if entity_type in {"street", "place"}:
            from street_registry import (
                looks_like_street,
                match_street,
                street_name_part,
                source_url as street_source_url,
            )

            if looks_like_street(observed):
                street_candidate = street_name_part(
                    observed
                )

                street_match = match_street(
                    street_candidate
                )

                if street_match:
                    canonical = street_match[
                        "canonical_text"
                    ]
                    status = street_match["status"]
                    confidence = street_match[
                        "confidence"
                    ]

                    match_type = street_match.get(
                        "match_type",
                        "registry",
                    )

                    if status == "CORRECTED":
                        evidence = (
                            "Orange County GIS street "
                            "centerline registry resolved the "
                            f"recording-derived form to "
                            f"{canonical} "
                            f"({match_type})."
                        )
                    else:
                        evidence = (
                            "Exact canonical street name "
                            "confirmed by the Orange County "
                            "GIS street centerline registry."
                        )

                    cleaned.append(
                        {
                            "observed_text": observed,
                            "canonical_text": canonical,
                            "entity_type": "street",
                            "status": status,
                            "confidence": confidence,
                            "evidence": evidence,
                            "official_source_url":
                                street_source_url(),
                            "verification_source":
                                "orange_county_street_registry",
                        }
                    )

                    continue

        # Deterministic safety guard:
        # VERIFIED/CORRECTED must actually have the
        # canonical text present in official material.
        #
        # This intentionally favors false negatives over
        # falsely identifying a person/place.
        source = find_official_support(
            canonical,
            agenda,
            agenda_url,
            extra_official.get("pages", []),
        )

        # Deterministic promotion:
        # if the exact proposed name appears in an official
        # source, accept it even if the model was conservative.
        if source:
            if status == "UNVERIFIED":
                if canonical.casefold() == observed.casefold():
                    status = "VERIFIED"
                else:
                    status = "CORRECTED"

                confidence = "high"
                evidence = (
                    "Exact canonical form found in official "
                    "meeting or city roster/directory material."
                )

        else:
            # The observed form itself may already be the exact
            # official name even if the model proposed something else.
            observed_source = find_official_support(
                observed,
                agenda,
                agenda_url,
                extra_official.get("pages", []),
            )

            if observed_source:
                canonical = observed
                source = observed_source
                status = "VERIFIED"
                confidence = "high"
                evidence = (
                    "Exact observed form found in official "
                    "meeting or city roster/directory material."
                )

            elif status in {
                "VERIFIED",
                "CORRECTED",
            }:
                status = "UNVERIFIED"
                canonical = observed
                confidence = "low"

                evidence = (
                    "Neither the official meeting material "
                    "nor the configured official city "
                    "roster/directory contained the proposed "
                    "canonical form exactly, so CouncilWatch "
                    "did not accept the identification."
                )

        cleaned.append(
            {
                "observed_text": observed,
                "canonical_text": canonical,
                "entity_type": entity_type,
                "status": status,
                "confidence": confidence,
                "evidence": evidence,
                "official_source_url": source,
            }
        )

    # A role-labeled elected official must never silently
    # disappear because the model chose different proper nouns.
    #
    # Missing mandatory candidates are retained as UNVERIFIED,
    # which keeps the publication whitelist fail-closed.
    represented_role_people = {
        _role_labeled_person_key(
            entity.get(
                "observed_text",
                ""
            )
        )
        for entity in cleaned
        if entity.get(
            "entity_type"
        )
        == "person"
    }

    represented_role_people.discard(
        ""
    )

    for candidate in mandatory_role_people:
        candidate_key = (
            _role_labeled_person_key(
                candidate
            )
        )

        if (
            not candidate_key
            or candidate_key
            in represented_role_people
        ):
            continue

        cleaned.append(
            {
                "observed_text":
                    candidate,

                "canonical_text":
                    candidate,

                "entity_type":
                    "person",

                "status":
                    "UNVERIFIED",

                "confidence":
                    "low",

                "evidence":
                    (
                        "The recording explicitly identified this "
                        "speaker by elected-official role, but the "
                        "secondary verifier omitted the candidate. "
                        "CouncilWatch retained it as UNVERIFIED "
                        "rather than silently dropping it."
                    ),

                "official_source_url":
                    "",
            }
        )

        represented_role_people.add(
            candidate_key
        )

    return cleaned


def build_coverage_plan(
    meeting,
    notes,
    agenda,
    entities,
):
    entity_context = json.dumps(
        entities,
        ensure_ascii=False,
        indent=2,
    )

    prompt = f"""
You are the assigning editor for a serious local-news
publication.

Read the ENTIRE meeting material and rank the substantive
stories/topics.

City: {meeting.get("city_name")}
Meeting: {meeting.get("meeting_date")}

Do NOT merely select the cleanest vote.

Score higher when justified for:

- large expenditures/contracts
- public safety
- policing
- automated license-plate readers
- ALPR
- Flock cameras
- surveillance/privacy/data retention
- development and land use
- regulations affecting residents/businesses
- taxes and fees
- elections, appointments and representation
- cancellation of elections or ballot contests
- actions that determine who holds public office
- transportation
- infrastructure
- controversial public comment
- major service changes

A topic can be important even when it was DISCUSSED ONLY.
Never convert discussion into an approval.

ACTION-STATUS EVIDENCE RULE:
Mark an item Approved, Adopted, Authorized, Awarded or Directed
ONLY when the supplied evidence explicitly connects THAT SAME
ITEM to the motion, vote or council action.

A generic statement such as "the Consent Calendar was approved"
does NOT establish approval of:
- a public-hearing item
- a new-business item
- another separately numbered agenda item
- an item whose placement in the consent calendar is not
  explicitly established by the supplied evidence

Do not infer an item's action status merely because another
vote occurred nearby in the notes.

If the evidence describes discussion or consideration but does
not clearly record the item's final action, use Discussed or
Considered rather than guessing that it passed.

When comparing topics, give substantial weight to FORMAL,
BINDING government action. Actions affecting elections,
representation, officeholders, taxes, land use, public safety,
or large public expenditures usually outrank discussion-only
public comment when their community impact is broader or more
lasting.

A petition or public-comment discussion may still rank highly,
but do not place it above a consequential binding council action
merely because the discussion was lengthy or emotionally vivid.

Closed-session personnel evaluations with no reportable
action usually score 1-2 and should not lead over
substantive public business.

Ceremonial recognitions usually score 1-2.

Consent-calendar items may still be highly newsworthy,
especially when substantial money or public impact is
involved.

If ALPR, license-plate readers, Flock Safety or similar
camera technology appears ANYWHERE in the notes, it must
receive its own coverage item.

Do NOT merge separate technologies or separate council actions
into one coverage item merely because they appeared in the same
discussion. In particular, distinguish speed-feedback/digital
signage from ALPR/Flock unless the source clearly establishes
they are the same technology or part of the same proposal.

The summary and why_it_matters fields must NOT introduce new
facts or consequences. They are editorial ranking aids only.

Do not say an action:
- "sets a precedent"
- "signals a commitment"
- "represents a formal shift"
- "ensures" a future result
- establishes a new standard

unless that consequence is explicitly supported by the notes
or official agenda.

Choose approximately 4-7 substantive topics.

Set must_include=true for roughly the top 3-5 items
that a useful local story should not omit.

================ VERIFIED ENTITY DATA ================

{entity_context}

================ SOURCE NOTES ================

{notes[:65000]}

================ OFFICIAL AGENDA ================

{agenda[:45000]}
"""

    client = genai.Client()

    response = retry_api_call(
        "Whole-meeting coverage planning",
        lambda: client.models.generate_content(
            model=STORY_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CoveragePlan,
                temperature=0.1,
            ),
        ),
    )

    parsed = getattr(
        response,
        "parsed",
        None,
    )

    if isinstance(
        parsed,
        CoveragePlan,
    ):
        plan = parsed
    else:
        plan = CoveragePlan.model_validate_json(
            response.text
        )

    return plan.model_dump()


def build_meeting_intelligence(
    meeting,
    notes,
    agenda,
):
    from entity_registry import (
        find_entities_in_text,
        normalize as normalize_registry_text,
    )

    verification_warning = ""

    registry_entities = find_entities_in_text(
        (notes or "")
        + "\n\n"
        + (agenda or "")
    )

    if registry_entities:
        print(
            "Local entity registry verified "
            f"{len(registry_entities)} "
            "government/public-agency entities."
        )

    # Deterministic registry verification survives even if
    # secondary Gemini verification is unavailable.
    entities = list(registry_entities)

    try:
        secondary_entities = verify_entities(
            meeting,
            notes,
            agenda,
            preverified_entities=registry_entities,
        ) or []

        seen = {
            normalize_registry_text(
                entity.get("canonical_text")
                or entity.get("observed_text", "")
            )
            for entity in entities
        }

        for entity in secondary_entities:
            key = normalize_registry_text(
                entity.get("canonical_text")
                or entity.get("observed_text", "")
            )

            if key and key in seen:
                continue

            entities.append(entity)

            if key:
                seen.add(key)

    except Exception as exc:
        # Entity verification must NEVER prevent
        # CouncilWatch from analyzing the meeting.
        verification_warning = (
            "Secondary proper-noun verification was unavailable "
            "for this meeting. Locally verified government "
            "entities were retained. Do not expand other "
            "uncertain or phonetic names. "
            f"Reason: {type(exc).__name__}"
        )

        print()
        print(
            "WARNING: entity verification unavailable."
        )
        print(
            "Continuing with whole-meeting coverage "
            "planning."
        )

    coverage = build_coverage_plan(
        meeting,
        notes,
        agenda,
        entities,
    )

    try:
        action_ledger = build_action_ledger(
            meeting,
            notes,
            agenda,
            coverage_items=coverage.get(
                "items",
                [],
            ),
        )
    except Exception as exc:
        print()
        print(
            "ERROR: action ledger unavailable:",
            type(exc).__name__,
            exc,
        )
        print(
            "Action ledger is required for safe story "
            "generation; failing closed."
        )
        raise

    editorial = str(
        coverage.get(
            "editorial_summary",
            "",
        )
    ).strip()

    if verification_warning:
        if editorial:
            editorial = (
                verification_warning
                + "\n\n"
                + editorial
            )
        else:
            editorial = (
                verification_warning
            )

    return {
        "entities": entities,
        "action_ledger": action_ledger,
        "coverage_items":
            coverage.get(
                "items",
                [],
            ),
        "editorial_summary":
            editorial,
        "verification_warning":
            verification_warning,
    }


def audit_verification_context(intelligence):
    """
    Identity/spelling evidence for the final fact audit.

    Deliberately excludes the AI-generated coverage plan and
    editorial_summary so generated editorial interpretation can
    never become evidence for another model-generated claim.
    """
    lines = [
        "VERIFIED ENTITY CONTEXT:",
        "IDENTITY/SPELLING ONLY — NOT EVIDENCE OF MEETING ACTIONS,",
        "POLICY EFFECTS, TECHNICAL RELATIONSHIPS OR CONSEQUENCES.",
    ]

    publishable_people = []

    for entity in intelligence.get("entities", []):
        status = str(
            entity.get("status", "UNVERIFIED")
        ).strip().upper()

        observed = str(
            entity.get("observed_text", "")
        ).strip()

        canonical = str(
            entity.get("canonical_text", observed)
        ).strip()

        lines.append(
            f"- {status}: {observed!r} -> {canonical!r}"
        )

        if (
            entity.get("entity_type") == "person"
            and status in {"VERIFIED", "CORRECTED"}
            and canonical
            and canonical not in publishable_people
        ):
            publishable_people.append(canonical)

    lines.append("")
    lines.append("PUBLISHABLE PERSON NAMES:")

    if publishable_people:
        for name in publishable_people:
            lines.append(f"- {name}")
    else:
        lines.append("- NONE")

    lines.append("")
    lines.append(
        "PERSON-NAME RULE: Any human name not listed above "
        "must not appear in headline, dek, body or key facts."
    )

    lines.append("")
    lines.append(
        "SOURCE-VALIDATED ACTION LEDGER:"
    )
    lines.append(
        "This is an index to exact source excerpts, "
        "not independent evidence."
    )

    actions = intelligence.get(
        "action_ledger",
        [],
    )

    if actions:
        for action in actions:
            topic = str(
                action.get("topic", "")
            ).strip()

            status = str(
                action.get(
                    "action_status",
                    "unclear",
                )
            ).strip().upper()

            validated = (
                action.get("validated")
                is True
            )

            lines.append(
                f"- {status}: {topic}"
                + (
                    " [VALIDATED]"
                    if validated
                    else " [NOT VALIDATED]"
                )
            )

            if action.get(
                "agenda_linkage_conflict"
            ):
                lines.append(
                    "  Agenda linkage conflict: YES"
                )
                lines.append(
                    "  Audit rule: neutral topic/action wording "
                    "without agenda-section timing is the "
                    "required conservative treatment."
                )

            quote = str(
                action.get(
                    "evidence_quote",
                    "",
                )
            ).strip()

            if quote:
                lines.append(
                    f"  Exact source excerpt: {quote}"
                )
    else:
        lines.append("- NONE AVAILABLE")

    return "\n".join(lines)


def writer_context(
    intelligence,
):
    lines = []

    lines.append(
        "REAL-WORLD ENTITY VERIFICATION:"
    )

    for entity in intelligence.get(
        "entities",
        [],
    ):
        observed = entity.get(
            "observed_text",
            "",
        )

        canonical = entity.get(
            "canonical_text",
            observed,
        )

        status = entity.get(
            "status",
            "UNVERIFIED",
        )

        source = entity.get(
            "official_source_url",
            "",
        )

        lines.append(
            f"- {status}: {observed!r} -> {canonical!r}"
        )

        if source:
            lines.append(
                f"  Official source: {source}"
            )

    lines.append("")
    lines.append(
        "ENTITY RULES:"
    )
    lines.append(
        "- Use canonical spelling for VERIFIED/CORRECTED."
    )
    lines.append(
        "- Never expand an UNVERIFIED partial/phonetic name."
    )
    lines.append(
        "- CRITICAL: if a PERSON is UNVERIFIED, do not publish "
        "that person's observed or proposed name in the headline, "
        "dek, body, or key facts. Refer generically by supported "
        "role, such as 'a resident' or 'a city staff member'."
    )
    lines.append(
        "- The writer may NEVER independently correct an "
        "UNVERIFIED person from the agenda or roster. Only names "
        "marked VERIFIED or CORRECTED by this verification layer "
        "may be published as identified people."
    )
    lines.append(
        "- Web evidence is for identity/spelling only."
    )

    lines.append("")
    lines.append(
        "PUBLISHABLE PERSON NAMES:"
    )

    publishable_people = []

    for entity in intelligence.get(
        "entities",
        [],
    ):
        if entity.get("entity_type") != "person":
            continue

        if entity.get("status") not in {
            "VERIFIED",
            "CORRECTED",
        }:
            continue

        canonical = str(
            entity.get("canonical_text", "")
        ).strip()

        if (
            canonical
            and canonical not in publishable_people
        ):
            publishable_people.append(canonical)

    if publishable_people:
        for name in publishable_people:
            lines.append(
                f"- {name}"
            )
    else:
        lines.append(
            "- NONE"
        )

    lines.append("")
    lines.append(
        "PERSON NAME WHITELIST RULE:"
    )
    lines.append(
        "- A human being may be named in the article ONLY if "
        "their exact canonical name appears in PUBLISHABLE "
        "PERSON NAMES above."
    )
    lines.append(
        "- Any other human name found in notes, agenda text, "
        "public comment, or model inference must NOT be "
        "published by name."
    )
    lines.append(
        "- For a non-whitelisted person, use only a supported "
        "generic description such as 'a resident', "
        "'a speaker', or 'a city staff member'."
    )

    lines.append("")
    lines.append(
        "SOURCE-VALIDATED ACTION LEDGER:"
    )

    action_ledger = intelligence.get(
        "action_ledger",
        [],
    )

    if action_ledger:
        for action in action_ledger:
            status = str(
                action.get(
                    "action_status",
                    "unclear",
                )
            ).upper()

            topic = str(
                action.get(
                    "topic",
                    "",
                )
            )

            item_number = str(
                action.get(
                    "item_number",
                    "",
                )
            ).strip()

            validated = action.get(
                "validated"
            ) is True

            prefix = (
                f"Item {item_number}: "
                if item_number
                else ""
            )

            lines.append(
                f"- {prefix}{topic}"
            )

            lines.append(
                f"  Status: {status}"
            )

            lines.append(
                "  Evidence validated: "
                + (
                    "YES"
                    if validated
                    else "NO"
                )
            )

            if action.get(
                "agenda_linkage_conflict"
            ):
                lines.append(
                    "  Agenda linkage conflict: YES"
                )
                lines.append(
                    "  Public-copy rule: report the supported "
                    "topic/action without claiming Consent "
                    "Calendar, Public Hearing, New Business or "
                    "an agenda item number."
                )

            quote = str(
                action.get(
                    "evidence_quote",
                    "",
                )
            ).strip()

            if quote:
                lines.append(
                    f"  Source excerpt: {quote}"
                )
    else:
        lines.append("- NONE AVAILABLE")

    lines.append("")
    lines.append(
        "ACTION STATUS RULES:"
    )
    lines.append(
        "- The action ledger controls factual action verbs."
    )
    lines.append(
        "- If a ledger status is UNCLEAR, do not claim that "
        "the item was approved, adopted, authorized, awarded "
        "or directed."
    )
    lines.append(
        "- The coverage plan below ranks newsworthiness only. "
        "Its action-status guesses are NOT factual evidence."
    )

    lines.append("")
    lines.append(
        "WHOLE-MEETING COVERAGE PLAN:"
    )

    for item in intelligence.get(
        "coverage_items",
        [],
    ):
        flag = (
            "MUST INCLUDE"
            if item.get(
                "must_include"
            )
            else "OPTIONAL"
        )

        lines.append(
            f"{item.get('rank')}. "
            f"[{item.get('score')}/10] "
            f"[{flag}] "
            f"{item.get('topic')}"
        )

    return "\n".join(lines)


def deterministic_verification_notes(intelligence):
    """
    Verification notes are generated from the verifier's actual
    result, never from the story-writing model.

    Only unresolved person identities are surfaced here.
    VERIFIED/CORRECTED identities need no ambiguity note.
    """
    notes = []
    seen = set()

    for entity in intelligence.get("entities", []):
        if entity.get("entity_type") != "person":
            continue

        if entity.get("status") != "UNVERIFIED":
            continue

        observed = str(
            entity.get("observed_text", "")
        ).strip()

        if not observed:
            continue

        key = observed.casefold()

        if key in seen:
            continue

        seen.add(key)

        notes.append(
            "The identity associated with the source reference "
            f"{observed!r} remains unverified; CouncilWatch did "
            "not rely on that name as an identified person."
        )

    return notes


def make_rich_story(
    meeting,
    notes,
    agenda,
    intelligence,
):
    context = writer_context(
        intelligence
    )

    prompt = f"""
Write a publication-quality local-government news story.

City: {meeting.get("city_name")}
Meeting date: {meeting.get("meeting_date")}
Meeting title: {meeting.get("title")}

The story must reflect the WHOLE meeting rather than
collapsing it into the easiest single vote.

IMPORTANT EVIDENCE RULE:
The coverage plan below is an EDITORIAL RANKING TOOL, not a
factual source. Its summaries and "why it matters" language
must never be used to establish a fact, consequence, precedent,
commitment, motive, or technical relationship.

Factual claims must come from the recording-derived notes or
official agenda material.

EDITORIAL REQUIREMENTS:

- Lead with the strongest consequential public topic.
- Prefer consequential binding government action over
  discussion-only public comment when the coverage ranking
  establishes broader or more lasting public impact.
- Do not lead with routine closed-session personnel
  evaluations when stronger public business exists.
- Include the substantive MUST INCLUDE topics from the
  coverage plan.
- A DISCUSSION ONLY topic can be covered if it matters,
  but clearly state that no council approval occurred.
- Never imply approval when something was merely discussed.
- If ALPR/license-plate readers/Flock/surveillance
  technology was substantively discussed, explain:
    * what was discussed
    * what problem officials said it addresses
    * any privacy/data concerns actually raised
    * whether any action was taken
- Include important dollar amounts when supported.
- Preserve the EXACT scope of contracts and approvals.
  For example, if the council awards a professional-services
  agreement for construction management or inspection, do NOT
  describe it as though the council awarded the construction
  contract itself.
- Never pluralize or group unlike council actions under a
  misleading action label. If one item is a professional
  services contract and another is approval of a project phase,
  design, environmental document, ordinance, or resolution,
  do NOT describe them collectively as "contracts".
- This exact-action rule applies especially to the headline
  and dek.
- Explain why actions matter to residents using concrete,
  supported consequences rather than generic significance.
- Avoid ceremonial filler unless genuinely newsworthy.
- Write in restrained, neutral newspaper language.
- Avoid promotional or inflated phrases such as:
  "took decisive action", "major investment",
  "significant investment", "significant discussion",
  "represents a major investment", "growing policy tension",
  or "long-standing commitment" unless that characterization
  is itself supported and necessary.
- Prefer specific verbs such as approved, awarded, adopted,
  appointed, canceled, discussed, rejected, or directed.
- Do not turn planning history into claims about "promises",
  "commitments", or obligations unless the supplied evidence
  explicitly establishes that characterization.
- Do not say an action "sets a precedent", "signals a formal
  commitment", "represents a formal shift", or guarantees a
  future result unless the recording-derived notes or official
  agenda explicitly support that conclusion.
- Never merge distinct technologies. Speed-feedback signs,
  digital message boards, ALPR/license-plate readers and Flock
  systems must remain separate unless the source explicitly
  establishes their relationship.
- Staff direction concerning one technology must not be
  generalized to another technology.
- Use verified/corrected proper-noun spellings below.
- PERSON NAMES ARE WHITELISTED. A human being may be named
  ONLY if their exact canonical name appears under
  PUBLISHABLE PERSON NAMES in the verified context below.
- This applies even if a name appears clearly in the recording
  notes or official agenda. If it is not on the whitelist,
  do not publish it by name.
- For any non-whitelisted person, use only a supported generic
  description such as "a resident", "a speaker",
  "a council member", or "a city staff member".
- NEVER independently resolve or correct a person using the
  agenda, roster, context clues, spelling similarity, or your
  own inference. Identity decisions belong exclusively to the
  verification layer.
- Do not add factual web material merely because it appeared
  during name verification.

LENGTH:
If the meeting contains several substantive topics,
target approximately 650-950 words.
A genuinely quiet meeting may be shorter.

HEADLINE:
Choose the most newsworthy theme or two.
Do not automatically headline the first agenda item.
Prefer concrete government actions over vague labels such as
"major projects" or "major developments."
Do not broaden the scope of a contract or approval.

DEK:
One clear sentence explaining the main significance.
State precisely what the council actually approved, awarded,
appointed, canceled, discussed or rejected.
Do not compress a professional-services agreement into a
construction contract or otherwise overstate an action.

BODY:
Use normal news paragraphs. Usually 6-10 paragraphs for
a substantive meeting.

KEY FACTS:
Provide 4-6 useful facts.

VERIFICATION NOTES:
Only include genuinely unresolved factual/name issues.

================ VERIFIED/EDITORIAL CONTEXT ================

{context}

================ RECORDING-DERIVED NOTES ================

{notes[:65000]}

================ OFFICIAL AGENDA MATERIAL ================

{agenda[:45000]}
"""

    client = genai.Client()

    response = retry_api_call(
        "Rich story generation",
        lambda: client.models.generate_content(
            model=STORY_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=StoryDraft,
                temperature=0.25,
            ),
        ),
    )

    parsed = getattr(
        response,
        "parsed",
        None,
    )

    if isinstance(parsed, StoryDraft):
        story = parsed
    else:
        story = StoryDraft.model_validate_json(
            response.text
        )

    print("Running editorial enforcement pass...")

    enforcement_prompt = f"""
You are the senior editor for a local-news publication.

Rewrite the draft below so it STRICTLY follows the ranked
whole-meeting coverage plan.

NON-NEGOTIABLE EDITORIAL RULES:

1. The #1 ranked topic is the primary story.
   If its score is 8/10 or higher, it MUST materially shape
   the headline AND the opening paragraph.

2. If the #1 topic was discussion only, the headline and lede
   must accurately say the council discussed, considered,
   debated, reviewed, or explored it. Never imply approval.

3. Every MUST INCLUDE topic must receive meaningful coverage,
   not merely a passing phrase.

4. Ranking matters. A lower-ranked routine vote must not
   displace a higher-ranked consequential topic just because
   the vote is easier to describe.

5. If the top two topics are both highly consequential, the
   headline may combine them, but the #1 topic may not disappear.

6. If there are three or more MUST INCLUDE topics, normally
   produce roughly 650-950 words, provided the source evidence
   supports that length. Do not pad with generic filler.

7. Preserve factual accuracy. Add NO unsupported facts.
   Use the recording-derived evidence and official agenda below.

8. Clearly distinguish:
   - approved actions
   - discussion only
   - public comments
   - staff recommendations

9. Keep normal newspaper-style paragraphs and a concise dek.

10. Use restrained, neutral local-news language. Remove generic
    AI/news-release phrasing such as "took decisive action",
    "major investment", "significant investment",
    "represents a major investment", or "growing policy tension"
    unless the characterization is directly supported and needed.

11. Preserve exact action and contract scope. A contract for
    construction management, inspection, design, consulting or
    other professional services must NOT be rewritten as though
    the council awarded the underlying construction project.

12. Prefer concrete government verbs and consequences:
    approved, awarded, adopted, appointed, canceled, discussed,
    rejected, directed, amount spent, election affected,
    property affected, rule changed, or next step established.

13. If a formal action changes elections, representation or who
    will hold public office, treat that as inherently significant
    civic news and follow the coverage-plan ranking accordingly.

14. PERSON-NAME SAFETY IS NON-NEGOTIABLE. If the verification
    context marks a person UNVERIFIED, remove that person's name
    from headline, dek, body and key facts. Refer generically by
    a supported role. Do not independently infer that an
    UNVERIFIED transcript name belongs to someone named in the
    agenda or city directory.

15. Do not invent verification corrections. A person's identity
    may be described as corrected only when the verification
    context itself marks that entity CORRECTED.

16. PERSON NAMES ARE AN EXPLICIT WHITELIST. Scan headline, dek,
    body and key facts. Every named human being must appear
    exactly under PUBLISHABLE PERSON NAMES in the verification
    context. If not, remove the name and substitute a supported
    generic role. A name appearing in the raw notes or agenda is
    NOT sufficient authorization to publish it.

17. Preserve each council action's exact type in headline, dek
    and body. Do not collectively call unlike actions
    "contracts", "approvals", or another narrower term when that
    description is not true for every item being grouped.

18. Do not describe historical planning decisions as promises,
    commitments or obligations unless the evidence explicitly
    says that.

================ COVERAGE PLAN ================

{context}

================ CURRENT DRAFT ================

{story.model_dump_json(indent=2)}

================ RECORDING-DERIVED EVIDENCE ================

{notes[:45000]}

================ OFFICIAL AGENDA ================

{agenda[:30000]}
"""

    enforced_response = retry_api_call(
        "Editorial enforcement",
        lambda: client.models.generate_content(
            model=STORY_MODEL,
            contents=enforcement_prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=StoryDraft,
                temperature=0.15,
            ),
        ),
    )

    enforced = getattr(
        enforced_response,
        "parsed",
        None,
    )

    if isinstance(enforced, StoryDraft):
        final_story = enforced
    else:
        final_story = StoryDraft.model_validate_json(
            enforced_response.text
        )

    def story_words(draft):
        body = draft.body

        if isinstance(body, list):
            return sum(
                len(str(part).split())
                for part in body
            )

        return len(str(body or "").split())

    must_items = [
        item
        for item in intelligence.get(
            "coverage_items",
            [],
        )
        if item.get("must_include")
    ]

    words_before = story_words(final_story)

    if (
        len(must_items) >= 3
        and words_before < 600
    ):
        print(
            "Depth check:",
            words_before,
            "words with",
            len(must_items),
            "MUST INCLUDE topics."
        )
        print(
            "Running one depth/coverage expansion pass..."
        )

        depth_prompt = f"""
You are performing a final depth edit on a local-government
news article.

The current article is too compressed for the number of
important topics in the meeting.

REQUIREMENTS:

- Preserve the existing #1-topic editorial emphasis.
- Keep the strongest ranked topic in the headline and lede.
- Give EACH MUST INCLUDE topic meaningful explanation.
- Explain what happened, why it matters to residents, and
  important supported details such as money, rules, concerns,
  or next steps.
- Clearly distinguish council action from discussion only.
- Use ONLY the evidence supplied below.
- Do not invent quotes, facts, names, motives, or background.
- Do not add generic filler simply to increase length.
- Normal target: approximately 600-850 words.
- Preserve concise newspaper-style paragraphs.

================ COVERAGE PLAN ================

{context}

================ PERSON-NAME VERIFICATION ================

{audit_verification_context(intelligence)}

================ CURRENT ARTICLE ================

{final_story.model_dump_json(indent=2)}

================ RECORDING-DERIVED EVIDENCE ================

{notes[:50000]}

================ OFFICIAL AGENDA ================

{agenda[:35000]}
"""

        depth_response = retry_api_call(
            "Depth and coverage expansion",
            lambda: client.models.generate_content(
                model=STORY_MODEL,
                contents=depth_prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=StoryDraft,
                    temperature=0.15,
                ),
            ),
        )

        expanded = getattr(
            depth_response,
            "parsed",
            None,
        )

        if isinstance(expanded, StoryDraft):
            final_story = expanded
        else:
            final_story = StoryDraft.model_validate_json(
                depth_response.text
            )

        print(
            "Depth pass result:",
            story_words(final_story),
            "words"
        )

    else:
        print(
            "Depth check passed:",
            words_before,
            "words"
        )

    # --------------------------------------------------
    # FINAL EVIDENCE / RESTRAINT PASS
    # --------------------------------------------------
    #
    # IMPORTANT:
    # The depth-expansion pass runs after the main editorial
    # enforcement pass. Without this final pass, expansion can
    # reintroduce promotional language, unsupported causal
    # claims, ambiguous technology references, or inferred
    # motives/consequences.
    #
    # This pass receives source evidence directly and NO
    # coverage-plan "why it matters" material.

    print(
        "Running final evidence/restraint pass..."
    )

    final_restraint_prompt = f"""
You are the FINAL copy desk for CouncilWatch.

Review and, where needed, rewrite the article below.

This is the last writing pass before the independent factual
audit. Be conservative.

Use ONLY:
1. the recording-derived evidence
2. the official agenda material

Do NOT use editorial-ranking language or outside knowledge as
evidence.

NON-NEGOTIABLE RULES:

1. PRESERVE EXACT GOVERNMENT ACTION.
   Distinguish approved, awarded, adopted, appointed, canceled,
   discussed, proposed, recommended and directed.

2. DO NOT INFER MOTIVE.
   Never say an action was taken "to save money", "to increase
   transparency", "to demonstrate commitment", or for another
   motive unless officials explicitly established that motive
   in the supplied evidence.

3. DO NOT INFER CONSEQUENCES OR SIGNIFICANCE.
   Remove unsupported language such as:
   - sets a precedent
   - signals a formal commitment
   - formal commitment
   - long-standing commitment
   - represents a formal shift
   - major investment
   - significant investment
   - decisive action
   - growing policy tension
   - guarantees
   - ensures a future result

   Replace such wording with the concrete action actually taken.

4. DO NOT INVENT CAUSAL LINKS.
   Do not say one event "prompted", "led to", "resulted in",
   "caused", or "triggered" another action unless the source
   explicitly establishes that relationship.

5. PUBLIC COMMENT MUST REMAIN ATTRIBUTED AND NEUTRAL.
   Do not intensify a speaker's remarks.
   A request for information or assurances must not become
   "anxiety", "fear", "negative impact", or another stronger
   characterization unless the speaker actually expressed it.

6. TECHNOLOGY SEPARATION IS STRICT.
   Speed-feedback signs, portable message boards, digital
   signage, ALPR/license-plate readers, Flock cameras and other
   surveillance technologies are DISTINCT unless the evidence
   explicitly establishes otherwise.

   When more than one technology appears in a story:
   - name the specific technology attached to each factual claim
   - do NOT use ambiguous phrases such as "these systems",
     "these devices", "the technology", or "additional units"
     when the antecedent could refer to more than one system
   - attach acquisition, funding, placement, data collection,
     retention, sharing and enforcement ONLY to the specific
     technology supported by the source

7. A committee's creation does not by itself establish future
   funding, resource allocation, policy adoption, commitment,
   or implementation. State only the mandate actually supported.

8. Do not transform staff's existing activity into a response
   caused by public comment unless chronology and causation are
   explicitly established.

9. Avoid promotional adjectives and generic importance claims.
   Prefer concrete facts, amounts, votes, locations, rules and
   next steps.

10. PERSON-NAME WHITELIST IS ABSOLUTE.
    A human being may be named ONLY when their exact canonical
    name appears under PUBLISHABLE PERSON NAMES below.

    If the whitelist says NONE, remove ALL human names from the
    headline, dek, body and key facts and substitute supported
    generic roles such as:
    - a resident
    - a speaker
    - a council member
    - the committee chair
    - a city staff member

    A name appearing in raw notes or the agenda is NOT by itself
    permission to publish it.

11. CROSS-FIELD ACTION CONSISTENCY IS REQUIRED.
    For the same agenda item, headline, dek, body and key facts
    must not disagree about whether the council approved,
    adopted, authorized, awarded, directed, considered or
    discussed it.

    Never use an approval verb in the headline when the supplied
    evidence supports only discussion or consideration.

    A generic consent-calendar vote cannot establish approval of
    a separately listed public-hearing or new-business item.

    When final disposition is genuinely unclear, use the most
    conservative supported action such as considered or
    discussed.

12. Preserve useful supported detail. This is a cleanup pass,
    not an instruction to make the article vague or shorter.

13. Headline, dek, body and key facts must all obey these rules.

14. REMOVE LEDE REDUNDANCY.
    The first two body paragraphs must not merely repeat the same
    council action in slightly different words.

    - Do not begin with generic meeting-recap language such as
      "The City Council met on..." when a substantive action can
      lead immediately.
    - If paragraph 1 states the main action and paragraph 2 only
      restates that same action with details, combine them into
      one stronger opening paragraph.
    - Each following paragraph should add materially new
      information, context, another action, or another topic.

15. KEEP PUBLIC-COMMENT-ONLY FACTS ATTRIBUTED.
    A claim made by a resident or speaker is evidence of what the
    speaker said. It is not automatically independent evidence
    that the underlying event or allegation occurred.

    If a date, accident, fatality, violation, allegation,
    property condition, motive, or other concrete fact appears
    only inside public comment and is not independently supported
    by the official agenda, staff material, or another official
    source supplied here, keep the attribution attached.

    Prefer constructions such as:
    - "a resident said..."
    - "a resident cited..."
    - "according to a speaker..."
    - "the speaker described..."

    Do NOT turn:
      "A resident requested traffic calming, citing an Aug. 8
      fatal accident"
    into:
      "traffic calming was requested following an Aug. 8 fatal
      accident"
    unless the accident itself is independently established by
    the supplied official evidence.

16. KEEP ADJACENT BUT UNRELATED TOPICS DISTINCT.
    When two nearby paragraphs mention cameras, surveillance,
    traffic enforcement, ALPR, speed control, or other similarly
    named technologies, use specific wording so a reader cannot
    reasonably infer they are the same system or proposal.

17. WRITE FOR HUMAN-REVIEW READINESS.
    Remove unnecessary recap language, duplicated facts,
    mechanical transitions and needless restatement.
    Preserve all useful supported facts.
    Do not add facts merely to improve prose.

================ CURRENT ARTICLE ================

{final_story.model_dump_json(indent=2)}

================ RECORDING-DERIVED EVIDENCE ================

{notes[:55000]}

================ OFFICIAL AGENDA ================

{agenda[:40000]}
"""

    restraint_response = retry_api_call(
        "Final evidence/restraint pass",
        lambda: client.models.generate_content(
            model=STORY_MODEL,
            contents=final_restraint_prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=StoryDraft,
                temperature=0.05,
            ),
        ),
    )

    restrained = getattr(
        restraint_response,
        "parsed",
        None,
    )

    if isinstance(
        restrained,
        StoryDraft,
    ):
        final_story = restrained
    else:
        final_story = StoryDraft.model_validate_json(
            restraint_response.text
        )

    # Never trust the writing model to invent or infer
    # verification notes. They come directly from the
    # deterministic verification result.
    final_story.verification_notes = (
        deterministic_verification_notes(
            intelligence
        )
    )

    return final_story
