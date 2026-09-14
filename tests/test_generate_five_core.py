import json
import sys
from types import SimpleNamespace

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
    work = tmp_path / "work"
    drafts.mkdir()
    work.mkdir()
    status = tmp_path / "status.json"

    monkeypatch.setattr(generate_five, "DRAFTS", drafts)
    monkeypatch.setattr(generate_five, "WORK", work)
    monkeypatch.setattr(generate_five, "STATUS_FILE", status)
    return drafts, work, status


def _unexpected(*args, **kwargs):
    raise AssertionError("unexpected external boundary call")


def test_load_status_defaults_reads_valid_json_and_fails_closed(tmp_path, monkeypatch):
    status_file = tmp_path / "status.json"
    monkeypatch.setattr(generate_five, "STATUS_FILE", status_file)

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
    status_file.write_text(json.dumps(saved), encoding="utf-8")
    assert generate_five.load_status() == saved

    status_file.write_text("{not-json", encoding="utf-8")
    assert generate_five.load_status() == expected_default


def test_save_status_and_path_helpers(tmp_path, monkeypatch):
    drafts, _, status_file = _configure_paths(tmp_path, monkeypatch)

    status = {
        "started_at": "kept",
        "updated_at": None,
        "cities": {},
    }
    generate_five.save_status(status)

    written = json.loads(status_file.read_text(encoding="utf-8"))
    assert written["started_at"] == "kept"
    assert written["updated_at"]
    assert status["updated_at"] == written["updated_at"]

    meeting = _meeting(
        external_id="  A/B?C_9-.$ ",
        city_slug="rsm",
    )
    assert generate_five.safe_id(meeting) == "ABC_9-"
    assert generate_five.story_path(meeting) == drafts / "rsm--ABC_9-.json"
    assert generate_five.notes_path(meeting) == drafts / "rsm--ABC_9-.notes.txt"


def test_main_reuses_five_existing_drafts_without_external_calls(
    tmp_path,
    monkeypatch,
    capsys,
):
    drafts, _, status_file = _configure_paths(tmp_path, monkeypatch)
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
        generate_five.story_path(meeting).write_text("existing", encoding="utf-8")

    monkeypatch.setattr(generate_five, "latest_ready_meetings", lambda: meetings)
    monkeypatch.setattr(generate_five, "download_audio", _unexpected)
    monkeypatch.setattr(generate_five, "make_source_notes", _unexpected)
    monkeypatch.setattr(generate_five, "agenda_text", _unexpected)
    monkeypatch.setattr(generate_five, "make_story", _unexpected)
    monkeypatch.setattr(generate_five, "audit_story", _unexpected)
    monkeypatch.setattr(sys, "argv", ["generate_five.py"])

    generate_five.main()

    output = capsys.readouterr().out
    assert "WARNING:" not in output
    assert output.count("already drafted; skipping") == 5
    assert "Finished. Ready: 5 | Failed: 0" in output

    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["started_at"] == "existing-start"
    assert set(status["cities"]) == {meeting["city_slug"] for meeting in meetings}
    assert all(row["phase"] == "complete" for row in status["cities"].values())
    assert all(
        row["message"] == "Existing draft reused."
        for row in status["cities"].values()
    )
    assert sorted(path.name for path in drafts.glob("*.json")) == sorted(
        generate_five.story_path(meeting).name for meeting in meetings
    )


