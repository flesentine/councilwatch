from __future__ import annotations

import sqlite3
from settings import PI_DB, CITY_NAMES


def latest_ready_meetings() -> list[dict]:
    if not PI_DB.exists():
        raise FileNotFoundError(f"CouncilWatch DB not found: {PI_DB}")

    sql = """
        SELECT m.*
        FROM meetings m
        JOIN (
          SELECT city_slug, MAX(meeting_date) AS max_date
          FROM meetings
          WHERE kind='completed'
            AND status='ready'
            AND recording_url <> ''
          GROUP BY city_slug
        ) x
          ON x.city_slug=m.city_slug
         AND x.max_date=m.meeting_date
        WHERE m.kind='completed'
          AND m.status='ready'
          AND m.recording_url <> ''
        ORDER BY m.city_slug, m.id
    """

    with sqlite3.connect(PI_DB) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(sql).fetchall()

    by_city = {}
    for row in rows:
        d = dict(row)
        d["city_name"] = CITY_NAMES.get(d["city_slug"], d["city_slug"])
        old = by_city.get(d["city_slug"])
        if old is None or int(d["id"]) > int(old["id"]):
            by_city[d["city_slug"]] = d

    return [by_city[c] for c in CITY_NAMES if c in by_city]
