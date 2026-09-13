import sqlite3

import pytest

import meetings


def create_db(path, rows=()):
    with sqlite3.connect(path) as con:
        con.execute(
            """
            CREATE TABLE meetings (
                id INTEGER PRIMARY KEY,
                city_slug TEXT NOT NULL,
                meeting_date TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                recording_url TEXT NOT NULL,
                title TEXT
            )
            """
        )
        con.executemany(
            """
            INSERT INTO meetings (
                id,
                city_slug,
                meeting_date,
                kind,
                status,
                recording_url,
                title
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def test_missing_database_raises_clear_error(tmp_path, monkeypatch):
    db = tmp_path / "missing.db"
    monkeypatch.setattr(meetings, "PI_DB", db)

    with pytest.raises(FileNotFoundError) as excinfo:
        meetings.latest_ready_meetings()

    assert str(db) in str(excinfo.value)


def test_latest_ready_meetings_filters_tiebreaks_and_orders_by_city_config(
    tmp_path,
    monkeypatch,
):
    db = tmp_path / "meetings.db"
    create_db(
        db,
        [
            # RSM: old eligible row.
            (
                1,
                "rsm",
                "2026-09-01",
                "completed",
                "ready",
                "https://example.test/rsm-old",
                "Old RSM",
            ),
            # RSM: latest eligible date, lower id.
            (
                2,
                "rsm",
                "2026-09-09",
                "completed",
                "ready",
                "https://example.test/rsm-2",
                "RSM 2",
            ),
            # RSM: same latest date, higher id must win.
            (
                3,
                "rsm",
                "2026-09-09",
                "completed",
                "ready",
                "https://example.test/rsm-3",
                "RSM 3",
            ),
            # Newer rows that must not qualify.
            (
                4,
                "rsm",
                "2026-09-20",
                "upcoming",
                "ready",
                "https://example.test/rsm-upcoming",
                "Upcoming RSM",
            ),
            (
                5,
                "rsm",
                "2026-09-19",
                "completed",
                "draft",
                "https://example.test/rsm-draft",
                "Draft RSM",
            ),
            (
                6,
                "rsm",
                "2026-09-18",
                "completed",
                "ready",
                "",
                "No recording RSM",
            ),
            # Aliso qualifies and SQL would alphabetize it before RSM.
            (
                10,
                "aliso-viejo",
                "2026-09-10",
                "completed",
                "ready",
                "https://example.test/aliso",
                "Aliso",
            ),
            # A ready city outside the configured set must not be returned.
            (
                20,
                "other-city",
                "2026-09-11",
                "completed",
                "ready",
                "https://example.test/other",
                "Other",
            ),
        ],
    )

    monkeypatch.setattr(meetings, "PI_DB", db)
    monkeypatch.setattr(
        meetings,
        "CITY_NAMES",
        {
            "rsm": "Rancho Santa Margarita",
            "aliso-viejo": "Aliso Viejo",
        },
    )

    result = meetings.latest_ready_meetings()

    assert [row["id"] for row in result] == [3, 10]
    assert [row["city_slug"] for row in result] == [
        "rsm",
        "aliso-viejo",
    ]
    assert [row["city_name"] for row in result] == [
        "Rancho Santa Margarita",
        "Aliso Viejo",
    ]
    assert result[0]["title"] == "RSM 3"


def test_returns_empty_list_when_database_has_no_eligible_meetings(
    tmp_path,
    monkeypatch,
):
    db = tmp_path / "meetings.db"
    create_db(
        db,
        [
            (
                1,
                "rsm",
                "2026-09-09",
                "completed",
                "draft",
                "https://example.test/draft",
                "Draft",
            ),
            (
                2,
                "rsm",
                "2026-09-10",
                "completed",
                "ready",
                "",
                "No recording",
            ),
        ],
    )

    monkeypatch.setattr(meetings, "PI_DB", db)
    monkeypatch.setattr(
        meetings,
        "CITY_NAMES",
        {"rsm": "Rancho Santa Margarita"},
    )

    assert meetings.latest_ready_meetings() == []
