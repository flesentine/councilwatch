#!/usr/bin/env python3

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from process_city import process_city
from settings import PI_DB, DRAFTS, CITY_NAMES


def now():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or now()).isoformat()


def audited_draft(slug, external_id):
    p = DRAFTS / f"{slug}--{external_id}.json"

    if not p.exists():
        return False

    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d.get("audit_ok") is True
    except Exception:
        return False


def seed_jobs(con):
    rows = con.execute("""
        SELECT city_slug, external_id
        FROM meetings
        WHERE kind='completed'
          AND status='ready'
          AND recording_status='found'
          AND recording_url <> ''
          AND meeting_date < date('now', 'localtime')
    """).fetchall()

    for slug, external_id in rows:
        con.execute("""
            INSERT OR IGNORE INTO processing_jobs
            (
                city_slug,
                external_id,
                state,
                attempts,
                created_at,
                updated_at
            )
            VALUES (?, ?, 'pending', 0, ?, ?)
        """, (slug, external_id, iso(), iso()))

    con.commit()


def reconcile_existing(con):
    jobs = con.execute("""
        SELECT city_slug, external_id
        FROM processing_jobs
        WHERE state <> 'done'
    """).fetchall()

    for slug, external_id in jobs:
        if audited_draft(slug, external_id):
            con.execute("""
                UPDATE processing_jobs
                SET state='done',
                    updated_at=?,
                    completed_at=?,
                    last_error=NULL
                WHERE city_slug=? AND external_id=?
            """, (iso(), iso(), slug, external_id))

            con.execute("""
                UPDATE meetings
                SET processed_at=?
                WHERE city_slug=? AND external_id=?
            """, (iso(), slug, external_id))

    con.commit()


def reclaim_stale(con):
    cutoff = iso(now() - timedelta(hours=3))

    con.execute("""
        UPDATE processing_jobs
        SET state='deferred_error',
            next_attempt_at=?,
            updated_at=?,
            last_error='Recovered stale running job'
        WHERE state='running'
          AND updated_at < ?
    """, (
        iso(now() + timedelta(hours=1)),
        iso(),
        cutoff,
    ))

    con.commit()


def classify_error(exc, attempts):
    message = f"{type(exc).__name__}: {exc}"
    low = message.lower()

    api_terms = [
        "429",
        "503",
        "resource_exhausted",
        "unavailable",
        "high demand",
        "quota",
        "temporary gemini",
    ]

    if any(x in low for x in api_terms):
        return (
            "deferred_api",
            now() + timedelta(hours=4),
            message,
        )

    permanent_media = [
        "output file #0 does not contain any stream",
        "no usable audio",
        "produced no usable audio",
        "did not produce usable audio",
    ]

    if any(x in low for x in permanent_media):
        return (
            "no_usable_media",
            None,
            message,
        )

    temporary_media = [
        "http error 403",
        "unable to download",
        "yt-dlp",
        "timed out",
        "connection reset",
    ]

    if any(x in low for x in temporary_media):
        return (
            "deferred_media",
            now() + timedelta(hours=8),
            message,
        )

    if attempts >= 3:
        return (
            "failed",
            None,
            message,
        )

    return (
        "deferred_error",
        now() + timedelta(hours=4),
        message,
    )


def print_status(con):
    rows = con.execute("""
        SELECT city_slug, external_id, state,
               attempts, next_attempt_at
        FROM processing_jobs
        ORDER BY city_slug, external_id
    """).fetchall()

    print()
    print("PROCESSING QUEUE")
    print("==============================================")

    for row in rows:
        print(
            f"{row[0]:15} "
            f"{row[1]:18} "
            f"{row[2]:18} "
            f"attempts={row[3]} "
            f"next={row[4] or '-'}"
        )


def run_one():
    with sqlite3.connect(PI_DB) as con:
        con.row_factory = sqlite3.Row

        seed_jobs(con)
        reclaim_stale(con)
        reconcile_existing(con)

        row = con.execute("""
            SELECT j.*, m.*
            FROM processing_jobs j
            JOIN meetings m
              ON m.city_slug=j.city_slug
             AND m.external_id=j.external_id
            WHERE j.state IN (
                'pending',
                'deferred_api',
                'deferred_media',
                'deferred_error'
            )
              AND (
                j.next_attempt_at IS NULL
                OR j.next_attempt_at <= ?
              )
              AND m.kind='completed'
              AND m.status='ready'
              AND m.recording_status='found'
              AND m.recording_url <> ''
              AND m.meeting_date < date('now', 'localtime')
            ORDER BY m.meeting_date ASC, m.id ASC
            LIMIT 1
        """, (iso(),)).fetchone()

        if row is None:
            print("No CouncilWatch jobs ready.")
            print_status(con)
            return

        meeting = dict(row)

        slug = meeting["city_slug"]
        external_id = str(meeting["external_id"])

        attempts = int(meeting["attempts"]) + 1

        con.execute("""
            UPDATE processing_jobs
            SET state='running',
                attempts=?,
                updated_at=?,
                next_attempt_at=NULL
            WHERE city_slug=? AND external_id=?
        """, (attempts, iso(), slug, external_id))

        con.commit()

        meeting["city_name"] = CITY_NAMES.get(
            slug,
            slug,
        )

        print()
        print("==============================================")
        print("AUTOMATIC COUNCILWATCH JOB")
        print("==============================================")
        print("City   :", meeting["city_name"])
        print("Date   :", meeting.get("meeting_date"))
        print("ID     :", external_id)
        print("Attempt:", attempts)
        print()

        try:
            process_city(
                slug,
                force_story=False,
                force_notes=False,
                meeting_override=meeting,
            )

            if not audited_draft(slug, external_id):
                raise RuntimeError(
                    "Processing returned without an audited draft"
                )

            con.execute("""
                UPDATE processing_jobs
                SET state='done',
                    updated_at=?,
                    completed_at=?,
                    last_error=NULL,
                    next_attempt_at=NULL
                WHERE city_slug=? AND external_id=?
            """, (
                iso(),
                iso(),
                slug,
                external_id,
            ))

            con.execute("""
                UPDATE meetings
                SET processed_at=?
                WHERE city_slug=? AND external_id=?
            """, (
                iso(),
                slug,
                external_id,
            ))

            con.commit()

            print()
            print("AUTOMATIC JOB COMPLETE")

        except Exception as exc:
            state, retry_at, error = classify_error(
                exc,
                attempts,
            )

            con.execute("""
                UPDATE processing_jobs
                SET state=?,
                    next_attempt_at=?,
                    last_error=?,
                    updated_at=?
                WHERE city_slug=? AND external_id=?
            """, (
                state,
                iso(retry_at) if retry_at else None,
                error[-4000:],
                iso(),
                slug,
                external_id,
            ))

            con.commit()

            print()
            print("AUTOMATIC JOB DEFERRED")
            print("State :", state)
            print("Error :", error)

            if retry_at:
                print("Retry :", iso(retry_at))

        print_status(con)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--status",
        action="store_true",
    )
    args = parser.parse_args()

    if args.status:
        with sqlite3.connect(PI_DB) as con:
            seed_jobs(con)
            reclaim_stale(con)
            reconcile_existing(con)
            print_status(con)
        return

    run_one()


if __name__ == "__main__":
    main()
