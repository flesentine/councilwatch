
from __future__ import annotations

import json
import time
from typing import List

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from settings import GEMINI_API_KEY, TRANSCRIPT_MODEL, STORY_MODEL


class StoryDraft(BaseModel):
    headline: str
    dek: str
    body: List[str] = Field(description="Article paragraphs in order.")
    key_facts: List[str]
    verification_notes: List[str]


class AuditIssue(BaseModel):
    severity: str = Field(description="material or minor")
    field: str = Field(description="headline, dek, body, key_facts, or verification_notes")
    draft_text: str = Field(
        description="Exact text from the draft that is being criticized. Must be copied verbatim."
    )
    source_evidence: str = Field(
        description="Short source-note or agenda evidence supporting the criticism."
    )
    correction: str = Field(description="Specific correction or deletion.")


class AuditResult(BaseModel):
    ok: bool
    issues: List[AuditIssue]
    corrected_headline: str = ""
    corrected_dek: str = ""
    corrected_body: List[str] = []
    corrected_key_facts: List[str] = []
    corrected_verification_notes: List[str] = []


def client():
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "No Gemini API key found in .env. Expected GEMINI_API_KEY, "
            "GOOGLE_API_KEY, GOOGLE_GENAI_API_KEY, or GOOGLE_GENERATIVE_AI_API_KEY."
        )
    return genai.Client(api_key=GEMINI_API_KEY)


def _wait_active(c, uploaded, timeout=900):
    started = time.time()
    while True:
        state = getattr(uploaded, "state", None)
        name = getattr(state, "name", "") if state else ""
        if not name or name == "ACTIVE":
            return uploaded
        if name == "FAILED":
            raise RuntimeError("Gemini file processing failed")
        if time.time() - started > timeout:
            raise TimeoutError("Timed out waiting for Gemini file processing")
        time.sleep(4)
        uploaded = c.files.get(name=uploaded.name)


def make_source_notes(audio_path, meeting: dict) -> str:
    c = client()
    print(f"    uploading audio to Gemini ({TRANSCRIPT_MODEL})...", flush=True)
    uploaded = c.files.upload(file=str(audio_path))
    uploaded = _wait_active(c, uploaded)

    prompt = f"""
You are preparing source notes for a local-government reporter.

Analyze the ENTIRE attached council-meeting recording.

CITY: {meeting['city_name']}
MEETING DATE: {meeting.get('meeting_date') or 'unknown'}
MEETING TITLE: {meeting.get('title') or 'City Council Meeting'}

Produce detailed, source-faithful reporting notes. This is NOT the article.

Requirements:
- Cover every substantive agenda topic in chronological order.
- Capture decisions, motions, votes, contract awards, dollar amounts, dates,
  deadlines, staff recommendations, resident/public comments, and major debate.
- Preserve proper names and official titles only when you can hear them clearly.
- If a name or number is uncertain, explicitly mark it uncertain rather than guessing.
- Separate what staff proposed from what the council actually approved.
- Do not invent context not present in the recording.
- Note when an item is ceremonial, consent-calendar, informational, or closed-session
  if that is clear from the recording.
- Flag facts that a reporter should verify against the written agenda.
- Be detailed enough that another model can write a strong local news article
  without listening to the recording.
"""
    response = c.models.generate_content(
        model=TRANSCRIPT_MODEL,
        contents=[prompt, uploaded],
        config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=12000,
        ),
    )
    text = (response.text or "").strip()
    if len(text) < 500:
        raise RuntimeError("Gemini returned unexpectedly short source notes")
    try:
        c.files.delete(name=uploaded.name)
    except Exception:
        pass
    return text


