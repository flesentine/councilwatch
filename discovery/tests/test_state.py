from __future__ import annotations

import json
from datetime import datetime

from discovery.state import StateDB


def _meeting(**overrides):
    data = {
        "city_slug": "rsm",
        "external_id": "100",
        "meeting_date": "2026-09-10",
        "title": "City Council Regular Meeting",
        "kind": "completed",
        "status": "ready",
        "recording_status": "found",
        "source_url": "https://example.test/source",
        "agenda_url": "https://example.test/agenda",
        "recording_url": "https://example.test/video",
        "notes": "",
    }
    data.update(overrides)
    return data


def test_schema_and_scan_lifecycle_persist_to_nested_database(tmp_path):
    path = tmp_path / "nested" / "state.db"
    db = StateDB(path)

    assert path.exists()

    scan_id = db.begin_scan()
    with db.connect() as con:
        row = con.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()

    assert row["finished_at"] is None
    assert row["ok_count"] == 0
    assert row["error_count"] == 0
    assert row["summary_json"] is None
    assert datetime.fromisoformat(row["started_at"]).tzinfo is not None

    summary = {"cities": ["rsm", "lake-forest"], "note": "done"}
    db.finish_scan(scan_id, ok_count=2, error_count=1, summary=summary)

    with db.connect() as con:
        row = con.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()

    assert datetime.fromisoformat(row["finished_at"]).tzinfo is not None
    assert row["ok_count"] == 2
    assert row["error_count"] == 1
    assert json.loads(row["summary_json"]) == summary


def test_existing_meeting_update_is_not_new_and_preserves_first_seen(tmp_path):
    db = StateDB(tmp_path / "state.db")
    original = _meeting(notes="first", recording_url="old-video")

    assert db.upsert_meeting(original) is True

    with db.connect() as con:
        before = dict(
            con.execute(
                "SELECT * FROM meetings WHERE city_slug=? AND external_id=?",
                ("rsm", "100"),
            ).fetchone()
        )

    updated = _meeting(
        title="City Council - Updated Title",
        notes="second",
        recording_url="new-video",
        status="updated",
    )
    assert db.upsert_meeting(updated) is False

    with db.connect() as con:
        after = dict(
            con.execute(
                "SELECT * FROM meetings WHERE city_slug=? AND external_id=?",
                ("rsm", "100"),
            ).fetchone()
        )

    assert after["id"] == before["id"]
    assert after["first_seen_at"] == before["first_seen_at"]
    assert after["last_seen_at"] >= before["last_seen_at"]
    assert after["title"] == "City Council - Updated Title"
    assert after["notes"] == "second"
    assert after["recording_url"] == "new-video"
    assert after["status"] == "updated"
    assert json.loads(after["raw_json"]) == updated


def test_completed_meeting_reconciles_single_upcoming_row_on_same_city_and_date(tmp_path):
    db = StateDB(tmp_path / "state.db")
    upcoming = _meeting(
        external_id="date-2026-09-10",
        kind="upcoming",
        status="scheduled",
        recording_status="pending",
        recording_url="",
    )
    assert db.upsert_meeting(upcoming) is True

    with db.connect() as con:
        before = dict(con.execute("SELECT * FROM meetings").fetchone())

    completed = _meeting(
        external_id="clip-777",
        title="City Council Completed Meeting",
        recording_url="https://example.test/video/777",
    )
    assert db.upsert_meeting(completed) is False

    with db.connect() as con:
        rows = [dict(row) for row in con.execute("SELECT * FROM meetings").fetchall()]

    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == before["id"]
    assert row["first_seen_at"] == before["first_seen_at"]
    assert row["external_id"] == "clip-777"
    assert row["kind"] == "completed"
    assert row["recording_status"] == "found"
    assert row["recording_url"] == "https://example.test/video/777"
    assert json.loads(row["raw_json"]) == completed


def test_completed_meeting_does_not_reconcile_ambiguous_upcoming_rows(tmp_path):
    db = StateDB(tmp_path / "state.db")

    assert db.upsert_meeting(
        _meeting(external_id="upcoming-a", kind="upcoming", status="scheduled")
    ) is True
    assert db.upsert_meeting(
        _meeting(external_id="upcoming-b", kind="upcoming", status="scheduled")
    ) is True

    completed = _meeting(external_id="clip-888")
    assert db.upsert_meeting(completed) is True

    with db.connect() as con:
        rows = con.execute(
            "SELECT external_id, kind FROM meetings ORDER BY id"
        ).fetchall()

    assert [(row["external_id"], row["kind"]) for row in rows] == [
        ("upcoming-a", "upcoming"),
        ("upcoming-b", "upcoming"),
        ("clip-888", "completed"),
    ]


def test_completed_meeting_does_not_reconcile_upcoming_row_on_other_date(tmp_path):
    db = StateDB(tmp_path / "state.db")

    assert db.upsert_meeting(
        _meeting(
            external_id="upcoming-old",
            meeting_date="2026-09-09",
            kind="upcoming",
            status="scheduled",
        )
    ) is True
    assert db.upsert_meeting(_meeting(external_id="clip-999")) is True

    assert {row["external_id"] for row in db.recent()} == {
        "upcoming-old",
        "clip-999",
    }


def test_recent_orders_by_date_then_city_and_honors_limit(tmp_path):
    db = StateDB(tmp_path / "state.db")

    db.upsert_meeting(
        _meeting(
            city_slug="rsm",
            external_id="rsm-old",
            meeting_date="2026-09-01",
        )
    )
    db.upsert_meeting(
        _meeting(
            city_slug="rsm",
            external_id="rsm-new",
            meeting_date="2026-09-12",
        )
    )
    db.upsert_meeting(
        _meeting(
            city_slug="aliso-viejo",
            external_id="av-new",
            meeting_date="2026-09-12",
        )
    )

    recent = db.recent(limit=2)

    assert [(row["city_slug"], row["external_id"]) for row in recent] == [
        ("aliso-viejo", "av-new"),
        ("rsm", "rsm-new"),
    ]
