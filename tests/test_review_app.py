import asyncio
import json
from pathlib import Path

import pytest

import review_app as review
from gemini_worker import AuditIssue, AuditResult


class FakeRequest:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    async def json(self):
        if self.error:
            raise self.error
        return self.payload


def response_json(response):
    return json.loads(response.body.decode("utf-8"))


def response_text(response):
    return response.body.decode("utf-8")


def make_draft(
    slug="alpha",
    external_id="100",
    meeting_date="2026-09-01",
    review_status="needs_review",
    audit_ok=True,
    published=False,
    **updates,
):
    data = {
        "city_slug": slug,
        "city_name": "Alpha City" if slug == "alpha" else "Beta City",
        "external_id": str(external_id),
        "meeting_date": meeting_date,
        "meeting_title": "Regular City Council Meeting",
        "headline": "Council approves park contract",
        "dek": "A concise summary.",
        "body": ["The Council approved the park contract."],
        "key_facts": ["The contract was approved."],
        "verification_notes": ["No unresolved identity issues."],
        "entity_verification": [],
        "coverage_plan": [],
        "audit_issues": [],
        "audit_ok": audit_ok,
        "review_status": review_status,
        "review_note": "",
        "revision": 1,
        "published": published,
        "published_at": "2026-09-02T00:00:00+00:00" if published else None,
        "source_url": "https://example.com/source",
        "agenda_url": "https://example.com/agenda",
        "recording_url": "https://example.com/recording",
    }
    data.update(updates)
    return data


@pytest.fixture
def review_env(tmp_path, monkeypatch):
    drafts = tmp_path / "drafts"
    drafts.mkdir()

    revisions = drafts / "_revisions"
    revisions.mkdir()

    status_file = tmp_path / "status.json"

    monkeypatch.setattr(review, "DRAFTS", drafts)
    monkeypatch.setattr(review, "REVISION_DIR", revisions)
    monkeypatch.setattr(review, "STATUS_FILE", status_file)
    monkeypatch.setattr(
        review,
        "CITY_NAMES",
        {
            "alpha": "Alpha City",
            "beta": "Beta City",
        },
    )

    return {
        "drafts": drafts,
        "revisions": revisions,
        "status": status_file,
    }


def write_draft(env, data, name=None):
    path = env["drafts"] / (
        name
        or f"{data['city_slug']}--{data['external_id']}.json"
    )
    review.write_json(path, data)
    return path


def test_small_helpers_escape_label_and_message():
    assert review.esc('<b>&"') == "&lt;b&gt;&amp;&quot;"
    assert review.esc(None) == ""

    assert review.review_status({}) == "needs_review"
    assert review.review_status({"review_status": "approved"}) == "approved"
    assert review.review_label("needs_fix") == "Needs fix"
    assert review.review_label("custom_state") == "Custom State"

    assert review.short_message("short", 10) == "short"
    assert review.short_message("first | second", 7) == "first"
    assert review.short_message("abcdefghij", 6) == "abcde…"

    stamp = review.now()
    assert stamp.endswith("+00:00")


def test_json_helpers_and_status_fallback(review_env):
    target = review_env["drafts"] / "sample.json"
    payload = {"name": "José", "value": 3}

    review.write_json(target, payload)
    assert review.read_json(target) == payload
    assert "José" in target.read_text(encoding="utf-8")

    assert review.load_status() == {"cities": {}}

    review_env["status"].write_text("not json", encoding="utf-8")
    assert review.load_status() == {"cities": {}}

    review_env["status"].write_text(
        json.dumps({"cities": {"alpha": {"phase": "done"}}}),
        encoding="utf-8",
    )
    assert review.load_status()["cities"]["alpha"]["phase"] == "done"


def test_current_draft_identity_is_exact():
    data = make_draft()

    assert review.is_current_draft(Path("alpha--100.json"), data)
    assert not review.is_current_draft(Path("alpha--100.before.json"), data)
    assert not review.is_current_draft(
        Path("alpha--100.json"),
        {"city_slug": "alpha", "external_id": ""},
    )


