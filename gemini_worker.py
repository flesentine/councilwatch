
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

However, wording is NOT merely stylistic when it adds or
strengthens a motive, consequence, causal relationship,
technical relationship, emotional characterization, policy
commitment, or factual significance that the supplied evidence
does not establish.

A claim can sound plausible and still be unsupported.

Material errors include:
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

    # Deterministic guardrail: reject hallucinated audit complaints.
    fields = {
        "headline": story.headline,
        "dek": story.dek,
        "body": "\n".join(story.body),
        "key_facts": "\n".join(story.key_facts),
        "verification_notes": "\n".join(story.verification_notes),
    }
    valid_issues = []
    for issue in result.issues:
        haystack = fields.get(issue.field, "")
        if issue.draft_text and issue.draft_text in haystack:
            valid_issues.append(issue)

    result.issues = valid_issues

    material = [
        i
        for i in valid_issues
        if i.severity.lower() == "material"
    ]

    result.ok = not bool(material)

    # Keep corrected fields whenever ANY valid issue remains,
    # including minor errors. process_city can then apply the
    # supplied correction instead of knowingly saving the error.
    if not valid_issues:
        result.corrected_headline = ""
        result.corrected_dek = ""
        result.corrected_body = []
        result.corrected_key_facts = []
        result.corrected_verification_notes = []

    return result
