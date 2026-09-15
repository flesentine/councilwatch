from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import process_city as pc
from gemini_worker import AuditIssue, AuditResult, StoryDraft


def meeting(**overrides):
    value = {
        "city_slug": "lake-forest",
        "city_name": "Lake Forest",
        "meeting_date": "2026-08-18",
        "title": "Regular City Council Meeting",
        "external_id": 123,
        "recording_url": "https://example.test/recording",
        "agenda_url": "https://example.test/agenda",
        "source_url": "https://example.test/source",
    }
    value.update(overrides)
    return value


def clean_story():
    return StoryDraft(
        headline="Council reviews local business",
        dek="The council discussed several local issues.",
        body=["The council discussed park maintenance and traffic safety."],
        key_facts=["Park maintenance was discussed."],
        verification_notes=[],
    )


def clean_intelligence():
    return {
        "entities": [],
        "action_ledger": [],
        "coverage_items": [],
        "editorial_summary": "",
    }


def clean_audit():
    return AuditResult(ok=True, issues=[])


@pytest.fixture
def isolated_runtime(monkeypatch, tmp_path):
    drafts = tmp_path / "drafts"
    work = tmp_path / "work"
    drafts.mkdir()
    work.mkdir()
    status_file = tmp_path / "status.json"

    monkeypatch.setattr(pc, "DRAFTS", drafts)
    monkeypatch.setattr(pc, "WORK", work)
    monkeypatch.setattr(pc, "STATUS_FILE", status_file)
    monkeypatch.setattr(pc, "retry_api_call", lambda _label, fn: fn())

    return drafts, work, status_file


def patch_clean_generation(monkeypatch, *, intelligence=None, story=None):
    intelligence = intelligence or clean_intelligence()
    story = story or clean_story()
    monkeypatch.setattr(pc, "build_meeting_intelligence", lambda *args: intelligence)
    monkeypatch.setattr(pc, "make_rich_story", lambda *args: story.model_copy(deep=True))
    monkeypatch.setattr(pc, "audit_story", lambda *args: clean_audit())
    monkeypatch.setattr(pc, "agenda_text", lambda _url: "Official agenda material")
    return intelligence, story


def draft_path(drafts: Path):
    return drafts / "lake-forest--123.json"


def notes_path(drafts: Path):
    return drafts / "lake-forest--123.notes.txt"


def intelligence_path(drafts: Path):
    return drafts / "lake-forest--123.intelligence.json"


