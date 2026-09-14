import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import auto_processor


FROZEN = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def _configure(tmp_path, monkeypatch):
    db = tmp_path / "queue.db"
    drafts = tmp_path / "drafts"
    drafts.mkdir()

    monkeypatch.setattr(auto_processor, "PI_DB", db)
    monkeypatch.setattr(auto_processor, "DRAFTS", drafts)
    monkeypatch.setattr(
        auto_processor,
        "CITY_NAMES",
        {"rsm": "Rancho Santa Margarita"},
    )
    monkeypatch.setattr(auto_processor, "now", lambda: FROZEN)

    with sqlite3.connect(db) as con:
        con.executescript(
            """
            CREATE TABLE meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                city_slug TEXT NOT NULL,
                external_id TEXT NOT NULL,
                kind TEXT,
                status TEXT,
                recording_status TEXT,
                recording_url TEXT,
                meeting_date TEXT,
                processed_at TEXT,
                UNIQUE(city_slug, external_id)
            );

            CREATE TABLE processing_jobs (
                city_slug TEXT NOT NULL,
                external_id TEXT NOT NULL,
                state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT,
                updated_at TEXT,
                next_attempt_at TEXT,
                completed_at TEXT,
                last_error TEXT,
                PRIMARY KEY(city_slug, external_id)
            );
            """
        )

    return db, drafts


def _meeting(
    con,
    slug="rsm",
    external_id="meeting-1",
    meeting_date="2020-01-01",
    kind="completed",
    status="ready",
    recording_status="found",
    recording_url="https://example.com/video",
):
    con.execute(
        """
        INSERT INTO meetings (
            city_slug, external_id, kind, status,
            recording_status, recording_url, meeting_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            slug,
            external_id,
            kind,
            status,
            recording_status,
            recording_url,
            meeting_date,
        ),
    )
    con.commit()


def _job(
    con,
    slug="rsm",
    external_id="meeting-1",
    state="pending",
    attempts=0,
    updated_at=None,
    next_attempt_at=None,
    last_error=None,
):
    con.execute(
        """
        INSERT INTO processing_jobs (
            city_slug, external_id, state, attempts,
            created_at, updated_at, next_attempt_at, last_error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            slug,
            external_id,
            state,
            attempts,
            auto_processor.iso(FROZEN - timedelta(days=1)),
            updated_at or auto_processor.iso(FROZEN),
            next_attempt_at,
            last_error,
        ),
    )
    con.commit()


def _draft(drafts, slug, external_id, payload):
    path = drafts / f"{slug}--{external_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_iso_and_audited_draft_fail_closed(tmp_path, monkeypatch):
    _, drafts = _configure(tmp_path, monkeypatch)

    assert auto_processor.iso(FROZEN) == FROZEN.isoformat()
    assert auto_processor.iso() == FROZEN.isoformat()
    assert auto_processor.audited_draft("rsm", "missing") is False

    path = drafts / "rsm--bad.json"
    path.write_text("{not-json", encoding="utf-8")
    assert auto_processor.audited_draft("rsm", "bad") is False

    _draft(drafts, "rsm", "false", {"audit_ok": False})
    _draft(drafts, "rsm", "true", {"audit_ok": True})
    assert auto_processor.audited_draft("rsm", "false") is False
    assert auto_processor.audited_draft("rsm", "true") is True


def test_seed_jobs_filters_and_is_idempotent(tmp_path, monkeypatch):
    db, _ = _configure(tmp_path, monkeypatch)

    with sqlite3.connect(db) as con:
        _meeting(con, external_id="eligible")
        _meeting(con, external_id="wrong-kind", kind="scheduled")
        _meeting(con, external_id="wrong-status", status="new")
        _meeting(con, external_id="no-recording", recording_status="missing")
        _meeting(con, external_id="empty-url", recording_url="")
        _meeting(con, external_id="future", meeting_date="2999-01-01")

        auto_processor.seed_jobs(con)
        auto_processor.seed_jobs(con)

        rows = con.execute(
            "SELECT external_id, state, attempts FROM processing_jobs"
        ).fetchall()

    assert rows == [("eligible", "pending", 0)]


def test_reconcile_existing_marks_only_audited_jobs_done(tmp_path, monkeypatch):
    db, drafts = _configure(tmp_path, monkeypatch)

    with sqlite3.connect(db) as con:
        _meeting(con, external_id="audited")
        _meeting(con, external_id="not-audited")
        _meeting(con, external_id="already-done")
        _job(con, external_id="audited", last_error="old")
        _job(con, external_id="not-audited")
        _job(con, external_id="already-done", state="done")

        _draft(drafts, "rsm", "audited", {"audit_ok": True})
        _draft(drafts, "rsm", "not-audited", {"audit_ok": False})

        auto_processor.reconcile_existing(con)

        jobs = {
            row[0]: row[1:]
            for row in con.execute(
                """
                SELECT external_id, state, completed_at, last_error
                FROM processing_jobs
                ORDER BY external_id
                """
            )
        }
        meetings = dict(
            con.execute(
                "SELECT external_id, processed_at FROM meetings"
            ).fetchall()
        )

    assert jobs["audited"] == (
        "done",
        FROZEN.isoformat(),
        None,
    )
    assert meetings["audited"] == FROZEN.isoformat()
    assert jobs["not-audited"][0] == "pending"
    assert meetings["not-audited"] is None
    assert jobs["already-done"][0] == "done"