def make_story(meeting: dict, notes: str, agenda: str) -> StoryDraft:
    c = client()
    prompt = f"""
Write a neutral local-news article from the source material below.

CITY: {meeting['city_name']}
MEETING DATE: {meeting.get('meeting_date') or 'unknown'}
MEETING TITLE: {meeting.get('title') or 'City Council Meeting'}

REPORTING RULES:
- Treat the recording-derived notes as the primary source.
- Use the agenda text to verify names, agenda-item wording, dates and amounts.
- Do not add outside facts or assumptions.
- Do not guess a person's identity. If a name is not confidently supported, omit it.
- Distinguish proposals/recommendations from actual council actions.
- Attribute contentious claims.
- State vote counts only if supported.
- Lead immediately with the most consequential LOCAL action or debate.
- Do NOT begin with generic wording such as "The City Council held its regular meeting."
- Prefer 1-2 main news themes. De-emphasize ceremony and routine councilmember reports.
- Include public comment only when it is materially newsworthy or helps explain a council issue.
- Prefer concrete numbers and resident consequences when supported.
- Avoid repeating the same dollar figure or decision in multiple ways.
- Do not create two separate key facts from one underlying financial fact.
- Write clean AP-like newspaper prose, not minutes and not a transcript recap.
- Aim for roughly 550-850 words; be shorter if the meeting does not support that length.
- No fabricated quotes. Paraphrase unless wording is unmistakable.
- Headline should be specific, concise and newsy; generally under 14 words.
- Dek should be one sentence.
- Body must be a list of normal article paragraphs.
- key_facts is REQUIRED and should contain 3-5 concise facts already supported by the story/source.
- verification_notes is REQUIRED and should flag genuine ambiguities, not routine disclaimers.

RECORDING-DERIVED SOURCE NOTES:
--- BEGIN NOTES ---
{notes}
--- END NOTES ---

WRITTEN AGENDA / OFFICIAL PAGE TEXT:
--- BEGIN AGENDA ---
{agenda[:50000]}
--- END AGENDA ---
"""
    response = c.models.generate_content(
        model=STORY_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.22,
            response_mime_type="application/json",
            response_schema=StoryDraft,
            max_output_tokens=10000,
        ),
    )
    return StoryDraft.model_validate_json(response.text)



def _normalize_audit_guard_text(
    value,
):
    import re as _re

    value = str(
        value or ""
    )

    value = value.replace(
        chr(8217),
        "'",
    )

    value = value.replace(
        "*",
        "",
    ).replace(
        "`",
        "",
    )

    value = _re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip().lower()


def _audit_issue_correction_is_noop(
    issue,
):
    """
    Reject an audit issue whose proposed correction does not
    actually change the criticized text.
    """
    import re as _re

    quoted = str(
        issue.draft_text
        or ""
    ).strip()

    correction = str(
        issue.correction
        or ""
    ).strip()

    if (
        not quoted
        or not correction
    ):
        return False

    if (
        _normalize_audit_guard_text(
            correction
        )
        == _normalize_audit_guard_text(
            quoted
        )
    ):
        return True

    target = _re.sub(
        r"^(?:change|replace)\s+(?:it\s+)?"
        r"(?:to|with)\s*:\s*",
        "",
        correction,
        flags=_re.I,
    ).strip()

    target = target.strip(
        "\"'“”"
    )

    return (
        _normalize_audit_guard_text(
            target
        )
        == _normalize_audit_guard_text(
            quoted
        )
    )


