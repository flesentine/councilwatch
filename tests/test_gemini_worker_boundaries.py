from __future__ import annotations

from types import SimpleNamespace

import pytest

import gemini_worker as gw


def story():
    return gw.StoryDraft(
        headline="Council update",
        dek="A neutral summary.",
        body=["Stephanie Oddo discussed park maintenance."],
        key_facts=["Park maintenance was discussed."],
        verification_notes=[],
    )


def issue(
    *,
    severity="minor",
    field="body",
    draft_text="Stephanie Oddo discussed park maintenance.",
    source_evidence="Source evidence.",
    correction="Stephanie Oddo discussed park upkeep.",
):
    return gw.AuditIssue(
        severity=severity,
        field=field,
        draft_text=draft_text,
        source_evidence=source_evidence,
        correction=correction,
    )


def whitelist_notes(*names):
    lines = ["PUBLISHABLE PERSON NAMES:"]
    lines.extend(f"- {name}" for name in names)
    if not names:
        lines.append("- NONE")
    lines.extend(["", "PERSON-NAME RULE: only listed names are publishable."])
    return "\n".join(lines)


def test_client_requires_api_key(monkeypatch):
    monkeypatch.setattr(gw, "GEMINI_API_KEY", "")
    with pytest.raises(RuntimeError, match="No Gemini API key"):
        gw.client()


def test_client_passes_configured_api_key(monkeypatch):
    sentinel = object()
    calls = []
    monkeypatch.setattr(gw, "GEMINI_API_KEY", "secret-key")
    monkeypatch.setattr(
        gw.genai,
        "Client",
        lambda **kwargs: calls.append(kwargs) or sentinel,
    )

    assert gw.client() is sentinel
    assert calls == [{"api_key": "secret-key"}]


def test_wait_active_accepts_missing_or_active_state():
    missing_state = SimpleNamespace(state=None)
    active = SimpleNamespace(state=SimpleNamespace(name="ACTIVE"))
    fake_client = SimpleNamespace(files=SimpleNamespace())

    assert gw._wait_active(fake_client, missing_state) is missing_state
    assert gw._wait_active(fake_client, active) is active


