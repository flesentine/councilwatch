from __future__ import annotations

import io
import json
import urllib.error
from datetime import datetime, timezone

import pytest

import newsletter


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


def test_now_utc_is_timezone_aware_utc():
    value = datetime.fromisoformat(newsletter.now_utc())

    assert value.tzinfo is not None
    assert value.utcoffset() == timezone.utc.utcoffset(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("", ""),
        ("2026-09-14", "September 14, 2026"),
        ("2026-09-14T18:20:00", "September 14, 2026"),
        ("not-a-date", "not-a-date"),
    ],
)
def test_pretty_date(value, expected):
    assert newsletter.pretty_date(value) == expected


def test_clean_subject_text_collapses_whitespace_and_trailing_punctuation():
    assert newsletter._clean_subject_text("  Hello\n  world.  ") == "Hello world"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"city_slug": "rsm", "city_name": "Rancho Santa Margarita"}, "RSM"),
        ({"city_slug": "unknown", "city_name": "Test City"}, "Test City"),
        ({}, "CouncilWatch"),
    ],
)
def test_subject_city(data, expected):
    assert newsletter._subject_city(data) == expected


@pytest.mark.parametrize(
    ("headline", "data", "short_city", "expected"),
    [
        (
            "Rancho Santa Margarita City Council Approves Park Work",
            {"city_name": "Rancho Santa Margarita"},
            "RSM",
            "Approves Park Work",
        ),
        (
            "RSM Council Reviews Budget",
            {"city_name": "Rancho Santa Margarita"},
            "RSM",
            "Reviews Budget",
        ),
        (
            "City Council Awards Contract",
            {},
            "CouncilWatch",
            "Awards Contract",
        ),
        (
            "Planning Commission Reviews Project",
            {"city_name": "Test City"},
            "Test City",
            "Planning Commission Reviews Project",
        ),
    ],
)
def test_strip_city_council_prefix(headline, data, short_city, expected):
    assert (
        newsletter._strip_city_council_prefix(headline, data, short_city)
        == expected
    )


def test_compress_subject_phrases_and_conjunctions():
    result = newsletter._compress_subject_phrases(
        "Geotechnical engineering agreements and school zone speed limits and traffic signal materials."
    )

    assert result == "Geotechnical Contracts & School Zones & Traffic Signals"


def test_trim_subject_core_prefers_meaningful_boundary():
    core = "First substantial topic, second substantial topic, third topic"
    result = newsletter._trim_subject_core(core, 43)

    assert result == "First substantial topic"


def test_trim_subject_core_uses_ellipsis_without_boundary():
    result = newsletter._trim_subject_core(
        "One verylongword followedbyanotherword", 22
    )

    assert result.endswith("...")
    assert len(result) <= 22


def test_trim_subject_core_tiny_budget_is_hard_cut():
    assert newsletter._trim_subject_core("abcdef", 4) == "abcd"


def test_newsletter_subject_blank_headline_uses_fallback():
    assert newsletter.newsletter_subject({"headline": "   "}) == "CouncilWatch update"


def test_newsletter_subject_strips_city_and_compresses_content():
    subject = newsletter.newsletter_subject(
        {
            "city_slug": "rsm",
            "city_name": "Rancho Santa Margarita",
            "headline": (
                "Rancho Santa Margarita City Council Approves "
                "Agenda Management Software and School Zone Speed Limits"
            ),
        }
    )

    assert subject == "RSM: Approves Agenda Software & School Zones"


def test_newsletter_subject_drops_generic_action_verb_before_topics():
    subject = newsletter.newsletter_subject(
        {
            "city_slug": "rsm",
            "headline": "Approves Community Center Contract",
        },
        max_length=32,
    )

    assert subject == "RSM: Community Center Contract"
    assert len(subject) <= 32


def test_newsletter_subject_hard_caps_long_content():
    subject = newsletter.newsletter_subject(
        {
            "city_name": "Test City",
            "headline": (
                "Test City Council Discusses a very long first issue, "
                "a second long issue and a third long issue"
            ),
        },
        max_length=44,
    )

    assert subject.startswith("Test City: ")
    assert len(subject) <= 44


