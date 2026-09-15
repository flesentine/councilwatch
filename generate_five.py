#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone

from meetings import latest_ready_meetings
from process_city import process_city
from settings import DRAFTS, STATUS_FILE, TRANSCRIPT_MODEL, STORY_MODEL


def load_status():
    if STATUS_FILE.exists():
        try:
            return json.loads(
                STATUS_FILE.read_text(encoding="utf-8")
            )
        except Exception:
            pass

    return {
        "started_at": None,
        "updated_at": None,
        "cities": {},
    }


def save_status(status):
    status["updated_at"] = datetime.now(
        timezone.utc
    ).isoformat()

    STATUS_FILE.write_text(
        json.dumps(
            status,
            indent=2,
        ),
        encoding="utf-8",
    )


def story_path(meeting):
    return (
        DRAFTS
        / f"{meeting['city_slug']}--{meeting['external_id']}.json"
    )


def read_draft(path):
    if not path.exists():
        return None

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8")
        )
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    return payload


def hardened_draft(payload):
    """
    Identify drafts produced by the modern process_city pipeline.

    Legacy generate_five drafts can have audit_ok=True but never
    received the validated action ledger, entity verification or
    exact saved-copy final audit. They must not be silently reused.
    """
    if not isinstance(payload, dict):
        return False

    return (
        payload.get("final_audit") is True
        and isinstance(
            payload.get("action_ledger"),
            list,
        )
        and isinstance(
            payload.get("coverage_plan"),
            list,
        )
        and isinstance(
            payload.get("entity_verification"),
            list,
        )
    )


def reusable_audited_draft(path):
    payload = read_draft(path)

    return (
        hardened_draft(payload)
        and payload.get("audit_ok") is True
    )


def mark_existing_complete(meeting, path):
    """
    Preserve the five-city status behavior for an already hardened,
    audited draft without overwriting status produced by another
    city/process_city invocation.
    """
    status = load_status()
    status["started_at"] = (
        status.get("started_at")
        or datetime.now(timezone.utc).isoformat()
    )
    status.setdefault("cities", {})
    status["cities"][meeting["city_slug"]] = {
        "city": meeting["city_name"],
        "phase": "complete",
        "draft": path.name,
        "meeting_date": meeting.get("meeting_date"),
        "external_id": meeting["external_id"],
        "message": "Existing hardened audited draft reused.",
    }
    save_status(status)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Regenerate both source evidence and story output "
            "for all five selected meetings."
        ),
    )
    args = parser.parse_args()

    meetings = latest_ready_meetings()

    if len(meetings) != 5:
        print(
            f"WARNING: expected 5 READY meetings; "
            f"found {len(meetings)}.",
            flush=True,
        )

    status = load_status()
    status["started_at"] = (
        status.get("started_at")
        or datetime.now(timezone.utc).isoformat()
    )
    save_status(status)

    print(
        "\nCouncilWatch private five-story generator",
        flush=True,
    )
    print("=" * 58, flush=True)
    print(
        f"Transcript model: {TRANSCRIPT_MODEL}",
        flush=True,
    )
    print(
        f"Story model     : {STORY_MODEL}",
        flush=True,
    )
    print(
        "Pipeline        : hardened process_city",
        flush=True,
    )

    success = 0
    failed = 0

    for index, meeting in enumerate(meetings, 1):
        slug = meeting["city_slug"]
        output = story_path(meeting)
        existing_payload = read_draft(output)
        existing_is_hardened = hardened_draft(
            existing_payload
        )

        print(
            f"\n[{index}/{len(meetings)}] "
            f"{meeting['city_name']} - "
            f"{meeting.get('meeting_date')}",
            flush=True,
        )

        if (
            not args.force
            and reusable_audited_draft(output)
        ):
            print(
                "  existing hardened audited draft; reusing",
                flush=True,
            )
            mark_existing_complete(
                meeting,
                output,
            )
            success += 1
            continue

        migration_force = (
            not args.force
            and output.exists()
            and not existing_is_hardened
        )

        if migration_force:
            print(
                "  legacy/unhardened draft detected; "
                "regenerating full evidence and story",
                flush=True,
            )

        try:
            process_city(
                slug,
                force_story=(
                    args.force
                    or migration_force
                ),
                force_notes=(
                    args.force
                    or migration_force
                ),
                meeting_override=meeting,
            )

            if not output.exists():
                raise RuntimeError(
                    "Hardened processing returned without "
                    "creating a review draft"
                )

            payload = json.loads(
                output.read_text(encoding="utf-8")
            )

            if not hardened_draft(payload):
                raise RuntimeError(
                    "Hardened processing returned a draft "
                    "without final-audit verification metadata"
                )

            success += 1

            if payload.get("audit_ok") is True:
                print(
                    f"  READY FOR REVIEW: {output.name}",
                    flush=True,
                )
            else:
                print(
                    "  READY FOR REVIEW - MATERIAL ISSUE: "
                    f"{output.name}",
                    flush=True,
                )

        except Exception as exc:
            failed += 1
            print(
                f"  FAILED: {type(exc).__name__}: {exc}",
                flush=True,
            )
            traceback.print_exc()

    print("\n" + "=" * 58, flush=True)
    print(
        f"Finished. Ready: {success} | Failed: {failed}",
        flush=True,
    )
    print(
        "Review: http://raspberrypi.local:8080",
        flush=True,
    )

    if failed:
        sys.exit(2)


if __name__ == "__main__":
    main()