def _protected_public_comment_entity(
    issue,
    notes,
):
    """
    Protect a source-supported proper-name phrase in attributed
    public comment from being canonicalized into a different
    agenda/entity name.

    Person names retain the older stricter requirement that the
    auditor's own source_evidence acknowledge the phrase.

    Organization-like names receive an additional protection:
    when the exact phrase is present in the recording-derived
    notes, it is authoritative for what the commenter said.
    """
    import re as _re

    draft_text = str(
        issue.draft_text
        or ""
    )

    low = draft_text.lower()

    if not any(
        marker in low
        for marker in (
            "speaker",
            "public comment",
            "resident",
            "commenter",
        )
    ):
        return ""

    notes_norm = (
        _normalize_audit_guard_text(
            notes
        )
    )

    evidence_norm = (
        _normalize_audit_guard_text(
            issue.source_evidence
        )
    )

    correction_norm = (
        _normalize_audit_guard_text(
            issue.correction
        )
    )

    proper_name_pattern = (
        r"\b[A-Z][A-Za-z0-9&./'()-]*"
        r"(?:\s+(?:"
        r"[A-Z][A-Za-z0-9&./'()-]*|"
        r"of|the|and"
        r")){2,}\b"
    )

    organization_words = {
        "association",
        "authority",
        "commission",
        "committee",
        "company",
        "corporation",
        "council",
        "department",
        "district",
        "foundation",
        "league",
        "organization",
        "society",
        "university",
    }

    for phrase in _re.findall(
        proper_name_pattern,
        draft_text,
    ):
        meaningful = [
            word
            for word
            in _re.findall(
                r"[A-Za-z0-9]+",
                phrase,
            )
            if word.lower()
            not in {
                "of",
                "the",
                "and",
            }
        ]

        if len(
            meaningful
        ) < 2:
            continue

        phrase_norm = (
            _normalize_audit_guard_text(
                phrase
            )
        )

        if (
            phrase_norm
            not in notes_norm
        ):
            continue

        if (
            phrase_norm
            in correction_norm
        ):
            continue

        phrase_words = {
            word.lower()
            for word
            in meaningful
        }

        organization_like = bool(
            phrase_words
            & organization_words
        )

        # Safest case: the auditor's own source-evidence text
        # acknowledges the exact source-note phrase that its
        # correction would overwrite.
        if (
            phrase_norm
            in evidence_norm
        ):
            return phrase

        if not organization_like:
            continue

        # Organization names get one additional narrowly scoped
        # protection: reject canonicalization into a DIFFERENT
        # organization name.
        #
        # Do not broadly protect every factual claim merely
        # because the organization itself appears in the notes.
        # A legitimate correction may need to delete or alter an
        # unsupported claim about a correctly named organization.
        replacement_organizations = []

        for replacement_phrase in _re.findall(
            proper_name_pattern,
            str(
                issue.correction
                or ""
            ),
        ):
            replacement_words = {
                word.lower()
                for word
                in _re.findall(
                    r"[A-Za-z0-9]+",
                    replacement_phrase,
                )
                if word.lower()
                not in {
                    "of",
                    "the",
                    "and",
                }
            }

            if not (
                replacement_words
                & organization_words
            ):
                continue

            replacement_norm = (
                _normalize_audit_guard_text(
                    replacement_phrase
                )
            )

            if (
                replacement_norm
                != phrase_norm
            ):
                replacement_organizations.append(
                    replacement_phrase
                )

        if replacement_organizations:
            return phrase

    return ""



def _audit_publishable_person_names(
    notes,
):
    """
    Parse the canonical human-name whitelist emitted by
    audit_verification_context().

    The verification layer owns identity/spelling decisions.
    """
    text = str(
        notes or ""
    )

    marker = (
        "PUBLISHABLE PERSON NAMES:"
    )

    start = text.rfind(
        marker
    )

    if start < 0:
        return []

    tail = text[
        start
        + len(marker):
    ]

    names = []

    for raw_line in tail.splitlines():
        line = raw_line.strip()

        if not line:
            if names:
                break

            continue

        if line.startswith(
            "PERSON-NAME RULE:"
        ):
            break

        if not line.startswith(
            "- "
        ):
            if names:
                break

            continue

        name = line[
            2:
        ].strip()

        if (
            not name
            or name.upper()
            == "NONE"
        ):
            continue

        if name not in names:
            names.append(
                name
            )

    return names