def test_load_draft_rows_filters_and_sorts(review_env):
    alpha_new = make_draft(
        slug="alpha",
        external_id="200",
        meeting_date="2026-09-03",
    )
    beta_same = make_draft(
        slug="beta",
        external_id="300",
        meeting_date="2026-09-03",
    )
    alpha_old = make_draft(
        slug="alpha",
        external_id="100",
        meeting_date="2026-09-01",
    )

    write_draft(review_env, alpha_old)
    write_draft(review_env, beta_same)
    write_draft(review_env, alpha_new)

    (review_env["drafts"] / "broken.json").write_text(
        "{not json",
        encoding="utf-8",
    )
    (review_env["drafts"] / "list.json").write_text(
        "[]",
        encoding="utf-8",
    )

    intelligence = dict(alpha_new)
    write_draft(
        review_env,
        intelligence,
        name="alpha--200.intelligence.json",
    )

    backup = dict(alpha_new)
    write_draft(
        review_env,
        backup,
        name="alpha--200.before-20260903.json",
    )

    rows = review.load_draft_rows()

    assert [
        (data["city_slug"], data["external_id"])
        for _, data in rows
    ] == [
        ("alpha", "200"),
        ("beta", "300"),
        ("alpha", "100"),
    ]

    assert [d["external_id"] for d in review.load_drafts()] == [
        "200",
        "300",
        "100",
    ]


def test_find_draft_and_backup_revision(review_env):
    data = make_draft(revision=7)
    path = write_draft(review_env, data)

    found_path, found = review.find_draft("alpha", 100)
    assert found_path == path
    assert found["headline"] == data["headline"]

    missing_path, missing = review.find_draft("alpha", "999")
    assert missing_path is None
    assert missing is None

    backup = review.backup_revision(path, data)
    assert backup.parent == review_env["revisions"]
    assert "alpha--100--rev007--" in backup.name
    assert review.read_json(backup) == data


def test_shell_escapes_title_but_preserves_supplied_markup():
    page = review.shell("<strong>Body</strong>", '<unsafe>&"')
    text = response_text(page)

    assert "<strong>Body</strong>" in text
    assert "<title>&lt;unsafe&gt;&amp;&quot;</n    assert "CouncilWatch" in text


