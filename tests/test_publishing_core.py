from __future__ import annotations

import json
from pathlib import Path

import pytest

import publishing


def test_published_path_requires_complete_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(publishing, "PUBLISHED_DIR", tmp_path)

    with pytest.raises(ValueError, match="city_slug and external_id"):
        publishing.published_path({"city_slug": "rsm"})

    with pytest.raises(ValueError, match="city_slug and external_id"):
        publishing.published_path({"external_id": "123"})

    with pytest.raises(ValueError, match="city_slug and external_id"):
        publishing.published_path({"city_slug": "", "external_id": ""})


def test_published_path_uses_city_and_external_id(tmp_path, monkeypatch):
    monkeypatch.setattr(publishing, "PUBLISHED_DIR", tmp_path)

    path = publishing.published_path(
        {"city_slug": "rsm", "external_id": 42}
    )

    assert path == tmp_path / "rsm--42.json"


def test_public_article_payload_whitelists_public_fields_and_defaults():
    data = {
        "city_slug": "rsm",
        "city_name": "Rancho Santa Margarita",
        "meeting_date": "2026-09-14",
        "meeting_title": "City Council",
        "external_id": "abc-123",
        "headline": "Council Approves Project",
        "dek": "A concise summary",
        "source_url": "https://example.test/source",
        "agenda_url": "https://example.test/agenda",
        "recording_url": "https://example.test/video",
        "revision": "7",
        "published_at": "2026-09-14T01:00:00+00:00",
        "generated_at_utc": "2026-09-13T23:00:00+00:00",
        "audit": {"internal": True},
        "review_notes": "do not publish",
        "coverage_plan": ["internal"],
        "entity_verification": {"working": True},
    }

    payload = publishing.public_article_payload(data)

    assert payload == {
        "schema_version": 1,
        "article_id": "rsm--abc-123",
        "city_slug": "rsm",
        "city_name": "Rancho Santa Margarita",
        "meeting_date": "2026-09-14",
        "meeting_title": "City Council",
        "external_id": "abc-123",
        "headline": "Council Approves Project",
        "dek": "A concise summary",
        "body": [],
        "key_facts": [],
        "source_url": "https://example.test/source",
        "agenda_url": "https://example.test/agenda",
        "recording_url": "https://example.test/video",
        "revision": 7,
        "published_at_utc": "2026-09-14T01:00:00+00:00",
        "generated_at_utc": "2026-09-13T23:00:00+00:00",
        "technology_assisted": True,
    }
    assert "audit" not in payload
    assert "review_notes" not in payload
    assert "coverage_plan" not in payload
    assert "entity_verification" not in payload


def test_public_article_payload_preserves_body_and_key_facts():
    body = ["Paragraph one", "Paragraph two"]
    facts = [{"label": "Vote", "value": "5-0"}]

    payload = publishing.public_article_payload(
        {
            "city_slug": "rsm",
            "external_id": "9",
            "body": body,
            "key_facts": facts,
        }
    )

    assert payload["body"] is body
    assert payload["key_facts"] is facts
    assert payload["revision"] == 1


def test_publish_copy_writes_utf8_json_atomically_to_temp_directory(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(publishing, "PUBLISHED_DIR", tmp_path)
    data = {
        "city_slug": "rsm",
        "city_name": "Rancho Santa Margarita",
        "external_id": "77",
        "headline": "Café project approved",
        "body": ["Résumé details — all public."],
        "revision": 2,
    }

    path = publishing.publish_copy(data)

    assert path == tmp_path / "rsm--77.json"
    assert path.exists()
    assert not Path(str(path) + ".tmp").exists()
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert "Café" in text
    assert "Résumé" in text
    assert "\\u00e9" not in text
    saved = json.loads(text)
    assert saved == publishing.public_article_payload(data)


def test_publish_copy_replaces_existing_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(publishing, "PUBLISHED_DIR", tmp_path)
    path = tmp_path / "rsm--88.json"
    path.write_text('{"old": true}\n', encoding="utf-8")

    result = publishing.publish_copy(
        {
            "city_slug": "rsm",
            "external_id": "88",
            "headline": "New version",
            "revision": 3,
        }
    )

    assert result == path
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["headline"] == "New version"
    assert saved["revision"] == 3
    assert "old" not in saved


def test_remove_published_copy_deletes_existing_and_is_idempotent(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(publishing, "PUBLISHED_DIR", tmp_path)
    data = {"city_slug": "rsm", "external_id": "101"}
    path = tmp_path / "rsm--101.json"
    path.write_text("{}\n", encoding="utf-8")

    first = publishing.remove_published_copy(data)
    second = publishing.remove_published_copy(data)

    assert first == path
    assert second == path
    assert not path.exists()