def test_reclaim_stale_only_recovers_old_running_jobs(tmp_path, monkeypatch):
    db, _ = _configure(tmp_path, monkeypatch)

    with sqlite3.connect(db) as con:
        _job(
            con,
            external_id="stale",
            state="running",
            updated_at=auto_processor.iso(FROZEN - timedelta(hours=4)),
        )
        _job(
            con,
            external_id="recent",
            state="running",
            updated_at=auto_processor.iso(FROZEN - timedelta(hours=1)),
        )
        _job(
            con,
            external_id="pending",
            state="pending",
            updated_at=auto_processor.iso(FROZEN - timedelta(hours=8)),
        )

        auto_processor.reclaim_stale(con)

        rows = {
            row[0]: row[1:]
            for row in con.execute(
                """
                SELECT external_id, state, next_attempt_at,
                       updated_at, last_error
                FROM processing_jobs
                """
            )
        }

    assert rows["stale"] == (
        "deferred_error",
        auto_processor.iso(FROZEN + timedelta(hours=1)),
        FROZEN.isoformat(),
        "Recovered stale running job",
    )
    assert rows["recent"][0] == "running"
    assert rows["pending"][0] == "pending"


def test_classify_error_covers_api_media_retry_and_terminal_paths(
    tmp_path,
    monkeypatch,
):
    _configure(tmp_path, monkeypatch)

    state, retry, message = auto_processor.classify_error(
        RuntimeError("429 quota exceeded"),
        1,
    )
    assert state == "deferred_api"
    assert retry == FROZEN + timedelta(hours=1)
    assert message == "RuntimeError: 429 quota exceeded"

    state, retry, _ = auto_processor.classify_error(
        RuntimeError("No usable audio was produced"),
        1,
    )
    assert (state, retry) == ("no_usable_media", None)

    state, retry, _ = auto_processor.classify_error(
        RuntimeError("yt-dlp timed out"),
        1,
    )
    assert state == "deferred_media"
    assert retry == FROZEN + timedelta(hours=8)

    state, retry, _ = auto_processor.classify_error(
        ValueError("ordinary permanent failure"),
        3,
    )
    assert (state, retry) == ("failed", None)

    state, retry, _ = auto_processor.classify_error(
        ValueError("ordinary retryable failure"),
        2,
    )
    assert state == "deferred_error"
    assert retry == FROZEN + timedelta(hours=4)


def test_print_status_formats_rows_and_missing_retry(tmp_path, monkeypatch, capsys):
    db, _ = _configure(tmp_path, monkeypatch)

    with sqlite3.connect(db) as con:
        _job(con, external_id="a", state="pending", attempts=1)
        _job(
            con,
            slug="zzz",
            external_id="b",
            state="deferred_api",
            attempts=2,
            next_attempt_at="2026-09-14T13:00:00+00:00",
        )
        auto_processor.print_status(con)

    output = capsys.readouterr().out
    assert "PROCESSING QUEUE" in output
    assert "a" in output and "attempts=1" in output and "next=-" in output
    assert "b" in output and "deferred_api" in output
    assert "next=2026-09-14T13:00:00+00:00" in output


def test_run_one_reports_no_ready_jobs(tmp_path, monkeypatch, capsys):
    _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(
        auto_processor,
        "process_city",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("process_city should not run")
        ),
    )

    auto_processor.run_one()

    output = capsys.readouterr().out
    assert "No CouncilWatch jobs ready." in output
    assert "PROCESSING QUEUE" in output


def test_run_one_success_processes_oldest_job_and_marks_meeting(tmp_path, monkeypatch, capsys):
    db, drafts = _configure(tmp_path, monkeypatch)

    with sqlite3.connect(db) as con:
        _meeting(
            con,
            external_id="newer",
            meeting_date="2020-02-01",
        )
        _meeting(
            con,
            external_id="older",
            meeting_date="2020-01-01",
        )

    observed = {}

    def fake_process(slug, **kwargs):
        meeting = kwargs["meeting_override"]
        observed["slug"] = slug
        observed["force_story"] = kwargs["force_story"]
        observed["force_notes"] = kwargs["force_notes"]
        observed["external_id"] = meeting["external_id"]
        observed["city_name"] = meeting["city_name"]
        _draft(
            drafts,
            slug,
            str(meeting["external_id"]),
            {"audit_ok": True},
        )

    monkeypatch.setattr(auto_processor, "process_city", fake_process)

    auto_processor.run_one()

    assert observed == {
        "slug": "rsm",
        "force_story": False,
        "force_notes": False,
        "external_id": "older",
        "city_name": "Rancho Santa Margarita",
    }

    with sqlite3.connect(db) as con:
        job = con.execute(
            """
            SELECT state, attempts, completed_at, last_error, next_attempt_at
            FROM processing_jobs
            WHERE external_id='older'
            """
        ).fetchone()
        newer = con.execute(
            "SELECT state, attempts FROM processing_jobs WHERE external_id='newer'"
        ).fetchone()
        processed = con.execute(
            "SELECT processed_at FROM meetings WHERE external_id='older'"
        ).fetchone()[0]

    assert job == ("done", 1, FROZEN.isoformat(), None, None)
    assert newer == ("pending", 0)
    assert processed == FROZEN.isoformat()

    output = capsys.readouterr().out
    assert "AUTOMATIC COUNCILWATCH JOB" in output
    assert "City   : Rancho Santa Margarita" in output
    assert "Attempt: 1" in output
    assert "AUTOMATIC JOB COMPLETE" in output