def _protected_publishable_person_rewrite(
    issue,
    notes,
):
    """
    Reject an audit attempt that downgrades an already
    canonical, whitelisted human name to a near-matching raw
    transcript/agenda spelling that is NOT on the whitelist.

    Examples:
      Stephanie Oddo -> Stephanie Otto
      Stephanie Oddo -> Council Member Otto

    This is deliberately NOT a blanket protection of the
    attribution itself.

    Allowed:
      Stephanie Oddo -> Stephanie Winstead
        when BOTH names are verified/whitelisted.

      Stephanie Oddo -> the District 4 representative
        when an attribution genuinely needs to be generalized.
    """
    import difflib as _difflib
    import re as _re

    whitelist = (
        _audit_publishable_person_names(
            notes
        )
    )

    if not whitelist:
        return ""

    draft = str(
        issue.draft_text
        or ""
    )

    correction = str(
        issue.correction
        or ""
    )

    if (
        not draft
        or not correction
    ):
        return ""

    whitelist_norm = {
        _normalize_audit_guard_text(
            name
        ):
            name
        for name in whitelist
    }

    name_pattern = (
        r"\b"
        r"[A-Z][A-Za-z'’.-]+"
        r"(?:\s+[A-Z][A-Za-z'’.-]+){1,3}"
        r"\b"
    )

    candidates = _re.findall(
        name_pattern,
        correction,
    )

    role_words = {
        "council",
        "councilmember",
        "member",
        "mayor",
        "commissioner",
        "supervisor",
        "director",
        "manager",
    }

    def words(
        value,
    ):
        return [
            word.casefold()
            for word
            in _re.findall(
                r"[A-Za-z'’.-]+",
                str(
                    value
                    or ""
                ),
            )
        ]

    def surname_similarity(
        left,
        right,
    ):
        return _difflib.SequenceMatcher(
            None,
            left,
            right,
        ).ratio()

    def surname_phonetic_key(
        value,
    ):
        """
        Narrow Soundex-style surname key for audit protection.

        Used only after the verification layer has already
        established a canonical whitelisted person.

        Examples:
          Oddo -> O300
          Otto -> O300

        It is NOT used to independently identify a person.
        """
        letters = _re.sub(
            r"[^a-z]",
            "",
            str(
                value
                or ""
            ).casefold(),
        )

        if not letters:
            return ""

        first = letters[
            0
        ].upper()

        groups = {
            **{
                char: "1"
                for char
                in "bfpv"
            },
            **{
                char: "2"
                for char
                in "cgjkqsxz"
            },
            **{
                char: "3"
                for char
                in "dt"
            },
            "l": "4",
            **{
                char: "5"
                for char
                in "mn"
            },
            "r": "6",
        }

        previous = groups.get(
            letters[
                0
            ],
            "",
        )

        digits = []

        for char in letters[
            1:
        ]:
            code = groups.get(
                char,
                "",
            )

            if not code:
                # Vowels plus h/w/y break adjacent-code runs.
                previous = ""
                continue

            if code != previous:
                digits.append(
                    code
                )

            previous = code

        return (
            first
            + "".join(
                digits
            )
            + "000"
        )[
            :4
        ]

    for canonical in whitelist:
        canonical_pattern = _re.compile(
            r"(?<!\w)"
            + _re.escape(
                canonical
            )
            + r"(?!\w)",
            _re.I,
        )

        if not canonical_pattern.search(
            draft
        ):
            continue

        canonical_words = words(
            canonical
        )

        if len(
            canonical_words
        ) < 2:
            continue

        canonical_first = (
            canonical_words[
                0
            ]
        )

        canonical_last = (
            canonical_words[
                -1
            ]
        )

        for candidate in candidates:
            candidate_norm = (
                _normalize_audit_guard_text(
                    candidate
                )
            )

            # The current canonical name itself is harmless.
            if (
                candidate_norm
                == _normalize_audit_guard_text(
                    canonical
                )
            ):
                continue

            # Switching to another independently verified person
            # is an attribution correction, not an identity
            # downgrade. Allow it.
            if (
                candidate_norm
                in whitelist_norm
            ):
                continue

            candidate_words = words(
                candidate
            )

            if len(
                candidate_words
            ) < 2:
                continue

            candidate_first = (
                candidate_words[
                    0
                ]
            )

            candidate_last = (
                candidate_words[
                    -1
                ]
            )

            last_similarity = (
                surname_similarity(
                    canonical_last,
                    candidate_last,
                )
            )

            canonical_phonetic = (
                surname_phonetic_key(
                    canonical_last
                )
            )

            candidate_phonetic = (
                surname_phonetic_key(
                    candidate_last
                )
            )

            same_phonetic_surname = (
                bool(
                    canonical_phonetic
                )
                and canonical_phonetic
                == candidate_phonetic
            )

            same_first_name = (
                candidate_first
                == canonical_first
            )

            role_labeled_variant = bool(
                set(
                    candidate_words
                )
                & role_words
            )

            # Exact first name plus either a strong spelling
            # match or the same narrow surname phonetic key.
            if (
                same_first_name
                and (
                    last_similarity
                    >= 0.67
                    or same_phonetic_surname
                )
            ):
                return canonical

            # Transcript role form such as
            # "Council Member Otto" for canonical
            # "Stephanie Oddo".
            #
            # The role itself does NOT identify the person.
            # This only prevents an already-verified canonical
            # name from being downgraded to its near/raw form.
            if (
                role_labeled_variant
                and (
                    last_similarity
                    >= 0.67
                    or same_phonetic_surname
                )
            ):
                return canonical

    return ""



