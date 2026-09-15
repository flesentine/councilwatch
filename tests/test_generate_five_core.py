import json
import sys

import pytest

import generate_five


def _meeting(index=1, **overrides):
    meeting = {
        "city_slug": f"city-{index}",
        "city_name": f"City {index}",
        "meeting_date": f"2026-09-{index:02d}",
        "title": f"Council Meeting {index}",
        "external_id": f"meeting-{index}",
        "source_url": f"https://example.com/source/{index}",
        "agenda_url": f"https://example.com/agenda/{index}",
        "recording_url": f"https://example.com/recording/{index}",
    }
    meeting.update(overrides)
    return meeting


def _configure_paths(tmp_path, monkeypatch):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    status = tmp_path / "status.json"

    monkeypatch.setattr(generate_five, "DRAFTS", drafts)
    monkeypatch.setattr(generate_five, "STATUS_FILE", status)
    return drafts, status


def _write_draft(
    path,
    *,
    audit_ok=True,
    headline="Existing headline",
    hardened=True,
):
    payload = {
        "audit_ok": audit_ok,
        "headline": headline,
    }

    if hardened:
        payload.update(
            {
                "final_audit": True,
                "action_ledger": [],
                "coverage_plan": [],
                "entity_verification": [],
            }
        )

    path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_load_status_defaults_reads_valid_json_and_fails_closed(
    tmp_path,
    monkeypatch,
):
    status_file = tmp_path / "status.json"
    monkeypatch.setattr(
        generate_five,
        "STATUS_FILE",
        status_file,
    )

    expected_default = {
        "started_at": None,
        "updated_at": None,
        "cities": {},
    }
    assert generate_five.load_status() == expected_default

    saved = {
        "started_at": "2026-09-14T10:00:00+00:00",
        "updated_at": "2026-09-14T10:01:00+00:00",
        "cities": {"rsm": {"phase": "complete"}},
    }
    status_file.write_text(
        json.dumps(saved),
        encoding="utf-8",
    )
    assert generate_five.load_status() == saved

    status_file.write_text(
        "{not-json",
        encoding="utf-8",
    )
    assert generate_five.load_status() == expected_default


def test_save_status_story_path_and_draft_classification_helpers(
    tmp_path,
    monkeypatch,
):
    drafts, status_file = _configure_paths(
        tmp_path,
        monkeypatch,
    )

    status = {
        "started_at": "kept",
        "updated_at": None,
        "cities": {},
    }
    generate_five.save_status(status)

    written = json.loads(
        status_file.read_text(encoding="utf-8")
    )
    assert written["started_at"] == "kept"
    assert written["updated_at"]
    assert status["updated_at"] == written["updated_at"]

    meeting = _meeting(
        city_slug="rsm",
        external_id="meeting-42",
    )
    path = generate_five.story_path(meeting)
    assert path == drafts / "rsm--meeting-42.json"
    assert generate_five.read_draft(path) is None
    assert not generate_five.hardened_draft(None)
    assert not generate_five.reusable_audited_draft(path)

    path.write_text("{bad-json", encoding="utf-8")
    assert generate_five.read_draft(path) is None
    assert not generate_five.reusable_audited_draft(path)

    path.write_text(
        json.dumps(["not", "a", "dict"]),
        encoding="utf-8",
    )
    assert generate_five.read_draft(path) is None

    _write_draft(
        path,
        audit_ok=True,
        hardened=False,
    )
    legacy = generate_five.read_draft(path)
    assert legacy["audit_ok"] is True
    assert not generate_five.hardened_draft(legacy)
    assert not generate_five.reusable_audited_draft(path)

    _write_draft(
        path,
        audit_ok=False,
        hardened=True,
    )
    hardened_failed = generate_five.read_draft(path)
    assert generate_five.hardened_draft(hardened_failed)
    assert not generate_five.reusable_audited_draft(path)

    _write_draft(
        path,
        audit_ok=True,
        hardened=True,
    )
    assert generate_five.hardened_draft(
        generate_five.read_draft(path)
    )
    assert generate_five.reusable_audited_draft(path)


def test_hardened_draft_requires_all_verification_metadata():
    base = {
        "final_audit": True,
        "action_ledger": [],
        "coverage_plan": [],
        "entity_verification": [],
    }
    assert generate_five.hardened_draft(base)

    for key in (
        "final_audit",
        "action_ledger",
        "coverage_plan",
        "entity_verification",
    ):
        payload = dict(base)
        payload.pop(key)
        assert not generate_five.hardened_draft(payload)

    wrong_type = dict(base)
    wrong_type["action_ledger"] = None
    assert not generate_five.hardened_draft(wrong_type)


