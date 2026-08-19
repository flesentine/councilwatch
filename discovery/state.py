
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  city_slug TEXT NOT NULL,
  external_id TEXT NOT NULL,
  meeting_date TEXT,
  title TEXT NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  recording_status TEXT NOT NULL,
  source_url TEXT,
  agenda_url TEXT,
  recording_url TEXT,
  notes TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  processed_at TEXT,
  raw_json TEXT NOT NULL,
  UNIQUE(city_slug, external_id)
);
CREATE INDEX IF NOT EXISTS idx_meetings_city_date ON meetings(city_slug, meeting_date DESC);
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  ok_count INTEGER NOT NULL DEFAULT 0,
  error_count INTEGER NOT NULL DEFAULT 0,
  summary_json TEXT
);
"""

class StateDB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as con:
            con.executescript(SCHEMA)

    def connect(self):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=15000")
        return con

    def begin_scan(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as con:
            cur = con.execute("INSERT INTO scans(started_at) VALUES(?)", (now,))
            return int(cur.lastrowid)

    def finish_scan(self, scan_id: int, ok_count: int, error_count: int, summary: dict):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as con:
            con.execute(
                "UPDATE scans SET finished_at=?, ok_count=?, error_count=?, summary_json=? WHERE id=?",
                (now, ok_count, error_count, json.dumps(summary), scan_id),
            )

    def upsert_meeting(self, meeting: dict) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        raw = json.dumps(meeting, sort_keys=True)
        with self.connect() as con:
            row = con.execute(
                "SELECT id FROM meetings WHERE city_slug=? AND external_id=?",
                (meeting["city_slug"], meeting["external_id"]),
            ).fetchone()

            # A meeting may first be discovered as an upcoming event with a
            # synthetic/date-based external_id, then later receive a vendor
            # recording/clip ID. Reconcile that transition instead of keeping
            # duplicate upcoming + completed rows.
            reconciled_row = None
            if (
                row is None
                and meeting.get("kind") == "completed"
                and meeting.get("meeting_date")
            ):
                candidates = con.execute(
                    """
                    SELECT id, external_id
                    FROM meetings
                    WHERE city_slug=?
                      AND meeting_date=?
                      AND kind='upcoming'
                    ORDER BY id
                    """,
                    (meeting["city_slug"], meeting["meeting_date"]),
                ).fetchall()
                if len(candidates) == 1:
                    reconciled_row = candidates[0]
                    con.execute(
                        """
                        UPDATE meetings
                        SET external_id=?, meeting_date=?, title=?, kind=?, status=?,
                            recording_status=?, source_url=?, agenda_url=?,
                            recording_url=?, notes=?, last_seen_at=?, raw_json=?
                        WHERE id=?
                        """,
                        (
                            meeting["external_id"],
                            meeting.get("meeting_date", ""),
                            meeting["title"],
                            meeting["kind"],
                            meeting["status"],
                            meeting["recording_status"],
                            meeting.get("source_url", ""),
                            meeting.get("agenda_url", ""),
                            meeting.get("recording_url", ""),
                            meeting.get("notes", ""),
                            now,
                            raw,
                            reconciled_row["id"],
                        ),
                    )
                    row = con.execute(
                        "SELECT id FROM meetings WHERE id=?",
                        (reconciled_row["id"],),
                    ).fetchone()

            is_new = row is None
            if is_new:
                con.execute(
                    """INSERT INTO meetings(
                         city_slug, external_id, meeting_date, title, kind, status, recording_status,
                         source_url, agenda_url, recording_url, notes, first_seen_at, last_seen_at, raw_json
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        meeting["city_slug"], meeting["external_id"], meeting.get("meeting_date", ""),
                        meeting["title"], meeting["kind"], meeting["status"], meeting["recording_status"],
                        meeting.get("source_url", ""), meeting.get("agenda_url", ""), meeting.get("recording_url", ""),
                        meeting.get("notes", ""), now, now, raw,
                    ),
                )
            else:
                con.execute(
                    """UPDATE meetings SET meeting_date=?, title=?, kind=?, status=?, recording_status=?,
                         source_url=?, agenda_url=?, recording_url=?, notes=?, last_seen_at=?, raw_json=?
                       WHERE city_slug=? AND external_id=?""",
                    (
                        meeting.get("meeting_date", ""), meeting["title"], meeting["kind"], meeting["status"],
                        meeting["recording_status"], meeting.get("source_url", ""), meeting.get("agenda_url", ""),
                        meeting.get("recording_url", ""), meeting.get("notes", ""), now, raw,
                        meeting["city_slug"], meeting["external_id"],
                    ),
                )
        return is_new

    def recent(self, limit=30):
        with self.connect() as con:
            return [dict(r) for r in con.execute(
                "SELECT * FROM meetings ORDER BY meeting_date DESC, city_slug LIMIT ?", (limit,)
            ).fetchall()]
