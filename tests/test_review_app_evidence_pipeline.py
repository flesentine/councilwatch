import asyncio
import json
from pathlib import Path

import review_app as review
from gemini_worker import AuditResult


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


def setup_env(tmp_path, monkeypatch):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    revisions = drafts / "_revisions"
    revisions.mkdir()

    monkeypatch.setattr(review, "DRAFTS", drafts)
    monkeypatch.setattr(review, "REVISION_DIR", revisions)

    return drafts


def base_draft(**updates):
    data = {
        "city_slug": "alpha",
        "city_name": "Alpha City",
        "external_id": "100",
        "meeting_date": "2026-09-01",
        "meeting_title": "Regular City Council Meeting",
        "headline": "Council discusses park contract",
        "dek": "A concise summary.",
        "body": ["The Council discussed the park contract."],
        "key_facts": ["The contract was discussed."],
        "verification_notes": [],
        "entity_verification": [],
        "coverage_plan": [{
            "rank": 1,
            "score": 9,
            "must_include": True,
            "topic": "Park Contract",
            "action_status": "approved",
            "summary": "Old generated summary",
            "why_it_matters": "Old generated rationale",
        }],
        "audit_issues": [],
        "audit_ok": True,
        "review_status": "needs_review",
        "review_note": "",
        "revision": 1,
        "published": False,
        "published_at": None,
        "source_url": "https://example.com/source",
        "agenda_url": "https://example.com/agenda",
        "recording_url": "https://example.com/recording",
    }
    data.update(updates)
    return data


def write_draft(drafts, data):
    path = drafts / "alpha--100.json"
    review.write_json(path, data)
    return path


def html_text(response):
    return response.body.decode("utf-8")


def test_manual_save_invalidates_coverage_plan_and_saves_evidence(
    tmp_path,
    monkeypatch,
):
    drafts = setup_env(
        tmp_path,
        monkeypatch,
    )
    path = write_draft(
        drafts,
        base_draft(),
    )

    result = asyncio.run(
        review.save_story(
            "alpha",
            "100",
            FakeRequest({
                "headline": "Updated headline",
                "dek": "Updated dek",
                "body": ["Updated body."],
                "key_facts": ["Updated fact."],
                "verification_notes": [],
                "evidence_supplement": (
                    "Official recording at 1:20 confirms the request."
                ),
            }),
        )
    )

    assert result == {
        "ok": True,
        "revision": 2,
    }

    stored = review.read_json(path)
    assert stored["coverage_plan_status"] == (
        "stale_after_manual_edit"
    )
    assert stored["coverage_plan_stale_at"]
    assert (
        drafts / "alpha--100.evidence.txt"
    ).read_text(
        encoding="utf-8"
    ) == (
        "Official recording at 1:20 confirms the request.\n"
    )


def test_stale_coverage_plan_is_hidden_from_review_page(
    tmp_path,
    monkeypatch,
):
    drafts = setup_env(
        tmp_path,
        monkeypatch,
    )
    write_draft(
        drafts,
        base_draft(
            coverage_plan_status=(
                "stale_after_manual_edit"
            ),
        ),
    )

    page = html_text(
        review.story(
            "alpha",
            "100",
        )
    )

    assert "STALE AFTER MANUAL EDIT" in page
    assert "Old generated summary" not in page
    assert "Old generated rationale" not in page


def test_reaudit_uses_cached_agenda_and_reviewer_source_evidence(
    tmp_path,
    monkeypatch,
):
    drafts = setup_env(
        tmp_path,
        monkeypatch,
    )
    path = write_draft(
        drafts,
        base_draft(
            audit_ok=False,
        ),
    )

    (drafts / "alpha--100.notes.txt").write_text(
        "RECORDING SOURCE NOTES",
        encoding="utf-8",
    )
    (drafts / "alpha--100.agenda.txt").write_text(
        "CACHED OFFICIAL AGENDA\n",
        encoding="utf-8",
    )
    (drafts / "alpha--100.evidence.txt").write_text(
        "Official recording at 1:20 confirms the exact request.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        review,
        "agenda_text",
        lambda _url: (_ for _ in ()).throw(
            AssertionError(
                "cached agenda should prevent live fetch"
            )
        ),
    )

    captured = {}

    def fake_audit(
        meeting,
        notes,
        agenda,
        story,
    ):
        captured["notes"] = notes
        captured["agenda"] = agenda
        return AuditResult(
            ok=True,
            issues=[],
        )

    monkeypatch.setattr(
        review,
        "audit_story",
        fake_audit,
    )

    result = review.reaudit_story(
        "alpha",
        "100",
    )

    assert result["audit_ok"] is True
    assert captured["agenda"] == (
        "CACHED OFFICIAL AGENDA"
    )
    assert "RECORDING SOURCE NOTES" in (
        captured["notes"]
    )
    assert (
        "REVIEWER-ADDED SOURCE EVIDENCE:"
        in captured["notes"]
    )
    assert (
        "Official recording at 1:20 confirms the exact request."
        in captured["notes"]
    )
    assert "Old generated summary" not in (
        captured["notes"]
    )
    assert review.read_json(path)[
        "audit_status"
    ] == "fresh"