def test_main_force_runs_full_pipeline_applies_audit_corrections_and_cleans_audio(
    tmp_path,
    monkeypatch,
    capsys,
):
    drafts, work, status_file = _configure_paths(tmp_path, monkeypatch)
    meeting = _meeting(1)
    out = generate_five.story_path(meeting)
    out.write_text("old draft", encoding="utf-8")

    calls = {}
    snapshots = []
    original_save_status = generate_five.save_status

    def capture_status(status):
        snapshots.append(json.loads(json.dumps(status)))
        original_save_status(status)

    def fake_download(url, audio):
        calls["download"] = (url, audio)
        audio.write_bytes(b"audio")

    def fake_source_notes(audio, supplied_meeting):
        calls["source_notes"] = (audio, supplied_meeting)
        return "source notes"

    def fake_agenda(url):
        calls["agenda"] = url
        return "agenda text"

    story = SimpleNamespace(
        headline="Original headline",
        dek="Original dek",
        body=["Original body"],
        key_facts=["Original fact"],
        verification_notes=["Original verification"],
    )

    def fake_story(supplied_meeting, notes, agenda):
        calls["story"] = (supplied_meeting, notes, agenda)
        return story

    class Issue:
        def model_dump(self):
            return {"kind": "regression", "message": "fixed"}

    audit = SimpleNamespace(
        ok=False,
        corrected_headline="Corrected headline",
        corrected_dek="Corrected dek",
        corrected_body=["Corrected body"],
        corrected_key_facts=["Corrected fact"],
        corrected_verification_notes=["Corrected verification"],
        issues=[Issue()],
    )

    def fake_audit(supplied_meeting, notes, agenda, supplied_story):
        calls["audit"] = (supplied_meeting, notes, agenda, supplied_story)
        return audit

    monkeypatch.setattr(generate_five, "latest_ready_meetings", lambda: [meeting])
    monkeypatch.setattr(generate_five, "save_status", capture_status)
    monkeypatch.setattr(generate_five, "download_audio", fake_download)
    monkeypatch.setattr(generate_five, "make_source_notes", fake_source_notes)
    monkeypatch.setattr(generate_five, "agenda_text", fake_agenda)
    monkeypatch.setattr(generate_five, "make_story", fake_story)
    monkeypatch.setattr(generate_five, "audit_story", fake_audit)
    monkeypatch.setattr(generate_five, "KEEP_MEDIA", False)
    monkeypatch.setattr(sys, "argv", ["generate_five.py", "--force"])

    generate_five.main()

    output = capsys.readouterr().out
    assert "WARNING: expected 5 READY meetings; found 1." in output
    assert "READY FOR REVIEW" in output
    assert "Finished. Ready: 1 | Failed: 0" in output

    audio = work / meeting["city_slug"] / "meeting.mp3"
    assert calls["download"] == (meeting["recording_url"], audio)
    assert calls["source_notes"] == (audio, meeting)
    assert calls["agenda"] == meeting["agenda_url"]
    assert calls["story"] == (meeting, "source notes", "agenda text")
    assert calls["audit"] == (meeting, "source notes", "agenda text", story)
    assert not audio.exists()

    notes = generate_five.notes_path(meeting)
    assert notes.read_text(encoding="utf-8") == "source notes"

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "READY FOR REVIEW"
    assert payload["city_slug"] == meeting["city_slug"]
    assert payload["city_name"] == meeting["city_name"]
    assert payload["meeting_date"] == meeting["meeting_date"]
    assert payload["meeting_title"] == meeting["title"]
    assert payload["external_id"] == meeting["external_id"]
    assert payload["headline"] == "Corrected headline"
    assert payload["dek"] == "Corrected dek"
    assert payload["body"] == ["Corrected body"]
    assert payload["key_facts"] == ["Corrected fact"]
    assert payload["verification_notes"] == ["Corrected verification"]
    assert payload["audit_ok"] is False
    assert payload["audit_issues"] == [{"kind": "regression", "message": "fixed"}]
    assert payload["source_url"] == meeting["source_url"]
    assert payload["agenda_url"] == meeting["agenda_url"]
    assert payload["recording_url"] == meeting["recording_url"]
    assert payload["transcript_model"] == generate_five.TRANSCRIPT_MODEL
    assert payload["story_model"] == generate_five.STORY_MODEL
    assert payload["published"] is False
    assert payload["generated_at_utc"]

    phases = [
        snapshot.get("cities", {}).get(meeting["city_slug"], {}).get("phase")
        for snapshot in snapshots
    ]
    assert "downloading" in phases
    assert "source_notes" in phases
    assert "agenda" in phases
    assert "writing" in phases
    assert "auditing" in phases
    assert phases[-1] == "complete"

    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["started_at"]
    assert status["cities"][meeting["city_slug"]]["phase"] == "complete"
    assert status["cities"][meeting["city_slug"]]["message"] == "READY FOR REVIEW"