def test_home_renders_queue_counts_and_processing_cards(review_env):
    approved = make_draft(
        external_id="100",
        review_status="approved",
        review_note="Looks <good>",
    )
    write_draft(review_env, approved)

    review_env["status"].write_text(
        json.dumps(
            {
                "cities": {
                    "beta": {
                        "phase": "failed",
                        "message": "Fetcher failed | detailed traceback",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    text = response_text(review.home())

    assert "<strong>1</strong> approved" in text
    assert "<strong>0</strong> needs review" in text
    assert "Looks &lt;good&gt;" in text
    assert "/story/alpha/100" in text
    assert "Beta City" in text
    assert "class=\"card processing fail\"" in text
    assert "Fetcher failed" in text
    assert "detailed traceback" not in text


def test_story_404_and_rich_rendering(review_env):
    missing = review.story("alpha", "999")
    assert missing.status_code == 404
    assert response_text(missing) == "Draft not found"

    data = make_draft(
        review_status="approved",
        audit_ok=True,
        entity_verification=[
            {
                "status": "CORRECTED",
                "observed_text": "Council Member Otto",
                "canonical_text": "Stephanie Oddo",
                "evidence": "Official roster <matched>",
                "official_source_url": "https://example.com/person?a=1&b=2",
            },
            "ignore me",
        ],
        coverage_plan=[
            {
                "rank": 1,
                "score": 9,
                "must_include": True,
                "topic": "Park Contract",
                "action_status": "approved",
                "summary": "Council action",
                "why_it_matters": "Local spending",
            },
            "ignore me",
        ],
        audit_issues=[
            {
                "severity": "minor",
                "field": "dek",
                "draft_text": "A concise summary.",
                "source_evidence": "Source notes",
                "correction": "Clarify wording",
            },
            "legacy issue string",
        ],
    )
    write_draft(review_env, data)

    text = response_text(review.story("alpha", "100"))

    assert "Stephanie Oddo" in text
    assert "source notes: “Council Member Otto”" in text
    assert "Official roster &lt;matched&gt;" in text
    assert "person?a=1&amp;b=2" in text
    assert "MUST INCLUDE" in text
    assert "Park Contract" in text
    assert "MINOR · dek" in text
    assert "legacy issue string" in text
    assert "Publish locally" in text
    assert "Approved · ready to publish" in text
    assert "Official source -&gt;" in text
    assert "Agenda -&gt;" in text
    assert "Recording -&gt;" in text


def test_story_publication_and_audit_labels(review_env):
    published = make_draft(
        published=True,
        review_status="approved",
        audit_ok=True,
        newsletter_draft_id="bd-1",
        newsletter_draft_revision=2,
        revision=2,
    )
    write_draft(review_env, published)

    text = response_text(review.story("alpha", "100"))
    assert "Published locally" in text
    assert "Unpublish" in text
    assert "Newsletter draft ready" in text
    assert "Audit passed:" in text
    assert "YES" in text

    published["published"] = False
    published["audit_ok"] = False
    published["audit_status"] = "stale_after_manual_edit"
    published["newsletter_draft_error"] = "network down"
    review.write_json(
        review_env["drafts"] / "alpha--100.json",
        published,
    )

    text = response_text(review.story("alpha", "100"))
    assert "STALE — article was edited after the last audit" in text
    assert "Newsletter draft failed" in text
    assert "Publish locally" not in text


def test_edit_story_404_and_form_render(review_env):
    assert review.edit_story("alpha", "404").status_code == 404

    data = make_draft(
        headline='Headline <unsafe> "quoted"',
        body=["Paragraph one.", "Paragraph two."],
        key_facts=["Fact one", "Fact two"],
        verification_notes=["Note one", "Note two"],
    )
    write_draft(review_env, data)

    text = response_text(review.edit_story("alpha", "100"))

    assert "Edit draft" in text
    assert "Headline &lt;unsafe&gt; &quot; in text
    assert "Paragraph one.\n\nParagraph two." in text
    assert "Fact one\nFact two" in text
    assert "Note one\nNote two" in text
    assert "/api/story/${SLUG}/${EXTERNAL_ID}/save" in text


def test_review_action_rejects_missing_invalid_and_unaudited(review_env):
    missing = asyncio.run(
        review.review_action(
            "alpha",
            "999",
            FakeRequest({"status": "approved"}),
        )
    )
    assert missing.status_code == 404

    data = make_draft(audit_ok=False)
    write_draft(review_env, data)

    invalid_json = asyncio.run(
        review.review_action(
            "alpha",
            "100",
            FakeRequest(error=ValueError("bad json")),
        )
    )
    assert invalid_json.status_code == 400
    assert response_json(invalid_json)["error"] == "Invalid review status."

    invalid_status = asyncio.run(
        review.review_action(
            "alpha",
            "100",
            FakeRequest({"status": "published"}),
        )
    )
    assert invalid_status.status_code == 400

    blocked = asyncio.run(
        review.review_action(
            "alpha",
            "100",
            FakeRequest({"status": "approved"}),
        )
    )
    assert blocked.status_code == 409
    assert "Re-audit first" in response_json(blocked)["error"]


def test_review_action_approve_and_reject_published(review_env, monkeypatch):
    data = make_draft(audit_ok=True)
    path = write_draft(review_env, data)

    approved = asyncio.run(
        review.review_action(
            "alpha",
            "100",
            FakeRequest(
                {
                    "status": "approved",
                    "note": "  checked carefully  ",
                }
            ),
        )
    )
    assert approved == {"ok": True, "review_status": "approved"}

    stored = review.read_json(path)
    assert stored["review_status"] == "approved"
    assert stored["review_note"] == "checked carefully"
    assert stored["approved_at"]
    assert stored["rejected_at"] is None
    assert list(review_env["revisions"].glob("*.json"))

    stored["published"] = True
    stored["published_at"] = review.now()
    review.write_json(path, stored)

    removed = []
    monkeypatch.setattr(
        review,
        "remove_published_copy",
        lambda target: removed.append(target["external_id"]) or Path("published.html"),
    )

    rejected = asyncio.run(
        review.review_action(
            "alpha",
            "100",
            FakeRequest({"status": "rejected", "note": "No"}),
        )
    )
    assert rejected == {"ok": True, "review_status": "rejected"}

    stored = review.read_json(path)
    assert stored["review_status"] == "rejected"
    assert stored["approved_at"] is None
    assert stored["rejected_at"]
    assert stored["published"] is False
    assert stored["published_at"] is None
    assert stored["unpublished_at"]
    assert removed == ["100"]


def test_save_story_rejects_bad_payloads(review_env):
    data = make_draft()
    write_draft(review_env, data)

    invalid = asyncio.run(
        review.save_story(
            "alpha",
            "100",
            FakeRequest(error=ValueError("bad")),
        )
    )
    assert invalid.status_code == 400
    assert response_json(invalid)["error"] == "Invalid edit payload."

    blank_headline = asyncio.run(
        review.save_story(
            "alpha",
            "100",
            FakeRequest({"headline": "   ", "body": ["Body"]}),
        )
    )
    assert blank_headline.status_code == 400

    blank_body = asyncio.run(
        review.save_story(
            "alpha",
            "100",
            FakeRequest({"headline": "Headline", "body": []}),
        )
    )
    assert blank_body.status_code == 400


def test_save_story_rejects_whitespace_only_body(review_env):
    data = make_draft()
    write_draft(review_env, data)

    response = asyncio.run(
        review.save_story(
            "alpha",
            "100",
            FakeRequest(
                {
                    "headline": "Headline",
                    "dek": "Dek",
                    "body": ["   ", "\n\t"],
                    "key_facts": [],
                    "verification_notes": [],
                }
            ),
        )
    )

    assert response.status_code == 400
    assert response_json(response)["error"] == "Article body cannot be blank."


def test_save_story_success_resets_review_audit_and_publication(review_env, monkeypatch):
    data = make_draft(
        review_status="approved",
        audit_ok=True,
        published=True,
        revision=3,
        approved_at=review.now(),
    )
    path = write_draft(review_env, data)

    removed = []
    monkeypatch.setattr(
        review,
        "remove_published_copy",
        lambda target: removed.append(target["external_id"]) or Path("old.html"),
    )

    result = asyncio.run(
        review.save_story(
            "alpha",
            "100",
            FakeRequest(
                {
                    "headline": "  New headline  ",
                    "dek": "  New dek  ",
                    "body": [" First paragraph ", "", " Second paragraph "],
                    "key_facts": [" Fact one ", "  "],
                    "verification_notes": [" Note one ", ""],
                }
            ),
        )
    )

    assert result == {"ok": True, "revision": 4}

    stored = review.read_json(path)
    assert stored["headline"] == "New headline"
    assert stored["dek"] == "New dek"
    assert stored["body"] == ["First paragraph", "Second paragraph"]
    assert stored["key_facts"] == ["Fact one"]
    assert stored["verification_notes"] == ["Note one"]
    assert stored["revision"] == 4
    assert stored["manually_edited"] is True
    assert stored["last_manual_edit_at"]
    assert stored["review_status"] == "needs_review"
    assert "Re-audit" in stored["review_note"]
    assert stored["approved_at"] is None
    assert stored["audit_ok"] is False
    assert stored["audit_issues"] == []
    assert stored["audit_status"] == "stale_after_manual_edit"
    assert stored["published"] is False
    assert stored["published_at"] is None
    assert stored["unpublished_at"]
    assert removed == ["100"]


def test_publish_story_gates(review_env):
    missing = review.publish_story("alpha", "404")
    assert missing.status_code == 404

    unaudited = make_draft(audit_ok=False, review_status="approved")
    path = write_draft(review_env, unaudited)

    blocked_audit = review.publish_story("alpha", "100")
    assert blocked_audit.status_code == 409
    assert "passing audit" in response_json(blocked_audit)["error"]

    unaudited["audit_ok"] = True
    unaudited["review_status"] = "needs_review"
    review.write_json(path, unaudited)

    blocked_review = review.publish_story("alpha", "100")
    assert blocked_review.status_code == 409
    assert "approve this exact revision" in response_json(blocked_review)["error"]


def test_publish_story_success(review_env, monkeypatch):
    data = make_draft(
        audit_ok=True,
        review_status="approved",
        revision=5,
    )
    path = write_draft(review_env, data)

    published_payloads = []
    newsletter_payloads = []

    monkeypatch.setattr(
        review,
        "publish_copy",
        lambda target: published_payloads.append(dict(target)) or Path("/tmp/public/alpha.html"),
    )
    monkeypatch.setattr(
        review,
        "ensure_buttondown_draft",
        lambda target: newsletter_payloads.append(dict(target)) or {
            "ok": True,
            "created": True,
            "updated": False,
        },
    )

    result = review.publish_story("alpha", "100")

    assert result["ok"] is True
    assert result["published"] is True
    assert result["published_revision"] == 5
    assert result["local_path"] == "/tmp/public/alpha.html"
    assert result["newsletter"]["ok"] is True

    stored = review.read_json(path)
    assert stored["published"] is True
    assert stored["published_at"]
    assert stored["last_published_at"] == stored["published_at"]
    assert stored["unpublished_at"] is None
    assert stored["published_revision"] == 5
    assert len(published_payloads) == 1
    assert len(newsletter_payloads) == 1


def test_publish_story_newsletter_failure_is_nonblocking(review_env, monkeypatch):
    data = make_draft(audit_ok=True, review_status="approved")
    path = write_draft(review_env, data)

    monkeypatch.setattr(
        review,
        "publish_copy",
        lambda target: Path("/tmp/public.html"),
    )

    def fail_newsletter(_target):
        raise RuntimeError("Buttondown unavailable")

    monkeypatch.setattr(review, "ensure_buttondown_draft", fail_newsletter)

    result = review.publish_story("alpha", "100")

    assert result["ok"] is True
    assert result["published"] is True
    assert result["newsletter"]["ok"] is False
    assert "Buttondown unavailable" in result["newsletter"]["error"]

    stored = review.read_json(path)
    assert stored["published"] is True
    assert stored["newsletter_draft_error"] == "Buttondown unavailable"
    assert stored["newsletter_draft_last_attempt_at"]


def test_unpublish_story(review_env, monkeypatch):
    missing = review.unpublish_story("alpha", "404")
    assert missing.status_code == 404

    data = make_draft(published=True)
    path = write_draft(review_env, data)

    monkeypatch.setattr(
        review,
        "remove_published_copy",
        lambda target: Path("/tmp/removed.html"),
    )

    result = review.unpublish_story("alpha", "100")

    assert result["ok"] is True
    assert result["published"] is False
    assert result["removed_path"] == "/tmp/removed.html"
    assert result["unpublished_at"]

    stored = review.read_json(path)
    assert stored["published"] is False
    assert stored["published_at"] is None
    assert stored["unpublished_at"] == result["unpublished_at"]


def test_reaudit_missing_source_notes(review_env):
    data = make_draft()
    write_draft(review_env, data)

    response = review.reaudit_story("alpha", "100")
    assert response.status_code == 400
    assert "Source notes are missing" in response_json(response)["error"]


def test_reaudit_audit_exception(review_env, monkeypatch):
    data = make_draft()
    write_draft(review_env, data)
    (review_env["drafts"] / "alpha--100.notes.txt").write_text(
        "Source notes",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        review,
        "audit_story",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("model down")),
    )

    response = review.reaudit_story("alpha", "100")
    assert response.status_code == 500
    assert "RuntimeError: model down" in response_json(response)["error"]


def test_reaudit_uses_evidence_boundary_filters_stale_issues_and_revokes_approval(
    review_env,
    monkeypatch,
):
    data = make_draft(
        review_status="approved",
        audit_ok=True,
        published=True,
        body=["The Council approved the park contract."],
    )
    path = write_draft(review_env, data)

    (review_env["drafts"] / "alpha--100.notes.txt").write_text(
        "SOURCE NOTES ONLY",
        encoding="utf-8",
    )
    review.write_json(
        review_env["drafts"] / "alpha--100.intelligence.json",
        {"entities": [{"canonical_text": "Stephanie Oddo"}]},
    )

    monkeypatch.setattr(
        review,
        "audit_verification_context",
        lambda intelligence: "VERIFIED IDENTITY CONTEXT",
    )
    monkeypatch.setattr(
        review,
        "agenda_text",
        lambda url: "OFFICIAL AGENDA",
    )

    captured = {}

    def fake_audit(meeting, notes, agenda, story):
        captured["meeting"] = meeting
        captured["notes"] = notes
        captured["agenda"] = agenda
        captured["story"] = story
        return AuditResult(
            ok=False,
            issues=[
                AuditIssue(
                    severity="material",
                    field="body",
                    draft_text="The Council approved the park contract.",
                    source_evidence="Agenda says discussed",
                    correction="Change approved to discussed.",
                ),
                AuditIssue(
                    severity="material",
                    field="body",
                    draft_text="This stale sentence is not present.",
                    source_evidence="Old issue",
                    correction="Delete it.",
                ),
            ],
        )

    monkeypatch.setattr(review, "audit_story", fake_audit)

    removed = []
    monkeypatch.setattr(
        review,
        "remove_published_copy",
        lambda target: removed.append(target["external_id"]) or Path("old.html"),
    )

    result = review.reaudit_story("alpha", "100")

    assert result == {
        "ok": True,
        "audit_ok": False,
        "issues": 1,
        "material": 1,
    }

    assert captured["notes"] == (
        "SOURCE NOTES ONLY\n\nVERIFIED IDENTITY CONTEXT"
    )
    assert captured["agenda"] == "OFFICIAL AGENDA"
    assert captured["meeting"]["city_slug"] == "alpha"
    assert captured["meeting"]["external_id"] == "100"
    assert captured["story"].headline == data["headline"]

    stored = review.read_json(path)
    assert stored["audit_ok"] is False
    assert len(stored["audit_issues"]) == 1
    assert stored["audit_issues"][0]["draft_text"] == (
        "The Council approved the park contract."
    )
    assert stored["audit_status"] == "fresh"
    assert stored["audit_checked_at"]
    assert stored["review_status"] == "needs_review"
    assert stored["approved_at"] is None
    assert stored["published"] is False
    assert stored["published_at"] is None
    assert stored["unpublished_at"]
    assert removed == ["100"]


def test_reaudit_tolerates_bad_intelligence_and_agenda_failure(review_env, monkeypatch):
    data = make_draft(review_status="needs_review", audit_ok=False)
    path = write_draft(review_env, data)

    (review_env["drafts"] / "alpha--100.notes.txt").write_text(
        "SOURCE NOTES",
        encoding="utf-8",
    )
    (review_env["drafts"] / "alpha--100.intelligence.json").write_text(
        "not json",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        review,
        "agenda_text",
        lambda url: (_ for _ in ()).throw(RuntimeError("agenda down")),
    )

    captured = {}

    def clean_audit(meeting, notes, agenda, story):
        captured["notes"] = notes
        captured["agenda"] = agenda
        return AuditResult(ok=True, issues=[])

    monkeypatch.setattr(review, "audit_story", clean_audit)

    result = review.reaudit_story("alpha", "100")

    assert result == {
        "ok": True,
        "audit_ok": True,
        "issues": 0,
        "material": 0,
    }
    assert captured["notes"] == "SOURCE NOTES"
    assert captured["agenda"] == ""

    stored = review.read_json(path)
    assert stored["audit_ok"] is True
    assert stored["review_status"] == "needs_review"


def test_health_counts_all_review_states_and_publication(review_env):
    drafts = [
        make_draft(external_id="1", review_status="needs_review"),
        make_draft(external_id="2", review_status="approved", published=True),
        make_draft(external_id="3", review_status="needs_fix"),
        make_draft(external_id="4", review_status="rejected"),
        make_draft(external_id="5", review_status="approved"),
    ]

    for data in drafts:
        write_draft(review_env, data)

    result = review.health()

    assert result == {
        "ok": True,
        "draft_count": 5,
        "needs_review": 1,
        "approved": 2,
        "needs_fix": 1,
        "rejected": 1,
        "published": 1,
    }