def test_existing_audited_draft_returns_before_any_generation(
    monkeypatch,
    isolated_runtime,
):
    drafts, _work, _status_file = isolated_runtime
    target = draft_path(drafts)
    target.write_text(
        json.dumps({"audit_ok": True, "headline": "Already complete"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        pc,
        "acquire_source",
        lambda *args, **kwargs: pytest.fail("source acquisition should not run"),
    )
    monkeypatch.setattr(
        pc,
        "make_rich_story",
        lambda *args, **kwargs: pytest.fail("story generation should not run"),
    )

    assert pc.process_city("lake-forest", meeting_override=meeting()) is None
    assert json.loads(target.read_text(encoding="utf-8"))["headline"] == "Already complete"


def test_cached_notes_and_intelligence_complete_clean_lifecycle_and_notification_warning(
    monkeypatch,
    isolated_runtime,
):
    drafts, _work, status_file = isolated_runtime
    notes_path(drafts).write_text("cached source notes " * 20, encoding="utf-8")
    intelligence_path(drafts).write_text(
        json.dumps(clean_intelligence()),
        encoding="utf-8",
    )

    generated = clean_story()
    monkeypatch.setattr(pc, "make_rich_story", lambda *args: generated.model_copy(deep=True))
    audit_calls = []

    def fake_audit(*args):
        audit_calls.append(args)
        return clean_audit()

    monkeypatch.setattr(pc, "audit_story", fake_audit)
    monkeypatch.setattr(pc, "agenda_text", lambda _url: "Official agenda material")
    monkeypatch.setattr(
        pc,
        "acquire_source",
        lambda *args, **kwargs: pytest.fail("cached notes should skip source acquisition"),
    )
    monkeypatch.setattr(
        pc,
        "build_meeting_intelligence",
        lambda *args: pytest.fail("cached intelligence should skip rebuilding"),
    )
    notices = []

    def failing_notice(meeting_value, payload):
        notices.append((meeting_value, payload))
        raise RuntimeError("mail service unavailable")

    monkeypatch.setattr(pc, "notify_ready_for_review", failing_notice)

    pc.process_city("lake-forest", meeting_override=meeting())

    payload = json.loads(draft_path(drafts).read_text(encoding="utf-8"))
    assert payload["audit_ok"] is True
    assert payload["status"] == "READY FOR REVIEW"
    assert payload["published"] is False
    assert payload["headline"] == generated.headline
    assert payload["source_url"] == "https://example.test/source"
    assert len(audit_calls) == 2
    assert len(notices) == 1

    status = json.loads(status_file.read_text(encoding="utf-8"))
    city = status["cities"]["lake-forest"]
    assert city["phase"] == "complete"
    assert city["message"] == "READY FOR REVIEW"
    assert city["draft"] == "lake-forest--123.json"


def test_caption_acquisition_rebuilds_intelligence_and_tolerates_agenda_failure(
    monkeypatch,
    isolated_runtime,
):
    drafts, _work, _status_file = isolated_runtime
    captions = "caption evidence " * 20
    acquisition_calls = []

    def acquire(recording_url, workdir, audio, refresh=False):
        acquisition_calls.append((recording_url, workdir, audio, refresh))
        return {"kind": "captions", "text": captions}

    monkeypatch.setattr(pc, "acquire_source", acquire)
    built = []

    def build(meeting_value, notes, agenda):
        built.append((meeting_value, notes, agenda))
        return clean_intelligence()

    monkeypatch.setattr(pc, "build_meeting_intelligence", build)
    monkeypatch.setattr(pc, "make_rich_story", lambda *args: clean_story())
    monkeypatch.setattr(pc, "audit_story", lambda *args: clean_audit())
    monkeypatch.setattr(
        pc,
        "agenda_text",
        lambda _url: (_ for _ in ()).throw(RuntimeError("agenda offline")),
    )
    monkeypatch.setattr(pc, "notify_ready_for_review", lambda *args: None)

    pc.process_city("lake-forest", meeting_override=meeting())

    assert acquisition_calls and acquisition_calls[0][3] is False
    assert built == [(meeting(), captions, "")]
    assert notes_path(drafts).read_text(encoding="utf-8") == captions
    cached_intelligence = json.loads(
        intelligence_path(drafts).read_text(encoding="utf-8")
    )
    assert cached_intelligence == clean_intelligence()
    assert json.loads(draft_path(drafts).read_text(encoding="utf-8"))["audit_ok"] is True


def test_audio_force_notes_refreshes_source_generates_notes_and_removes_audio(
    monkeypatch,
    isolated_runtime,
):
    drafts, work, _status_file = isolated_runtime
    notes_path(drafts).write_text("stale notes " * 20, encoding="utf-8")
    intelligence_path(drafts).write_text(json.dumps({"stale": True}), encoding="utf-8")

    source_audio = work / "downloaded.mp3"
    source_audio.write_bytes(b"audio-bytes")
    refresh_values = []

    def acquire(_recording_url, _workdir, _audio, refresh=False):
        refresh_values.append(refresh)
        return {"kind": "audio", "path": str(source_audio)}

    monkeypatch.setattr(pc, "acquire_source", acquire)
    generated_notes = "audio-derived source notes " * 20
    note_calls = []

    def make_notes(path, meeting_value):
        note_calls.append((path, meeting_value))
        return generated_notes

    monkeypatch.setattr(pc, "make_comprehensive_source_notes", make_notes)
    built = []
    monkeypatch.setattr(
        pc,
        "build_meeting_intelligence",
        lambda meeting_value, notes, agenda: (
            built.append((meeting_value, notes, agenda)) or clean_intelligence()
        ),
    )
    monkeypatch.setattr(pc, "agenda_text", lambda _url: "Agenda")
    monkeypatch.setattr(pc, "make_rich_story", lambda *args: clean_story())
    monkeypatch.setattr(pc, "audit_story", lambda *args: clean_audit())
    monkeypatch.setattr(pc, "notify_ready_for_review", lambda *args: None)

    pc.process_city(
        "lake-forest",
        force_notes=True,
        meeting_override=meeting(),
    )

    assert refresh_values == [True]
    assert note_calls and note_calls[0][0] == source_audio
    assert notes_path(drafts).read_text(encoding="utf-8") == generated_notes
    assert built and built[0][1] == generated_notes
    assert not source_audio.exists()


def test_short_source_fails_closed_updates_failed_status_and_reraises(
    monkeypatch,
    isolated_runtime,
):
    drafts, _work, status_file = isolated_runtime
    monkeypatch.setattr(
        pc,
        "acquire_source",
        lambda *args, **kwargs: {"kind": "captions", "text": "too short"},
    )

    with pytest.raises(RuntimeError, match="unexpectedly short"):
        pc.process_city("lake-forest", meeting_override=meeting())

    assert not draft_path(drafts).exists()
    status = json.loads(status_file.read_text(encoding="utf-8"))
    city = status["cities"]["lake-forest"]
    assert city["phase"] == "failed"
    assert "RuntimeError" in city["message"]
    assert "unexpectedly short" in city["message"]


def test_deterministic_material_issue_saves_review_draft_without_notification(
    monkeypatch,
    isolated_runtime,
):
    drafts, _work, status_file = isolated_runtime
    notes_path(drafts).write_text("cached notes " * 20, encoding="utf-8")
    intelligence_path(drafts).write_text(json.dumps(clean_intelligence()), encoding="utf-8")
    monkeypatch.setattr(pc, "agenda_text", lambda _url: "Agenda")
    monkeypatch.setattr(pc, "make_rich_story", lambda *args: clean_story())
    monkeypatch.setattr(pc, "audit_story", lambda *args: clean_audit())

    issue = AuditIssue(
        severity="material",
        field="headline",
        draft_text="Council reviews local business",
        source_evidence="The final action remains unresolved.",
        correction="Verify the action before publication.",
    )
    monkeypatch.setattr(
        pc,
        "unresolved_high_priority_formal_action_issues",
        lambda *args: [issue],
    )
    monkeypatch.setattr(
        pc,
        "unsupported_conduit_financing_story_issues",
        lambda *args: [],
    )
    monkeypatch.setattr(pc, "missing_required_topic_issues", lambda *args: [])
    monkeypatch.setattr(
        pc,
        "notify_ready_for_review",
        lambda *args: pytest.fail("material draft must not notify ready-for-review"),
    )

    pc.process_city("lake-forest", meeting_override=meeting())

    payload = json.loads(draft_path(drafts).read_text(encoding="utf-8"))
    assert payload["audit_ok"] is False
    assert len(payload["audit_issues"]) == 1
    assert payload["audit_issues"][0]["source_evidence"] == issue.source_evidence

    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["cities"]["lake-forest"]["message"] == (
        "READY FOR REVIEW - MATERIAL ISSUE"
    )


def test_initial_audit_correction_reaudits_corrected_copy(
    monkeypatch,
    isolated_runtime,
):
    drafts, _work, _status_file = isolated_runtime
    notes_path(drafts).write_text("cached notes " * 20, encoding="utf-8")
    intelligence_path(drafts).write_text(json.dumps(clean_intelligence()), encoding="utf-8")
    monkeypatch.setattr(pc, "agenda_text", lambda _url: "Agenda")
    monkeypatch.setattr(pc, "make_rich_story", lambda *args: clean_story())
    monkeypatch.setattr(pc, "notify_ready_for_review", lambda *args: None)

    issue = AuditIssue(
        severity="minor",
        field="dek",
        draft_text="The council discussed several local issues.",
        source_evidence="Use a more precise neutral summary.",
        correction="The council discussed local issues.",
    )
    sequence = [
        AuditResult(
            ok=False,
            issues=[issue],
            corrected_dek="The council discussed local issues.",
        ),
        clean_audit(),
        clean_audit(),
    ]
    calls = []

    def fake_audit(*args):
        calls.append(args[3].dek)
        return sequence.pop(0)

    monkeypatch.setattr(pc, "audit_story", fake_audit)

    pc.process_city("lake-forest", meeting_override=meeting())

    payload = json.loads(draft_path(drafts).read_text(encoding="utf-8"))
    assert payload["dek"] == "The council discussed local issues."
    assert payload["audit_ok"] is True
    assert len(calls) == 3
    assert calls[0] == "The council discussed several local issues."
    assert calls[1:] == ["The council discussed local issues."] * 2


def test_last_final_correction_triggers_exact_saved_copy_audit(
    monkeypatch,
    isolated_runtime,
):
    drafts, _work, _status_file = isolated_runtime
    notes_path(drafts).write_text("cached notes " * 20, encoding="utf-8")
    intelligence_path(drafts).write_text(json.dumps(clean_intelligence()), encoding="utf-8")
    monkeypatch.setattr(pc, "agenda_text", lambda _url: "Agenda")
    monkeypatch.setattr(pc, "make_rich_story", lambda *args: clean_story())
    monkeypatch.setattr(pc, "notify_ready_for_review", lambda *args: None)

    audit_labels = []

    def direct_retry(label, fn):
        audit_labels.append(label)
        return fn()

    monkeypatch.setattr(pc, "retry_api_call", direct_retry)

    issue = AuditIssue(
        severity="minor",
        field="dek",
        draft_text="placeholder",
        source_evidence="placeholder",
        correction="placeholder",
    )
    audits = [clean_audit()] + [AuditResult(ok=False, issues=[issue])] * 4 + [clean_audit()]
    monkeypatch.setattr(pc, "audit_story", lambda *args: audits.pop(0))
    monkeypatch.setattr(pc, "valid_audit_issues", lambda _story, audit: list(audit.issues))
    monkeypatch.setattr(pc, "apply_audit_corrections", lambda _story, _audit: True)

    pc.process_city("lake-forest", meeting_override=meeting())

    assert audit_labels == [
        "Audit pass 1",
        "Final audit pass 1",
        "Final audit pass 2",
        "Final audit pass 3",
        "Final audit pass 4",
        "Exact saved-copy audit",
    ]
    assert json.loads(draft_path(drafts).read_text(encoding="utf-8"))["audit_ok"] is True


def test_main_routes_cli_flags_to_process_city(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pc,
        "process_city",
        lambda city, force_story=False, force_notes=False: calls.append(
            (city, force_story, force_notes)
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "process_city.py",
            "--city",
            "laguna-niguel",
            "--force-story",
            "--force-notes",
        ],
    )

    pc.main()

    assert calls == [("laguna-niguel", True, True)]