def _guard_audit_result(
    result,
    story,
    notes,
):
    """
    Deterministically validate Gemini audit output.

    Critical rule:
    if an invalid audit complaint is rejected for a field, do
    NOT allow Gemini's full corrected version of that same field
    to be applied. The corrected field may contain the rejected
    edit mixed together with valid edits.

    Failing closed is safer than silently applying a correction
    that the deterministic guard explicitly rejected.
    """
    fields = {
        "headline":
            story.headline,

        "dek":
            story.dek,

        "body":
            "\n".join(
                story.body
            ),

        "key_facts":
            "\n".join(
                story.key_facts
            ),

        "verification_notes":
            "\n".join(
                story.verification_notes
            ),
    }

    valid_issues = []
    blocked_fields = set()

    for issue in result.issues:
        haystack = fields.get(
            issue.field,
            "",
        )

        if (
            not issue.draft_text
            or issue.draft_text
            not in haystack
        ):
            continue

        if _audit_issue_correction_is_noop(
            issue
        ):
            print(
                "    audit guard: dropped no-op correction: "
                f"{issue.draft_text!r}",
                flush=True,
            )
            continue

        protected_person = (
            _protected_publishable_person_rewrite(
                issue,
                notes,
            )
        )

        if protected_person:
            print(
                "    audit guard: dropped attempted "
                "canonical-person downgrade: "
                f"{protected_person!r}",
                flush=True,
            )

            blocked_fields.add(
                issue.field
            )

            continue

        protected_entity = (
            _protected_public_comment_entity(
                issue,
                notes,
            )
        )

        if protected_entity:
            print(
                "    audit guard: dropped attempted "
                "source-note entity overwrite: "
                f"{protected_entity!r}",
                flush=True,
            )

            blocked_fields.add(
                issue.field
            )

            continue

        valid_issues.append(
            issue
        )

    result.issues = (
        valid_issues
    )

    # If a rejected correction touched a field, Gemini's entire
    # corrected version of that field is contaminated because it
    # may contain both accepted and rejected edits.
    if "headline" in blocked_fields:
        result.corrected_headline = (
            story.headline
        )

    if "dek" in blocked_fields:
        result.corrected_dek = (
            story.dek
        )

    if "body" in blocked_fields:
        result.corrected_body = list(
            story.body
        )

    if "key_facts" in blocked_fields:
        result.corrected_key_facts = list(
            story.key_facts
        )

    if (
        "verification_notes"
        in blocked_fields
    ):
        result.corrected_verification_notes = list(
            story.verification_notes
        )

    material = [
        issue
        for issue
        in valid_issues
        if str(
            issue.severity
        ).lower()
        == "material"
    ]

    result.ok = not bool(
        material
    )

    if not valid_issues:
        result.corrected_headline = ""
        result.corrected_dek = ""
        result.corrected_body = []
        result.corrected_key_facts = []
        result.corrected_verification_notes = []

    return result