def test_mark_existing_complete_reloads_status_and_preserves_other_city(
    tmp_path,
    monkeypatch,
):
    drafts, status_file = _configure_paths(
        tmp_path,
        monkeypatch,
    )
    meeting = _meeting(
        city_slug="lake-forest",
        city_name="Lake Forest",
        external_id="abc123",
    )
    path = drafts / "lake-forest--abc123.json"
    _write_draft(path)

    status_file.write_text(
        json.dumps(
            {
                "started_at": "existing-start",
                "updated_at": "old",
                "cities": {
                    "rsm": {
                        "phase": "complete",
                        "message": "Keep me",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    generate_five.mark_existing_complete(
        meeting,
        path,
    )

    status = json.loads(
        status_file.read_text(encoding="utf-8")
    )
    assert status["started_at"] == "existing-start"
    assert status["cities"]["rsm"]["message"] == "Keep me"

    reused = status["cities"]["lake-forest"]
    assert reused["phase"] == "complete"
    assert reused["draft"] == path.name
    assert reused["message"] == "Existing hardened audited draft reused."


def test_main_reuses_five_hardened_audited_drafts_without_process_city(
    tmp_path,
    monkeypatch,
    capsys,
):
    _, status_file = _configure_paths(
        tmp_path,
        monkeypatch,
    )
    meetings = [_meeting(i) for i in range(1, 6)]

    status_file.write_text(
        json.dumps(
            {
                "started_at": "existing-start",
                "updated_at": None,
                "cities": {},
            }
        ),
        encoding="utf-8",
    )

    for meeting in meetings:
        _write_draft(
            generate_five.story_path(meeting),
            audit_ok=True,
            hardened=True,
        )

    monkeypatch.setattr(
        generate_five,
        "latest_ready_meetings",
        lambda: meetings,
    )
    monkeypatch.setattr(
        generate_five,
        "process_city",
        lambda *args, **kwargs: pytest.fail(
            "hardened audited drafts should be reused"
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["generate_five.py"],
    )

    generate_five.main()

    output = capsys.readouterr().out
    assert "WARNING:" not in output
    assert output.count(
        "existing hardened audited draft; reusing"
    ) == 5
    assert "Pipeline        : hardened process_city" in output
    assert "Finished. Ready: 5 | Failed: 0" in output

    status = json.loads(
        status_file.read_text(encoding="utf-8")
    )
    assert status["started_at"] == "existing-start"
    assert set(status["cities"]) == {
        meeting["city_slug"]
        for meeting in meetings
    }
    assert all(
        row["message"] == "Existing hardened audited draft reused."
        for row in status["cities"].values()
    )


def test_main_migrates_legacy_audit_ok_draft_with_full_force(
    tmp_path,
    monkeypatch,
    capsys,
):
    _configure_paths(tmp_path, monkeypatch)
    meeting = _meeting(1)
    path = generate_five.story_path(meeting)
    _write_draft(
        path,
        audit_ok=True,
        headline="Legacy result",
        hardened=False,
    )

    calls = []

    def fake_process(
        slug,
        force_story=False,
        force_notes=False,
        meeting_override=None,
    ):
        calls.append(
            (
                slug,
                force_story,
                force_notes,
                meeting_override,
            )
        )
        _write_draft(
            path,
            audit_ok=True,
            headline="Hardened result",
            hardened=True,
        )

    monkeypatch.setattr(
        generate_five,
        "latest_ready_meetings",
        lambda: [meeting],
    )
    monkeypatch.setattr(
        generate_five,
        "process_city",
        fake_process,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["generate_five.py"],
    )

    generate_five.main()

    assert calls == [
        (
            meeting["city_slug"],
            True,
            True,
            meeting,
        )
    ]
    output = capsys.readouterr().out
    assert "legacy/unhardened draft detected" in output
    assert "READY FOR REVIEW" in output


def test_main_regenerates_hardened_material_draft_without_reacquiring_notes(
    tmp_path,
    monkeypatch,
):
    _configure_paths(tmp_path, monkeypatch)
    meeting = _meeting(2)
    path = generate_five.story_path(meeting)
    _write_draft(
        path,
        audit_ok=False,
        hardened=True,
    )

    calls = []

    def fake_process(
        slug,
        force_story=False,
        force_notes=False,
        meeting_override=None,
    ):
        calls.append(
            (
                slug,
                force_story,
                force_notes,
                meeting_override,
            )
        )
        _write_draft(
            path,
            audit_ok=True,
            hardened=True,
        )

    monkeypatch.setattr(
        generate_five,
        "latest_ready_meetings",
        lambda: [meeting],
    )
    monkeypatch.setattr(
        generate_five,
        "process_city",
        fake_process,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["generate_five.py"],
    )

    generate_five.main()

    assert calls == [
        (
            meeting["city_slug"],
            False,
            False,
            meeting,
        )
    ]


def test_main_force_maps_to_force_story_and_force_notes(
    tmp_path,
    monkeypatch,
):
    _configure_paths(tmp_path, monkeypatch)
    meeting = _meeting(3)
    path = generate_five.story_path(meeting)
    _write_draft(
        path,
        audit_ok=True,
        hardened=True,
    )

    calls = []

    def fake_process(
        slug,
        force_story=False,
        force_notes=False,
        meeting_override=None,
    ):
        calls.append(
            (
                slug,
                force_story,
                force_notes,
                meeting_override,
            )
        )
        _write_draft(
            path,
            audit_ok=True,
            hardened=True,
        )

    monkeypatch.setattr(
        generate_five,
        "latest_ready_meetings",
        lambda: [meeting],
    )
    monkeypatch.setattr(
        generate_five,
        "process_city",
        fake_process,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["generate_five.py", "--force"],
    )

    generate_five.main()

    assert calls == [
        (
            meeting["city_slug"],
            True,
            True,
            meeting,
        )
    ]


def test_main_preserves_reviewable_hardened_material_issue_draft(
    tmp_path,
    monkeypatch,
    capsys,
):
    _configure_paths(tmp_path, monkeypatch)
    meeting = _meeting(4)
    path = generate_five.story_path(meeting)

    def fake_process(*args, **kwargs):
        _write_draft(
            path,
            audit_ok=False,
            headline="Material issue remains",
            hardened=True,
        )

    monkeypatch.setattr(
        generate_five,
        "latest_ready_meetings",
        lambda: [meeting],
    )
    monkeypatch.setattr(
        generate_five,
        "process_city",
        fake_process,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["generate_five.py"],
    )

    generate_five.main()

    output = capsys.readouterr().out
    assert "READY FOR REVIEW - MATERIAL ISSUE" in output
    assert "Finished. Ready: 1 | Failed: 0" in output


def test_main_rejects_output_missing_hardened_verification_metadata(
    tmp_path,
    monkeypatch,
    capsys,
):
    _configure_paths(tmp_path, monkeypatch)
    meeting = _meeting(5)
    path = generate_five.story_path(meeting)

    def fake_process(*args, **kwargs):
        _write_draft(
            path,
            audit_ok=True,
            hardened=False,
        )

    monkeypatch.setattr(
        generate_five,
        "latest_ready_meetings",
        lambda: [meeting],
    )
    monkeypatch.setattr(
        generate_five,
        "process_city",
        fake_process,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["generate_five.py"],
    )

    with pytest.raises(SystemExit) as exc:
        generate_five.main()

    assert exc.value.code == 2
    output = capsys.readouterr().out
    assert "without final-audit verification metadata" in output
    assert "Finished. Ready: 0 | Failed: 1" in output


def test_main_isolates_city_failure_continues_and_exits_two(
    tmp_path,
    monkeypatch,
    capsys,
):
    _configure_paths(tmp_path, monkeypatch)
    failed_meeting = _meeting(1)
    good_meeting = _meeting(2)
    calls = []

    def fake_process(
        slug,
        force_story=False,
        force_notes=False,
        meeting_override=None,
    ):
        calls.append(slug)
        if slug == failed_meeting["city_slug"]:
            raise RuntimeError("source acquisition exploded")

        _write_draft(
            generate_five.story_path(good_meeting),
            audit_ok=True,
            hardened=True,
        )

    monkeypatch.setattr(
        generate_five,
        "latest_ready_meetings",
        lambda: [failed_meeting, good_meeting],
    )
    monkeypatch.setattr(
        generate_five,
        "process_city",
        fake_process,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["generate_five.py"],
    )

    with pytest.raises(SystemExit) as exc:
        generate_five.main()

    assert exc.value.code == 2
    assert calls == [
        failed_meeting["city_slug"],
        good_meeting["city_slug"],
    ]

    output = capsys.readouterr().out
    assert "FAILED: RuntimeError: source acquisition exploded" in output
    assert "Finished. Ready: 1 | Failed: 1" in output


def test_main_fails_city_if_hardened_pipeline_returns_without_draft(
    tmp_path,
    monkeypatch,
    capsys,
):
    _configure_paths(tmp_path, monkeypatch)
    meeting = _meeting(4)

    monkeypatch.setattr(
        generate_five,
        "latest_ready_meetings",
        lambda: [meeting],
    )
    monkeypatch.setattr(
        generate_five,
        "process_city",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["generate_five.py"],
    )

    with pytest.raises(SystemExit) as exc:
        generate_five.main()

    assert exc.value.code == 2
    output = capsys.readouterr().out
    assert "returned without creating a review draft" in output
    assert "Finished. Ready: 0 | Failed: 1" in output