def test_newsletter_body_builds_escaped_rich_html_and_official_links():
    body = newsletter.newsletter_body(
        {
            "city_name": "RSM & Friends",
            "meeting_date": "2026-09-14",
            "dek": "Budget <review> & details",
            "body": [" First <paragraph> & more ", "", "Second paragraph"],
            "source_url": "https://example.test/source?a=1&b=2",
            "agenda_url": "ftp://example.test/not-allowed",
            "recording_url": "http://example.test/watch?q=\"quoted\"",
        }
    )

    assert body.startswith("<!-- buttondown-editor-mode: fancy -->")
    assert "RSM &amp; Friends · September 14, 2026" in body
    assert "<em>Budget &lt;review&gt; &amp; details</em>" in body
    assert "<p>First &lt;paragraph&gt; &amp; more</p>" in body
    assert "<p>Second paragraph</p>" in body
    assert "Official sources" in body
    assert "Official source" in body
    assert "Recording" in body
    assert "ftp://" not in body
    assert "a=1&amp;b=2" in body
    assert "&quot;quoted&quot;" in body
    assert "technology-assisted analysis" in body


def test_newsletter_body_minimal_story_omits_optional_sections():
    body = newsletter.newsletter_body({"body": []})

    assert "Official sources" not in body
    assert "<em>" not in body
    assert "CouncilWatch covers local government" in body


def test_request_requires_api_key(monkeypatch):
    monkeypatch.setattr(newsletter, "BUTTONDOWN_API_KEY", "")

    with pytest.raises(RuntimeError, match="BUTTONDOWN_API_KEY is not configured"):
        newsletter._request("POST", "emails", {"hello": "world"})