def audit_story(meeting: dict, notes: str, agenda: str, story: StoryDraft) -> AuditResult:
    c = client()
    article = {
        "headline": story.headline,
        "dek": story.dek,
        "body": story.body,
        "key_facts": story.key_facts,
        "verification_notes": story.verification_notes,
    }

    prompt = f"""
You are the final fact-checker for a local-news draft.

IMPORTANT: The draft schema intentionally contains FIVE fields:
1. headline
2. dek
3. body
4. key_facts
5. verification_notes

The presence of key_facts and verification_notes is REQUIRED. Do NOT flag them merely
because they are not normal article body text.

Check the draft ONLY against the supplied recording-derived notes and agenda.
Do not use outside knowledge.

The supplied source notes may contain a section named
PUBLISHABLE PERSON NAMES. Treat that list as an absolute
publication whitelist. A raw transcript or agenda appearance
does not override the whitelist.

OFFICIAL-AGENDA STRUCTURE AUTHORITY:

- The WRITTEN OFFICIAL AGENDA is authoritative for agenda item
  numbers, agenda item titles and section placement such as
  CONSENT CALENDAR, PUBLIC HEARINGS and NEW BUSINESS.

- Recording-derived notes may establish what was said, moved,
  voted on or discussed, but they MUST NOT override the
  official agenda's item numbering or section placement.

- If source notes claim that a topic was item 17, item 18 or
  another number but the official agenda assigns that topic to
  a different item, THE OFFICIAL AGENDA WINS.

- Never write or accept an audit explanation saying
  "the agenda lists..." unless the supplied agenda text actually
  says that.

- A generic Consent Calendar approval cannot establish approval
  of an item that the official agenda places under PUBLIC
  HEARINGS or NEW BUSINESS.

- When the recording establishes discussion but does not clearly
  establish the final disposition of the correctly matched
  agenda item, use discussed, considered or unclear rather than
  inferring approval.

CONFLICTED AGENDA-LINKAGE POLICY:

- Sometimes recording-derived notes correctly support that a
  topic was discussed or considered while incorrectly labeling
  the agenda item number or agenda section.

- The written official agenda remains authoritative for item
  number, title and formal section placement.

- HOWEVER, when the verification context explicitly says
  "Agenda linkage conflict: YES", do not infer that the
  discussion itself occurred during either the source-note
  section or the official-agenda section.

- In that situation the REQUIRED conservative reader-facing
  treatment is neutral wording such as:
  "The Council discussed zoning and development code
  amendments."

- Do NOT require "during the Consent Calendar", "during a
  public hearing", "during new business", or an agenda item
  number when the action record has an agenda-linkage conflict.

- Neutral wording is not an omission or audit error when it
  preserves the supported topic and action while avoiding the
  disputed timing/section relationship.

PUBLIC-COPY ITEM NUMBER POLICY:

- CouncilWatch intentionally omits agenda item numbers from
  reader-facing headline, dek, body and key facts.

- The absence of an agenda item number is NOT an error.

- Do NOT require or propose adding "Item 21", "Agenda Item 21",
  or any other agenda number to reader-facing copy.

- When official agenda section context is relevant, use plain
  reader-facing wording such as:
  "During a public hearing, the Council discussed..."
  or
  "During new business, the Council considered..."

- If the draft correctly identifies the topic, action and
  official agenda section, do not flag it merely because the
  agenda item number is omitted.

- If source notes contain an incorrect item-number association
  but the official agenda establishes the correct section, the
  official agenda controls. Correct the section relationship,
  not by inserting an item number into public copy.

EVIDENCE-SEPARATION RULES:
- Treat the recording-derived notes and the written agenda as independent
  evidence streams.
- The agenda can verify an official council action without overriding a
  separately attributed statement made during public comment.
- Similar or overlapping organization names do NOT establish that they are
  the same organization.
- Do NOT replace an organization explicitly supported by the source notes
  with a similarly named organization from the agenda merely because the
  agenda contains the latter.
- When a draft distinguishes an official agenda action from a separate
  public-comment claim, audit each clause against its own supporting evidence.
- If public-comment wording is directly supported by the recording-derived
  notes, it is not an error merely because the agenda uses a different
  organization name elsewhere.
- If the sources are genuinely ambiguous, prefer neutral/general wording
  rather than canonicalizing one organization into another.
- Source evidence in an audit issue must describe the supplied evidence
  accurately. Never claim that the notes say one organization when the notes
  explicitly name another.

CITY: {meeting['city_name']}
MEETING DATE: {meeting.get('meeting_date') or 'unknown'}

A valid audit issue MUST:
- identify one of the five draft fields;
- copy the criticized draft wording EXACTLY into draft_text;
- provide source evidence that conflicts with or fails to support that wording;
- provide a concrete correction.

Do NOT invent a sentence that is not actually in the draft.
Do NOT claim "the body says..." unless that exact wording appears in the body.
Do NOT fail the draft because a fact appears in key_facts rather than body.
Do NOT treat a purely stylistic preference as a material
factual error.

AUDIT STABILITY / NO-CHURN RULES:

- Never return an issue whose proposed correction is identical
  to the criticized draft text.

- Do not flag a factually supported sentence merely because a
  different equally supported phrasing is possible.

- Agenda-section wording is OPTIONAL unless the draft itself
  states or materially implies an incorrect section.

- Do not require a supported specific Council action to be
  rewritten as "a series of items on the Consent Calendar" or
  require adding "as part of the Consent Calendar" merely for
  stylistic completeness.

- "speaker", "resident" and "public commenter" are all acceptable
  neutral attribution labels when the supplied notes establish
  that the statement occurred during public comment. Do not flag
  one merely to prefer another.

- PUBLISHABLE PERSON NAMES are the canonical identity and
  spelling authority. If the draft uses a whitelisted canonical
  person name, NEVER replace it with a raw transcript or agenda
  spelling that is not itself on the whitelist.

- If attribution is genuinely wrong, you may replace the person
  with another WHITELISTED canonical person or remove/generalize
  the attribution. Do not downgrade a verified canonical spelling
  to a phonetic/raw variant.

- When attributed public-comment wording uses an organization
  name that appears explicitly in the recording-derived notes,
  preserve that source-note organization name. Do not replace it
  with a similar organization from the agenda, verification
  context or another evidence stream.

- A correction must repair an actual source conflict or
  unsupported factual claim, not merely rearrange supported
  information.

However, wording is NOT merely stylistic when it adds or
strengthens a motive, consequence, causal relationship,
technical relationship, emotional characterization, policy
commitment, or factual significance that the supplied evidence
does not establish.

A claim can sound plausible and still be unsupported.

Material errors include:
- any human name in headline, dek, body or key facts that is not
  explicitly listed under PUBLISHABLE PERSON NAMES in the
  supplied verification context. If that list says NONE, no
  human names may appear in publishable article fields
- disagreement between headline, dek, body or key facts about
  the action status of the same agenda item, such as the
  headline saying "approved" while the body says "discussed"
- attributing approval of a particular agenda item to a generic
  Consent Calendar vote when the supplied evidence does not
  explicitly establish that the item was on that Consent
  Calendar; this is especially material when the agenda places
  the item under PUBLIC HEARINGS or NEW BUSINESS
- unsupported or misspelled names/titles
- merging two distinct or ambiguously related technologies,
  programs, contracts or council actions into one factual claim
- attributing staff direction, funding, data collection,
  retention, enforcement or law-enforcement sharing to a
  technology when the source supports it only for another
- ambiguous technology references such as "these systems",
  "these devices", "the technology", or "additional units"
  when multiple distinct technologies are in context and the
  source does not establish which one the claim describes
- unsupported causal claims that one event prompted, caused,
  triggered, led to, or resulted in another action
- unsupported motives attributed to officials or a government
  action, even when the inferred motive seems reasonable
- public-comment paraphrases that materially intensify what a
  speaker actually said, such as converting a request for
  information or assurances into fear, anxiety, harm, or a
  claimed negative impact
- a concrete event, allegation, accident, fatality, violation,
  property condition, date, or similar factual claim that is
  supported only as something a public commenter asserted but
  is rewritten as an independently established fact. Preserve
  attribution with wording such as "a resident said" or
  "citing..." unless the supplied official material separately
  establishes the underlying fact
- unsupported claims that creating a committee itself commits
  city resources, funding, policy adoption or implementation
- unsupported interpretive claims such as "sets a precedent",
  "signals a formal commitment", "formal commitment",
  "long-standing commitment", "represents a formal shift",
  "major investment", "significant investment",
  "decisive action", "growing policy tension",
  or guarantees/ensures a future consequence
- unsupported dates, vote counts, dollar amounts, addresses, contract values
- incorrect spelling of a publishable proper name; known name
  corrections must not survive into publication
- proposal/recommendation described as final council action, or vice versa
- describing approval of design, environmental documentation,
  professional services, construction management, inspection,
  or another project phase as approval/award of the entire
  underlying project or construction contract
- a headline, dek or key fact that obscures professional-services
  contract scope in a way that could imply the underlying
  construction contract itself was awarded
- materially misleading headline/dek
- one underlying financial fact misleadingly represented as two different savings/cost facts
- a concrete factual claim unsupported by BOTH the notes and agenda

Minor issues include:
- imprecise but not materially misleading wording
- redundant wording
- supported fact placed awkwardly
- an opening paragraph followed by a second paragraph that
  substantially repeats the same council action instead of
  adding distinct news value
- generic meeting-recap ledes such as "The City Council met..."
  when the article can lead directly with the substantive action

Set ok=true when there are NO material errors.
Minor issues alone do not make ok=false.

If there are ANY valid issues, whether material or minor:
- return the FULL corrected headline, dek, body, key_facts, and verification_notes.
- preserve supported material; only change what is necessary.

Set ok=false when one or more MATERIAL issues exist.
Minor issues alone may leave ok=true, but their corrected fields
must still be supplied so the pipeline can repair them.

If there are NO valid issues:
- leave all corrected_* fields empty.

SOURCE NOTES:
--- BEGIN NOTES ---
{notes}
--- END NOTES ---

AGENDA:
--- BEGIN AGENDA ---
{agenda[:50000]}
--- END AGENDA ---

DRAFT JSON:
{json.dumps(article, ensure_ascii=False)}
"""
    response = c.models.generate_content(
        model=STORY_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.03,
            response_mime_type="application/json",
            response_schema=AuditResult,
            max_output_tokens=10000,
        ),
    )
    result = AuditResult.model_validate_json(response.text)

    return _guard_audit_result(
        result,
        story,
        notes,
    )
