import json
import sys

import generate_five


def test_corrupt_existing_draft_forces_full_hardened_regeneration(
    tmp_path,
    monkeypatch,
    capsys,
):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    status_file = tmp_path / "status.json"

    monkeypatch.setattr(generate_five, "DRAFTS", drafts)
    monkeypatch.setattr(generate_five, "STATUS_FILE", status_file)

    meeting = {
        "city_slug": "lake-forest",
        "city_name": "Lake Forest",
        "meeting_date": "2026-09-01",
        "title": "Regular City Council Meeting",
        "external_id": "meeting-42",
        "source_url": "https://example.test/source",
        "agenda_url": "https://example.test/agenda",
        "recording_url": "https://example.test/recording",
    }

    story_file = drafts / "lake-forest--meeting-42.json"
    story_file.write_text('{"audit_ok": true', encoding="utf-8")

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
        story_file.write_text(
            json.dumps(
                {
                    "audit_ok": True,
                    "final_audit": True,
                    "action_ledger": [],
                    "coverage_plan": [],
                    "entity_verification": [],
                    "headline": "Hardened replacement",
                }
            ),
            encoding="utf-8",
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
            "lake-forest",
            True,
            True,
            meeting,
        )
    ]
    assert json.loads(
        story_file.read_text(encoding="utf-8")
    )["headline"] == "Hardened replacement"

    output = capsys.readouterr().out
    assert "legacy/unhardened draft detected" in output
    assert "Finished. Ready: 1 | Failed: 0" in output
