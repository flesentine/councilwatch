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


def parse_agenda_structure(agenda):
    """
    Deterministically extract numbered agenda items and their
    official sections.

    The official agenda, not transcript-derived notes, controls
    item numbering, item titles and section placement.
    """

    items = []
    current_section = ""

    for raw_line in str(
        agenda or ""
    ).splitlines():

        line = re.sub(
            r"\s+",
            " ",
            raw_line.strip(),
        )

        if not line:
            continue

        upper = (
            line.replace(
                "\u2019",
                "'",
            )
            .replace(
                "\u2018",
                "'",
            )
            .upper()
        )

        if upper in AGENDA_SECTION_NAMES:
            current_section = upper
            continue

        match = re.match(
            r"^(\d+)\.\s+(.+)$",
            line,
        )

        if not match:
            continue

        items.append(
            {
                "item_number":
                    match.group(1),
                "section":
                    current_section,
                "title":
                    match.group(2).strip(),
            }
        )

    return items


def _agenda_match_score(
    topic,
    agenda_title,
):
    return len(
        _action_words(topic)
        & _action_words(agenda_title)
    )


def _resolve_agenda_item(
    topic,
    proposed_item_number,
    agenda_items,
):
    """
    Resolve a model-proposed item number against the actual
    official agenda.

    A proposed number is accepted only when its official title
    actually matches the topic. Otherwise we search the official
    agenda for the best topic match.
    """

    proposed = str(
        proposed_item_number or ""
    ).strip()

    if proposed:
        for item in agenda_items:
            if (
                item["item_number"]
                == proposed
                and _agenda_match_score(
                    topic,
                    item["title"],
                ) >= 2
            ):
                return item

    best = None
    best_score = 0

    for item in agenda_items:
        score = _agenda_match_score(
            topic,
            item["title"],
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


def _evidence_agenda_item_numbers(value):
    """
    Extract explicit agenda item numbers from source text.

    Only phrases such as:
      Item 21
      Items 17 and 18
      Agenda Item 21
      Agenda Items 17, 18

    are considered. Dates, addresses, vote totals and years are
    intentionally ignored.
    """

    numbers = set()

    pattern = re.compile(
        r"\b(?:agenda\s+)?items?\s+"
        r"("
        r"\d+"
        r"(?:"
        r"\s*(?:,|and|&|/)\s*\d+"
        r")*"
        r")",
        re.I,
    )

    for match in pattern.finditer(
        str(value or "")
    ):
        numbers.update(
            re.findall(
                r"\d+",
                match.group(1),
            )
        )

    return numbers

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


def _quote_is_in_source(quote, source):
    q = _action_norm(quote)
    s = _action_norm(source)

    return bool(q) and q in s


def _formal_action_has_topic_support(
    topic,
    item_number,
    quote,
):
    quote_words = _action_words(quote)
    topic_words = _action_words(topic)

    overlap = len(
        quote_words & topic_words
    )

    item_match = False

    item_number = str(
        item_number or ""
    ).strip()

    if item_number:
        item_match = bool(
            re.search(
                rf"\b{re.escape(item_number)}\b",
                str(quote),
            )
        )

    has_action_language = bool(
        quote_words & ACTION_EVIDENCE_TERMS
    )

    return (
        has_action_language
        and (
            overlap >= 2
            or item_match
        )
    )


def _best_supported_nonformal_quote(
    topic,
    notes,
):
    """
    Search recording-derived notes for source-supported
    discussion/consideration evidence for a specific topic.

    Used only when an agenda-mapped action record was paired
    with evidence that does not actually describe that topic.
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

    # Examine single lines and two-line windows so a markdown
    # topic heading can remain attached to the sentence below it.
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

            candidate_words = _action_words(
                candidate
            )

            overlap = len(
                topic_words
                & candidate_words
            )

            if overlap < 2:
                continue

            if re.search(
                r"\b("
                r"discussion|discussed|discussing"
                r")\b",
                normalized,
            ):
                status = "discussed"

            elif re.search(
                r"\b("
                r"considered|consideration|considering"
                r")\b",
                normalized,
            ):
                status = "considered"

            else:
                continue

            # Prefer the strongest topic overlap, then the
            # shorter evidence excerpt.
            score = (
                overlap * 100
                - len(normalized)
            )

            candidates.append(
                (
                    score,
                    status,
                    candidate.strip(),
                )
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    _, status, quote = candidates[0]

    return {
        "action_status": status,
        "evidence_quote": quote,
    }


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

    cleaned = []

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
        ):
            topic_overlap = len(
                _action_words(topic)
                & _action_words(quote)
            )

            if topic_overlap < 2:
                repaired = (
                    _best_supported_nonformal_quote(
                        topic,
                        notes,
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

        # Canonical staff-follow-up evidence:
        #
        # If the model returned a generic nonformal status such
        # as "discussed", but the recording-derived notes contain
        # explicit topic-matched evidence that staff follow-up
        # was requested, preserve that more precise action.
        if status not in ACTION_FORMAL_STATUSES:
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

        formal = (
            status
            in ACTION_FORMAL_STATUSES
        )

        formal_valid = True

        if formal:
            # An agenda listing alone never proves a final
            # formal council disposition.
            formal_valid = (
                source_name == "notes"
                and quote_valid
                and _formal_action_has_topic_support(
                    topic,
                    item_number,
                    quote,
                )
            )

        validated = (
            quote_valid
            and formal_valid
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

        if formal and not validated:
            quote_norm = _action_norm(
                quote
            )

            # A stronger formal action may fail validation while
            # the exact source quote still clearly establishes a
            # lower-level action such as discussion.
            #
            # Preserve that supported action rather than losing
            # the topic entirely.
            if (
                quote_valid
                and re.search(
                    r"\b("
                    r"discussion|discussed|discussing"
                    r")\b",
                    quote_norm,
                )
            ):
                status = "discussed"
                validated = True
                validation_note = (
                    "Formal action was not validated; "
                    "exact source evidence supports discussion."
                )

            elif (
                quote_valid
                and re.search(
                    r"\b("
                    r"considered|consideration|considering"
                    r")\b",
                    quote_norm,
                )
            ):
                status = "considered"
                validated = True
                validation_note = (
                    "Formal action was not validated; "
                    "exact source evidence supports consideration."
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

        agenda_linkage_conflict = bool(
            item_number
            and evidence_item_numbers
            and item_number
            not in evidence_item_numbers
        )

        if agenda_linkage_conflict:
            conflict_note = (
                "Source evidence explicitly references agenda "
                "item(s) "
                + ", ".join(
                    sorted(
                        evidence_item_numbers,
                        key=int,
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
                        key=int,
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

Identify approximately 10-20 significant proper nouns that
could reasonably appear in a news article:

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
            and not _person_correction_plausible(
                observed,
                canonical,
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
