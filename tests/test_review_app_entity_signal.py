import json

import review_app as review


def _draft():
    return {
        "city_slug": "mission-viejo",
        "city_name": "Mission Viejo",
        "external_id": "2631",
        "meeting_date": "2026-09-14",
        "meeting_title": "Planning and Transportation Commission",
        "headline": "Planning Commission reviews zoning changes",
        "dek": "The commission discussed commercial zoning updates.",
        "body": [
            "The Planning and Transportation Commission discussed Code Enforcement and ABC licensing.",
            "Gary Disney participated in the meeting.",
        ],
        "key_facts": ["City Council action would occur later."],
        "verification_notes": [
            "The source reference 'Brotan' remains unverified."
        ],
        "entity_verification": [
            {
                "observed_text": "Brotan",
                "canonical_text": "Brotan",
                "entity_type": "person",
                "status": "UNVERIFIED",
                "confidence": "low",
                "evidence": "Identity remains unresolved.",
                "official_source_url": "",
            },
            {
                "observed_text": "Disney",
                "canonical_text": "Gary Disney",
                "entity_type": "person",
                "status": "VERIFIED",
                "confidence": "high",
                "evidence": "Matches official roster.",
                "official_source_url": "https://example.gov/roster",
            },
            {
                "observed_text": "Code Enforcement",
                "canonical_text": "Code Enforcement",
                "entity_type": "government_body",
                "status": "VERIFIED",
                "confidence": "high",
                "evidence": "Official department name.",
                "official_source_url": "https://example.gov/code",
            },
            {
                "observed_text": "Economic Development Committee",
                "canonical_text": "Economic Development Committee",
                "entity_type": "government_body",
                "status": "UNVERIFIED",
                "confidence": "low",
                "evidence": "Not resolved by current source set.",
                "official_source_url": "",
            },
            {
                "observed_text": "ABC",
                "canonical_text": "ABC",
                "entity_type": "government_body",
                "status": "UNVERIFIED",
                "confidence": "low",
                "evidence": "Bare acronym.",
                "official_source_url": "",
            },
            {
                "observed_text": "CEQA",
                "canonical_text": "CEQA",
                "entity_type": "program",
                "status": "UNVERIFIED",
                "confidence": "low",
                "evidence": "Planning terminology.",
                "official_source_url": "",
            },
            {
                "observed_text": "CPTED",
                "canonical_text": "CPTED",
                "entity_type": "technology",
                "status": "UNVERIFIED",
                "confidence": "low",
                "evidence": "Planning terminology.",
                "official_source_url": "",
            },
            {
                "observed_text": "Civic Center",
                "canonical_text": "Civic Center",
                "entity_type": "street",
                "status": "VERIFIED",
                "confidence": "high",
                "evidence": "Official address.",
                "official_source_url": "https://example.gov/address",
            },
        ],
        "coverage_plan": [],
        "audit_issues": [],
        "audit_ok": True,
        "review_status": "needs_review",
        "review_note": "",
        "revision": 1,
        "published": False,
        "source_url": "https://example.gov/source",
        "agenda_url": "https://example.gov/agenda",
        "recording_url": "https://example.gov/recording",
    }


def test_story_focuses_entity_panel_without_mutating_stored_data(
    tmp_path,
    monkeypatch,
):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    revisions = drafts / "_revisions"
    revisions.mkdir()

    monkeypatch.setattr(review, "DRAFTS", drafts)
    monkeypatch.setattr(review, "REVISION_DIR", revisions)
    monkeypatch.setattr(review, "STATUS_FILE", tmp_path / "status.json")
    monkeypatch.setattr(
        review,
        "CITY_NAMES",
        {"mission-viejo": "Mission Viejo"},
    )

    data = _draft()
    path = drafts / "mission-viejo--2631.json"
    review.write_json(path, data)

    page = review.story(
        "mission-viejo",
        "2631",
    ).body.decode("utf-8")

    assert "Identity &amp; official-name verification" in page
    assert "Brotan" in page
    assert "Gary Disney" in page
    assert "Code Enforcement" in page

    assert "Economic Development Committee" not in page
    assert "Bare acronym." not in page
    assert "Planning terminology." not in page
    assert "Civic Center" not in page

    assert "Lower-signal terminology checks" in page
    assert "remain stored in the draft verification data." in page

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["entity_verification"] == data["entity_verification"]