def test_request_builds_buttondown_request_and_decodes_json(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response(b'{"id":"draft-123","status":"draft"}')

    monkeypatch.setattr(newsletter, "BUTTONDOWN_API_KEY", "secret-key")
    monkeypatch.setattr(
        newsletter,
        "BUTTONDOWN_API_BASE",
        "https://api.buttondown.test/v1/",
    )
    monkeypatch.setattr(newsletter.urllib.request, "urlopen", fake_urlopen)

    result = newsletter._request(
        "PATCH",
        "/emails/draft-123",
        {"subject": "Caf\u00e9", "status": "draft"},
    )

    request = captured["request"]
    assert request.full_url == "https://api.buttondown.test/v1/emails/draft-123"
    assert request.get_method() == "PATCH"
    assert request.headers["Authorization"] == "Token secret-key"
    assert request.headers["Content-type"] == "application/json"
    assert json.loads(request.data.decode("utf-8")) == {
        "subject": "Caf\u00e9",
        "status": "draft",
    }
    assert captured["timeout"] == 30
    assert result == {"id": "draft-123", "status": "draft"}


def test_request_wraps_http_error_with_response_detail(monkeypatch):
    def fail(_request, timeout):
        assert timeout == 30
        raise urllib.error.HTTPError(
            "https://api.buttondown.test/v1/emails",
            422,
            "Unprocessable",
            hdrs=None,
            fp=io.BytesIO(b"bad payload details"),
        )

    monkeypatch.setattr(newsletter, "BUTTONDOWN_API_KEY", "key")
    monkeypatch.setattr(newsletter.urllib.request, "urlopen", fail)

    with pytest.raises(
        RuntimeError,
        match=r"Buttondown HTTP 422: bad payload details",
    ):
        newsletter._request("POST", "emails", {})


def test_request_wraps_url_error(monkeypatch):
    monkeypatch.setattr(newsletter, "BUTTONDOWN_API_KEY", "key")
    monkeypatch.setattr(
        newsletter.urllib.request,
        "urlopen",
        lambda _request, timeout: (_ for _ in ()).throw(
            urllib.error.URLError("network down")
        ),
    )

    with pytest.raises(RuntimeError, match="Buttondown connection failed: network down"):
        newsletter._request("POST", "emails", {})


def test_request_rejects_invalid_json(monkeypatch):
    monkeypatch.setattr(newsletter, "BUTTONDOWN_API_KEY", "key")
    monkeypatch.setattr(
        newsletter.urllib.request,
        "urlopen",
        lambda _request, timeout: _Response(b"not-json"),
    )

    with pytest.raises(RuntimeError, match="Buttondown returned invalid JSON"):
        newsletter._request("POST", "emails", {})


def test_ensure_buttondown_draft_reuses_current_revision_without_request(monkeypatch):
    data = {
        "revision": 3,
        "newsletter_draft_id": "draft-current",
        "newsletter_draft_revision": 3,
        "newsletter_draft_subject": "Existing subject",
    }

    monkeypatch.setattr(
        newsletter,
        "_request",
        lambda *args, **kwargs: pytest.fail("Buttondown should not be called"),
    )

    result = newsletter.ensure_buttondown_draft(data)

    assert result == {
        "ok": True,
        "created": False,
        "updated": False,
        "id": "draft-current",
        "status": "draft",
        "subject": "Existing subject",
        "message": "Existing Buttondown draft is current.",
    }


def test_ensure_buttondown_draft_creates_new_draft_and_updates_state(monkeypatch):
    calls = []
    data = {
        "revision": 4,
        "city_slug": "rsm",
        "headline": "RSM City Council Reviews Budget",
        "body": ["Story paragraph"],
    }

    monkeypatch.setattr(newsletter, "now_utc", lambda: "2026-09-14T01:00:00+00:00")

    def fake_request(method, path, payload):
        calls.append((method, path, payload))
        return {"id": "draft-new", "status": "draft"}

    monkeypatch.setattr(newsletter, "_request", fake_request)

    result = newsletter.ensure_buttondown_draft(data)

    assert calls[0][0:2] == ("POST", "emails")
    assert calls[0][2]["status"] == "draft"
    assert calls[0][2]["subject"] == "RSM: Reviews Budget"
    assert "Story paragraph" in calls[0][2]["body"]
    assert result["ok"] is True
    assert result["created"] is True
    assert result["updated"] is False
    assert result["id"] == "draft-new"
    assert result["subject_length"] == len(result["subject"])
    assert data["newsletter_draft_id"] == "draft-new"
    assert data["newsletter_draft_revision"] == 4
    assert data["newsletter_draft_status"] == "draft"
    assert data["newsletter_draft_last_attempt_at"] == "2026-09-14T01:00:00+00:00"
    assert data["newsletter_draft_created_at"] == "2026-09-14T01:00:00+00:00"
    assert data["newsletter_draft_error"] is None


def test_ensure_buttondown_draft_updates_existing_and_can_reuse_existing_id(monkeypatch):
    calls = []
    data = {
        "revision": 6,
        "newsletter_draft_id": "draft-old",
        "newsletter_draft_revision": 5,
        "city_name": "Test City",
        "headline": "Test City Council Awards Contract",
        "body": ["Updated story"],
    }

    monkeypatch.setattr(newsletter, "now_utc", lambda: "2026-09-14T02:00:00+00:00")

    def fake_request(method, path, payload):
        calls.append((method, path, payload))
        return {"status": "draft"}

    monkeypatch.setattr(newsletter, "_request", fake_request)

    result = newsletter.ensure_buttondown_draft(data)

    assert calls[0][0:2] == ("PATCH", "emails/draft-old")
    assert result["created"] is False
    assert result["updated"] is True
    assert result["id"] == "draft-old"
    assert data["newsletter_draft_revision"] == 6
    assert "newsletter_draft_created_at" not in data


def test_ensure_buttondown_draft_force_update_bypasses_current_revision(monkeypatch):
    data = {
        "revision": 2,
        "newsletter_draft_id": "draft-2",
        "newsletter_draft_revision": 2,
        "headline": "Council Reviews Budget",
    }
    calls = []

    monkeypatch.setattr(newsletter, "now_utc", lambda: "attempt")
    monkeypatch.setattr(
        newsletter,
        "_request",
        lambda method, path, payload: (
            calls.append((method, path)),
            {"id": "draft-2"},
        )[1],
    )

    result = newsletter.ensure_buttondown_draft(data, force_update=True)

    assert calls == [("PATCH", "emails/draft-2")]
    assert result["updated"] is True


def test_ensure_buttondown_draft_missing_response_id_fails_without_claiming_success(monkeypatch):
    data = {
        "revision": 1,
        "headline": "Council Reviews Budget",
    }

    monkeypatch.setattr(newsletter, "now_utc", lambda: "attempt-time")
    monkeypatch.setattr(newsletter, "_request", lambda *args: {})

    result = newsletter.ensure_buttondown_draft(data)

    assert result["ok"] is False
    assert result["created"] is False
    assert result["updated"] is False
    assert result["id"] is None
    assert "did not contain an email ID" in result["error"]
    assert data["newsletter_draft_last_attempt_at"] == "attempt-time"
    assert "did not contain an email ID" in data["newsletter_draft_error"]
    assert "newsletter_draft_id" not in data


def test_ensure_buttondown_draft_request_failure_records_error_and_preserves_existing_id(monkeypatch):
    data = {
        "revision": 8,
        "newsletter_draft_id": "existing-id",
        "newsletter_draft_revision": 7,
        "headline": "Council Reviews Budget",
    }

    monkeypatch.setattr(newsletter, "now_utc", lambda: "attempt-time")

    def fail(*_args):
        raise RuntimeError("Buttondown unavailable")

    monkeypatch.setattr(newsletter, "_request", fail)

    result = newsletter.ensure_buttondown_draft(data)

    assert result == {
        "ok": False,
        "created": False,
        "updated": False,
        "id": "existing-id",
        "error": "Buttondown unavailable",
    }
    assert data["newsletter_draft_last_attempt_at"] == "attempt-time"
    assert data["newsletter_draft_error"] == "Buttondown unavailable"
    assert data["newsletter_draft_id"] == "existing-id"