def test_main_keeps_original_story_without_corrections_and_retains_media(
    tmp_path,
    monkeypatch,
):
    _configure_paths(tmp_path, monkeypatch)
    meeting = _meeting(2, agenda_url=None)
    observed = {}

    def fake_download(url, audio):
        audio.write_bytes(b"keep-me")
        observed["audio"] = audio

    monkeypatch.setattr(generate_five, "latest_ready_meetings", lambda: [meeting])
    monkeypatch.setattr(generate_five, "download_audio", fake_download)
    monkeypatch.setattr(generate_five, "make_source_notes", lambda audio, row: "notes")

    def fake_agenda(url):
        observed["agenda_url"] = url
        return ""

    monkeypatch.setattr(generate_five, "agenda_text", fake_agenda)
    monkeypatch.setattr(
        generate_five,
        "make_story",
        lambda row, notes, agenda: SimpleNamespace(
            headline="Original headline",
            dek="Original dek",
            body=["Original body"],
            key_facts=["Original fact"],
            verification_notes=["Original verification"],
        ),
    )
    monkeypatch.setattr(
        generate_five,
        "audit_story",
        lambda row, notes, agenda, story: SimpleNamespace(
            ok=True,
            issues=[],
        ),
    )
    monkeypatch.setattr(generate_five, "KEEP_MEDIA", True)
    monkeypatch.setattr(sys, "argv", ["generate_five.py"])

    generate_five.main()

    assert observed["agenda_url"] == ""
    assert observed["audio"].read_bytes() == b"keep-me"

    payload = json.loads(generate_five.story_path(meeting).read_text(encoding="utf-8"))
    assert payload["headline"] == "Original headline"
    assert payload["dek"] == "Original dek"
    assert payload["body"] == ["Original body"]
    assert payload["key_facts"] == ["Original fact"]
    assert payload["verification_notes"] == ["Original verification"]
    assert payload["audit_ok"] is True
    assert payload["audit_issues"] == []


def test_main_records_failure_cleans_audio_and_exits_two(
    tmp_path,
    monkeypatch,
    capsys,
):
    _, work, status_file = _configure_paths(tmp_path, monkeypatch)
    meeting = _meeting(3)

    def fake_download(url, audio):
        audio.write_bytes(b"temporary")

    def fail_source_notes(audio, supplied_meeting):
        raise RuntimeError("source notes exploded")

    monkeypatch.setattr(generate_five, "latest_ready_meetings", lambda: [meeting])
    monkeypatch.setattr(generate_five, "download_audio", fake_download)
    monkeypatch.setattr(generate_five, "make_source_notes", fail_source_notes)
    monkeypatch.setattr(generate_five, "agenda_text", _unexpected)
    monkeypatch.setattr(generate_five, "make_story", _unexpected)
    monkeypatch.setattr(generate_five, "audit_story", _unexpected)
    monkeypatch.setattr(generate_five, "KEEP_MEDIA", False)
    monkeypatch.setattr(sys, "argv", ["generate_five.py"])

    with pytest.raises(SystemExit) as exc:
        generate_five.main()

    assert exc.value.code == 2
    output = capsys.readouterr().out
    assert "FAILED: RuntimeError: source notes exploded" in output
    assert "Finished. Ready: 0 | Failed: 1" in output

    audio = work / meeting["city_slug"] / "meeting.mp3"
    assert not audio.exists()
    assert not generate_five.story_path(meeting).exists()

    status = json.loads(status_file.read_text(encoding="utf-8"))
    failed = status["cities"][meeting["city_slug"]]
    assert failed["phase"] == "failed"
    assert failed["message"] == "RuntimeError: source notes exploded"
    assert failed["meeting_date"] == meeting["meeting_date"]
    assert failed["external_id"] == meeting["external_id"]
