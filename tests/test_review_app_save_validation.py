import asyncio
import json

import review_app as review


class FakeRequest:
    async def json(self):
        return {
            "headline": "Still a real headline",
            "dek": "Still a real dek",
            "body": ["   ", "\n\t"],
            "key_facts": [],
            "verification_notes": [],
        }


def test_whitespace_only_body_is_rejected_before_mutation(
    tmp_path,
    monkeypatch,
):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    revisions = drafts / "_revisions"
    revisions.mkdir()

    monkeypatch.setattr(review, "DRAFTS", drafts)
    monkeypatch.setattr(review, "REVISION_DIR", revisions)

    original = {
        "city_slug": "alpha",
        "city_name": "Alpha City",
        "external_id": "100",
        "meeting_date": "2026-09-01",
        "meeting_title": "Regular City Council Meeting",
        "headline": "Original headline",
        "dek": "Original dek",
        "body": ["Original article paragraph."],
        "key_facts": [],
        "verification_notes": [],
        "audit_ok": True,
        "audit_issues": [],
        "review_status": "approved",
        "review_note": "",
        "revision": 7,
        "published": False,
        "published_at": None,
    }

    path = drafts / "alpha--100.json"
    review.write_json(path, original)
    before = path.read_text(encoding="utf-8")

    response = asyncio.run(
        review.save_story(
            "alpha",
            "100",
            FakeRequest(),
        )
    )

    assert response.status_code == 400
    payload = json.loads(response.body.decode("utf-8"))
    assert payload == {
        "ok": False,
        "error": "Article body cannot be blank.",
    }
    assert path.read_text(encoding="utf-8") == before
    assert list(revisions.glob("*.json")) == []
