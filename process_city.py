#!/usr/bin/env python3

import argparse
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path

from agenda import agenda_text
from gemini_worker import (
    make_source_notes,
    make_story,
    audit_story,
)
from source_acquisition import acquire_source
from meetings import latest_ready_meetings
from meeting_intelligence import (
    build_meeting_intelligence,
    writer_context,
    make_rich_story,
    make_comprehensive_source_notes,
    retry_api_call,
)
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

        audit_notes = (
            notes
            + "\n\n"
            + writer_context(
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

        # Always run one final audit against exactly
        # the text that will be saved.
        print()
        print("Running FINAL audit on saved copy...")

        final_audit = retry_api_call(
            "Final audit",
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
