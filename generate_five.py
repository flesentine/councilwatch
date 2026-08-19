#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone

from agenda import agenda_text
from gemini_worker import make_source_notes, make_story, audit_story
from media import download_audio
from meetings import latest_ready_meetings
from settings import DRAFTS, WORK, STATUS_FILE, KEEP_MEDIA, TRANSCRIPT_MODEL, STORY_MODEL


def load_status():
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text())
        except Exception:
            pass
    return {"started_at": None, "updated_at": None, "cities": {}}


def save_status(status):
    status["updated_at"] = datetime.now(timezone.utc).isoformat()
    STATUS_FILE.write_text(json.dumps(status, indent=2))


def safe_id(meeting):
    return "".join(c for c in str(meeting["external_id"]) if c.isalnum() or c in "-_")


def story_path(meeting):
    return DRAFTS / f"{meeting['city_slug']}--{safe_id(meeting)}.json"


def notes_path(meeting):
    return DRAFTS / f"{meeting['city_slug']}--{safe_id(meeting)}.notes.txt"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    meetings = latest_ready_meetings()
    if not args.city and len(meetings) != 5:
        print(f"WARNING: expected 5 READY meetings; found {len(meetings)}.", flush=True)

    status = load_status()
    status["started_at"] = status.get("started_at") or datetime.now(timezone.utc).isoformat()
    save_status(status)

    print("\nCouncilWatch private five-story generator", flush=True)
    print("=" * 58, flush=True)
    print(f"Transcript model: {TRANSCRIPT_MODEL}", flush=True)
    print(f"Story model     : {STORY_MODEL}", flush=True)

    success = 0
    failed = 0

    for idx, meeting in enumerate(meetings, 1):
        slug = meeting["city_slug"]
        out = story_path(meeting)
        print(f"\n[{idx}/{len(meetings)}] {meeting['city_name']} - {meeting.get('meeting_date')}", flush=True)

        if out.exists() and not args.force:
            print("  already drafted; skipping", flush=True)
            status["cities"][slug] = {
                "city": meeting["city_name"], "phase": "complete", "draft": out.name,
                "meeting_date": meeting.get("meeting_date"), "external_id": meeting["external_id"],
                "message": "Existing draft reused.",
            }
            save_status(status)
            success += 1
            continue

        city_work = WORK / slug
        city_work.mkdir(parents=True, exist_ok=True)
        audio = city_work / "meeting.mp3"

        try:
            status["cities"][slug] = {
                "city": meeting["city_name"], "phase": "downloading",
                "meeting_date": meeting.get("meeting_date"), "external_id": meeting["external_id"],
                "message": "Downloading/extracting meeting audio.",
            }
            save_status(status)
            print("  downloading/extracting audio...", flush=True)
            download_audio(meeting["recording_url"], audio)

            status["cities"][slug]["phase"] = "source_notes"
            status["cities"][slug]["message"] = "Gemini is analyzing the full meeting."
            save_status(status)
            print("  generating detailed source notes...", flush=True)
            notes = make_source_notes(audio, meeting)
            notes_path(meeting).write_text(notes)

            status["cities"][slug]["phase"] = "agenda"
            status["cities"][slug]["message"] = "Reading official agenda/source page."
            save_status(status)
            print("  reading agenda...", flush=True)
            agenda = agenda_text(meeting.get("agenda_url") or "")

            status["cities"][slug]["phase"] = "writing"
            status["cities"][slug]["message"] = "Writing private draft story."
            save_status(status)
            print("  writing story...", flush=True)
            story = make_story(meeting, notes, agenda)

            status["cities"][slug]["phase"] = "auditing"
            status["cities"][slug]["message"] = "Checking names, numbers and claims."
            save_status(status)
            print("  auditing facts/names/repetition...", flush=True)
            audit = audit_story(meeting, notes, agenda, story)

            if not audit.ok and audit.corrected_body:
                if audit.corrected_headline:
                    story.headline = audit.corrected_headline
                if audit.corrected_dek:
                    story.dek = audit.corrected_dek
                story.body = audit.corrected_body
                if audit.corrected_key_facts:
                    story.key_facts = audit.corrected_key_facts
                if audit.corrected_verification_notes:
                    story.verification_notes = audit.corrected_verification_notes

            payload = {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "status": "READY FOR REVIEW",
                "city_slug": slug,
                "city_name": meeting["city_name"],
                "meeting_date": meeting.get("meeting_date"),
                "meeting_title": meeting.get("title"),
                "external_id": meeting["external_id"],
                "headline": story.headline,
                "dek": story.dek,
                "body": story.body,
                "key_facts": story.key_facts,
                "verification_notes": story.verification_notes,
                "audit_ok": audit.ok,
                "audit_issues": [i.model_dump() for i in audit.issues],
                "source_url": meeting.get("source_url"),
                "agenda_url": meeting.get("agenda_url"),
                "recording_url": meeting.get("recording_url"),
                "transcript_model": TRANSCRIPT_MODEL,
                "story_model": STORY_MODEL,
                "published": False,
            }
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

            status["cities"][slug] = {
                "city": meeting["city_name"], "phase": "complete", "draft": out.name,
                "meeting_date": meeting.get("meeting_date"), "external_id": meeting["external_id"],
                "message": "READY FOR REVIEW",
            }
            save_status(status)
            success += 1
            print(f"  READY FOR REVIEW: {out.name}", flush=True)

        except Exception as exc:
            failed += 1
            status["cities"][slug] = {
                "city": meeting["city_name"], "phase": "failed",
                "meeting_date": meeting.get("meeting_date"), "external_id": meeting["external_id"],
                "message": f"{type(exc).__name__}: {exc}",
            }
            save_status(status)
            print(f"  FAILED: {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()

        finally:
            if audio.exists() and not KEEP_MEDIA:
                try:
                    audio.unlink()
                except Exception:
                    pass

    print("\n" + "=" * 58, flush=True)
    print(f"Finished. Ready: {success} | Failed: {failed}", flush=True)
    print("Review: http://raspberrypi.local:8080", flush=True)
    if failed:
        sys.exit(2)


if __name__ == "__main__":
    main()