def test_wait_active_polls_until_active(monkeypatch):
    initial = SimpleNamespace(
        name="upload-1",
        state=SimpleNamespace(name="PROCESSING"),
    )
    active = SimpleNamespace(
        name="upload-1",
        state=SimpleNamespace(name="ACTIVE"),
    )
    get_calls = []
    fake_client = SimpleNamespace(
        files=SimpleNamespace(
            get=lambda **kwargs: get_calls.append(kwargs) or active,
        )
    )
    sleeps = []
    monkeypatch.setattr(gw.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert gw._wait_active(fake_client, initial) is active
    assert get_calls == [{"name": "upload-1"}]
    assert sleeps == [4]


def test_wait_active_raises_failed_and_timeout(monkeypatch):
    failed = SimpleNamespace(state=SimpleNamespace(name="FAILED"))
    fake_client = SimpleNamespace(files=SimpleNamespace())
    with pytest.raises(RuntimeError, match="file processing failed"):
        gw._wait_active(fake_client, failed)

    processing = SimpleNamespace(
        name="upload-2",
        state=SimpleNamespace(name="PROCESSING"),
    )
    times = iter([100.0, 102.0])
    monkeypatch.setattr(gw.time, "time", lambda: next(times))
    with pytest.raises(TimeoutError, match="Timed out"):
        gw._wait_active(fake_client, processing, timeout=1)


def test_make_source_notes_uses_fake_file_and_model_and_ignores_delete_failure(
    monkeypatch,
    tmp_path,
):
    audio = tmp_path / "meeting.mp3"
    audio.write_bytes(b"audio")
    uploaded = SimpleNamespace(
        name="upload-3",
        state=SimpleNamespace(name="ACTIVE"),
    )
    calls = {"upload": [], "generate": [], "delete": []}
    long_text = "Detailed source evidence. " * 30

    class Files:
        def upload(self, **kwargs):
            calls["upload"].append(kwargs)
            return uploaded

        def delete(self, **kwargs):
            calls["delete"].append(kwargs)
            raise RuntimeError("cleanup unavailable")

    class Models:
        def generate_content(self, **kwargs):
            calls["generate"].append(kwargs)
            return SimpleNamespace(text=long_text)

    fake_client = SimpleNamespace(files=Files(), models=Models())
    monkeypatch.setattr(gw, "client", lambda: fake_client)

    result = gw.make_source_notes(
        audio,
        {
            "city_name": "Lake Forest",
            "meeting_date": "2026-08-18",
            "title": "Regular Meeting",
        },
    )

    assert result == long_text.strip()
    assert calls["upload"] == [{"file": str(audio)}]
    assert calls["delete"] == [{"name": "upload-3"}]
    generate = calls["generate"][0]
    assert generate["model"] == gw.TRANSCRIPT_MODEL
    assert uploaded in generate["contents"]


def test_make_source_notes_rejects_short_model_output(monkeypatch, tmp_path):
    audio = tmp_path / "meeting.mp3"
    audio.write_bytes(b"audio")
    uploaded = SimpleNamespace(
        name="upload-short",
        state=SimpleNamespace(name="ACTIVE"),
    )
    deletes = []
    fake_client = SimpleNamespace(
        files=SimpleNamespace(
            upload=lambda **kwargs: uploaded,
            delete=lambda **kwargs: deletes.append(kwargs),
        ),
        models=SimpleNamespace(
            generate_content=lambda **kwargs: SimpleNamespace(text="too short"),
        ),
    )
    monkeypatch.setattr(gw, "client", lambda: fake_client)

    with pytest.raises(RuntimeError, match="unexpectedly short source notes"):
        gw.make_source_notes(audio, {"city_name": "Lake Forest"})

    assert deletes == []


def test_make_story_parses_fake_json_response(monkeypatch):
    expected = gw.StoryDraft(
        headline="Park contract approved",
        dek="The council approved a park contract.",
        body=["The council approved the contract."],
        key_facts=["The contract was approved."],
        verification_notes=[],
    )
    calls = []
    fake_client = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=lambda **kwargs: (
                calls.append(kwargs)
                or SimpleNamespace(text=expected.model_dump_json())
            )
        )
    )
    monkeypatch.setattr(gw, "client", lambda: fake_client)

    result = gw.make_story(
        {
            "city_name": "Lake Forest",
            "meeting_date": None,
            "title": None,
        },
        "recording notes",
        "agenda text",
    )

    assert result == expected
    assert calls[0]["model"] == gw.STORY_MODEL
    assert "MEETING DATE: unknown" in calls[0]["contents"]
    assert "MEETING TITLE: City Council Meeting" in calls[0]["contents"]


def test_audit_noop_detection_handles_direct_and_prefixed_replacements():
    direct = issue(draft_text="Same wording", correction=" same   wording ")
    prefixed = issue(
        draft_text="Stephanie Oddo spoke.",
        correction='Replace it with: "Stephanie Oddo spoke."',
    )
    changed = issue(draft_text="Old wording", correction="New wording")
    empty = issue(draft_text="", correction="New wording")

    assert gw._audit_issue_correction_is_noop(direct)
    assert gw._audit_issue_correction_is_noop(prefixed)
    assert not gw._audit_issue_correction_is_noop(changed)
    assert not gw._audit_issue_correction_is_noop(empty)


def test_publishable_person_parser_uses_last_section_deduplicates_and_ignores_none():
    notes = """
PUBLISHABLE PERSON NAMES:
- Old Name

PERSON-NAME RULE: old

PUBLISHABLE PERSON NAMES:
- Stephanie Oddo
- Stephanie Oddo
- NONE
- John Smith

PERSON-NAME RULE: current
"""
    assert gw._audit_publishable_person_names(notes) == [
        "Stephanie Oddo",
        "John Smith",
    ]
    assert gw._audit_publishable_person_names("no marker") == []


def test_guard_blocks_canonical_person_downgrade_and_restores_entire_field():
    current = story()
    bad = issue(
        severity="material",
        draft_text="Stephanie Oddo discussed park maintenance.",
        source_evidence="The transcript sounded like Stephanie Otto.",
        correction="Stephanie Otto discussed park maintenance.",
    )
    result = gw.AuditResult(
        ok=False,
        issues=[bad],
        corrected_body=["Stephanie Otto discussed park maintenance."],
    )

    guarded = gw._guard_audit_result(
        result,
        current,
        whitelist_notes("Stephanie Oddo"),
    )

    assert guarded.issues == []
    assert guarded.ok is True
    assert guarded.corrected_body == []
    assert guarded.corrected_headline == ""