def test_run_one_defers_temporary_media_failure(tmp_path, monkeypatch, capsys):
    db, _ = _configure(tmp_path, monkeypatch)

    with sqlite3.connect(db) as con:
        _meeting(con, slug="unknown", external_id="media-fail")

    def fail_process(*args, **kwargs):
        raise RuntimeError("yt-dlp timed out")

    monkeypatch.setattr(auto_processor, "process_city", fail_process)

    auto_processor.run_one()

    with sqlite3.connect(db) as con:
        row = con.execute(
            """
            SELECT state, attempts, next_attempt_at, last_error
            FROM processing_jobs
            WHERE external_id='media-fail'
            """
        ).fetchone()

    assert row == (
        "deferred_media",
        1,
        auto_processor.iso(FROZEN + timedelta(hours=8)),
        "RuntimeError: yt-dlp timed out",
    )

    output = capsys.readouterr().out
    assert "City   : unknown" in output
    assert "AUTOMATIC JOB DEFERRED" in output
    assert "State : deferred_media" in output
    assert "Retry : 2026-09-14T20:00:00+00:00" in output


def test_run_one_terminal_media_failure_has_no_retry(tmp_path, monkeypatch, capsys):
    db, _ = _configure(tmp_path, monkeypatch)

    with sqlite3.connect(db) as con:
        _meeting(con, external_id="terminal-media")

    monkeypatch.setattr(
        auto_processor,
        "process_city",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("No usable audio")
        ),
    )

    auto_processor.run_one()

    with sqlite3.connect(db) as con:
        row = con.execute(
            """
            SELECT state, next_attempt_at, last_error
            FROM processing_jobs
            WHERE external_id='terminal-media'
            """
        ).fetchone()

    assert row == (
        "no_usable_media",
        None,
        "RuntimeError: No usable audio",
    )
    output = capsys.readouterr().out
    assert "State : no_usable_media" in output
    assert "Retry :" not in output


def test_run_one_defers_when_processor_returns_without_audited_draft(
    tmp_path,
    monkeypatch,
):
    db, _ = _configure(tmp_path, monkeypatch)

    with sqlite3.connect(db) as con:
        _meeting(con, external_id="no-draft")

    monkeypatch.setattr(auto_processor, "process_city", lambda *args, **kwargs: None)

    auto_processor.run_one()

    with sqlite3.connect(db) as con:
        row = con.execute(
            """
            SELECT state, next_attempt_at, last_error
            FROM processing_jobs
            WHERE external_id='no-draft'
            """
        ).fetchone()

    assert row[0] == "deferred_error"
    assert row[1] == auto_processor.iso(FROZEN + timedelta(hours=4))
    assert row[2] == (
        "RuntimeError: Processing returned without an audited draft"
    )


def test_main_status_seeds_reclaims_reconciles_and_never_runs_processor(
    tmp_path,
    monkeypatch,
    capsys,
):
    db, drafts = _configure(tmp_path, monkeypatch)

    with sqlite3.connect(db) as con:
        _meeting(con, external_id="fresh")
        _meeting(con, external_id="audited")
        _job(
            con,
            external_id="audited",
            state="running",
            updated_at=auto_processor.iso(FROZEN - timedelta(hours=4)),
        )

    _draft(drafts, "rsm", "audited", {"audit_ok": True})

    monkeypatch.setattr(
        auto_processor,
        "process_city",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("status mode must not process")
        ),
    )
    monkeypatch.setattr(sys, "argv", ["auto_processor.py", "--status"])

    auto_processor.main()

    with sqlite3.connect(db) as con:
        states = dict(
            con.execute(
                "SELECT external_id, state FROM processing_jobs"
            ).fetchall()
        )

    assert states == {"audited": "done", "fresh": "pending"}
    assert "PROCESSING QUEUE" in capsys.readouterr().out


def test_main_without_status_delegates_to_run_one(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(auto_processor, "run_one", lambda: calls.append("run"))
    monkeypatch.setattr(sys, "argv", ["auto_processor.py"])

    auto_processor.main()

    assert calls == ["run"]