def test_guard_allows_switch_to_another_whitelisted_person():
    current = story()
    correction = issue(
        severity="material",
        draft_text="Stephanie Oddo discussed park maintenance.",
        source_evidence="Official evidence attributes the statement to John Smith.",
        correction="John Smith discussed park maintenance.",
    )
    result = gw.AuditResult(
        ok=False,
        issues=[correction],
        corrected_body=["John Smith discussed park maintenance."],
    )

    guarded = gw._guard_audit_result(
        result,
        current,
        whitelist_notes("Stephanie Oddo", "John Smith"),
    )

    assert guarded.issues == [correction]
    assert guarded.ok is False
    assert guarded.corrected_body == ["John Smith discussed park maintenance."]


def test_guard_blocks_public_comment_organization_canonicalization():
    current = gw.StoryDraft(
        headline="Council update",
        dek="Summary",
        body=[
            "A resident from Foothill Ranch Community Association spoke during public comment."
        ],
        key_facts=[],
        verification_notes=[],
    )
    bad = issue(
        severity="minor",
        draft_text=current.body[0],
        source_evidence="An agenda page mentions another association.",
        correction=(
            "A resident from Foothill Ranch Homeowners Association spoke during public comment."
        ),
    )
    result = gw.AuditResult(
        ok=True,
        issues=[bad],
        corrected_body=[bad.correction],
    )
    notes = (
        "Recording-derived notes: A resident from Foothill Ranch Community Association "
        "spoke during public comment."
    )

    guarded = gw._guard_audit_result(result, current, notes)

    assert guarded.issues == []
    assert guarded.ok is True
    assert guarded.corrected_body == []


def test_guard_drops_invalid_draft_text_and_noop_and_clears_corrections():
    current = story()
    missing = issue(
        draft_text="This sentence is not in the story.",
        correction="Replacement sentence.",
    )
    noop = issue(
        field="dek",
        draft_text="A neutral summary.",
        correction="A neutral summary.",
    )
    result = gw.AuditResult(
        ok=False,
        issues=[missing, noop],
        corrected_headline="Changed headline",
        corrected_dek="Changed dek",
        corrected_body=["Changed body"],
        corrected_key_facts=["Changed fact"],
        corrected_verification_notes=["Changed note"],
    )

    guarded = gw._guard_audit_result(result, current, "")

    assert guarded.issues == []
    assert guarded.ok is True
    assert guarded.corrected_headline == ""
    assert guarded.corrected_dek == ""
    assert guarded.corrected_body == []
    assert guarded.corrected_key_facts == []
    assert guarded.corrected_verification_notes == []


def test_guard_minor_issue_keeps_ok_true_but_material_issue_sets_false():
    current = story()
    minor = issue(severity="minor")
    minor_result = gw.AuditResult(
        ok=False,
        issues=[minor],
        corrected_body=[minor.correction],
    )
    guarded_minor = gw._guard_audit_result(minor_result, current, "")
    assert guarded_minor.issues == [minor]
    assert guarded_minor.ok is True

    material = issue(severity="material")
    material_result = gw.AuditResult(
        ok=True,
        issues=[material],
        corrected_body=[material.correction],
    )
    guarded_material = gw._guard_audit_result(material_result, current, "")
    assert guarded_material.issues == [material]
    assert guarded_material.ok is False


def test_audit_story_parses_fake_model_result_and_runs_guard(monkeypatch):
    current = story()
    no_op = issue(
        field="dek",
        draft_text="A neutral summary.",
        correction="A neutral summary.",
    )
    raw = gw.AuditResult(
        ok=False,
        issues=[no_op],
        corrected_dek="A neutral summary.",
    )
    calls = []
    fake_client = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=lambda **kwargs: (
                calls.append(kwargs)
                or SimpleNamespace(text=raw.model_dump_json())
            )
        )
    )
    monkeypatch.setattr(gw, "client", lambda: fake_client)

    result = gw.audit_story(
        {"city_name": "Lake Forest", "meeting_date": "2026-08-18"},
        whitelist_notes("Stephanie Oddo"),
        "Official agenda",
        current,
    )

    assert result.ok is True
    assert result.issues == []
    assert result.corrected_dek == ""
    assert calls[0]["model"] == gw.STORY_MODEL
    assert "PUBLISHABLE PERSON NAMES" in calls[0]["contents"]
